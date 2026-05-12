import ipaddress
import logging
from django.db import DatabaseError
from django.utils.timezone import now

from account.decorators import login_required, check_contest_permission
from contest.models import ContestStatus, ContestRuleType, Contest, ACMContestRank, OIContestRank
from judge.tasks import judge_task
from options.options import SysOptions
# from judge.dispatcher import JudgeDispatcher
from problem.models import Problem, ProblemRuleType
from utils.api import APIView, validate_serializer
from utils.cache import cache
from utils.captcha import Captcha
from utils.throttling import TokenBucket
from ..models import Submission
from ..serializers import (CreateSubmissionSerializer, SubmissionModelSerializer,
                           ShareSubmissionSerializer)
from ..serializers import SubmissionSafeModelSerializer, SubmissionListSerializer

logger = logging.getLogger(__name__)


class SubmissionAPI(APIView):
    def throttling(self, request):
        # 使用 open_api 的请求暂不做限制
        auth_method = getattr(request, "auth_method", "")
        if auth_method == "api_key":
            return
        user_bucket = TokenBucket(key=str(request.user.id),
                                  redis_conn=cache, **SysOptions.throttling["user"])
        can_consume, wait = user_bucket.consume()
        if not can_consume:
            return "Please wait %d seconds" % (int(wait))

        # ip_bucket = TokenBucket(key=request.session["ip"],
        #                         redis_conn=cache, **SysOptions.throttling["ip"])
        # can_consume, wait = ip_bucket.consume()
        # if not can_consume:
        #     return "Captcha is required"

    @check_contest_permission(check_type="problems")
    def check_contest_permission(self, request):
        contest = self.contest
        if contest.status == ContestStatus.CONTEST_ENDED:
            return self.error("The contest have ended")
        if not request.user.is_contest_admin(contest):
            user_ip = ipaddress.ip_address(request.session.get("ip"))
            if contest.allowed_ip_ranges:
                if not any(user_ip in ipaddress.ip_network(cidr, strict=False) for cidr in contest.allowed_ip_ranges):
                    return self.error("Your IP is not allowed in this contest")

    @validate_serializer(CreateSubmissionSerializer)
    @login_required
    def post(self, request):
        data = request.data
        hide_id = False
        if data.get("contest_id"):
            error = self.check_contest_permission(request)
            if error:
                return error
            contest = self.contest
            if not contest.problem_details_permission(request.user):
                hide_id = True

        if data.get("captcha"):
            if not Captcha(request).check(data["captcha"]):
                return self.error("Invalid captcha")
        error = self.throttling(request)
        if error:
            return self.error(error)

        try:
            problem = Problem.objects.get(id=data["problem_id"], contest_id=data.get("contest_id"), visible=True)
        except Problem.DoesNotExist:
            return self.error("Problem not exist")
        if data["language"] not in problem.languages:
            return self.error(f"{data['language']} is not allowed in the problem")
        submission = Submission.objects.create(user_id=request.user.id,
                                               username=request.user.username,
                                               language=data["language"],
                                               code=data["code"],
                                               problem_id=problem.id,
                                               ip=request.session["ip"],
                                               contest_id=data.get("contest_id"))
        # use this for debug
        # JudgeDispatcher(submission.id, problem.id).judge()
        judge_task.send(submission.id, problem.id)
        if hide_id:
            return self.success()
        else:
            return self.success({"submission_id": submission.id})

    @login_required
    def get(self, request):
        submission_id = request.GET.get("id")
        if not submission_id:
            return self.error("Parameter id doesn't exist")
        try:
            submission = Submission.objects.select_related("problem").get(id=submission_id)
        except (Submission.DoesNotExist, ValueError, DatabaseError):
            return self.error("Submission doesn't exist")
        if not submission.check_user_permission(request.user):
            return self.error("No permission for this submission")

        if submission.problem.rule_type == ProblemRuleType.OI or request.user.is_admin_role():
            submission_data = SubmissionModelSerializer(submission).data
        else:
            submission_data = SubmissionSafeModelSerializer(submission).data
        # 是否有权限取消共享
        submission_data["can_unshare"] = submission.check_user_permission(request.user, check_share=False)
        return self.success(submission_data)

    @validate_serializer(ShareSubmissionSerializer)
    @login_required
    def put(self, request):
        """
        share submission
        """
        try:
            submission = Submission.objects.select_related("problem").get(id=request.data["id"])
        except (Submission.DoesNotExist, ValueError, DatabaseError):
            return self.error("Submission doesn't exist")
        if not submission.check_user_permission(request.user, check_share=False):
            return self.error("No permission to share the submission")
        if submission.contest and submission.contest.status == ContestStatus.CONTEST_UNDERWAY:
            return self.error("Can not share submission now")
        submission.shared = request.data["shared"]
        submission.save(update_fields=["shared"])
        return self.success()


