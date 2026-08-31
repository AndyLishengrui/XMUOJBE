import re

from django import forms

from options.options import SysOptions
from utils.api import UsernameSerializer, serializers
from utils.constants import Difficulty
from utils.serializers import LanguageNameMultiChoiceField, SPJLanguageNameChoiceField, LanguageNameChoiceField

from .models import Problem, ProblemRuleType, ProblemTag, ProblemIOMode, Course, Chapter, ChapterProblem
from .tag import clean_tag_aliases, clean_tag_name, normalize_tag_name
from .utils import parse_problem_template, is_problem_public_test_case_download_enabled


class TestCaseUploadForm(forms.Form):
    spj = forms.CharField(max_length=12)
    file = forms.FileField()


class CreateSampleSerializer(serializers.Serializer):
    input = serializers.CharField(trim_whitespace=False)
    output = serializers.CharField(trim_whitespace=False)


class CreateTestCaseScoreSerializer(serializers.Serializer):
    input_name = serializers.CharField(max_length=32)
    output_name = serializers.CharField(max_length=32, allow_blank=True, allow_null=True)
    score = serializers.IntegerField(min_value=0)


class CreateProblemCodeTemplateSerializer(serializers.Serializer):
    pass


class ProblemIOModeSerializer(serializers.Serializer):
    io_mode = serializers.ChoiceField(choices=ProblemIOMode.choices())
    input = serializers.CharField()
    output = serializers.CharField()

    def validate(self, attrs):
        if attrs["input"] == attrs["output"]:
            raise serializers.ValidationError("Invalid io mode")
        for item in (attrs["input"], attrs["output"]):
            if not re.match("^[a-zA-Z0-9.]+$", item):
                raise serializers.ValidationError("Invalid io file name format")
        return attrs


class CreateOrEditProblemSerializer(serializers.Serializer):
    _id = serializers.CharField(max_length=32, allow_blank=True, allow_null=True)
    title = serializers.CharField(max_length=1024)
    description = serializers.CharField()
    input_description = serializers.CharField()
    output_description = serializers.CharField()
    samples = serializers.ListField(child=CreateSampleSerializer(), allow_empty=False)
    test_case_id = serializers.CharField(max_length=64)
    test_case_score = serializers.ListField(child=CreateTestCaseScoreSerializer(), allow_empty=True)
    time_limit = serializers.IntegerField(min_value=1, max_value=1000 * 60)
    memory_limit = serializers.IntegerField(min_value=1, max_value=1024)
    languages = LanguageNameMultiChoiceField()
    template = serializers.DictField(child=serializers.CharField(min_length=1))
    rule_type = serializers.ChoiceField(choices=[ProblemRuleType.ACM, ProblemRuleType.OI])
    io_mode = ProblemIOModeSerializer()
    spj = serializers.BooleanField()
    spj_language = SPJLanguageNameChoiceField(allow_blank=True, allow_null=True)
    spj_code = serializers.CharField(allow_blank=True, allow_null=True)
    spj_compile_ok = serializers.BooleanField(default=False)
    visible = serializers.BooleanField()
    difficulty = serializers.ChoiceField(choices=Difficulty.choices())
    tags = serializers.ListField(child=serializers.CharField(max_length=32), allow_empty=True)
    hint = serializers.CharField(allow_blank=True, allow_null=True)
    source = serializers.CharField(max_length=256, allow_blank=True, allow_null=True)
    share_submission = serializers.BooleanField()
    allow_public_test_case_download = serializers.BooleanField(required=False, default=False)


class CreateProblemSerializer(CreateOrEditProblemSerializer):
    pass


class EditProblemSerializer(CreateOrEditProblemSerializer):
    id = serializers.IntegerField()


class CreateContestProblemSerializer(CreateOrEditProblemSerializer):
    contest_id = serializers.IntegerField()


class EditContestProblemSerializer(CreateOrEditProblemSerializer):
    id = serializers.IntegerField()
    contest_id = serializers.IntegerField()


class BatchUpdateContestProblemLanguagesSerializer(serializers.Serializer):
    contest_id = serializers.IntegerField()
    languages = LanguageNameMultiChoiceField()


