import copy
import os
import zipfile
from ipaddress import ip_network

import dateutil.parser
from django.db import OperationalError, ProgrammingError, connection, transaction
from django.db.models import Count, Q
from django.http import FileResponse
from django.utils.timezone import now

from account.decorators import check_contest_permission, ensure_created_by
from account.models import User
from submission.models import Submission, JudgeStatus
from utils.api import APIView, validate_serializer
from utils.cache import cache
from utils.constants import CacheKey, ContestRuleType, ContestStatus
from utils.shortcuts import rand_str
from utils.tasks import delete_files
from ..models import Contest, ContestAnnouncement, ACMContestRank, OIContestRank
from ..serializers import (ContestAnnouncementSerializer, ContestAdminSerializer,
                           CreateConetestSeriaizer, CreateContestAnnouncementSerializer,
                           EditConetestSeriaizer, EditContestAnnouncementSerializer,
                           ACMContesHelperSerializer, )


# 后台比赛列表「点表头排序」允许的字段白名单（?ordering=start_time / -start_time …）。
# 键 == 前端传的排序键；值 == 实际 order_by 的字段序列（owner 先按中文名、再按用户名）。
# ⚠️ 不在白名单里的值一律忽略 —— 绝不能把用户输入直接丢给 order_by。
ADMIN_CONTEST_ORDERING_FIELDS = {
    "id": ("id",),
    "title": ("title",),
    "owner": ("created_by__userprofile__real_name", "created_by__username"),
    "start_time": ("start_time",),
    "end_time": ("end_time",),
    "create_time": ("create_time",),
}


class ContestAPI(APIView):
    @validate_serializer(CreateConetestSeriaizer)
    def post(self, request):
        data = request.data
        data["start_time"] = dateutil.parser.parse(data["start_time"])
        data["end_time"] = dateutil.parser.parse(data["end_time"])
        data["created_by"] = request.user
        if data["end_time"] <= data["start_time"]:
            return self.error("Start time must occur earlier than end time")
        if data.get("password") and data["password"] == "":
            data["password"] = None
        for ip_range in data["allowed_ip_ranges"]:
            try:
                ip_network(ip_range, strict=False)
            except ValueError:
                return self.error(f"{ip_range} is not a valid cidr network")
        contest = Contest.objects.create(**data)
        return self.success(ContestAdminSerializer(contest).data)

    @validate_serializer(EditConetestSeriaizer)
    def put(self, request):
        data = request.data
        try:
            contest = Contest.objects.get(id=data.pop("id"))
            ensure_created_by(contest, request.user)
        except Contest.DoesNotExist:
            return self.error("Contest does not exist")
        data["start_time"] = dateutil.parser.parse(data["start_time"])
        data["end_time"] = dateutil.parser.parse(data["end_time"])
        if data["end_time"] <= data["start_time"]:
            return self.error("Start time must occur earlier than end time")
        if not data["password"]:
            data["password"] = None
        for ip_range in data["allowed_ip_ranges"]:
            try:
                ip_network(ip_range, strict=False)
            except ValueError:
                return self.error(f"{ip_range} is not a valid cidr network")
        if not contest.real_time_rank and data.get("real_time_rank"):
            cache_key = f"{CacheKey.contest_rank_cache}:{contest.id}"
            cache.delete(cache_key)

        for k, v in data.items():
            setattr(contest, k, v)
        contest.save()
        return self.success(ContestAdminSerializer(contest).data)

    def get(self, request):
        contest_id = request.GET.get("id")
        if contest_id:
            try:
                contest = Contest.objects.get(id=contest_id)
                ensure_created_by(contest, request.user)
                return self.success(ContestAdminSerializer(contest).data)
            except Contest.DoesNotExist:
                return self.error("Contest does not exist")

        contests = Contest.objects.select_related(
            'created_by', 'created_by__userprofile').all().order_by("-create_time")
        if request.user.is_admin():
            contests = contests.filter(created_by=request.user)

        keyword = request.GET.get("keyword")
        if keyword:
            contests = contests.filter(title__icontains=keyword)
        owner = request.GET.get("owner")
        if owner:
            # 与公开列表同款：用户名或中文姓名都能筛。下拉传的是 username，
            # 但老链接/手输中文名（2026-09-22 那版分享格式）也必须能用。
            contests = contests.filter(Q(created_by__username__icontains=owner) |
                                       Q(created_by__userprofile__real_name__icontains=owner))
        rule_type = request.GET.get("rule_type")
        if rule_type in (ContestRuleType.ACM, ContestRuleType.OI):
            contests = contests.filter(rule_type=rule_type)
        status = request.GET.get("status")
        if status:
            # 与公开 ContestListAPI 完全一致的语义；非法值自然落入「不过滤」。
            cur = now()
            if status == ContestStatus.CONTEST_NOT_START:
                contests = contests.filter(start_time__gt=cur)
            elif status == ContestStatus.CONTEST_ENDED:
                contests = contests.filter(end_time__lt=cur)
            elif status == ContestStatus.CONTEST_UNDERWAY:
                contests = contests.filter(start_time__lte=cur, end_time__gte=cur)
        # 表头点击排序。认不出的值一律忽略（保持默认 -create_time）。
        ordering = request.GET.get("ordering")
        if ordering:
            fields = ADMIN_CONTEST_ORDERING_FIELDS.get(ordering.lstrip("-"))
            if fields:
                desc = ordering.startswith("-")
                # 末尾补 id：时间/姓名相同的行之间顺序确定，翻页不会重复或漏行。
                contests = contests.order_by(
                    *[("-" if desc else "") + field for field in fields], "id")
        return self.success(self.paginate_data(request, contests, ContestAdminSerializer))

    def delete(self, request):
        contest_id = request.GET.get("id")
        if not contest_id:
            return self.error("Parameter error")

        hard_delete = request.GET.get("hard") == "1"

        try:
            contest = Contest.objects.get(id=contest_id)
            ensure_created_by(contest, request.user)
        except Contest.DoesNotExist:
            return self.error("Contest does not exist")

        cache_key = f"{CacheKey.contest_rank_cache}:{contest.id}"

        if hard_delete:
            # Running contest data is still being mutated by submissions and rank updates.
            if contest.status == ContestStatus.CONTEST_UNDERWAY:
                return self.error("Running contest cannot be hard deleted")
            try:
                deleted_rows, _ = contest.delete()
            except OperationalError:
                # PostgreSQL submission table can be physically corrupted on disk in some deployments.
                # Fallback to a SQL cleanup path that does not touch submission table.
                try:
                    with transaction.atomic():
                        with connection.cursor() as cursor:
                            cursor.execute("SET LOCAL session_replication_role = replica")
                            cursor.execute(
                                "DELETE FROM problem_tags "
                                "WHERE problem_id IN (SELECT id FROM problem WHERE contest_id = %s)",
                                [contest.id]
                            )
                            cursor.execute("DELETE FROM acm_contest_rank WHERE contest_id = %s", [contest.id])
                            cursor.execute("DELETE FROM oi_contest_rank WHERE contest_id = %s", [contest.id])
                            cursor.execute("DELETE FROM contest_announcement WHERE contest_id = %s", [contest.id])
                            cursor.execute("DELETE FROM problem WHERE contest_id = %s", [contest.id])
                            cursor.execute("DELETE FROM contest WHERE id = %s", [contest.id])
                            deleted_rows = cursor.rowcount
                except (OperationalError, ProgrammingError):
                    return self.error("Database storage is inconsistent (submission table corrupted). Contact DBA and repair PostgreSQL before hard delete.")
            cache.delete(cache_key)
            return self.success({"id": int(contest_id), "mode": "hard", "deleted_rows": deleted_rows})

        if contest.visible:
            contest.visible = False
            contest.save(update_fields=["visible"])
        cache.delete(cache_key)
        return self.success({"id": contest.id, "mode": "soft", "visible": contest.visible})