class SubmissionListAPI(APIView):
    @staticmethod
    def _safe_fallback_result(user, problem_display_id=None):
        try:
            return SubmissionListAPI._build_fallback_result(user, problem_display_id)
        except Exception:
            return {"results": [], "total": 0}

    @staticmethod
    def _build_fallback_result(user, problem_display_id=None):
        if not user or not user.is_authenticated:
            return {"results": [], "total": 0}

        try:
            profile = user.userprofile
        except Exception:
            return {"results": [], "total": 0}
        acm_root = profile.acm_problems_status or {}
        oi_root = profile.oi_problems_status or {}
        acm_status = acm_root.get("problems", {})
        oi_status = oi_root.get("problems", {})
        acm_contest_status = acm_root.get("contest_problems", {})
        oi_contest_status = oi_root.get("contest_problems", {})
        merged = {}
        merged.update(acm_status)
        merged.update(oi_status)
        merged_contest = {}
        merged_contest.update(acm_contest_status)
        merged_contest.update(oi_contest_status)

        rows = []
        if problem_display_id:
            try:
                problem = Problem.objects.get(_id=problem_display_id, contest_id__isnull=True, visible=True)
                status_info = merged.get(str(problem.id))
                if status_info:
                    rows.append({
                        "id": f"fallback-public-{user.id}-{problem.id}",
                        "problem": problem._id,
                        "username": user.username,
                        "result": status_info.get("status", 1),
                        "language": "-",
                        "create_time": now(),
                        "statistic_info": {"time_cost": 0, "memory_cost": 0},
                        "show_link": False
                    })
            except Problem.DoesNotExist:
                # If the display id is from a contest problem but frontend routes
                # to /status, still provide a best-effort row from contest status.
                contest_problem = Problem.objects.filter(_id=problem_display_id, visible=True).order_by("id").first()
                if contest_problem:
                    status_info = merged_contest.get(str(contest_problem.id)) or merged.get(str(contest_problem.id))
                    if status_info:
                        rows.append({
                            "id": f"fallback-public-{user.id}-{contest_problem.id}",
                            "problem": contest_problem._id,
                            "username": user.username,
                            "result": status_info.get("status", 1),
                            "language": "-",
                            "create_time": now(),
                            "statistic_info": {"time_cost": 0, "memory_cost": 0},
                            "show_link": False
                        })
            except DatabaseError:
                pass
        else:
            # Emergency fallback for broken submission storage: build a compact
            # status list from cached user problem statuses.
            problem_ids = set()
            problem_ids.update([int(pid) for pid in merged.keys() if str(pid).isdigit()])
            problem_ids.update([int(pid) for pid in merged_contest.keys() if str(pid).isdigit()])
            if problem_ids:
                problems = Problem.objects.filter(id__in=problem_ids).only("id", "_id")
                problem_map = {p.id: p for p in problems}
                # Keep output bounded and deterministic.
                sorted_ids = sorted(problem_ids, reverse=True)[:50]
                for pid in sorted_ids:
                    p = problem_map.get(pid)
                    if not p:
                        continue
                    status_info = merged.get(str(pid)) or merged_contest.get(str(pid))
                    if not status_info:
                        continue
                    rows.append({
                        "id": f"fallback-public-{user.id}-{pid}",
                        "problem": p._id,
                        "username": user.username,
                        "result": status_info.get("status", 1),
                        "language": "-",
                        "create_time": now(),
                        "statistic_info": {"time_cost": 0, "memory_cost": 0},
                        "show_link": False
                    })
        return {"results": rows, "total": len(rows)}

    def get(self, request):
        try:
            if not request.GET.get("limit"):
                return self.error("Limit is needed")
            if request.GET.get("contest_id"):
                return self.error("Parameter error")

            submissions = Submission.objects.filter(contest_id__isnull=True).select_related("problem__created_by")
            problem_id = request.GET.get("problem_id")
            myself = request.GET.get("myself")
            result = request.GET.get("result")
            username = request.GET.get("username")
            if problem_id:
                try:
                    problem = Problem.objects.get(_id=problem_id, contest_id__isnull=True, visible=True)
                except Problem.DoesNotExist:
                    # Do not broaden query scope when public problem id is missing.
                    return self.success(self._safe_fallback_result(request.user, problem_id))
                except DatabaseError:
                    return self.success(self._safe_fallback_result(request.user, problem_id))
                submissions = submissions.filter(problem=problem)
            try:
                submission_list_show_all = SysOptions.submission_list_show_all
            except Exception:
                submission_list_show_all = True

            if (myself and myself == "1") or not submission_list_show_all:
                submissions = submissions.filter(user_id=request.user.id)
            elif username:
                submissions = submissions.filter(username__icontains=username)
            if result:
                submissions = submissions.filter(result=result)

            data = self.paginate_data(request, submissions)
            data["results"] = SubmissionListSerializer(data["results"], many=True, user=request.user).data
            is_own_scope = myself == "1" or (request.user.is_authenticated and username == request.user.username)
            if request.user.is_authenticated and problem_id and is_own_scope and len(data["results"]) == 0:
                fallback = self._safe_fallback_result(request.user, problem_id)
                if fallback["total"] > 0:
                    return self.success(fallback)
            return self.success(data)
        except Exception:
            logger.exception("SubmissionListAPI failed, query=%s, user_id=%s", dict(request.GET), getattr(request.user, "id", None))
            return self.success(self._safe_fallback_result(request.user, problem_id))


