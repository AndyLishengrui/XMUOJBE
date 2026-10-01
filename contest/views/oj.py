import io

import xlsxwriter
from django.db.models import Q
from django.http import HttpResponse
from django.utils.timezone import now
from django.core.cache import cache

from problem.models import Problem
from utils.api import APIView, validate_serializer
from utils.constants import CacheKey, CONTEST_PASSWORD_SESSION_KEY
from utils.shortcuts import datetime2str, check_is_id
from account.models import AdminType, User, UserProfile
from account.models import AdminType
from account.decorators import login_required, check_contest_permission, check_contest_password

from utils.constants import ContestRuleType, ContestStatus, ContestType
from ..models import ContestAnnouncement, Contest, OIContestRank, ACMContestRank, ContestParticipation
from ..serializers import ContestAnnouncementSerializer
from ..serializers import ContestSerializer, ContestPasswordVerifySerializer
from ..serializers import OIContestRankSerializer, ACMContestRankSerializer


# 比赛列表「点表头排序」允许的字段白名单（?ordering=start_time / -start_time …）。
# ⚠️ 必须是白名单：用户输入直接丢给 order_by 等于允许按任意字段（含关联字段）排序。
# 键 == 前端列的 key；不传 ordering 时**完全保持模型默认序**（Contest.Meta.ordering
# = ("-start_time",)），线上老前端不传这个参数，它的结果必须逐字节不变。
CONTEST_ORDERING_FIELDS = {
    "title": "title",
    "start_time": "start_time",
    "end_time": "end_time",
}


class ContestAnnouncementListAPI(APIView):
    @check_contest_permission(check_type="announcements")
    def get(self, request):
        contest_id = request.GET.get("contest_id")
        if not contest_id:
            return self.error("Invalid parameter, contest_id is required")
        data = ContestAnnouncement.objects.select_related("created_by").filter(contest_id=contest_id, visible=True)
        max_id = request.GET.get("max_id")
        if max_id:
            data = data.filter(id__gt=max_id)
        return self.success(ContestAnnouncementSerializer(data, many=True).data)


class ContestAPI(APIView):
    def get(self, request):
        id = request.GET.get("id")
        if not id or not check_is_id(id):
            return self.error("Invalid parameter, id is required")
        try:
            contest = Contest.objects.get(id=id, visible=True)
        except Contest.DoesNotExist:
            return self.error("Contest does not exist")
        # Record entry for authenticated users when contest page is accessed.
        if request.user.is_authenticated:
            can_record = contest.contest_type == ContestType.PUBLIC_CONTEST or request.user.is_contest_admin(contest)
            if not can_record and contest.contest_type == ContestType.PASSWORD_PROTECTED_CONTEST:
                session_pass = request.session.get(CONTEST_PASSWORD_SESSION_KEY, {}).get(contest.id)
                can_record = check_contest_password(session_pass, contest.password)
            if can_record:
                try:
                    ContestParticipation.mark_enter(request.user, contest)
                    ContestParticipation.calibrate_once(request.user, contest)
                except Exception:
                    pass
        data = ContestSerializer(contest).data
        data["now"] = datetime2str(now())
        return self.success(data)


class ContestListAPI(APIView):
    def get(self, request):
        contests = Contest.objects.select_related(
            "created_by", "created_by__userprofile").filter(visible=True)
        keyword = request.GET.get("keyword")
        rule_type = request.GET.get("rule_type")
        status = request.GET.get("status")
        owner = request.GET.get("owner")
        category = request.GET.get("category")
        if keyword:
            # 关键词除了标题，也匹配「创建者（任课老师）」的用户名/姓名，
            # 这样学生可以直接在比赛列表的搜索框里输入老师名字筛出自己班的实验，
            # 例如 /contest?keyword=曾鸣
            contests = contests.filter(Q(title__icontains=keyword) |
                                       Q(created_by__username__icontains=keyword) |
                                       Q(created_by__userprofile__real_name__icontains=keyword))
        if owner:
            # 与后台比赛列表同款的「按老师过滤」
            contests = contests.filter(Q(created_by__username__icontains=owner) |
                                       Q(created_by__userprofile__real_name__icontains=owner))
        # 「实验」与「题库」分列表。⚠️ 不传/其它值 → 一律不过滤：
        # 生产上的老前端从不传 category，它拿全量后自己过滤，行为必须逐字节不变。
        if category == "experiment":
            contests = contests.filter(is_question_bank=False)
        elif category == "question_bank":
            contests = contests.filter(is_question_bank=True)
        if rule_type:
            contests = contests.filter(rule_type=rule_type)
        if status:
            cur = now()
            if status == ContestStatus.CONTEST_NOT_START:
                contests = contests.filter(start_time__gt=cur)
            elif status == ContestStatus.CONTEST_ENDED:
                contests = contests.filter(end_time__lt=cur)
            else:
                contests = contests.filter(start_time__lte=cur, end_time__gte=cur)
        # 表头点击排序（题目 / 开始时间 / 截止时间）。认不出的值一律忽略 ——
        # 前端有个「任课老师」列仍是**前端当前页**排序，它不传这个参数。
        ordering = request.GET.get("ordering")
        if ordering:
            field = CONTEST_ORDERING_FIELDS.get(ordering.lstrip("-"))
            if field:
                # 末尾补 id：时间相同的行之间顺序确定，翻页不会重复/漏行
                contests = contests.order_by(("-" if ordering.startswith("-") else "") + field, "id")
        return self.success(self.paginate_data(request, contests, ContestSerializer))


