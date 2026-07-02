import random
from collections import defaultdict
from django.db.models import Q
from utils.api import APIView
from account.decorators import check_contest_permission, check_contest_password
from ..models import Problem, ProblemRuleType
from ..serializers import ProblemSerializer, TagSerializer, ProblemSafeSerializer
from ..tag import get_problem_tag_queryset, normalize_tag_name
from contest.models import ContestRuleType, ContestStatus, ContestType

import hashlib
import json
import os
import shutil
import tempfile
import zipfile
from wsgiref.util import FileWrapper
from django.conf import settings
from utils.shortcuts import rand_str, natural_sort_key
from django.http import StreamingHttpResponse, FileResponse
from utils.api import APIError
from utils.constants import CONTEST_PASSWORD_SESSION_KEY

from ..utils import is_problem_public_test_case_download_enabled

class ProblemTagAPI(APIView):
    def get(self, request):
        keyword = request.GET.get("keyword")
        tags = get_problem_tag_queryset(keyword=keyword, only_used=True).order_by("-problem_count", "rank", "name", "id")
        return self.success(TagSerializer(tags, many=True).data)


class PickOneAPI(APIView):
    def get(self, request):
        problems = Problem.objects.filter(contest_id__isnull=True, visible=True)
        count = problems.count()
        if count == 0:
            return self.error("No problem to pick")
        return self.success(problems[random.randint(0, count - 1)]._id)


class ProblemAPI(APIView):
    @staticmethod
    def _add_problem_status(request, queryset_values):
        if request.user.is_authenticated:
            profile = request.user.userprofile
            acm_problems_status = profile.acm_problems_status.get("problems", {})
            oi_problems_status = profile.oi_problems_status.get("problems", {})
            # paginate data
            results = queryset_values.get("results")
            if results is not None:
                problems = results
            else:
                problems = [queryset_values, ]
            for problem in problems:
                if problem["rule_type"] == ProblemRuleType.ACM:
                    problem["my_status"] = acm_problems_status.get(str(problem["id"]), {}).get("status")
                else:
                    problem["my_status"] = oi_problems_status.get(str(problem["id"]), {}).get("status")

    def get(self, request):
        # 问题详情页
        problem_id = request.GET.get("problem_id")
        if problem_id:
            try:
                problem = Problem.objects.select_related("created_by") \
                    .get(_id=problem_id, contest_id__isnull=True, visible=True)
                problem_data = ProblemSerializer(problem).data
                problem_data["can_download_test_case"] = is_problem_public_test_case_download_enabled(problem)
                self._add_problem_status(request, problem_data)
                return self.success(problem_data)
            except Problem.DoesNotExist:
                return self.error("Problem does not exist")

        limit = request.GET.get("limit")
        if not limit:
            return self.error("Limit is needed")

        problems = Problem.objects.select_related("created_by").filter(contest_id__isnull=True, visible=True)
        # 按照标签筛选
        tag_text = request.GET.get("tag")
        if tag_text:
            normalized_tag = normalize_tag_name(tag_text)
            problems = problems.filter(Q(tags__normalized_name=normalized_tag) |
                                       Q(tags__name=tag_text)).distinct()

        # 搜索的情况
        keyword = request.GET.get("keyword", "").strip()
        if keyword:
            problems = problems.filter(Q(title__icontains=keyword) | Q(_id__icontains=keyword))

        # 难度筛选
        difficulty = request.GET.get("difficulty")
        if difficulty:
            problems = problems.filter(difficulty=difficulty)
        # 根据profile 为做过的题目添加标记
        data = self.paginate_data(request, problems, ProblemSerializer)
        self._add_problem_status(request, data)
        return self.success(data)


class ContestProblemAPI(APIView):
    def _add_problem_status(self, request, queryset_values):
        if request.user.is_authenticated:
            profile = request.user.userprofile
            if self.contest.rule_type == ContestRuleType.ACM:
                problems_status = profile.acm_problems_status.get("contest_problems", {})
            else:
                problems_status = profile.oi_problems_status.get("contest_problems", {})

            # Fallback from contest-scoped submissions only. This keeps contest
            # and global problem submissions isolated while restoring status tags.
            from submission.models import Submission, JudgeStatus
            problem_ids = [p["id"] for p in queryset_values]
            submission_status_map = defaultdict(lambda: None)
            if problem_ids:
                rows = Submission.objects.filter(
                    contest_id=self.contest.id,
                    user_id=request.user.id,
                    problem_id__in=problem_ids
                ).values("problem_id", "result")
                for row in rows:
                    pid = row["problem_id"]
                    result = row["result"]
                    if result == JudgeStatus.ACCEPTED:
                        submission_status_map[pid] = JudgeStatus.ACCEPTED
                    elif submission_status_map[pid] is None:
                        submission_status_map[pid] = result

            for problem in queryset_values:
                status = problems_status.get(str(problem["id"]), {}).get("status")
                if status is None:
                    status = submission_status_map.get(problem["id"])
                problem["my_status"] = status

    @check_contest_permission(check_type="problems")
    def get(self, request):
        problem_id = request.GET.get("problem_id")
        if problem_id:
            try:
                problem = Problem.objects.select_related("created_by").get(_id=problem_id,
                                                                           contest=self.contest,
                                                                           visible=True)
            except Problem.DoesNotExist:
                return self.error("Problem does not exist.")
            if self.contest.problem_details_permission(request.user):
                problem_data = ProblemSerializer(problem).data
                self._add_problem_status(request, [problem_data, ])
            else:
                problem_data = ProblemSafeSerializer(problem).data
            problem_data["can_download_test_case"] = is_problem_public_test_case_download_enabled(problem)
            return self.success(problem_data)

        contest_problems = Problem.objects.select_related("created_by").filter(contest=self.contest, visible=True)
        if self.contest.problem_details_permission(request.user):
            data = ProblemSerializer(contest_problems, many=True).data
            self._add_problem_status(request, data)
        else:
            data = ProblemSafeSerializer(contest_problems, many=True).data
        for index, problem in enumerate(contest_problems):
            data[index]["can_download_test_case"] = is_problem_public_test_case_download_enabled(problem)
        return self.success(data)


