import copy
from datetime import datetime, timedelta

from django.utils import timezone

from utils.api.tests import APITestCase
from problem.models import Problem
from submission.models import Submission, JudgeStatus

from .models import ContestAnnouncement, ContestRuleType, Contest

DEFAULT_CONTEST_DATA = {"title": "test title", "description": "test description",
                        "start_time": timezone.localtime(timezone.now()),
                        "end_time": timezone.localtime(timezone.now()) + timedelta(days=1),
                        "rule_type": ContestRuleType.ACM,
                        "password": "123",
                        "allowed_ip_ranges": [],
                        "visible": True, "real_time_rank": True}


class ContestAdminAPITest(APITestCase):
    def setUp(self):
        self.create_super_admin()
        self.url = self.reverse("contest_admin_api")
        self.data = copy.deepcopy(DEFAULT_CONTEST_DATA)

    def test_create_contest(self):
        response = self.client.post(self.url, data=self.data)
        self.assertSuccess(response)
        return response

    def test_create_contest_with_invalid_cidr(self):
        self.data["allowed_ip_ranges"] = ["127.0.0"]
        resp = self.client.post(self.url, data=self.data)
        self.assertTrue(resp.data["data"].endswith("is not a valid cidr network"))

    def test_update_contest(self):
        id = self.test_create_contest().data["data"]["id"]
        update_data = {"id": id, "title": "update title",
                       "description": "update description",
                       "password": "12345",
                       "visible": False, "real_time_rank": False}
        data = copy.deepcopy(self.data)
        data.update(update_data)
        response = self.client.put(self.url, data=data)
        self.assertSuccess(response)
        response_data = response.data["data"]
        for k in data.keys():
            if isinstance(data[k], datetime):
                continue
            self.assertEqual(response_data[k], data[k])

    def test_get_contests(self):
        self.test_create_contest()
        response = self.client.get(self.url)
        self.assertSuccess(response)

    def test_admin_only_sees_own_contests(self):
        self.client.logout()
        admin_a = self.create_admin("admin_a", "admin_a_123")
        self.client.logout()
        admin_b = self.create_admin("admin_b", "admin_b_123")

        Contest.objects.create(created_by=admin_a, **copy.deepcopy(DEFAULT_CONTEST_DATA))
        Contest.objects.create(created_by=admin_b, **copy.deepcopy(DEFAULT_CONTEST_DATA))

        self.client.logout()
        self.client.login(username="admin_a", password="admin_a_123")
        response = self.client.get(self.url)
        self.assertSuccess(response)
        results = response.data["data"]["results"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["created_by"]["username"], "admin_a")

    def test_super_admin_sees_all_contests(self):
        self.client.logout()
        admin_a = self.create_admin("admin_sa_a", "admin_sa_a_123")
        self.client.logout()
        admin_b = self.create_admin("admin_sa_b", "admin_sa_b_123")

        Contest.objects.create(created_by=admin_a, **copy.deepcopy(DEFAULT_CONTEST_DATA))
        Contest.objects.create(created_by=admin_b, **copy.deepcopy(DEFAULT_CONTEST_DATA))

        self.client.logout()
        self.client.login(username="root", password="root")
        response = self.client.get(self.url)
        self.assertSuccess(response)
        results = response.data["data"]["results"]
        self.assertEqual(len(results), 2)

    def test_search_contests_by_title_and_owner_independently(self):
        self.client.logout()
        owner = self.create_admin("andy_owner_search", "owner_search_123")
        Contest.objects.create(
            created_by=owner,
            **dict(copy.deepcopy(DEFAULT_CONTEST_DATA), title="Graph Lab Search")
        )

        self.client.logout()
        self.client.login(username="root", password="root")

        # Search by title keyword only
        resp_title = self.client.get(self.url + "?keyword=Graph")
        self.assertSuccess(resp_title)
        self.assertEqual(resp_title.data["data"]["total"], 1)

        # Search by owner only
        resp_owner = self.client.get(self.url + "?owner=Andy")
        self.assertSuccess(resp_owner)
        self.assertEqual(resp_owner.data["data"]["total"], 1)

        # Combined: both filters must match
        resp_both = self.client.get(self.url + "?keyword=Graph&owner=Andy")
        self.assertSuccess(resp_both)
        self.assertEqual(resp_both.data["data"]["total"], 1)

        # Combined: title matches but owner doesn't
        resp_mismatch = self.client.get(self.url + "?keyword=Graph&owner=nonexistent")
        self.assertSuccess(resp_mismatch)
        self.assertEqual(resp_mismatch.data["data"]["total"], 0)

    def test_get_one_contest(self):
        id = self.test_create_contest().data["data"]["id"]
        response = self.client.get("{}?id={}".format(self.url, id))
        self.assertSuccess(response)

    def test_soft_delete_contest(self):
        id = self.test_create_contest().data["data"]["id"]
        response = self.client.delete("{}?id={}".format(self.url, id))
        self.assertSuccess(response)
        contest = Contest.objects.get(id=id)
        self.assertFalse(contest.visible)

    def test_hard_delete_contest(self):
        id = self.test_create_contest().data["data"]["id"]
        contest = Contest.objects.get(id=id)
        contest.start_time = timezone.localtime(timezone.now()) - timedelta(days=2)
        contest.end_time = timezone.localtime(timezone.now()) - timedelta(days=1)
        contest.save(update_fields=["start_time", "end_time"])
        response = self.client.delete("{}?id={}&hard=1".format(self.url, id))
        self.assertSuccess(response)
        self.assertFalse(Contest.objects.filter(id=id).exists())

    def test_hard_delete_running_contest(self):
        id = self.test_create_contest().data["data"]["id"]
        response = self.client.delete("{}?id={}&hard=1".format(self.url, id))
        self.assertFailed(response, "Running contest cannot be hard deleted")
        self.assertTrue(Contest.objects.filter(id=id).exists())

    def _create_problem(self, owner, contest=None, display_id="A-100"):
        return Problem.objects.create(
            _id=display_id,
            contest=contest,
            title="test",
            description="<p>test</p>",
            input_description="in",
            output_description="out",
            samples=[{"input": "1", "output": "1"}],
            test_case_id="dummy-test-case",
            test_case_score=[{"input_name": "1.in", "output_name": "1.out", "score": 0}],
            languages=["C", "C++", "Python3"],
            template={},
            created_by=owner,
            time_limit=1000,
            memory_limit=256,
            spj=False,
            rule_type="ACM",
            difficulty="Low",
        )

    def test_hard_delete_contest_cascade_contest_data_only(self):
        owner = self.create_admin("owner", "owner123")
        self.client.logout()
        self.client.login(username="root", password="root")

        contest = Contest.objects.create(created_by=owner, **copy.deepcopy(DEFAULT_CONTEST_DATA))
        contest.start_time = timezone.localtime(timezone.now()) - timedelta(days=2)
        contest.end_time = timezone.localtime(timezone.now()) - timedelta(days=1)
        contest.save(update_fields=["start_time", "end_time"])

        contest_problem = self._create_problem(owner=owner, contest=contest, display_id="A-101")
        public_problem = self._create_problem(owner=owner, contest=None, display_id="P-101")

        Submission.objects.create(
            contest=contest,
            problem=contest_problem,
            user_id=owner.id,
            username=owner.username,
            code="print(1)",
            result=JudgeStatus.ACCEPTED,
            language="Python3",
        )
        Submission.objects.create(
            contest=None,
            problem=public_problem,
            user_id=owner.id,
            username=owner.username,
            code="print(2)",
            result=JudgeStatus.ACCEPTED,
            language="Python3",
        )

        response = self.client.delete("{}?id={}&hard=1".format(self.url, contest.id))
        self.assertSuccess(response)

        self.assertFalse(Contest.objects.filter(id=contest.id).exists())
        self.assertFalse(Problem.objects.filter(id=contest_problem.id).exists())
        self.assertFalse(Submission.objects.filter(problem_id=contest_problem.id).exists())

        self.assertTrue(Problem.objects.filter(id=public_problem.id).exists())
        self.assertTrue(Submission.objects.filter(problem_id=public_problem.id).exists())

    def test_admin_cannot_delete_other_admin_contest(self):
        self.client.logout()
        owner = self.create_admin("contest_owner", "owner123")
        contest = Contest.objects.create(created_by=owner, **copy.deepcopy(DEFAULT_CONTEST_DATA))

        self.client.logout()
        self.create_admin("other_admin", "other123")

        response = self.client.delete("{}?id={}".format(self.url, contest.id))
        self.assertFailed(response, "Contest does not exist")
        self.assertTrue(Contest.objects.filter(id=contest.id).exists())


