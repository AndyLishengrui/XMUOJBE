from .models import Submission
from utils.api import serializers
from utils.serializers import LanguageNameChoiceField


class CreateSubmissionSerializer(serializers.Serializer):
    problem_id = serializers.IntegerField()
    language = LanguageNameChoiceField(visible_only=True)
    code = serializers.CharField(max_length=1024 * 1024)
    contest_id = serializers.IntegerField(required=False)
    captcha = serializers.CharField(required=False)


class ShareSubmissionSerializer(serializers.Serializer):
    id = serializers.CharField()
    shared = serializers.BooleanField()


class SubmissionModelSerializer(serializers.ModelSerializer):
    problem = serializers.SlugRelatedField(read_only=True, slug_field="_id")
    contest_is_exam = serializers.SerializerMethodField()

    class Meta:
        model = Submission
        fields = "__all__"

    def get_contest_is_exam(self, obj):
        if obj.contest_id:
            return obj.contest.is_exam
        return False


class SubmissionSafeModelSerializer(serializers.ModelSerializer):
    problem = serializers.SlugRelatedField(read_only=True, slug_field="_id")

    class Meta:
        model = Submission
        exclude = ("info", "contest", "ip")


class SubmissionListSerializer(serializers.ModelSerializer):
    problem = serializers.SlugRelatedField(read_only=True, slug_field="_id")
    show_link = serializers.SerializerMethodField()
    real_name = serializers.SerializerMethodField()

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)

    class Meta:
        model = Submission
        exclude = ("info", "contest", "code", "ip")

    def get_show_link(self, obj):
        if self.user is None or not self.user.is_authenticated:
            return False
        return obj.check_user_permission(self.user)

    def get_real_name(self, obj):
        if self.user is None or not self.user.is_authenticated:
            return None
        if self.user.is_super_admin():
            from account.models import UserProfile
            try:
                return UserProfile.objects.get(user_id=obj.user_id).real_name
            except UserProfile.DoesNotExist:
                return None
        if obj.contest_id:
            from contest.models import Contest
            try:
                contest = Contest.objects.get(id=obj.contest_id)
                if self.user.is_contest_admin(contest):
                    from account.models import UserProfile
                    try:
                        return UserProfile.objects.get(user_id=obj.user_id).real_name
                    except UserProfile.DoesNotExist:
                        return None
            except Contest.DoesNotExist:
                pass
        return None