class DLTestCaseZipProcessor(object):
    def process_zip(self, uploaded_zip_file, spj, dir=""):
        try:
            zip_file = zipfile.ZipFile(uploaded_zip_file, "r")
        except zipfile.BadZipFile:
            raise APIError("Bad zip file")
        name_list = zip_file.namelist()
        test_case_list = self.filter_name_list(name_list, spj=spj, dir=dir)
        if not test_case_list:
            raise APIError("Empty file")

        test_case_id = rand_str()
        test_case_dir = os.path.join(settings.TEST_CASE_DIR, test_case_id)
        os.mkdir(test_case_dir)
        os.chmod(test_case_dir, 0o710)

        size_cache = {}
        md5_cache = {}

        for item in test_case_list:
            with open(os.path.join(test_case_dir, item), "wb") as f:
                content = zip_file.read(f"{dir}{item}").replace(b"\r\n", b"\n")
                size_cache[item] = len(content)
                if item.endswith(".out"):
                    md5_cache[item] = hashlib.md5(content.rstrip()).hexdigest()
                f.write(content)
        test_case_info = {"spj": spj, "test_cases": {}, "allow_public_test_case_download": False}

        info = []

        if spj:
            for index, item in enumerate(test_case_list):
                data = {"input_name": item, "input_size": size_cache[item]}
                info.append(data)
                test_case_info["test_cases"][str(index + 1)] = data
        else:
            # ["1.in", "1.out", "2.in", "2.out"] => [("1.in", "1.out"), ("2.in", "2.out")]
            test_case_list = zip(*[test_case_list[i::2] for i in range(2)])
            for index, item in enumerate(test_case_list):
                data = {"stripped_output_md5": md5_cache[item[1]],
                        "input_size": size_cache[item[0]],
                        "output_size": size_cache[item[1]],
                        "input_name": item[0],
                        "output_name": item[1]}
                info.append(data)
                test_case_info["test_cases"][str(index + 1)] = data

        with open(os.path.join(test_case_dir, "info"), "w", encoding="utf-8") as f:
            f.write(json.dumps(test_case_info, indent=4))

        for item in os.listdir(test_case_dir):
            os.chmod(os.path.join(test_case_dir, item), 0o640)

        return info, test_case_id

    def filter_name_list(self, name_list, spj, dir=""):
        ret = []
        prefix = 1
        if spj:
            while True:
                in_name = f"{prefix}.in"
                if f"{dir}{in_name}" in name_list:
                    ret.append(in_name)
                    prefix += 1
                    continue
                else:
                    return sorted(ret, key=natural_sort_key)
        else:
            while True:
                in_name = f"{prefix}.in"
                out_name = f"{prefix}.out"
                if f"{dir}{in_name}" in name_list and f"{dir}{out_name}" in name_list:
                    ret.append(in_name)
                    ret.append(out_name)
                    prefix += 1
                    continue
                else:
                    return sorted(ret, key=natural_sort_key)


class DLTestCaseAPI(APIView, DLTestCaseZipProcessor):
    request_parsers = ()

    def _can_access_problem(self, request, problem):
        if not problem.visible:
            return False, "Problem does not exists"
        if not problem.contest:
            return True, None

        contest = problem.contest
        user = request.user
        if not user.is_authenticated:
            return False, "Please login first."
        if user.is_contest_admin(contest):
            return True, None
        if contest.contest_type == ContestType.PASSWORD_PROTECTED_CONTEST:
            contest_password = request.session.get(CONTEST_PASSWORD_SESSION_KEY, {}).get(contest.id)
            if not check_contest_password(contest_password, contest.password):
                return False, "Wrong password or password expired"
        if contest.status == ContestStatus.CONTEST_NOT_START:
            return False, "Contest has not started yet."
        return True, None

    def get(self, request):
        problem_id = request.GET.get("problem_id")
        if not problem_id:
            return self.error("Parameter error, problem_id is required")
        try:
            problem = Problem.objects.select_related("contest").get(id=problem_id)
        except Problem.DoesNotExist:
            return self.error("Problem does not exists")

        has_access, error_message = self._can_access_problem(request, problem)
        if not has_access:
            return self.error(error_message)
        if not is_problem_public_test_case_download_enabled(problem):
            return self.error("Problem does not support download")
        if problem.contest and problem.contest.is_exam:
            return self.error("考试题目不允许下载测试数据")

        test_case_dir = os.path.join(settings.TEST_CASE_DIR, problem.test_case_id)
        if not os.path.isdir(test_case_dir):
            return self.error("Test case does not exists")
        name_list = self.filter_name_list(os.listdir(test_case_dir), problem.spj)
        name_list.append("info")
        file_name = os.path.join(test_case_dir, problem.test_case_id + ".zip")
        with zipfile.ZipFile(file_name, "w") as file:
            for test_case in name_list:
                file.write(f"{test_case_dir}/{test_case}", test_case)
        response = StreamingHttpResponse(FileWrapper(open(file_name, "rb")),
                                         content_type="application/octet-stream")

        response["Content-Disposition"] = f"attachment; filename=problem_{problem.id}_test_cases.zip"
        response["Content-Length"] = os.path.getsize(file_name)
        return response