import ipaddress
import os
import zipfile

from django.contrib import auth
from django.db.models import Q
from django.conf import settings
from django.http import StreamingHttpResponse
from wsgiref.util import FileWrapper

from account.decorators import login_required, check_contest_password
from contest.models import Contest, ContestStatus, ContestRuleType, ContestType
from judge.tasks import judge_task
from options.options import SysOptions
from problem.models import Problem, ProblemRuleType
from problem.views.oj import DLTestCaseZipProcessor
from problem.utils import is_problem_public_test_case_download_enabled, load_test_case_info
from submission.models import Submission
from utils.api import APIView, CSRFExemptAPIView, validate_serializer
from utils.cache import cache
from utils.captcha import Captcha
from utils.shortcuts import rand_str
from utils.throttling import TokenBucket

from .serializers import (
    PluginContestSerializer,
    PluginLoginSerializer,
    PluginProblemDetailSerializer,
    PluginProblemSummarySerializer,
    PluginSubmissionCreateSerializer,
    PluginSubmissionSerializer,
    PluginUserSerializer,
)


class ContestAccessMixin(object):
    def get_contest_or_error(self, contest_id):
        try:
            return Contest.objects.select_related("created_by").get(id=contest_id, visible=True), None
        except Contest.DoesNotExist:
            return None, self.error(f"Contest {contest_id} doesn't exist")

    def check_plugin_contest_permission(self, request, contest, check_type="problems", contest_password=None):
        user = request.user
        if not user.is_authenticated:
            return self.error("Please login first.")

        if user.is_contest_admin(contest):
            return None

        if contest.contest_type == ContestType.PASSWORD_PROTECTED_CONTEST:
            if not check_contest_password(contest_password, contest.password):
                return self.error("Wrong password or password expired")

        if contest.status == ContestStatus.CONTEST_NOT_START and check_type != "details":
            return self.error("Contest has not started yet.")

        if contest.status == ContestStatus.CONTEST_UNDERWAY and contest.rule_type == ContestRuleType.OI:
            if not contest.real_time_rank and check_type in {"ranks", "submissions"}:
                return self.error(f"No permission to get {check_type}")
        return None


class ProblemStatusMixin(object):
    @staticmethod
    def add_problem_status(request, problems, rule_type=None, contest_mode=False):
        if not request.user.is_authenticated:
            return
        profile = request.user.userprofile
        if contest_mode:
            if rule_type == ContestRuleType.ACM:
                source = profile.acm_problems_status.get("contest_problems", {})
            else:
                source = profile.oi_problems_status.get("contest_problems", {})
        else:
            acm_status = profile.acm_problems_status.get("problems", {})
            oi_status = profile.oi_problems_status.get("problems", {})
            for problem in problems:
                source = acm_status if problem["rule_type"] == ProblemRuleType.ACM else oi_status
                problem["my_status"] = source.get(str(problem["id"]), {}).get("status")
            return

        for problem in problems:
            problem["my_status"] = source.get(str(problem["id"]), {}).get("status")


class PluginLoginAPI(CSRFExemptAPIView):
    @validate_serializer(PluginLoginSerializer)
    def post(self, request):
        data = request.data
        user = auth.authenticate(username=data["username"], password=data["password"])
        if not user:
            return self.error("Invalid username or password")
        if user.is_disabled:
            return self.error("Your account has been disabled")
        if user.two_factor_auth:
            tfa_code = data.get("tfa_code")
            if not tfa_code:
                return self.error("tfa_required")
            from otpauth import OtpAuth
            if not OtpAuth(user.tfa_token).valid_totp(tfa_code):
                return self.error("Invalid two factor verification code")

        token = rand_str()
        user.auth_token = token
        user.save(update_fields=["auth_token"])
        return self.success({
            "token": token,
            "user": PluginUserSerializer(user.userprofile).data,
        })


class PluginLogoutAPI(APIView):
    @login_required
    def post(self, request):
        request.user.auth_token = None
        request.user.save(update_fields=["auth_token"])
        return self.success("Succeeded")


class PluginBootstrapAPI(APIView):
    def get(self, request):
        user_data = None
        if request.user.is_authenticated:
            user_data = PluginUserSerializer(request.user.userprofile).data
        return self.success({
            "site": {
                "name": SysOptions.website_name,
                "base_url": SysOptions.website_base_url,
                "shortcut": SysOptions.website_name_shortcut,
            },
            "languages": SysOptions.visible_languages,
            "user": user_data,
        })