class ContestAPITest(APITestCase):
    def setUp(self):
        user = self.create_admin()
        self.contest = Contest.objects.create(created_by=user, **DEFAULT_CONTEST_DATA)
        self.url = self.reverse("contest_api") + "?id=" + str(self.contest.id)

    def test_get_contest_list(self):
        url = self.reverse("contest_list_api")
        response = self.client.get(url + "?limit=10")
        self.assertSuccess(response)
        self.assertEqual(len(response.data["data"]["results"]), 1)

    def test_get_one_contest(self):
        resp = self.client.get(self.url)
        self.assertSuccess(resp)

    def test_regular_user_validate_contest_password(self):
        self.create_user("test", "test123")
        url = self.reverse("contest_password_api")
        resp = self.client.post(url, {"contest_id": self.contest.id, "password": "error_password"})
        self.assertDictEqual(resp.data, {"error": "error", "data": "Wrong password or password expired"})

        resp = self.client.post(url, {"contest_id": self.contest.id, "password": DEFAULT_CONTEST_DATA["password"]})
        self.assertSuccess(resp)

    def test_regular_user_access_contest(self):
        self.create_user("test", "test123")
        url = self.reverse("contest_access_api")
        resp = self.client.get(url + "?contest_id=" + str(self.contest.id))
        self.assertFalse(resp.data["data"]["access"])

        password_url = self.reverse("contest_password_api")
        resp = self.client.post(password_url,
                                {"contest_id": self.contest.id, "password": DEFAULT_CONTEST_DATA["password"]})
        self.assertSuccess(resp)
        resp = self.client.get(self.url)
        self.assertSuccess(resp)


