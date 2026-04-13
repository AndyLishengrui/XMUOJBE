from judge.tasks import judge_task
# from judge.dispatcher import JudgeDispatcher
from utils.api import APIView
from ..models import Submission


class SubmissionRejudgeAPI(APIView):
    def get(self, request):
        id = request.GET.get("id")
        if not id:
            return self.error("Parameter error, id is required")
        try:
            submission = Submission.objects.select_related("problem", "contest").get(id=id)
        except Submission.DoesNotExist:
            return self.error("Submission does not exists")

        if submission.contest_id:
            if not request.user.is_contest_admin(submission.contest):
                return self.error("Permission denied")
        elif not request.user.is_super_admin():
            return self.error("Permission denied")

        submission.statistic_info = {}
        submission.save()

        judge_task.send(submission.id, submission.problem.id)
        return self.success()
