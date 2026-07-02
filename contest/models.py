from utils.constants import ContestRuleType  # noqa
from django.db import models
from django.utils.timezone import now
from utils.models import JSONField

from utils.constants import ContestStatus, ContestType
from account.models import User
from utils.models import RichTextField


class Contest(models.Model):
    title = models.TextField()
    description = RichTextField()
    # show real time rank or cached rank
    real_time_rank = models.BooleanField()
    password = models.TextField(null=True)
    # enum of ContestRuleType
    rule_type = models.TextField()
    start_time = models.DateTimeField()
    end_time = models.DateTimeField()
    create_time = models.DateTimeField(auto_now_add=True)
    last_update_time = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(User, on_delete=models.CASCADE)
    # 是否可见 false的话相当于删除
    visible = models.BooleanField(default=True)
    allowed_ip_ranges = JSONField(default=list)
    is_exam = models.BooleanField(default=False)

    @property
    def status(self):
        if self.start_time > now():
            # 没有开始 返回1
            return ContestStatus.CONTEST_NOT_START
        elif self.end_time < now():
            # 已经结束 返回-1
            return ContestStatus.CONTEST_ENDED
        else:
            # 正在进行 返回0
            return ContestStatus.CONTEST_UNDERWAY

    @property
    def contest_type(self):
        if self.password:
            return ContestType.PASSWORD_PROTECTED_CONTEST
        return ContestType.PUBLIC_CONTEST

    # 是否有权查看problem 的一些统计信息 诸如submission_number, accepted_number 等
    def problem_details_permission(self, user):
        return self.rule_type == ContestRuleType.ACM or \
               self.status == ContestStatus.CONTEST_ENDED or \
               user.is_authenticated and user.is_contest_admin(self) or \
               self.real_time_rank

    class Meta:
        db_table = "contest"
        ordering = ("-start_time",)