class ContestAnnouncementAdminAPITest(APITestCase):
    def setUp(self):
        self.create_super_admin()
        self.url = self.reverse("contest_announcement_admin_api")
        contest_id = self.create_contest().data["data"]["id"]
        self.data = {"title": "test title", "content": "test content", "contest_id": contest_id, "visible": True}

    def create_contest(self):
        url = self.reverse("contest_admin_api")
        data = DEFAULT_CONTEST_DATA
        return self.client.post(url, data=data)

    def test_create_contest_announcement(self):
        response = self.client.post(self.url, data=self.data)
        self.assertSuccess(response)
        return response

    def test_delete_contest_announcement(self):
        id = self.test_create_contest_announcement().data["data"]["id"]
        response = self.client.delete("{}?id={}".format(self.url, id))
        self.assertSuccess(response)
        self.assertFalse(ContestAnnouncement.objects.filter(id=id).exists())

    def test_get_contest_announcements(self):
        self.test_create_contest_announcement()
        response = self.client.get(self.url + "?contest_id=" + str(self.data["contest_id"]))
        self.assertSuccess(response)

    def test_get_one_contest_announcement(self):
        id = self.test_create_contest_announcement().data["data"]["id"]
        response = self.client.get("{}?id={}".format(self.url, id))
        self.assertSuccess(response)


class ContestAnnouncementListAPITest(APITestCase):
    def setUp(self):
        self.create_super_admin()
        self.url = self.reverse("contest_announcement_api")

    def create_contest_announcements(self):
        contest_id = self.client.post(self.reverse("contest_admin_api"), data=DEFAULT_CONTEST_DATA).data["data"]["id"]
        url = self.reverse("contest_announcement_admin_api")
        self.client.post(url, data={"title": "test title1", "content": "test content1", "contest_id": contest_id})
        self.client.post(url, data={"title": "test title2", "content": "test content2", "contest_id": contest_id})
        return contest_id

    def test_get_contest_announcement_list(self):
        contest_id = self.create_contest_announcements()
        response = self.client.get(self.url, data={"contest_id": contest_id})
        self.assertSuccess(response)


class ContestRankAPITest(APITestCase):
    def setUp(self):
        user = self.create_admin()
        self.acm_contest = Contest.objects.create(created_by=user, **DEFAULT_CONTEST_DATA)
        self.create_user("test", "test123")
        self.url = self.reverse("contest_rank_api")

    def get_contest_rank(self):
        resp = self.client.get(self.url + "?contest_id=" + self.acm_contest.id)
        self.assertSuccess(resp)