class PluginContestListAPI(APIView):
    def get(self, request):
        contests = Contest.objects.select_related("created_by").filter(visible=True)
        keyword = request.GET.get("keyword")
        status = request.GET.get("status")
        rule_type = request.GET.get("rule_type")
        if keyword:
            contests = contests.filter(title__icontains=keyword)
        if rule_type:
            contests = contests.filter(rule_type=rule_type)
        if status:
            from django.utils.timezone import now
            current_time = now()
            if status == ContestStatus.CONTEST_NOT_START:
                contests = contests.filter(start_time__gt=current_time)
            elif status == ContestStatus.CONTEST_ENDED:
                contests = contests.filter(end_time__lt=current_time)
            else:
                contests = contests.filter(start_time__lte=current_time, end_time__gte=current_time)
        data = self.paginate_data(request, contests, PluginContestSerializer)
        return self.success(data)


class PluginProblemsetAPI(APIView, ProblemStatusMixin):
    def get(self, request):
        problems = Problem.objects.select_related("created_by").filter(contest_id__isnull=True, visible=True)
        keyword = request.GET.get("keyword", "").strip()
        difficulty = request.GET.get("difficulty")
        tag = request.GET.get("tag")
        if keyword:
            problems = problems.filter(Q(title__icontains=keyword) | Q(_id__icontains=keyword))
        if difficulty:
            problems = problems.filter(difficulty=difficulty)
        if tag:
            problems = problems.filter(tags__name=tag).distinct()
        data = self.paginate_data(request, problems, PluginProblemSummarySerializer)
        for item, problem in zip(data["results"], problems[int(request.GET.get("offset", 0)):int(request.GET.get("offset", 0)) + len(data["results"])]):
            item["can_download_test_case"] = is_problem_public_test_case_download_enabled(problem)
        self.add_problem_status(request, data["results"])
        return self.success(data)


class PluginContestWorkspaceAPI(APIView, ContestAccessMixin, ProblemStatusMixin):
    def get(self, request):
        contest_id = request.GET.get("contest_id")
        if not contest_id:
            return self.error("Parameter error, contest_id is required")
        contest, error = self.get_contest_or_error(contest_id)
        if error:
            return error
        permission_error = self.check_plugin_contest_permission(
            request,
            contest,
            check_type="problems",
            contest_password=request.GET.get("contest_password"),
        )
        if permission_error:
            return permission_error

        problems = Problem.objects.select_related("created_by").filter(contest=contest, visible=True)
        problem_data = PluginProblemSummarySerializer(problems, many=True).data
        for index, problem in enumerate(problems):
            problem_data[index]["can_download_test_case"] = is_problem_public_test_case_download_enabled(problem)
        self.add_problem_status(request, problem_data, rule_type=contest.rule_type, contest_mode=True)
        return self.success({
            "contest": PluginContestSerializer(contest).data,
            "problems": problem_data,
        })


class PluginProblemWorkspaceAPI(APIView, ContestAccessMixin, ProblemStatusMixin):
    def get_problem(self, problem_ref, contest=None):
        query = Problem.objects.select_related("created_by", "contest")
        if contest is None:
            query = query.filter(contest_id__isnull=True, visible=True)
        else:
            query = query.filter(contest=contest, visible=True)
        if str(problem_ref).isdigit():
            return query.filter(id=int(problem_ref)).first()
        return query.filter(_id=problem_ref).first()

    def get(self, request):
        problem_ref = request.GET.get("problem_id")
        if not problem_ref:
            return self.error("Parameter error, problem_id is required")
        contest = None
        contest_id = request.GET.get("contest_id")
        if contest_id:
            contest, error = self.get_contest_or_error(contest_id)
            if error:
                return error
            permission_error = self.check_plugin_contest_permission(
                request,
                contest,
                check_type="problems",
                contest_password=request.GET.get("contest_password"),
            )
            if permission_error:
                return permission_error
        problem = self.get_problem(problem_ref, contest=contest)
        if not problem:
            return self.error("Problem does not exist")

        problem_data = PluginProblemDetailSerializer(problem).data
        problem_data["can_download_test_case"] = is_problem_public_test_case_download_enabled(problem)
        self.add_problem_status(request, [problem_data], rule_type=contest.rule_type if contest else None,
                                contest_mode=contest is not None)

        is_exam = bool(problem.contest and problem.contest.is_exam)
        problem_data["is_exam"] = is_exam

        # 考试题目：强制禁用下载，不暴露测试数据清单
        if is_exam:
            problem_data["can_download_test_case"] = False
            problem_data["test_case_manifest"] = None
        else:
            test_case_info = load_test_case_info(problem.test_case_id) if problem_data["can_download_test_case"] else None
            problem_data["test_case_manifest"] = None
            if test_case_info is not None:
                problem_data["test_case_manifest"] = {
                    "spj": test_case_info.get("spj", False),
                    "test_cases": list(test_case_info.get("test_cases", {}).values()),
                    "download_url": f"/api/dl_test_case?problem_id={problem.id}",
                }
        return self.success(problem_data)


