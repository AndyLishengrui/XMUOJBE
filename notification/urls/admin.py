from django.conf.urls import url

from notification.views.admin import NotificationAdminAPI, NotificationManageView

urlpatterns = [
    url(r"^notification/?$", NotificationAdminAPI.as_view(), name="notification_admin"),
    url(r"^notification/sent/?$", NotificationAdminAPI.as_view(), name="notification_sent"),
    url(r"^notification/manage/?$", NotificationManageView.as_view(), name="notification_manage"),
]
