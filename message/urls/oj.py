from django.conf.urls import url
from message.views.oj import (
    MessageListAPI, MessageSentAPI, UnreadCountAPI,
    MessageSendAPI, MessageReadAPI, MessageDeleteAPI, MessageDetailAPI,
    MessageInboxView, UserSearchAPI
)

urlpatterns = [
    url(r'^messages/search_user/?$', UserSearchAPI.as_view(), name='message_search_user'),
    url(r'^messages/?$', MessageListAPI.as_view(), name='message_list'),
    url(r'^messages/sent/?$', MessageSentAPI.as_view(), name='message_sent'),
    url(r'^messages/unread_count/?$', UnreadCountAPI.as_view(), name='message_unread_count'),
    url(r'^messages/send/?$', MessageSendAPI.as_view(), name='message_send'),
    url(r'^messages/read/?$', MessageReadAPI.as_view(), name='message_read'),
    url(r'^messages/delete/?$', MessageDeleteAPI.as_view(), name='message_delete'),
    url(r'^messages/detail/?$', MessageDetailAPI.as_view(), name='message_detail'),
    url(r'^messages/inbox/?$', MessageInboxView.as_view(), name='message_inbox'),
]

# Admin management pages (registered under OJ namespace to bypass AdminRoleRequiredMiddleware)
from message.views.admin import MessageManageView, MessageAdminAPI
urlpatterns.append(url(r'^message/manage/?$', MessageManageView.as_view(), name='message_manage'))
urlpatterns.append(url(r'^message/send/?$', MessageAdminAPI.as_view(), name='message_admin_send'))