class ContestSubmissionListAPI(APIView):
    @staticmethod
    def _safe_fallback_result_for_contest(contest, user, problem_display_id=None):
        try:
            return ContestSubmissionListAPI._fallback_result_for_contest(contest, user, problem_display_id)
        except Exception:
            return {"results": [], "total": 0}

    @staticmethod
    def _fallback_result_for_contest(contest, user, problem_display_id=None):
        if not user or not user.is_authenticated:
            return {"results": [], "total": 0}

        rows = []

        def _append_row(problem_obj, result_code):
            rows.append({
                "id": f"fallback-contest-{contest.id}-{user.id}-{problem_obj.id}",
                "problem": problem_obj._id,
                "username": user.username,
                "result": result_code,
                "language": "-",
                "create_time": now(),
                "statistic_info": {"time_cost": 0, "memory_cost": 0},
                "show_link": False
            })

        problem_map = {}
        if problem_display_id:
            try:
                p = Problem.objects.get(_id=problem_display_id, contest_id=contest.id, visible=True)
                problem_map[str(p.id)] = p
            except Problem.DoesNotExist:
                return {"results": [], "total": 0}
            except DatabaseError:
                return {"results": [], "total": 0}
        else:
            for p in Problem.objects.filter(contest_id=contest.id, visible=True).only("id", "_id"):
                problem_map[str(p.id)] = p

        if contest.rule_type == ContestRuleType.ACM:
            rank = ACMContestRank.objects.filter(contest=contest, user_id=user.id).first()
            submission_info = rank.submission_info if rank and isinstance(rank.submission_info, dict) else {}
            for pid, info in submission_info.items():
                problem_obj = problem_map.get(str(pid))
                if not problem_obj:
                    continue
                is_ac = bool(info and info.get("is_ac"))
                _append_row(problem_obj, 0 if is_ac else 1)
        else:
            rank = OIContestRank.objects.filter(contest=contest, user_id=user.id).first()
            submission_info = rank.submission_info if rank and isinstance(rank.submission_info, dict) else {}
            for pid, score in submission_info.items():
                problem_obj = problem_map.get(str(pid))
                if not problem_obj:
                    continue
                _append_row(problem_obj, 0 if (score or 0) > 0 else 1)

        return {"results": rows, "total": len(rows)}

    @check_contest_permission(check_type="submissions")
    def get(self, request):
        if not request.GET.get("limit"):
            return self.error("Limit is needed")

        contest = self.contest
        problem_id = request.GET.get("problem_id")
        try:
            submissions = Submission.objects.filter(contest_id=contest.id).select_related("problem__created_by")
        except DatabaseError:
            # Keep contest page usable when submission storage has corruption.
            return self.success(self._safe_fallback_result_for_contest(contest, request.user, problem_id))
        myself = request.GET.get("myself")
        result = request.GET.get("result")
        username = request.GET.get("username")
        if problem_id:
            try:
                problem = Problem.objects.get(_id=problem_id, contest_id=contest.id, visible=True)
            except Problem.DoesNotExist:
                return self.success(self._safe_fallback_result_for_contest(contest, request.user, problem_id))
            except DatabaseError:
                return self.success(self._safe_fallback_result_for_contest(contest, request.user, problem_id))
            submissions = submissions.filter(problem=problem)

        if myself and myself == "1":
            submissions = submissions.filter(user_id=request.user.id)
        elif username:
            submissions = submissions.filter(username__icontains=username)
        if result:
            submissions = submissions.filter(result=result)

        # filter the test submissions submitted before contest start
        if contest.status != ContestStatus.CONTEST_NOT_START:
            submissions = submissions.filter(create_time__gte=contest.start_time)

        # 封榜的时候只能看到自己的提交
        if contest.rule_type == ContestRuleType.ACM:
            if not contest.real_time_rank and not request.user.is_contest_admin(contest):
                submissions = submissions.filter(user_id=request.user.id)

        try:
            data = self.paginate_data(request, submissions)
            data["results"] = SubmissionListSerializer(data["results"], many=True, user=request.user).data
            is_own_scope = myself == "1" or (request.user.is_authenticated and username == request.user.username)
            if request.user.is_authenticated and problem_id and is_own_scope and len(data["results"]) == 0:
                fallback = self._safe_fallback_result_for_contest(contest, request.user, problem_id)
                if fallback["total"] > 0:
                    return self.success(fallback)
            return self.success(data)
        except Exception:
            logger.exception("ContestSubmissionListAPI failed, contest_id=%s, query=%s, user_id=%s",
                             getattr(contest, "id", None), dict(request.GET), getattr(request.user, "id", None))
            return self.success(self._safe_fallback_result_for_contest(contest, request.user, problem_id))