class ContestPasswordVerifyAPI(APIView):
    @validate_serializer(ContestPasswordVerifySerializer)
    @login_required
    def post(self, request):
        data = request.data
        try:
            contest = Contest.objects.get(id=data["contest_id"], visible=True, password__isnull=False)
        except Contest.DoesNotExist:
            return self.error("Contest does not exist")
        if not check_contest_password(data["password"], contest.password):
            return self.error("Wrong password or password expired")

        # password verify OK.
        if CONTEST_PASSWORD_SESSION_KEY not in request.session:
            request.session[CONTEST_PASSWORD_SESSION_KEY] = {}
        request.session[CONTEST_PASSWORD_SESSION_KEY][contest.id] = data["password"]
        # https://docs.djangoproject.com/en/dev/topics/http/sessions/#when-sessions-are-saved
        request.session.modified = True
        try:
            ContestParticipation.mark_enter(request.user, contest)
            ContestParticipation.calibrate_once(request.user, contest)
        except Exception:
            pass
        return self.success(True)


class ContestAccessAPI(APIView):
    @login_required
    def get(self, request):
        contest_id = request.GET.get("contest_id")
        if not contest_id:
            return self.error()
        try:
            contest = Contest.objects.get(id=contest_id, visible=True, password__isnull=False)
        except Contest.DoesNotExist:
            return self.error("Contest does not exist")
        session_pass = request.session.get(CONTEST_PASSWORD_SESSION_KEY, {}).get(contest.id)
        access = check_contest_password(session_pass, contest.password)
        if access:
            try:
                ContestParticipation.mark_enter(request.user, contest)
                ContestParticipation.calibrate_once(request.user, contest)
            except Exception:
                pass
        return self.success({"access": access})


class ContestRankAPI(APIView):
    def get_rank(self):
        if self.contest.rule_type == ContestRuleType.ACM:
            return ACMContestRank.objects.filter(contest=self.contest,
                                                 user__admin_type=AdminType.REGULAR_USER,
                                                 user__is_disabled=False).\
                select_related("user").order_by("-accepted_number", "total_time")
        else:
            return OIContestRank.objects.filter(contest=self.contest,
                                                user__admin_type=AdminType.REGULAR_USER,
                                                user__is_disabled=False). \
                select_related("user").order_by("-total_score")

    def column_string(self, n):
        string = ""
        while n > 0:
            n, remainder = divmod(n - 1, 26)
            string = chr(65 + remainder) + string
        return string

    @check_contest_permission(check_type="ranks")
    def get(self, request):
        download_csv = request.GET.get("download_csv")
        force_refresh = request.GET.get("force_refresh")
        is_contest_admin = request.user.is_authenticated and request.user.is_contest_admin(self.contest)
        if self.contest.rule_type == ContestRuleType.OI:
            serializer = OIContestRankSerializer
        else:
            serializer = ACMContestRankSerializer

        if force_refresh == "1" and is_contest_admin:
            qs = self.get_rank()
        else:
            cache_key = f"{CacheKey.contest_rank_cache}:{self.contest.id}"
            qs = cache.get(cache_key)
            if not qs:
                qs = self.get_rank()
                cache.set(cache_key, qs)

        if download_csv:
            data = serializer(qs, many=True, is_contest_admin=is_contest_admin).data
            contest_problems = Problem.objects.filter(contest=self.contest, visible=True).order_by("_id")
            problem_ids = [item.id for item in contest_problems]

            f = io.BytesIO()
            workbook = xlsxwriter.Workbook(f)
            worksheet = workbook.add_worksheet()
            worksheet.write("A1", "UserID")
            worksheet.write("C1", "学号")
            worksheet.write("D1", "姓名")
            worksheet.write("B1", "班级") 
            if self.contest.rule_type == ContestRuleType.OI:
                worksheet.write("E1", "总分")
                for item in range(contest_problems.count()):
                    worksheet.write(self.column_string(6 + item) + "1", f"{contest_problems[item].title}")
                for index, item in enumerate(data):
                    worksheet.write_string(index + 1, 0, str(item["user"]["id"]))
                    worksheet.write_string(index + 1, 2, item["user"]["username"])
                    worksheet.write_string(index + 1, 3, item["user"]["real_name"] or "")
                    usera = User.objects.get(id=item["user"]["id"])                                       
                    user = UserProfile.objects.get(user=usera) 
                    worksheet.write_string(index + 1, 1, user.school or "")  
                    worksheet.write_string(index + 1, 4, str(item["total_score"]))
                    for k, v in item["submission_info"].items():
                        worksheet.write_string(index + 1, 5 + problem_ids.index(int(k)), str(v))
            else:
                worksheet.write("D1", "AC")
                worksheet.write("E1", "Total Submission")
                worksheet.write("F1", "Total Time")
                for item in range(contest_problems.count()):
                    worksheet.write(self.column_string(7 + item) + "1", f"{contest_problems[item].title}")

                for index, item in enumerate(data):
                    worksheet.write_string(index + 1, 0, str(item["user"]["id"]))
                    worksheet.write_string(index + 1, 1, item["user"]["username"])
                    worksheet.write_string(index + 1, 2, item["user"]["real_name"] or "")
                    worksheet.write_string(index + 1, 3, str(item["accepted_number"]))
                    worksheet.write_string(index + 1, 4, str(item["submission_number"]))
                    worksheet.write_string(index + 1, 5, str(item["total_time"]))
                    for k, v in item["submission_info"].items():
                        worksheet.write_string(index + 1, 6 + problem_ids.index(int(k)), str(v["is_ac"]))

            workbook.close()
            f.seek(0)
            response = HttpResponse(f.read())
            response["Content-Disposition"] = f"attachment; filename=content-{self.contest.id}-rank.xlsx"
            response["Content-Type"] = "application/xlsx"
            return response

        page_qs = self.paginate_data(request, qs)
        page_qs["results"] = serializer(page_qs["results"], many=True, is_contest_admin=is_contest_admin).data
        return self.success(page_qs)
