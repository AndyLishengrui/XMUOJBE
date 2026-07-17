from django.db import models
from account.models import User


class Message(models.Model):
    sender = models.ForeignKey(User, on_delete=models.SET_NULL, null=True,
                               related_name='sent_messages')
    recipient = models.ForeignKey(User, on_delete=models.CASCADE,
                                  related_name='received_messages')
    reply_to = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True,
                                 related_name='replies')
    title = models.CharField(max_length=256)
    content = models.TextField(default='')
    is_read = models.BooleanField(default=False)
    is_deleted = models.BooleanField(default=False)
    create_time = models.DateTimeField(auto_now_add=True)
    read_time = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'message'
        ordering = ['-create_time']
        indexes = [
            models.Index(fields=['recipient', '-create_time'], name='msg_recip_time'),
            models.Index(fields=['recipient', 'is_read'], name='msg_recip_read'),
            models.Index(fields=['sender', '-create_time'], name='msg_sender_time'),
        ]

    def __str__(self):
        return f'[{self.id}] {self.title} -> {self.recipient_id}'
