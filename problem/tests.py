import copy
import hashlib
import json
import os
import shutil
from datetime import timedelta
from zipfile import ZipFile

from django.conf import settings
from django.core.management import call_command

from utils.api.tests import APITestCase

from .models import ProblemTag, ProblemIOMode
from .models import Problem, ProblemRuleType
from contest.models import Contest, ContestRuleType
from contest.tests import DEFAULT_CONTEST_DATA

from .views.admin import TestCaseAPI
from .utils import parse_problem_template, PUBLIC_TEST_CASE_DOWNLOAD_KEY

DEFAULT_PROBLEM_DATA = {"_id": "A-110", "title": "test", "description": "<p>test</p>", "input_description": "test",
                        "output_description": "test", "time_limit": 1000, "memory_limit": 256, "difficulty": "Low",
                        "visible": True, "tags": ["test"], "languages": ["C", "C++", "Java", "Python2"], "template": {},
                        "samples": [{"input": "test", "output": "test"}], "spj": False, "spj_language": "C",
                        "spj_code": "", "spj_compile_ok": True, "test_case_id": "499b26290cc7994e0b497212e842ea85",
                        "test_case_score": [{"output_name": "1.out", "input_name": "1.in", "output_size": 0,
                                             "stripped_output_md5": "d41d8cd98f00b204e9800998ecf8427e",
                                             "input_size": 0, "score": 0}],
                        "io_mode": {"io_mode": ProblemIOMode.standard, "input": "input.txt", "output": "output.txt"},
                        "share_submission": False,
                        "rule_type": "ACM", "hint": "<p>test</p>", "source": "test"}


class ProblemCreateTestBase(APITestCase):
    @staticmethod
    def add_problem(problem_data, created_by):
        data = copy.deepcopy(problem_data)
        if data["spj"]:
            if not data["spj_language"] or not data["spj_code"]:
                raise ValueError("Invalid spj")
            data["spj_version"] = hashlib.md5(
                (data["spj_language"] + ":" + data["spj_code"]).encode("utf-8")).hexdigest()
        else:
            data["spj_language"] = None
            data["spj_code"] = None
        if data["rule_type"] == ProblemRuleType.OI:
            total_score = 0
            for item in data["test_case_score"]:
                if item["score"] <= 0:
                    raise ValueError("invalid score")
                else:
                    total_score += item["score"]
            data["total_score"] = total_score
        data["created_by"] = created_by
        tags = data.pop("tags")

        data["languages"] = list(data["languages"])

        problem = Problem.objects.create(**data)

        for item in tags:
            try:
                tag = ProblemTag.objects.get(name=item)
            except ProblemTag.DoesNotExist:
                tag = ProblemTag.objects.create(name=item, normalized_name=item.lower())
            problem.tags.add(tag)
        return problem

    @staticmethod
    def ensure_test_case_dir(test_case_id, spj=False, allow_public_download=False):
        test_case_dir = os.path.join(settings.TEST_CASE_DIR, test_case_id)
        os.makedirs(test_case_dir, exist_ok=True)
        input_path = os.path.join(test_case_dir, "1.in")
        with open(input_path, "w", encoding="utf-8") as input_file:
            input_file.write("1\n")
        test_cases = {
            "1": {
                "input_name": "1.in",
                "input_size": os.path.getsize(input_path)
            }
        }
        if not spj:
            output_path = os.path.join(test_case_dir, "1.out")
            with open(output_path, "w", encoding="utf-8") as output_file:
                output_file.write("1\n")
            test_cases["1"].update({
                "output_name": "1.out",
                "output_size": os.path.getsize(output_path),
                "stripped_output_md5": hashlib.md5(b"1").hexdigest()
            })
        with open(os.path.join(test_case_dir, "info"), "w", encoding="utf-8") as info_file:
            json.dump({
                "spj": spj,
                "test_cases": test_cases,
                PUBLIC_TEST_CASE_DOWNLOAD_KEY: allow_public_download
            }, info_file, indent=4)
        return test_case_dir


