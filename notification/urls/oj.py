from django.conf.urls import url

from notification.views.oj import (
    NotificationListAPI, UnreadCountAPI, NotificationReadAPI,
    NotificationDeleteAPI, BatchReadAPI, NotificationDetailView
)
from notification.views.admin import NotificationManageView, NotificationNoteView

urlpatterns = [
    url(r"^notification/manage/?$", NotificationManageView.as_view(), name="notification_manage"),
    url(r"^notification/(?P<notification_id>\d+)/?$", NotificationDetailView.as_view(), name="notification_detail"),
    url(r"^notifications/?$", NotificationListAPI.as_view(), name="notification_list"),
    url(r"^notifications/unread_count/?$", UnreadCountAPI.as_view(), name="notification_unread_count"),
    url(r"^notifications/read/?$", NotificationReadAPI.as_view(), name="notification_read"),
    url(r"^notifications/delete/?$", NotificationDeleteAPI.as_view(), name="notification_delete"),
    url(r"^notifications/batch_read/?$", BatchReadAPI.as_view(), name="notification_batch_read"),
    url(r"^notification/note-detail/?$", NotificationNoteView.as_view(), name="notification_note_detail"),
]