class BatchUpdateProblemTagsSerializer(serializers.Serializer):
    problem_ids = serializers.ListField(child=serializers.IntegerField(min_value=1), allow_empty=False)
    operation = serializers.ChoiceField(choices=["replace", "append", "remove"])
    tags = serializers.ListField(child=serializers.CharField(max_length=32), allow_empty=True, required=False)


class BatchUpdateProblemSourceSerializer(serializers.Serializer):
    problem_ids = serializers.ListField(child=serializers.IntegerField(min_value=1), allow_empty=False)
    source = serializers.CharField(max_length=256, allow_blank=True, allow_null=True, required=False)


class TagSerializer(serializers.ModelSerializer):
    problem_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = ProblemTag
        fields = ("id", "name", "normalized_name", "problem_count", "rank")


class ProblemTagAdminSerializer(serializers.ModelSerializer):
    problem_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = ProblemTag
        fields = ("id", "name", "normalized_name", "aliases", "is_active", "rank", "description", "problem_count")


class UpsertProblemTagSerializer(serializers.Serializer):
    id = serializers.IntegerField(required=False)
    name = serializers.CharField(max_length=32)
    aliases = serializers.ListField(child=serializers.CharField(max_length=32), required=False)
    is_active = serializers.BooleanField(required=False)
    rank = serializers.IntegerField(required=False)
    description = serializers.CharField(allow_blank=True, allow_null=True, required=False)

    def validate_name(self, value):
        value = clean_tag_name(value)
        if not value:
            raise serializers.ValidationError("Tag name can not be empty")
        return value

    def validate_aliases(self, value):
        return clean_tag_aliases(value)

    def validate(self, attrs):
        attrs["normalized_name"] = normalize_tag_name(attrs["name"])
        if not attrs["normalized_name"]:
            raise serializers.ValidationError("Invalid tag name")
        attrs["aliases"] = clean_tag_aliases(attrs.get("aliases", []), attrs["name"])
        return attrs


class MergeProblemTagSerializer(serializers.Serializer):
    target_tag_id = serializers.IntegerField()
    source_tag_ids = serializers.ListField(child=serializers.IntegerField(min_value=1), allow_empty=False)

    def validate(self, attrs):
        source_tag_ids = [tag_id for tag_id in attrs["source_tag_ids"] if tag_id != attrs["target_tag_id"]]
        if not source_tag_ids:
            raise serializers.ValidationError("At least one different source tag is required")
        attrs["source_tag_ids"] = list(dict.fromkeys(source_tag_ids))
        return attrs


class CompileSPJSerializer(serializers.Serializer):
    spj_language = SPJLanguageNameChoiceField()
    spj_code = serializers.CharField()


class BaseProblemSerializer(serializers.ModelSerializer):
    tags = serializers.SlugRelatedField(many=True, slug_field="name", read_only=True)
    created_by = UsernameSerializer()

    def get_public_template(self, obj):
        ret = {}
        for lang, code in obj.template.items():
            ret[lang] = parse_problem_template(code)["template"]
        return ret


class ProblemAdminSerializer(BaseProblemSerializer):
    class Meta:
        model = Problem
        fields = "__all__"


class ProblemSerializer(BaseProblemSerializer):
    template = serializers.SerializerMethodField("get_public_template")

    class Meta:
        model = Problem
        exclude = ("test_case_score", "test_case_id", "visible", "is_public",
                   "spj_code", "spj_version", "spj_compile_ok")


class ProblemSafeSerializer(BaseProblemSerializer):
    template = serializers.SerializerMethodField("get_public_template")

    class Meta:
        model = Problem
        exclude = ("test_case_score", "test_case_id", "visible", "is_public",
                   "spj_code", "spj_version", "spj_compile_ok",
                   "difficulty", "submission_number", "accepted_number", "statistic_info")


class ContestProblemMakePublicSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    display_id = serializers.CharField(max_length=32)


