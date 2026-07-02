from django.conf.urls import url

from .views import (
    PluginBootstrapAPI,
    PluginContestListAPI,
    PluginContestWorkspaceAPI,
    PluginLoginAPI,
    PluginLogoutAPI,
    PluginProblemsetAPI,
    PluginProblemWorkspaceAPI,
    PluginSubmissionAPI,
    PluginTestCaseDownloadAPI,
)

urlpatterns = [
    url(r"^login/?$", PluginLoginAPI.as_view(), name="plugin_login_api"),
    url(r"^logout/?$", PluginLogoutAPI.as_view(), name="plugin_logout_api"),
    url(r"^bootstrap/?$", PluginBootstrapAPI.as_view(), name="plugin_bootstrap_api"),
    url(r"^contests/?$", PluginContestListAPI.as_view(), name="plugin_contest_list_api"),
    url(r"^contest_workspace/?$", PluginContestWorkspaceAPI.as_view(), name="plugin_contest_workspace_api"),
    url(r"^problemset/?$", PluginProblemsetAPI.as_view(), name="plugin_problemset_api"),
    url(r"^problem_workspace/?$", PluginProblemWorkspaceAPI.as_view(), name="plugin_problem_workspace_api"),
    url(r"^submission/?$", PluginSubmissionAPI.as_view(), name="plugin_submission_api"),
    url(r"^test_case_download/?$", PluginTestCaseDownloadAPI.as_view(), name="plugin_test_case_download_api"),
]