class ProblemTagListAPITest(APITestCase):
    def test_get_tag_list(self):
        ProblemTag.objects.create(name="name1", normalized_name="name1")
        ProblemTag.objects.create(name="name2", normalized_name="name2")
        resp = self.client.get(self.reverse("problem_tag_list_api"))
        self.assertSuccess(resp)


class ProblemTagAdminAPITest(APITestCase):
    def setUp(self):
        self.user = self.create_super_admin()
        self.url = self.reverse("problem_tag_admin_api")

    def test_create_problem_tag(self):
        resp = self.client.post(self.url, data={"name": "DP", "aliases": ["dynamic programming"]})
        self.assertSuccess(resp)
        self.assertEqual(resp.data["data"]["name"], "DP")
        self.assertEqual(resp.data["data"]["normalized_name"], "dp")

    def test_edit_problem_tag(self):
        tag = ProblemTag.objects.create(name="Graph", normalized_name="graph")
        resp = self.client.put(self.url, data={"id": tag.id, "name": "Graph Theory", "aliases": ["graph"]})
        self.assertSuccess(resp)
        tag.refresh_from_db()
        self.assertEqual(tag.name, "Graph Theory")

    def test_get_single_problem_tag(self):
        tag = ProblemTag.objects.create(name="Graph", normalized_name="graph", aliases=["图论"])
        resp = self.client.get(self.url, data={"id": tag.id})
        self.assertSuccess(resp)
        self.assertEqual(resp.data["data"]["id"], tag.id)
        self.assertEqual(resp.data["data"]["name"], tag.name)

    def test_delete_problem_tag(self):
        tag = ProblemTag.objects.create(name="Graph", normalized_name="graph")
        problem = ProblemCreateTestBase.add_problem(DEFAULT_PROBLEM_DATA, self.user)
        problem.tags.set([tag])

        resp = self.client.delete(self.url, data={"id": tag.id})
        self.assertSuccess(resp)
        self.assertFalse(ProblemTag.objects.filter(id=tag.id).exists())
        self.assertEqual(problem.tags.count(), 0)

    def test_audit_command_runs(self):
        ProblemTag.objects.create(name="Greedy", normalized_name="greedy")
        ProblemTag.objects.create(name="greedy", normalized_name="greedy")
        call_command("audit_problem_tags")

    def test_merge_problem_tags(self):
        target_tag = ProblemTag.objects.create(name="Dijkstra", normalized_name="dijkstra", aliases=[])
        source_tag = ProblemTag.objects.create(name="dijkstra", normalized_name="dijkstra", aliases=[])
        problem = ProblemCreateTestBase.add_problem(DEFAULT_PROBLEM_DATA, self.user)
        problem.tags.set([source_tag])

        resp = self.client.post(self.reverse("problem_tag_merge_api"), data={
            "target_tag_id": target_tag.id,
            "source_tag_ids": [source_tag.id]
        })
        self.assertSuccess(resp)
        problem.refresh_from_db()
        self.assertEqual(list(problem.tags.values_list("name", flat=True)), ["Dijkstra"])
        self.assertFalse(ProblemTag.objects.filter(id=source_tag.id).exists())