class ExportProblemSerializer(serializers.ModelSerializer):
    display_id = serializers.SerializerMethodField()
    description = serializers.SerializerMethodField()
    input_description = serializers.SerializerMethodField()
    output_description = serializers.SerializerMethodField()
    test_case_score = serializers.SerializerMethodField()
    hint = serializers.SerializerMethodField()
    spj = serializers.SerializerMethodField()
    template = serializers.SerializerMethodField()
    source = serializers.SerializerMethodField()
    allow_public_test_case_download = serializers.SerializerMethodField()
    tags = serializers.SlugRelatedField(many=True, slug_field="name", read_only=True)

    def get_display_id(self, obj):
        return obj._id

    def _html_format_value(self, value):
        return {"format": "html", "value": value}

    def get_description(self, obj):
        return self._html_format_value(obj.description)

    def get_input_description(self, obj):
        return self._html_format_value(obj.input_description)

    def get_output_description(self, obj):
        return self._html_format_value(obj.output_description)

    def get_hint(self, obj):
        return self._html_format_value(obj.hint)

    def get_test_case_score(self, obj):
        return [{"score": item["score"] if obj.rule_type == ProblemRuleType.OI else 100,
                 "input_name": item["input_name"], "output_name": item["output_name"]}
                for item in obj.test_case_score]

    def get_spj(self, obj):
        return {"code": obj.spj_code,
                "language": obj.spj_language} if obj.spj else None

    def get_template(self, obj):
        ret = {}
        for k, v in obj.template.items():
            ret[k] = parse_problem_template(v)
        return ret

    def get_source(self, obj):
        return obj.source or f"{SysOptions.website_name} {SysOptions.website_base_url}"

    def get_allow_public_test_case_download(self, obj):
        return is_problem_public_test_case_download_enabled(obj)

    class Meta:
        model = Problem
        fields = ("display_id", "title", "description", "tags",
                  "input_description", "output_description",
                  "test_case_score", "hint", "time_limit", "memory_limit", "samples",
                  "template", "spj", "rule_type", "source", "allow_public_test_case_download", "template")


class AddContestProblemSerializer(serializers.Serializer):
    contest_id = serializers.IntegerField()
    problem_id = serializers.IntegerField()
    display_id = serializers.CharField()


class ExportProblemRequestSerialzier(serializers.Serializer):
    problem_id = serializers.ListField(child=serializers.IntegerField(), allow_empty=False)


class UploadProblemForm(forms.Form):
    file = forms.FileField()


class FormatValueSerializer(serializers.Serializer):
    format = serializers.ChoiceField(choices=["html", "markdown"])
    value = serializers.CharField(allow_blank=True)


class TestCaseScoreSerializer(serializers.Serializer):
    score = serializers.IntegerField(min_value=1)
    input_name = serializers.CharField(max_length=32)
    output_name = serializers.CharField(max_length=32, allow_blank=True, allow_null=True)


class TemplateSerializer(serializers.Serializer):
    prepend = serializers.CharField()
    template = serializers.CharField()
    append = serializers.CharField()


class SPJSerializer(serializers.Serializer):
    code = serializers.CharField()
    language = SPJLanguageNameChoiceField()


class AnswerSerializer(serializers.Serializer):
    code = serializers.CharField()
    language = LanguageNameChoiceField()


class ImportProblemSerializer(serializers.Serializer):
    display_id = serializers.CharField(max_length=128)
    title = serializers.CharField(max_length=128)
    description = FormatValueSerializer()
    input_description = FormatValueSerializer()
    output_description = FormatValueSerializer()
    hint = FormatValueSerializer()
    test_case_score = serializers.ListField(child=TestCaseScoreSerializer(), allow_null=True)
    time_limit = serializers.IntegerField(min_value=1, max_value=60000)
    memory_limit = serializers.IntegerField(min_value=1, max_value=10240)
    samples = serializers.ListField(child=CreateSampleSerializer())
    template = serializers.DictField(child=TemplateSerializer())
    spj = SPJSerializer(allow_null=True)
    rule_type = serializers.ChoiceField(choices=ProblemRuleType.choices())
    source = serializers.CharField(max_length=200, allow_blank=True, allow_null=True)
    allow_public_test_case_download = serializers.BooleanField(required=False, default=False)
    answers = serializers.ListField(child=AnswerSerializer())
    tags = serializers.ListField(child=serializers.CharField())