class PluginSubmissionAPI(APIView, ContestAccessMixin):
    def throttling(self, request):
        user_bucket = TokenBucket(key=str(request.user.id), redis_conn=cache, **SysOptions.throttling["user"])
        can_consume, wait = user_bucket.consume()
        if not can_consume:
            return "Please wait %d seconds" % (int(wait))

    def ensure_contest_submission_access(self, request, contest, contest_password):
        permission_error = self.check_plugin_contest_permission(
            request,
            contest,
            check_type="problems",
            contest_password=contest_password,
        )
        if permission_error:
            return permission_error
        if contest.status == ContestStatus.CONTEST_ENDED:
            return self.error("The contest have ended")
        if not request.user.is_contest_admin(contest):
            user_ip = ipaddress.ip_address(request.session.get("ip", request.ip))
            if contest.allowed_ip_ranges:
                if not any(user_ip in ipaddress.ip_network(cidr, strict=False) for cidr in contest.allowed_ip_ranges):
                    return self.error("Your IP is not allowed in this contest")
        return None

    @validate_serializer(PluginSubmissionCreateSerializer)
    @login_required
    def post(self, request):
        data = request.data
        contest = None
        if data.get("contest_id"):
            contest, error = self.get_contest_or_error(data["contest_id"])
            if error:
                return error
            permission_error = self.ensure_contest_submission_access(request, contest, data.get("contest_password"))
            if permission_error:
                return permission_error

        if data.get("captcha") and not Captcha(request).check(data["captcha"]):
            return self.error("Invalid captcha")
        throttle_error = self.throttling(request)
        if throttle_error:
            return self.error(throttle_error)

        try:
            problem = Problem.objects.get(id=data["problem_id"], contest=contest, visible=True)
        except Problem.DoesNotExist:
            return self.error("Problem not exist")
        if data["language"] not in problem.languages:
            return self.error(f"{data['language']} is not allowed in the problem")

        submission = Submission.objects.create(
            user_id=request.user.id,
            username=request.user.username,
            language=data["language"],
            code=data["code"],
            problem_id=problem.id,
            ip=request.session.get("ip", request.ip),
            contest_id=data.get("contest_id"),
        )
        judge_task.send(submission.id, problem.id)
        payload = {
            "submission_id": submission.id,
            "display_id": problem._id,
        }
        return self.success(payload)

    @login_required
    def get(self, request):
        submission_id = request.GET.get("submission_id") or request.GET.get("id")
        if not submission_id:
            return self.error("Parameter submission_id doesn't exist")
        try:
            submission = Submission.objects.select_related("problem").get(id=submission_id)
        except Submission.DoesNotExist:
            return self.error("Submission doesn't exist")
        if not submission.check_user_permission(request.user):
            return self.error("No permission for this submission")
        return self.success(PluginSubmissionSerializer(submission).data)


class PluginTestCaseDownloadAPI(APIView, ContestAccessMixin, DLTestCaseZipProcessor):
    request_parsers = ()

    def get(self, request):
        problem_id = request.GET.get("problem_id")
        if not problem_id:
            return self.error("Parameter error, problem_id is required")
        try:
            problem = Problem.objects.select_related("contest").get(id=problem_id, visible=True)
        except Problem.DoesNotExist:
            return self.error("Problem does not exists")

        if not is_problem_public_test_case_download_enabled(problem):
            return self.error("Problem does not support download")

        contest = problem.contest
        if contest:
            # 考试题目禁止下载测试数据（核心防线）
            if contest.is_exam:
                return self.error("考试题目不允许下载测试数据")

            permission_error = self.check_plugin_contest_permission(
                request,
                contest,
                check_type="problems",
                contest_password=request.GET.get("contest_password"),
            )
            if permission_error:
                return permission_error

        test_case_dir = os.path.join(settings.TEST_CASE_DIR, problem.test_case_id)
        if not os.path.isdir(test_case_dir):
            return self.error("Test case does not exists")

        name_list = self.filter_name_list(os.listdir(test_case_dir), problem.spj)
        name_list.append("info")
        file_name = os.path.join(test_case_dir, problem.test_case_id + ".zip")
        with zipfile.ZipFile(file_name, "w") as zip_file:
            for test_case in name_list:
                zip_file.write(f"{test_case_dir}/{test_case}", test_case)

        response = StreamingHttpResponse(FileWrapper(open(file_name, "rb")),
                                         content_type="application/octet-stream")
        response["Content-Disposition"] = f"attachment; filename=problem_{problem.id}_test_cases.zip"
        response["Content-Length"] = os.path.getsize(file_name)
        return response