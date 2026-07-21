from utils.api import serializers
from .models import Notification


class NotificationSerializer(serializers.ModelSerializer):
    sender_name = serializers.SerializerMethodField()
    recipient_name = serializers.SerializerMethodField()
    recipient = serializers.SerializerMethodField()

    class Meta:
        model = Notification
        fields = ['id', 'title', 'content', 'link', 'is_read',
                  'create_time', 'read_time', 'sender_name',
                  'recipient_name', 'recipient']

    def get_sender_name(self, obj):
        if obj.sender:
            try:
                return obj.sender.userprofile.real_name or obj.sender.username
            except Exception:
                return obj.sender.username
        return None

    def get_recipient_name(self, obj):
        try:
            return obj.recipient.userprofile.real_name or obj.recipient.username
        except Exception:
            return obj.recipient.username

    def get_recipient(self, obj):
        return obj.recipient.username


class SendNotificationSerializer(serializers.Serializer):
    recipients = serializers.ListField(child=serializers.CharField())
    title = serializers.CharField(max_length=256)
    content = serializers.CharField(allow_blank=True, required=False, default='')
    link = serializers.CharField(allow_blank=True, required=False, default='')