class ContestTeacherListAPI(APIView):
    """后台比赛列表「任课老师」下拉的候选项。

    与公开 /api/contest_teachers 的区别：后台要管理**不可见**的比赛，
    所以这里不按 visible 过滤；scope 与后台列表完全一致
    （Admin 只统计自己的比赛，Super Admin 统计全部）。
    """

    def get(self, request):
        contests = Contest.objects.all()
        if request.user.is_admin():
            contests = contests.filter(created_by=request.user)
        # ⚠️ 必须先 order_by() 清掉模型默认序（Contest.Meta.ordering = ("-start_time",)）：
        #    values()+annotate() 会把默认序字段一并塞进 GROUP BY ⇒ 同一个人被拆成多行。
        rows = (contests
                .order_by()
                .values("created_by__username", "created_by__userprofile__real_name")
                .annotate(count=Count("id")))
        data = [{
            "username": row["created_by__username"],
            "real_name": (row["created_by__userprofile__real_name"] or "").strip(),
            "count": row["count"],
        } for row in rows]
        data.sort(key=lambda x: (-x["count"], x["real_name"], x["username"]))
        return self.success(data)


class ContestAnnouncementAPI(APIView):
    @validate_serializer(CreateContestAnnouncementSerializer)
    def post(self, request):
        """
        Create one contest_announcement.
        """
        data = request.data
        try:
            contest = Contest.objects.get(id=data.pop("contest_id"))
            ensure_created_by(contest, request.user)
            data["contest"] = contest
            data["created_by"] = request.user
        except Contest.DoesNotExist:
            return self.error("Contest does not exist")
        announcement = ContestAnnouncement.objects.create(**data)
        return self.success(ContestAnnouncementSerializer(announcement).data)

    @validate_serializer(EditContestAnnouncementSerializer)
    def put(self, request):
        """
        update contest_announcement
        """
        data = request.data
        try:
            contest_announcement = ContestAnnouncement.objects.get(id=data.pop("id"))
            ensure_created_by(contest_announcement, request.user)
        except ContestAnnouncement.DoesNotExist:
            return self.error("Contest announcement does not exist")
        for k, v in data.items():
            setattr(contest_announcement, k, v)
        contest_announcement.save()
        return self.success()

    def delete(self, request):
        """
        Delete one contest_announcement.
        """
        contest_announcement_id = request.GET.get("id")
        if contest_announcement_id:
            if request.user.is_admin():
                ContestAnnouncement.objects.filter(id=contest_announcement_id,
                                                   contest__created_by=request.user).delete()
            else:
                ContestAnnouncement.objects.filter(id=contest_announcement_id).delete()
        return self.success()

    def get(self, request):
        """
        Get one contest_announcement or contest_announcement list.
        """
        contest_announcement_id = request.GET.get("id")
        if contest_announcement_id:
            try:
                contest_announcement = ContestAnnouncement.objects.get(id=contest_announcement_id)
                ensure_created_by(contest_announcement, request.user)
                return self.success(ContestAnnouncementSerializer(contest_announcement).data)
            except ContestAnnouncement.DoesNotExist:
                return self.error("Contest announcement does not exist")

        contest_id = request.GET.get("contest_id")
        if not contest_id:
            return self.error("Parameter error")
        contest_announcements = ContestAnnouncement.objects.filter(contest_id=contest_id)
        if request.user.is_admin():
            contest_announcements = contest_announcements.filter(created_by=request.user)
        keyword = request.GET.get("keyword")
        if keyword:
            contest_announcements = contest_announcements.filter(title__contains=keyword)
        return self.success(ContestAnnouncementSerializer(contest_announcements, many=True).data)


