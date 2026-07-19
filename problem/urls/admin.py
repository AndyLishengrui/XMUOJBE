from django.conf.urls import url

from ..views.admin import (ContestProblemAPI, ProblemAPI, TestCaseAPI, MakeContestProblemPublicAPIView,
                           BatchUpdateContestProblemLanguagesAPI, BatchUpdateProblemAPI,
                           CompileSPJAPI, AddContestProblemAPI, ExportProblemAPI, ImportProblemAPI,
                           FPSProblemImport, ProblemTagAdminAPI, ProblemTagAuditAPI, ProblemTagMergeAPI,
                           CourseAdminAPI, ChapterAdminAPI, ChapterProblemAdminAPI,
                           MoveChapterProblemAdminAPI, ProblemTitlesAdminAPI)

urlpatterns = [
    url(r"^test_case/?$", TestCaseAPI.as_view(), name="test_case_api"),
    url(r"^compile_spj/?$", CompileSPJAPI.as_view(), name="compile_spj"),
    url(r"^problem/?$", ProblemAPI.as_view(), name="problem_admin_api"),
    url(r"^problem/batch_update/?$", BatchUpdateProblemAPI.as_view(), name="problem_batch_update_admin_api"),
    url(r"^problem/tags/?$", ProblemTagAdminAPI.as_view(), name="problem_tag_admin_api"),
    url(r"^problem/tag_audit/?$", ProblemTagAuditAPI.as_view(), name="problem_tag_audit_api"),
    url(r"^problem/tag_merge/?$", ProblemTagMergeAPI.as_view(), name="problem_tag_merge_api"),
    url(r"^contest/problem/?$", ContestProblemAPI.as_view(), name="contest_problem_admin_api"),
    url(r"^contest/problem/batch_languages/?$", BatchUpdateContestProblemLanguagesAPI.as_view(),
        name="contest_problem_batch_language_admin_api"),
    url(r"^contest_problem/make_public/?$", MakeContestProblemPublicAPIView.as_view(), name="make_public_api"),
    url(r"^contest/add_problem_from_public/?$", AddContestProblemAPI.as_view(), name="add_contest_problem_from_public_api"),
    url(r"^export_problem/?$", ExportProblemAPI.as_view(), name="export_problem_api"),
    url(r"^import_problem/?$", ImportProblemAPI.as_view(), name="import_problem_api"),
    url(r"^import_fps/?$", FPSProblemImport.as_view(), name="fps_problem_api"),
    # Course / Chapter / Problem management
    url(r"^groups/courses/?$", CourseAdminAPI.as_view(), name="course_admin_api"),
    url(r"^groups/chapters/?$", ChapterAdminAPI.as_view(), name="chapter_admin_api"),
    url(r"^groups/problems/?$", ChapterProblemAdminAPI.as_view(), name="chapter_problem_admin_api"),
    url(r"^groups/problems/move/?$", MoveChapterProblemAdminAPI.as_view(), name="move_chapter_problem_api"),
    url(r"^groups/problem_titles/?$", ProblemTitlesAdminAPI.as_view(), name="problem_titles_api"),
]