class TestCaseUploadAPITest(APITestCase):
    def setUp(self):
        self.api = TestCaseAPI()
        self.url = self.reverse("test_case_api")
        self.create_super_admin()

    def test_filter_file_name(self):
        self.assertEqual(self.api.filter_name_list(["1.in", "1.out", "2.in", ".DS_Store"], spj=False),
                         ["1.in", "1.out"])
        self.assertEqual(self.api.filter_name_list(["2.in", "2.out"], spj=False), [])

        self.assertEqual(self.api.filter_name_list(["1.in", "1.out", "2.in"], spj=True), ["1.in", "2.in"])
        self.assertEqual(self.api.filter_name_list(["2.in", "3.in"], spj=True), [])

    def make_test_case_zip(self):
        base_dir = os.path.join("/tmp", "test_case")
        shutil.rmtree(base_dir, ignore_errors=True)
        os.mkdir(base_dir)
        file_names = ["1.in", "1.out", "2.in", ".DS_Store"]
        for item in file_names:
            with open(os.path.join(base_dir, item), "w", encoding="utf-8") as f:
                f.write(item + "\n" + item + "\r\n" + "end")
        zip_file = os.path.join(base_dir, "test_case.zip")
        with ZipFile(os.path.join(base_dir, "test_case.zip"), "w") as f:
            for item in file_names:
                f.write(os.path.join(base_dir, item), item)
        return zip_file

    def test_upload_spj_test_case_zip(self):
        with open(self.make_test_case_zip(), "rb") as f:
            resp = self.client.post(self.url,
                                    data={"spj": "true", "file": f}, format="multipart")
            self.assertSuccess(resp)
            data = resp.data["data"]
            self.assertEqual(data["spj"], True)
            test_case_dir = os.path.join(settings.TEST_CASE_DIR, data["id"])
            self.assertTrue(os.path.exists(test_case_dir))
            for item in data["info"]:
                name = item["input_name"]
                with open(os.path.join(test_case_dir, name), "r", encoding="utf-8") as f:
                    self.assertEqual(f.read(), name + "\n" + name + "\n" + "end")

    def test_upload_test_case_zip(self):
        with open(self.make_test_case_zip(), "rb") as f:
            resp = self.client.post(self.url,
                                    data={"spj": "false", "file": f}, format="multipart")
            self.assertSuccess(resp)
            data = resp.data["data"]
            self.assertEqual(data["spj"], False)
            test_case_dir = os.path.join(settings.TEST_CASE_DIR, data["id"])
            self.assertTrue(os.path.exists(test_case_dir))
            for item in data["info"]:
                name = item["input_name"]
                with open(os.path.join(test_case_dir, name), "r", encoding="utf-8") as f:
                    self.assertEqual(f.read(), name + "\n" + name + "\n" + "end")


class ProblemAdminAPITest(APITestCase):
    def setUp(self):
        self.url = self.reverse("problem_admin_api")
        self.create_super_admin()
        self.data = copy.deepcopy(DEFAULT_PROBLEM_DATA)
        ProblemTag.objects.create(name="test", normalized_name="test")
        ProblemCreateTestBase.ensure_test_case_dir(self.data["test_case_id"])

    def test_create_problem(self):
        resp = self.client.post(self.url, data=self.data)
        self.assertSuccess(resp)
        return resp

    def test_duplicate_display_id(self):
        self.test_create_problem()

        resp = self.client.post(self.url, data=self.data)
        self.assertFailed(resp, "Display ID already exists")

    def test_spj(self):
        data = copy.deepcopy(self.data)
        data["spj"] = True

        resp = self.client.post(self.url, data)
        self.assertFailed(resp, "Invalid spj")

        data["spj_code"] = "test"
        resp = self.client.post(self.url, data=data)
        self.assertSuccess(resp)

    def test_get_problem(self):
        self.test_create_problem()
        resp = self.client.get(self.url)
        self.assertSuccess(resp)

    def test_get_one_problem(self):
        problem_id = self.test_create_problem().data["data"]["id"]
        resp = self.client.get(self.url + "?id=" + str(problem_id))
        self.assertSuccess(resp)

    def test_edit_problem(self):
        problem_id = self.test_create_problem().data["data"]["id"]
        data = copy.deepcopy(self.data)
        data["id"] = problem_id
        resp = self.client.put(self.url, data=data)
        self.assertSuccess(resp)

    def test_problem_download_flag_round_trip(self):
        problem_id = self.test_create_problem().data["data"]["id"]
        get_resp = self.client.get(self.url + "?id=" + str(problem_id))
        self.assertSuccess(get_resp)
        self.assertEqual(get_resp.data["data"]["allow_public_test_case_download"], False)

        data = copy.deepcopy(self.data)
        data["id"] = problem_id
        data["allow_public_test_case_download"] = True
        edit_resp = self.client.put(self.url, data=data)
        self.assertSuccess(edit_resp)

        get_resp = self.client.get(self.url + "?id=" + str(problem_id))
        self.assertSuccess(get_resp)
        self.assertEqual(get_resp.data["data"]["allow_public_test_case_download"], True)
        with open(os.path.join(settings.TEST_CASE_DIR, self.data["test_case_id"], "info"), "r", encoding="utf-8") as info_file:
            info = json.load(info_file)
        self.assertEqual(info[PUBLIC_TEST_CASE_DOWNLOAD_KEY], True)