class AbstractContestRank(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    contest = models.ForeignKey(Contest, on_delete=models.CASCADE)
    submission_number = models.IntegerField(default=0)

    class Meta:
        abstract = True


class ACMContestRank(AbstractContestRank):
    accepted_number = models.IntegerField(default=0)
    # total_time is only for ACM contest, total_time =  ac time + none-ac times * 20 * 60
    total_time = models.IntegerField(default=0)
    # {"23": {"is_ac": True, "ac_time": 8999, "error_number": 2, "is_first_ac": True}}
    # key is problem id
    submission_info = JSONField(default=dict)

    class Meta:
        db_table = "acm_contest_rank"
        unique_together = (("user", "contest"),)


class OIContestRank(AbstractContestRank):
    total_score = models.IntegerField(default=0)
    # {"23": 333}
    # key is problem id, value is current score
    submission_info = JSONField(default=dict)

    class Meta:
        db_table = "oi_contest_rank"
        unique_together = (("user", "contest"),)


class ContestParticipation(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    contest = models.ForeignKey(Contest, on_delete=models.CASCADE)
    first_enter_time = models.DateTimeField(auto_now_add=True)
    last_enter_time = models.DateTimeField(auto_now=True)
    enter_count = models.IntegerField(default=1)
    has_submission = models.BooleanField(default=False)
    submission_number = models.IntegerField(default=0)
    accepted_number = models.IntegerField(default=0)
    total_score = models.IntegerField(default=0)
    is_calibrated = models.BooleanField(default=False)
    calibrated_at = models.DateTimeField(null=True)

    @classmethod
    def mark_enter(cls, user, contest):
        obj, created = cls.objects.get_or_create(user=user, contest=contest)
        if not created:
            cls.objects.filter(id=obj.id).update(
                enter_count=models.F("enter_count") + 1,
                last_enter_time=now()
            )
        return obj

    @classmethod
    def sync_from_rank(cls, user_id, contest, submission_number, accepted_number=0, total_score=0):
        defaults = {
            "has_submission": submission_number > 0,
            "submission_number": submission_number,
            "accepted_number": accepted_number,
            "total_score": total_score
        }
        cls.objects.update_or_create(user_id=user_id, contest=contest, defaults=defaults)

    @classmethod
    def calibrate_once(cls, user, contest, force=False):
        obj, _ = cls.objects.get_or_create(user=user, contest=contest)
        if obj.is_calibrated and not force:
            return obj

        from django.db import DatabaseError
        from problem.models import Problem
        from submission.models import Submission, JudgeStatus

        problem_id_map = dict(Problem.objects.filter(contest_id=contest.id, visible=True)
                  .values_list("id", "_id"))

        submission_number = obj.submission_number
        accepted_number = obj.accepted_number
        total_score = obj.total_score
        has_submission = obj.has_submission

        if contest.rule_type == ContestRuleType.ACM:
            rank = ACMContestRank.objects.filter(user=user, contest=contest).first()
            if rank:
                submission_number = rank.submission_number
                accepted_number = rank.accepted_number
                total_score = 0
                has_submission = rank.submission_number > 0
                if rank.submission_info:
                    profile = user.userprofile
                    acm_status = profile.acm_problems_status or {}
                    contest_problems = acm_status.get("contest_problems", {})
                    for pid, info in rank.submission_info.items():
                        display_id = problem_id_map.get(int(pid), pid) if str(pid).isdigit() else pid
                        contest_problems[pid] = {
                            "status": JudgeStatus.ACCEPTED if info.get("is_ac") else JudgeStatus.WRONG_ANSWER,
                            "_id": display_id
                        }
                    acm_status["contest_problems"] = contest_problems
                    profile.acm_problems_status = acm_status
                    profile.save(update_fields=["acm_problems_status"])
        else:
            rank = OIContestRank.objects.filter(user=user, contest=contest).first()
            if rank:
                submission_number = rank.submission_number
                accepted_number = 0
                total_score = rank.total_score
                has_submission = rank.submission_number > 0
                if rank.submission_info:
                    profile = user.userprofile
                    oi_status = profile.oi_problems_status or {}
                    contest_problems = oi_status.get("contest_problems", {})
                    for pid, score in rank.submission_info.items():
                        display_id = problem_id_map.get(int(pid), pid) if str(pid).isdigit() else pid
                        contest_problems[pid] = {
                            "status": JudgeStatus.ACCEPTED if score and score > 0 else JudgeStatus.WRONG_ANSWER,
                            "_id": display_id,
                            "score": score or 0
                        }
                    oi_status["contest_problems"] = contest_problems
                    profile.oi_problems_status = oi_status
                    profile.save(update_fields=["oi_problems_status"])

        # Best-effort submission scan for historical alignment. If submission
        # storage is unavailable/corrupted, fallback to rank-derived data above.
        try:
            qs = Submission.objects.filter(contest_id=contest.id, user_id=user.id).order_by("create_time")
            if qs.exists():
                has_submission = True
                submission_number = max(submission_number, qs.count())

                profile = user.userprofile
                if contest.rule_type == ContestRuleType.ACM:
                    by_problem = {}
                    for row in qs.values("problem_id", "result", "create_time"):
                        pid = str(row["problem_id"])
                        item = by_problem.setdefault(pid, {"is_ac": False, "error_number": 0, "ac_time": None})
                        if row["result"] == JudgeStatus.ACCEPTED:
                            if not item["is_ac"]:
                                item["is_ac"] = True
                                if contest.start_time and row["create_time"] >= contest.start_time:
                                    item["ac_time"] = int((row["create_time"] - contest.start_time).total_seconds())
                                else:
                                    item["ac_time"] = 0
                        elif not item["is_ac"]:
                            item["error_number"] += 1

                    accepted_number = max(accepted_number, sum(1 for v in by_problem.values() if v["is_ac"]))
                    acm_status = profile.acm_problems_status or {}
                    contest_problems = acm_status.get("contest_problems", {})
                    for pid, info in by_problem.items():
                        display_id = problem_id_map.get(int(pid), pid) if pid.isdigit() else pid
                        contest_problems[pid] = {
                            "status": JudgeStatus.ACCEPTED if info["is_ac"] else JudgeStatus.WRONG_ANSWER,
                            "_id": display_id
                        }
                    acm_status["contest_problems"] = contest_problems
                    profile.acm_problems_status = acm_status
                    profile.save(update_fields=["acm_problems_status"])
                else:
                    best_scores = {}
                    for row in qs.values("problem_id", "result", "statistic_info"):
                        pid = str(row["problem_id"])
                        stat = row.get("statistic_info") or {}
                        score = 0
                        if isinstance(stat, dict):
                            score = stat.get("score", 0) or 0
                        if score == 0 and row["result"] == JudgeStatus.ACCEPTED:
                            score = 100
                        if score > best_scores.get(pid, 0):
                            best_scores[pid] = score

                    derived_total_score = sum(best_scores.values())
                    total_score = max(total_score, derived_total_score)
                    oi_status = profile.oi_problems_status or {}
                    contest_problems = oi_status.get("contest_problems", {})
                    for pid, score in best_scores.items():
                        display_id = problem_id_map.get(int(pid), pid) if pid.isdigit() else pid
                        contest_problems[pid] = {
                            "status": JudgeStatus.ACCEPTED if score > 0 else JudgeStatus.WRONG_ANSWER,
                            "_id": display_id,
                            "score": score
                        }
                    oi_status["contest_problems"] = contest_problems
                    profile.oi_problems_status = oi_status
                    profile.save(update_fields=["oi_problems_status"])
        except DatabaseError:
            pass

        obj.has_submission = has_submission
        obj.submission_number = submission_number
        obj.accepted_number = accepted_number
        obj.total_score = total_score
        obj.is_calibrated = True
        obj.calibrated_at = now()
        obj.save(update_fields=[
            "has_submission", "submission_number", "accepted_number", "total_score",
            "is_calibrated", "calibrated_at", "last_enter_time"
        ])
        return obj

    class Meta:
        db_table = "contest_participation"
        unique_together = (("user", "contest"),)
        ordering = ("-last_enter_time", "-id")
        index_together = (("user", "last_enter_time"),)


class ContestAnnouncement(models.Model):
    contest = models.ForeignKey(Contest, on_delete=models.CASCADE)
    title = models.TextField()
    content = RichTextField()
    created_by = models.ForeignKey(User, on_delete=models.CASCADE)
    visible = models.BooleanField(default=True)
    create_time = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "contest_announcement"
        ordering = ("-create_time",)
