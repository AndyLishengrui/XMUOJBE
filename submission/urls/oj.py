from django.conf.urls import url

from ..views.oj import SubmissionAPI, SubmissionListAPI, ContestSubmissionListAPI, SubmissionExistsAPI, CoachReportAPI, RunCodeAPI

urlpatterns = [
    url(r"^submission/?$", SubmissionAPI.as_view(), name="submission_api"),
    url(r"^submissions/?$", SubmissionListAPI.as_view(), name="submission_list_api"),
    url(r"^submission_exists/?$", SubmissionExistsAPI.as_view(), name="submission_exists"),
    url(r"^run_code/?$", RunCodeAPI.as_view(), name="run_code_api"),
    url(r"^contest_submissions/?$", ContestSubmissionListAPI.as_view(), name="contest_submission_list_api"),
    url(r"^coach_report/?$", CoachReportAPI.as_view(), name="coach_report_api"),
]
