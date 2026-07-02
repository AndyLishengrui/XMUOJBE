from utils.api import serializers, UsernameSerializer
from utils.serializers import LanguageNameChoiceField

from account.models import UserProfile
from contest.models import Contest
from problem.models import Problem
from problem.utils import parse_problem_template
from submission.models import Submission, JudgeStatus


JUDGE_STATUS_LABELS = {
    JudgeStatus.COMPILE_ERROR: "Compile Error",
    JudgeStatus.WRONG_ANSWER: "Wrong Answer",
    JudgeStatus.ACCEPTED: "Accepted",
    JudgeStatus.CPU_TIME_LIMIT_EXCEEDED: "CPU Time Limit Exceeded",
    JudgeStatus.REAL_TIME_LIMIT_EXCEEDED: "Real Time Limit Exceeded",
    JudgeStatus.MEMORY_LIMIT_EXCEEDED: "Memory Limit Exceeded",
    JudgeStatus.RUNTIME_ERROR: "Runtime Error",
    JudgeStatus.SYSTEM_ERROR: "System Error",
    JudgeStatus.PENDING: "Pending",
    JudgeStatus.JUDGING: "Judging",
    JudgeStatus.PARTIALLY_ACCEPTED: "Partially Accepted",
}


class PluginLoginSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField()
    tfa_code = serializers.CharField(required=False, allow_blank=True)


class PluginSubmissionCreateSerializer(serializers.Serializer):
    problem_id = serializers.IntegerField()
    language = LanguageNameChoiceField(visible_only=True)
    code = serializers.CharField(max_length=1024 * 1024)
    contest_id = serializers.IntegerField(required=False)
    contest_password = serializers.CharField(required=False, allow_blank=True)
    captcha = serializers.CharField(required=False)


class PluginUserSerializer(serializers.ModelSerializer):
    id = serializers.IntegerField(source="user_id", read_only=True)
    username = serializers.CharField(source="user.username", read_only=True)
    admin_type = serializers.CharField(source="user.admin_type", read_only=True)
    is_admin_role = serializers.SerializerMethodField()

    class Meta:
        model = UserProfile
        fields = ("id", "username", "real_name", "avatar", "school", "major", "admin_type", "is_admin_role")

    def get_is_admin_role(self, obj):
        return obj.user.is_admin_role()


class PluginContestSerializer(serializers.ModelSerializer):
    status = serializers.CharField()
    contest_type = serializers.CharField()
    created_by = UsernameSerializer()
    require_password = serializers.SerializerMethodField()

    class Meta:
        model = Contest
        exclude = ("password", "visible", "allowed_ip_ranges")

    def get_require_password(self, obj):
        return bool(obj.password)


class PluginProblemSummarySerializer(serializers.ModelSerializer):
    display_id = serializers.CharField(source="_id")
    tags = serializers.SlugRelatedField(many=True, slug_field="name", read_only=True)

    class Meta:
        model = Problem
        fields = (
            "id", "display_id", "title", "difficulty", "rule_type", "languages",
            "accepted_number", "submission_number", "tags", "share_submission"
        )


class PluginProblemDetailSerializer(PluginProblemSummarySerializer):
    template = serializers.SerializerMethodField("get_public_template")
    created_by = UsernameSerializer()

    class Meta:
        model = Problem
        fields = (
            "id", "display_id", "title", "description", "input_description", "output_description",
            "samples", "hint", "source", "time_limit", "memory_limit", "io_mode", "difficulty",
            "rule_type", "languages", "template", "tags", "share_submission", "created_by"
        )

    def get_public_template(self, obj):
        templates = {}
        for language, code in obj.template.items():
            templates[language] = parse_problem_template(code)["template"]
        return templates


class PluginSubmissionSerializer(serializers.ModelSerializer):
    result_label = serializers.SerializerMethodField()
    display_id = serializers.SerializerMethodField()

    class Meta:
        model = Submission
        fields = (
            "id", "problem_id", "display_id", "contest_id", "language", "result",
            "result_label", "statistic_info", "info", "create_time"
        )

    def get_result_label(self, obj):
        return JUDGE_STATUS_LABELS.get(obj.result, str(obj.result))

    def get_display_id(self, obj):
        return obj.problem._id