from django.db import models
from utils.models import JSONField

from account.models import User
from contest.models import Contest
from utils.models import RichTextField
from utils.constants import Choices


class ProblemTag(models.Model):
    name = models.TextField()
    normalized_name = models.TextField(default="", db_index=True)
    aliases = JSONField(default=list)
    is_active = models.BooleanField(default=True)
    rank = models.IntegerField(default=0)
    description = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "problem_tag"
        ordering = ("rank", "name", "id")


class ProblemRuleType(Choices):
    ACM = "ACM"
    OI = "OI"


class ProblemDifficulty(object):
    High = "High"
    Mid = "Mid"
    Low = "Low"


class ProblemIOMode(Choices):
    standard = "Standard IO"
    file = "File IO"


def _default_io_mode():
    return {"io_mode": ProblemIOMode.standard, "input": "input.txt", "output": "output.txt"}


class Problem(models.Model):
    # display ID
    _id = models.TextField(db_index=True)
    contest = models.ForeignKey(Contest, null=True, on_delete=models.CASCADE)
    # for contest problem
    is_public = models.BooleanField(default=False)
    title = models.TextField()
    # HTML
    description = RichTextField()
    input_description = RichTextField()
    output_description = RichTextField()
    # [{input: "test", output: "123"}, {input: "test123", output: "456"}]
    samples = JSONField()
    test_case_id = models.TextField()
    # [{"input_name": "1.in", "output_name": "1.out", "score": 0}]
    test_case_score = JSONField()
    hint = RichTextField(null=True)
    languages = JSONField()
    template = JSONField()
    create_time = models.DateTimeField(auto_now_add=True)
    # we can not use auto_now here
    last_update_time = models.DateTimeField(null=True)
    created_by = models.ForeignKey(User, on_delete=models.CASCADE)
    # ms
    time_limit = models.IntegerField()
    # MB
    memory_limit = models.IntegerField()
    # io mode
    io_mode = JSONField(default=_default_io_mode)
    # special judge related
    spj = models.BooleanField(default=False)
    spj_language = models.TextField(null=True)
    spj_code = models.TextField(null=True)
    spj_version = models.TextField(null=True)
    spj_compile_ok = models.BooleanField(default=False)
    rule_type = models.TextField()
    visible = models.BooleanField(default=True)
    difficulty = models.TextField()
    tags = models.ManyToManyField(ProblemTag)
    source = models.TextField(null=True)
    # for OI mode
    total_score = models.IntegerField(default=0)
    submission_number = models.BigIntegerField(default=0)
    accepted_number = models.BigIntegerField(default=0)
    # {JudgeStatus.ACCEPTED: 3, JudgeStaus.WRONG_ANSWER: 11}, the number means count
    statistic_info = JSONField(default=dict)
    share_submission = models.BooleanField(default=False)

    class Meta:
        db_table = "problem"
        unique_together = (("_id", "contest"),)
        ordering = ("create_time",)

    def add_submission_number(self):
        self.submission_number = models.F("submission_number") + 1
        self.save(update_fields=["submission_number"])

    def add_ac_number(self):
        self.accepted_number = models.F("accepted_number") + 1
        self.save(update_fields=["accepted_number"])

# ---- Course / Chapter / ChapterProblem Models ----

class Course(models.Model):
    """教材（分组/班级的升级版）"""
    title = models.CharField(max_length=255, db_index=True)
    description = models.TextField(null=True, blank=True)
    visible = models.BooleanField(default=True)
    order = models.IntegerField(default=0)
    contest_id = models.IntegerField(null=True, blank=True, help_text="关联的隐藏OI比赛ID，用于提交/AC跟踪")
    created_time = models.DateTimeField(auto_now_add=True)
    updated_time = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "course"
        ordering = ("order", "id")

    def __str__(self):
        return self.title


class Chapter(models.Model):
    """教材下的章节"""
    course = models.ForeignKey(Course, related_name="chapters", on_delete=models.CASCADE)
    title = models.CharField(max_length=255)
    visible = models.BooleanField(default=True)
    order = models.IntegerField(default=0)
    created_time = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "chapter"
        ordering = ("order", "id")

    def __str__(self):
        return f"{self.course.title} - {self.title}"


class ChapterProblem(models.Model):
    """章节内的题目关联"""
    PROBLEM_TYPES = (
        ("example", "例题"),
        ("exercise", "习题"),
    )
    chapter = models.ForeignKey(Chapter, related_name="problems", on_delete=models.CASCADE)
    display_id = models.CharField(max_length=64, help_text="题号，对应 Problem._id")
    type = models.CharField(max_length=16, choices=PROBLEM_TYPES, default="exercise")
    order = models.IntegerField(default=0)
    created_time = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "chapter_problem"
        ordering = ("order", "id")
        unique_together = (("chapter", "display_id"),)

    def __str__(self):
        return f"{self.chapter.title} - {self.display_id}"