class SubmissionExistsAPI(APIView):
    def get(self, request):
        if not request.GET.get("problem_id"):
            return self.error("Parameter error, problem_id is required")
        if not request.user.is_authenticated:
            return self.success(False)

        contest_id = request.GET.get("contest_id")
        problem_id = request.GET["problem_id"]

        # Contest problem: prefer submission table, fallback to rank JSON when
        # submission storage is unavailable/corrupted.
        if contest_id:
            exists = False
            try:
                exists = Submission.objects.filter(
                    problem_id=problem_id,
                    contest_id=contest_id,
                    user_id=request.user.id
                ).exists()
            except DatabaseError:
                exists = False

            if not exists:
                try:
                    contest = Contest.objects.get(id=contest_id, visible=True)
                    if contest.rule_type == ContestRuleType.ACM:
                        rank = ACMContestRank.objects.filter(contest=contest, user_id=request.user.id).first()
                        if rank and isinstance(rank.submission_info, dict):
                            exists = str(problem_id) in rank.submission_info
                    else:
                        rank = OIContestRank.objects.filter(contest=contest, user_id=request.user.id).first()
                        if rank and isinstance(rank.submission_info, dict):
                            exists = str(problem_id) in rank.submission_info
                except Contest.DoesNotExist:
                    exists = False

            return self.success(exists)

        try:
            exists = Submission.objects.filter(
                problem_id=problem_id,
                user_id=request.user.id
            ).exists()
        except DatabaseError:
            # Keep problem pages available when submission storage is corrupted.
            exists = False
        return self.success(exists)
