from utils.api import serializers

from .models import JudgeServer


class EditSMTPConfigSerializer(serializers.Serializer):
    server = serializers.CharField(max_length=128)
    port = serializers.IntegerField(default=25)
    email = serializers.CharField(max_length=256)
    password = serializers.CharField(max_length=128, required=False, allow_null=True, allow_blank=True)
    tls = serializers.BooleanField()


class CreateSMTPConfigSerializer(EditSMTPConfigSerializer):
    password = serializers.CharField(max_length=128)


class TestSMTPConfigSerializer(serializers.Serializer):
    email = serializers.EmailField()


class CreateEditWebsiteConfigSerializer(serializers.Serializer):
    website_base_url = serializers.CharField(max_length=128)
    website_name = serializers.CharField(max_length=64)
    website_name_shortcut = serializers.CharField(max_length=64)
    website_footer = serializers.CharField(max_length=1024 * 1024)
    allow_register = serializers.BooleanField()
    submission_list_show_all = serializers.BooleanField()
    # ⚠️ 只写 required=False、**不给 default**：旧版后台页面（缓存里的 JS）不带这个字段时，
    #    validated_data 里就不会有它，post 的 setattr 循环会跳过 -> 不会把设置悄悄重置。
    show_problem_links = serializers.BooleanField(required=False)


class JudgeServerSerializer(serializers.ModelSerializer):
    status = serializers.CharField()

    class Meta:
        model = JudgeServer
        fields = "__all__"


class JudgeServerHeartbeatSerializer(serializers.Serializer):
    hostname = serializers.CharField(max_length=128)
    judger_version = serializers.CharField(max_length=32)
    cpu_core = serializers.IntegerField(min_value=1)
    memory = serializers.FloatField(min_value=0, max_value=100)
    cpu = serializers.FloatField(min_value=0, max_value=100)
    action = serializers.ChoiceField(choices=("heartbeat", ))
    service_url = serializers.CharField(max_length=256)


class EditJudgeServerSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    is_disabled = serializers.BooleanField()
