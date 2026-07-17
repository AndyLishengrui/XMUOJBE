from utils.api import serializers
from .models import Message


class MessageSerializer(serializers.ModelSerializer):
    sender_name = serializers.SerializerMethodField()
    recipient_name = serializers.SerializerMethodField()
    sender_username = serializers.SerializerMethodField()
    recipient_username = serializers.SerializerMethodField()
    reply_to_title = serializers.SerializerMethodField()
    reply_to_sender_name = serializers.SerializerMethodField()

    class Meta:
        model = Message
        fields = ['id', 'title', 'content', 'is_read', 'is_deleted',
                  'create_time', 'read_time', 'sender_name', 'recipient_name',
                  'sender_username', 'recipient_username',
                  'reply_to', 'reply_to_title', 'reply_to_sender_name']

    def get_sender_name(self, obj):
        if obj.sender:
            try:
                return obj.sender.userprofile.real_name or obj.sender.username
            except Exception:
                return obj.sender.username
        return None

    def get_recipient_name(self, obj):
        if obj.recipient:
            try:
                return obj.recipient.userprofile.real_name or obj.recipient.username
            except Exception:
                return obj.recipient.username
        return None

    def get_sender_username(self, obj):
        return obj.sender.username if obj.sender else None

    def get_recipient_username(self, obj):
        return obj.recipient.username if obj.recipient else None

    def get_reply_to_title(self, obj):
        if obj.reply_to:
            return obj.reply_to.title
        return None

    def get_reply_to_sender_name(self, obj):
        if obj.reply_to and obj.reply_to.sender:
            try:
                return obj.reply_to.sender.userprofile.real_name or obj.reply_to.sender.username
            except Exception:
                return obj.reply_to.sender.username
        return None


class SendMessageSerializer(serializers.Serializer):
    recipients = serializers.ListField(child=serializers.CharField())
    title = serializers.CharField(max_length=256)
    content = serializers.CharField(allow_blank=True, required=False, default='')
    reply_to = serializers.IntegerField(required=False, default=None)