class ACMContestHelper(APIView):
    @check_contest_permission(check_type="ranks")
    def get(self, request):
        ranks = ACMContestRank.objects.filter(contest=self.contest, accepted_number__gt=0) \
            .values("id", "user__username", "user__userprofile__real_name", "submission_info")
        results = []
        for rank in ranks:
            for problem_id, info in rank["submission_info"].items():
                if info["is_ac"]:
                    results.append({
                        "id": rank["id"],
                        "username": rank["user__username"],
                        "real_name": rank["user__userprofile__real_name"],
                        "problem_id": problem_id,
                        "ac_info": info,
                        "checked": info.get("checked", False)
                    })
        results.sort(key=lambda x: -x["ac_info"]["ac_time"])
        return self.success(results)

    @check_contest_permission(check_type="ranks")
    @validate_serializer(ACMContesHelperSerializer)
    def put(self, request):
        data = request.data
        try:
            rank = ACMContestRank.objects.get(pk=data["rank_id"])
        except ACMContestRank.DoesNotExist:
            return self.error("Rank id does not exist")
        problem_rank_status = rank.submission_info.get(data["problem_id"])
        if not problem_rank_status:
            return self.error("Problem id does not exist")
        problem_rank_status["checked"] = data["checked"]
        rank.save(update_fields=("submission_info",))
        return self.success()


class DownloadContestSubmissions(APIView):
    def _dump_submissions(self, contest, exclude_admin=True, dlType="0"):
        problem_ids = contest.problem_set.all().values_list("id", "_id")
        id2display_id = {k[0]: k[1] for k in problem_ids}
        ac_map = {k[0]: False for k in problem_ids}
        if dlType == "0": # download all
            submissions = Submission.objects.filter(contest=contest).order_by("-create_time")
        else:
            submissions = Submission.objects.filter(contest=contest, result=JudgeStatus.ACCEPTED).order_by("-create_time")
        user_ids = submissions.values_list("user_id", flat=True)
        users = User.objects.filter(id__in=user_ids)
        path = f"/tmp/{rand_str()}.zip"
        with zipfile.ZipFile(path, "w") as zip_file:
            for user in users:
                if user.is_admin_role() and exclude_admin:
                    continue
                user_ac_map = copy.deepcopy(ac_map)
                user_submissions = submissions.filter(user_id=user.id)
                for submission in user_submissions:
                    problem_id = submission.problem_id
                    if user_ac_map[problem_id]:
                        continue
                    file_name = f"{user.username}_{id2display_id[submission.problem_id]}.txt"
                    compression = zipfile.ZIP_DEFLATED
                    zip_file.writestr(zinfo_or_arcname=f"{file_name}",
                                      data=submission.code,
                                      compress_type=compression)
                    user_ac_map[problem_id] = True
        return path

    def get(self, request):
        contest_id = request.GET.get("contest_id")
        if not contest_id:
            return self.error("Parameter error")
        try:
            contest = Contest.objects.get(id=contest_id)
            ensure_created_by(contest, request.user)
        except Contest.DoesNotExist:
            return self.error("Contest does not exist")

        exclude_admin = request.GET.get("exclude_admin") == "1"
        dlType = request.GET.get("dlType")
        zip_path = self._dump_submissions(contest, exclude_admin, dlType)
        delete_files.send_with_options(args=(zip_path,), delay=300_000)
        resp = FileResponse(open(zip_path, "rb"))
        resp["Content-Type"] = "application/zip"
        resp["Content-Disposition"] = f"attachment;filename={os.path.basename(zip_path)}"
        return resp