class ProblemAPITest(ProblemCreateTestBase):
    def setUp(self):
        self.url = self.reverse("problem_api")
        admin = self.create_admin(login=False)
        self.ensure_test_case_dir(DEFAULT_PROBLEM_DATA["test_case_id"])
        self.problem = self.add_problem(DEFAULT_PROBLEM_DATA, admin)
        self.create_user("test", "test123")

    def test_get_problem_list(self):
        resp = self.client.get(f"{self.url}?limit=10")
        self.assertSuccess(resp)

    def get_one_problem(self):
        resp = self.client.get(self.url + "?id=" + self.problem._id)
        self.assertSuccess(resp)

    def test_problem_detail_exposes_download_flag(self):
        resp = self.client.get(self.url + "?problem_id=" + self.problem._id)
        self.assertSuccess(resp)
        self.assertEqual(resp.data["data"]["can_download_test_case"], False)


class DownloadTestCaseAPITest(ProblemCreateTestBase):
    def setUp(self):
        self.url = self.reverse("dl_test_case_api")
        admin = self.create_admin(login=False)
        self.test_case_id = DEFAULT_PROBLEM_DATA["test_case_id"]
        self.ensure_test_case_dir(self.test_case_id, allow_public_download=True)
        data = copy.deepcopy(DEFAULT_PROBLEM_DATA)
        data["test_case_id"] = self.test_case_id
        self.problem = self.add_problem(data, admin)

    def test_public_problem_can_download_test_case(self):
        resp = self.client.get(self.url + f"?problem_id={self.problem.id}")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("attachment; filename=problem_", resp["Content-Disposition"])

    def test_problem_download_disabled_returns_error(self):
        self.ensure_test_case_dir(self.test_case_id, allow_public_download=False)
        resp = self.client.get(self.url + f"?problem_id={self.problem.id}")
        self.assertFailed(resp, "Problem does not support download")

    def test_contest_problem_requires_contest_access(self):
        contest_data = copy.deepcopy(DEFAULT_CONTEST_DATA)
        contest_data["password"] = ""
        contest_data["start_time"] = contest_data["start_time"] + timedelta(hours=1)
        contest = Contest.objects.create(**{
            "title": contest_data["title"],
            "description": contest_data["description"],
            "real_time_rank": contest_data["real_time_rank"],
            "password": contest_data["password"],
            "rule_type": contest_data["rule_type"],
            "start_time": contest_data["start_time"],
            "end_time": contest_data["end_time"],
            "created_by": self.problem.created_by,
            "visible": contest_data["visible"],
            "allowed_ip_ranges": contest_data["allowed_ip_ranges"]
        })
        self.problem.contest = contest
        self.problem.save()
        self.client.logout()
        resp = self.client.get(self.url + f"?problem_id={self.problem.id}")
        self.assertFailed(resp, "Please login first.")


