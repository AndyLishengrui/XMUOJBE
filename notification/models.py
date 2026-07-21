from django.db import models
from account.models import User


class Notification(models.Model):
    sender = models.ForeignKey(User, on_delete=models.SET_NULL, null=True,
                               related_name='sent_notifications')
    recipient = models.ForeignKey(User, on_delete=models.CASCADE,
                                  related_name='notifications')
    title = models.CharField(max_length=256)
    content = models.TextField(default='')
    link = models.CharField(max_length=512, default='')
    is_read = models.BooleanField(default=False)
    is_deleted = models.BooleanField(default=False)
    create_time = models.DateTimeField(auto_now_add=True)
    read_time = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'notification'
        ordering = ['-create_time']
        indexes = [
            models.Index(fields=['recipient', '-create_time'],
                         name='notif_recip_time'),
            models.Index(fields=['recipient', 'is_read'],
                         name='notif_recip_read'),
        ]

    def __str__(self):
        return f'[{self.id}] {self.title} -> {self.recipient_id}'
