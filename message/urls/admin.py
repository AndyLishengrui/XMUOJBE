from django.conf.urls import url
from message.views.admin import MessageAdminAPI, MessageManageView

urlpatterns = [
    url(r'^message/?$', MessageAdminAPI.as_view(), name='message_admin'),
    url(r'^message/manage/?$', MessageManageView.as_view(), name='message_manage'),
]