class ContestProblemAdminTest(APITestCase):
    def setUp(self):
        self.url = self.reverse("contest_problem_admin_api")
        self.batch_lang_url = self.reverse("contest_problem_batch_language_admin_api")
        self.create_admin()
        self.contest = self.client.post(self.reverse("contest_admin_api"), data=DEFAULT_CONTEST_DATA).data["data"]
        ProblemTag.objects.create(name="test", normalized_name="test")

    def test_create_contest_problem(self):
        data = copy.deepcopy(DEFAULT_PROBLEM_DATA)
        data["contest_id"] = self.contest["id"]
        resp = self.client.post(self.url, data=data)
        self.assertSuccess(resp)
        return resp.data["data"]

    def test_get_contest_problem(self):
        self.test_create_contest_problem()
        contest_id = self.contest["id"]
        resp = self.client.get(self.url + "?contest_id=" + str(contest_id))
        self.assertSuccess(resp)
        self.assertEqual(len(resp.data["data"]["results"]), 1)

    def test_get_one_contest_problem(self):
        contest_problem = self.test_create_contest_problem()
        contest_id = self.contest["id"]
        problem_id = contest_problem["id"]
        resp = self.client.get(f"{self.url}?contest_id={contest_id}&id={problem_id}")
        self.assertSuccess(resp)

    def test_batch_update_contest_problem_languages(self):
        data1 = copy.deepcopy(DEFAULT_PROBLEM_DATA)
        data1["contest_id"] = self.contest["id"]
        data1["_id"] = "A-110"
        data2 = copy.deepcopy(DEFAULT_PROBLEM_DATA)
        data2["contest_id"] = self.contest["id"]
        data2["_id"] = "A-111"
        self.client.post(self.url, data=data1)
        self.client.post(self.url, data=data2)

        resp = self.client.post(self.batch_lang_url, data={
            "contest_id": self.contest["id"],
            "languages": ["C", "Java", "Python3"]
        })
        self.assertSuccess(resp)
        self.assertEqual(resp.data["data"]["updated_count"], 2)

        problems = Problem.objects.filter(contest_id=self.contest["id"]).order_by("_id")
        for problem in problems:
            self.assertListEqual(problem.languages, ["C", "Java", "Python3"])

    def test_batch_update_contest_problem_languages_empty(self):
        resp = self.client.post(self.batch_lang_url, data={
            "contest_id": self.contest["id"],
            "languages": []
        })
        self.assertFailed(resp)


class ContestProblemTest(ProblemCreateTestBase):
    def setUp(self):
        admin = self.create_admin()
        self.ensure_test_case_dir(DEFAULT_PROBLEM_DATA["test_case_id"], allow_public_download=True)
        url = self.reverse("contest_admin_api")
        contest_data = copy.deepcopy(DEFAULT_CONTEST_DATA)
        contest_data["password"] = ""
        contest_data["start_time"] = contest_data["start_time"] + timedelta(hours=1)
        self.contest = self.client.post(url, data=contest_data).data["data"]
        self.problem = self.add_problem(DEFAULT_PROBLEM_DATA, admin)
        self.problem.contest_id = self.contest["id"]
        self.problem.save()
        self.url = self.reverse("contest_problem_api")

    def test_admin_get_contest_problem_list(self):
        contest_id = self.contest["id"]
        resp = self.client.get(self.url + "?contest_id=" + str(contest_id))
        self.assertSuccess(resp)
        self.assertEqual(len(resp.data["data"]), 1)

    def test_admin_get_one_contest_problem(self):
        contest_id = self.contest["id"]
        problem_id = self.problem._id
        resp = self.client.get("{}?contest_id={}&problem_id={}".format(self.url, contest_id, problem_id))
        self.assertSuccess(resp)

    def test_regular_user_get_not_started_contest_problem(self):
        self.create_user("test", "test123")
        resp = self.client.get(self.url + "?contest_id=" + str(self.contest["id"]))
        self.assertDictEqual(resp.data, {"error": "error", "data": "Contest has not started yet."})

    def test_reguar_user_get_started_contest_problem(self):
        self.create_user("test", "test123")
        contest = Contest.objects.first()
        contest.start_time = contest.start_time - timedelta(hours=1)
        contest.save()
        resp = self.client.get(self.url + "?contest_id=" + str(self.contest["id"]))
        self.assertSuccess(resp)

    def test_contest_problem_detail_returns_download_flag_in_safe_branch(self):
        contest = Contest.objects.get(id=self.contest["id"])
        contest.rule_type = ContestRuleType.OI
        contest.real_time_rank = False
        contest.start_time = contest.start_time - timedelta(hours=2)
        contest.save()
        self.problem.rule_type = ProblemRuleType.OI
        self.problem.save()

        self.create_user("test_user", "test123")
        resp = self.client.get(f"{self.url}?contest_id={contest.id}&problem_id={self.problem._id}")
        self.assertSuccess(resp)
        self.assertEqual(resp.data["data"]["can_download_test_case"], True)