class FPSProblemSerializer(serializers.Serializer):
    class UnitSerializer(serializers.Serializer):
        unit = serializers.ChoiceField(choices=["MB", "s", "ms"])
        value = serializers.IntegerField(min_value=1, max_value=60000)

    title = serializers.CharField(max_length=128)
    description = serializers.CharField()
    input = serializers.CharField()
    output = serializers.CharField()
    hint = serializers.CharField(allow_blank=True, allow_null=True)
    time_limit = UnitSerializer()
    memory_limit = UnitSerializer()
    samples = serializers.ListField(child=CreateSampleSerializer())
    source = serializers.CharField(max_length=200, allow_blank=True, allow_null=True)
    spj = SPJSerializer(allow_null=True)
    template = serializers.ListField(child=serializers.DictField(), allow_empty=True, allow_null=True)
    append = serializers.ListField(child=serializers.DictField(), allow_empty=True, allow_null=True)
    prepend = serializers.ListField(child=serializers.DictField(), allow_empty=True, allow_null=True)

# ---- Course / Chapter Serializers (consolidated) ----
from rest_framework import serializers


class CourseSerializer(serializers.ModelSerializer):
    chapters = serializers.SerializerMethodField()

    class Meta:
        model = Course
        fields = ("id", "title", "description", "visible", "order", "contest_id", "chapters", "created_time", "updated_time")

    def get_chapters(self, obj):
        chapters = obj.chapters.all().order_by("order", "id")
        result = []
        for ch in chapters:
            probs = [{"display_id": cp.display_id, "type": cp.type, "order": cp.order}
                     for cp in ch.problems.all().order_by("order", "id")]
            result.append({
                "id": ch.id,
                "title": ch.title,
                "visible": ch.visible,
                "order": ch.order,
                "problems": probs
            })
        return result


class ChapterSerializer(serializers.ModelSerializer):
    problems = serializers.SerializerMethodField()

    class Meta:
        model = Chapter
        fields = ("id", "course", "title", "visible", "order", "problems", "created_time")

    def get_problems(self, obj):
        return [{"display_id": cp.display_id, "type": cp.type, "order": cp.order}
                for cp in obj.problems.all().order_by("order", "id")]


class ChapterProblemSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChapterProblem
        fields = ("id", "chapter", "display_id", "type", "order", "created_time")


class CourseCreateSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=255)
    description = serializers.CharField(allow_blank=True, required=False, default="")
    visible = serializers.BooleanField(default=True)
    order = serializers.IntegerField(default=0)


class CourseEditSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    title = serializers.CharField(max_length=255)
    description = serializers.CharField(allow_blank=True, required=False, default="")
    visible = serializers.BooleanField(default=True)
    order = serializers.IntegerField(default=0)


class ChapterCreateSerializer(serializers.Serializer):
    course_id = serializers.IntegerField()
    title = serializers.CharField(max_length=255)
    visible = serializers.BooleanField(default=True)
    order = serializers.IntegerField(default=0)


class ChapterEditSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    title = serializers.CharField(max_length=255, required=False)
    visible = serializers.BooleanField(default=True, required=False)
    order = serializers.IntegerField(default=0, required=False)


class ChapterProblemCreateSerializer(serializers.Serializer):
    course_id = serializers.IntegerField()
    chapter_id = serializers.IntegerField()
    display_id = serializers.CharField(max_length=64)
    type = serializers.ChoiceField(choices=["example", "exercise"])
    order = serializers.IntegerField(default=0)


class ChapterProblemEditSerializer(serializers.Serializer):
    course_id = serializers.IntegerField()
    chapter_id = serializers.IntegerField()
    display_id = serializers.CharField(max_length=64)
    type = serializers.ChoiceField(choices=["example", "exercise"], required=False)
    order = serializers.IntegerField(required=False)


class ChapterProblemMoveSerializer(serializers.Serializer):
    course_id = serializers.IntegerField()
    from_chapter_id = serializers.IntegerField()
    to_chapter_id = serializers.IntegerField()
    display_id = serializers.CharField(max_length=64)
    type = serializers.ChoiceField(choices=["example", "exercise"], required=False)