class AddProblemFromPublicProblemAPITest(ProblemCreateTestBase):
    def setUp(self):
        admin = self.create_admin()
        self.ensure_test_case_dir(DEFAULT_PROBLEM_DATA["test_case_id"], allow_public_download=True)
        url = self.reverse("contest_admin_api")
        contest_data = copy.deepcopy(DEFAULT_CONTEST_DATA)
        contest_data["password"] = ""
        contest_data["start_time"] = contest_data["start_time"] + timedelta(hours=1)
        self.contest = self.client.post(url, data=contest_data).data["data"]
        self.problem = self.add_problem(DEFAULT_PROBLEM_DATA, admin)
        self.url = self.reverse("add_contest_problem_from_public_api")
        self.data = {
            "display_id": "1000",
            "contest_id": self.contest["id"],
            "problem_id": self.problem.id
        }

    def test_add_contest_problem(self):
        resp = self.client.post(self.url, data=self.data)
        self.assertSuccess(resp)
        self.assertTrue(Problem.objects.all().exists())
        self.assertTrue(Problem.objects.filter(contest_id=self.contest["id"]).exists())

    def test_add_contest_problem_copies_test_case_independently(self):
        resp = self.client.post(self.url, data=self.data)
        self.assertSuccess(resp)
        contest_problem = Problem.objects.get(contest_id=self.contest["id"], _id=self.data["display_id"])
        self.assertNotEqual(contest_problem.test_case_id, self.problem.test_case_id)

        contest_admin_url = self.reverse("contest_problem_admin_api")
        get_resp = self.client.get(f"{contest_admin_url}?contest_id={self.contest['id']}&id={contest_problem.id}")
        self.assertSuccess(get_resp)
        edit_data = get_resp.data["data"]
        edit_data["contest_id"] = self.contest["id"]
        edit_data["allow_public_test_case_download"] = False
        edit_resp = self.client.put(contest_admin_url, data=edit_data)
        self.assertSuccess(edit_resp)

        with open(os.path.join(settings.TEST_CASE_DIR, self.problem.test_case_id, "info"), "r", encoding="utf-8") as info_file:
            public_info = json.load(info_file)
        with open(os.path.join(settings.TEST_CASE_DIR, contest_problem.test_case_id, "info"), "r", encoding="utf-8") as info_file:
            contest_info = json.load(info_file)
        self.assertEqual(public_info[PUBLIC_TEST_CASE_DOWNLOAD_KEY], True)
        self.assertEqual(contest_info[PUBLIC_TEST_CASE_DOWNLOAD_KEY], False)


class ParseProblemTemplateTest(APITestCase):
    def test_parse(self):
        template_str = """
//PREPEND BEGIN
aaa
//PREPEND END

//TEMPLATE BEGIN
bbb
//TEMPLATE END

//APPEND BEGIN
ccc
//APPEND END
"""

        ret = parse_problem_template(template_str)
        self.assertEqual(ret["prepend"], "aaa\n")
        self.assertEqual(ret["template"], "bbb\n")
        self.assertEqual(ret["append"], "ccc\n")

    def test_parse1(self):
        template_str = """
//PREPEND BEGIN
aaa
//PREPEND END

//APPEND BEGIN
ccc
//APPEND END
//APPEND BEGIN
ddd
//APPEND END
"""

        ret = parse_problem_template(template_str)
        self.assertEqual(ret["prepend"], "aaa\n")
        self.assertEqual(ret["template"], "")
        self.assertEqual(ret["append"], "ccc\n")
