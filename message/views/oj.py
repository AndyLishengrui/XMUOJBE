from django.shortcuts import render, redirect
from django.db.models import Q
from django.core.paginator import Paginator
from datetime import datetime
from utils.api import APIView, CSRFExemptAPIView, validate_serializer
from account.decorators import login_required, admin_role_required, super_admin_required
from account.models import User, AdminType
from message.models import Message
from message.serializers import MessageSerializer, SendMessageSerializer


class MessageListAPI(APIView):
    @login_required
    def get(self, request):
        """List inbox messages for current user."""
        qs = Message.objects.filter(
            recipient=request.user,
            is_deleted=False
        ).select_related('sender', 'sender__userprofile')
        return self.success(self.paginate_data(request, qs, MessageSerializer))


class MessageSentAPI(APIView):
    @login_required
    def get(self, request):
        """List sent messages for current user."""
        qs = Message.objects.filter(
            sender=request.user,
            is_deleted=False
        ).select_related('recipient', 'recipient__userprofile')
        return self.success(self.paginate_data(request, qs, MessageSerializer))


class UnreadCountAPI(APIView):
    @login_required
    def get(self, request):
        """Return unread message count for current user."""
        count = Message.objects.filter(
            recipient=request.user,
            is_read=False,
            is_deleted=False
        ).count()
        return self.success({'unread_count': count})


class MessageSendAPI(CSRFExemptAPIView):
    @login_required
    def post(self, request):
        """Send message to one or more recipients."""
        recipients_data = request.data.get('recipients', [])
        title = request.data.get('title', '')
        content = request.data.get('content', '')
        reply_to_id = request.data.get('reply_to')

        if not title:
            return self.error('Title is required')
        if not recipients_data:
            return self.error('At least one recipient is required')

        # Verify reply_to exists if provided
        reply_to_msg = None
        if reply_to_id:
            try:
                reply_to_msg = Message.objects.get(id=reply_to_id)
            except Message.DoesNotExist:
                return self.error('Reply target message not found')

        users = User.objects.filter(username__in=recipients_data)

        # Restrict: non-admin users can only send to admins
        if not request.user.is_admin_role():
            users = users.filter(admin_type__in=[AdminType.ADMIN, AdminType.SUPER_ADMIN])

        found = set(users.values_list('username', flat=True))
        not_found = [u for u in recipients_data if u not in found]

        messages = []
        for user in users:
            msg = Message(
                sender=request.user,
                recipient=user,
                title=title,
                content=content,
            )
            if reply_to_msg:
                msg.reply_to = reply_to_msg
            messages.append(msg)
        Message.objects.bulk_create(messages)

        return self.success({
            'sent': len(messages),
            'not_found': not_found,
        })


class MessageReadAPI(CSRFExemptAPIView):
    @login_required
    def post(self, request):
        """Mark a message as read. Returns updated message data."""
        mid = request.GET.get('id') or (request.data or {}).get('id')
        if not mid:
            return self.error('Missing id')
        try:
            message = Message.objects.select_related(
                'sender__userprofile', 'recipient__userprofile', 'reply_to__sender__userprofile'
            ).get(
                id=mid,
                recipient=request.user,
                is_deleted=False
            )
        except Message.DoesNotExist:
            return self.error('Message not found')
        if not message.is_read:
            message.is_read = True
            message.read_time = datetime.now()
            message.save(update_fields=['is_read', 'read_time'])
        return self.success(MessageSerializer(message).data)


class MessageDeleteAPI(CSRFExemptAPIView):
    @login_required
    def delete(self, request):
        """Soft-delete a message."""
        mid = request.GET.get('id') or (request.data or {}).get('id')
        if not mid:
            return self.error('Missing id')
        try:
            message = Message.objects.get(
                id=mid,
                recipient=request.user,
                is_deleted=False
            )
        except Message.DoesNotExist:
            return self.error('Message not found')
        message.is_deleted = True
        message.save(update_fields=['is_deleted'])
        return self.success({'deleted': mid})


class MessageDetailAPI(APIView):
    @login_required
    def get(self, request):
        """Get single message detail with reply chain context."""
        mid = request.GET.get('id') or (request.data or {}).get('id')
        if not mid:
            return self.error('Missing id')
        try:
            message = Message.objects.select_related(
                'sender__userprofile', 'recipient__userprofile',
                'reply_to__sender__userprofile'
            ).get(
                id=mid,
                is_deleted=False
            )
            if message.recipient != request.user and message.sender != request.user:
                return self.error('Message not found')
        except Message.DoesNotExist:
            return self.error('Message not found')

        # Auto-mark as read if recipient views
        if message.recipient == request.user and not message.is_read:
            message.is_read = True
            message.read_time = datetime.now()
            message.save(update_fields=['is_read', 'read_time'])

        return self.success(MessageSerializer(message).data)


class MessageInboxView(APIView):
    def get(self, request):
        """Render user-facing message inbox page."""
        if not request.user.is_authenticated:
            return redirect('/login/')

        tab = request.GET.get('tab', 'inbox')
        search = request.GET.get('search', '')
        page = int(request.GET.get('page', 1))
        page_size = int(request.GET.get('page_size', 30))
        if page_size not in (20, 30, 50, 100):
            page_size = 30

        qs = Message.objects.select_related(
            'sender__userprofile', 'recipient__userprofile'
        )

        if tab == 'inbox':
            qs = qs.filter(recipient=request.user, is_deleted=False)
        elif tab == 'sent':
            qs = qs.filter(sender=request.user, is_deleted=False)
        else:
            tab = 'inbox'
            qs = qs.filter(recipient=request.user, is_deleted=False)

        if search:
            qs = qs.filter(
                Q(title__icontains=search) |
                Q(content__icontains=search) |
                Q(sender__username__icontains=search) |
                Q(recipient__username__icontains=search)
            )

        qs = qs.order_by('-create_time')

        paginator = Paginator(qs, page_size)
        page_obj = paginator.get_page(page)

        messages = []
        for m in page_obj.object_list:
            sender_name = ''
            if m.sender:
                try:
                    sender_name = m.sender.userprofile.real_name or m.sender.username
                except:
                    sender_name = m.sender.username
            recipient_name = ''
            if m.recipient:
                try:
                    recipient_name = m.recipient.userprofile.real_name or m.recipient.username
                except:
                    recipient_name = m.recipient.username
            messages.append({
                'id': m.id,
                'title': m.title,
                'content': m.content,
                'sender_name': sender_name,
                'recipient_name': recipient_name,
                'sender_username': m.sender.username if m.sender else '',
                'recipient_username': m.recipient.username if m.recipient else '',
                'is_read': m.is_read,
                'create_time': m.create_time,
            })

        inbox_total = Message.objects.filter(recipient=request.user, is_deleted=False).count()
        unread = Message.objects.filter(recipient=request.user, is_read=False, is_deleted=False).count()
        sent_total = Message.objects.filter(sender=request.user, is_deleted=False).count()

        total_pages = paginator.num_pages
        page_range = []
        if total_pages <= 7:
            page_range = list(range(1, total_pages + 1))
        else:
            page_range.append(1)
            if page > 3:
                page_range.append('...')
            start = max(2, page - 1)
            end = min(total_pages - 1, page + 1)
            for p in range(start, end + 1):
                page_range.append(p)
            if page < total_pages - 2:
                page_range.append('...')
            page_range.append(total_pages)

        return render(request, 'message_inbox.html', {
            'messages': messages,
            'tab': tab,
            'search': search,
            'page': page,
            'page_size': page_size,
            'page_size_options': [20, 30, 50, 100],
            'total_pages': total_pages,
            'page_range': page_range,
            'has_prev': page_obj.has_previous(),
            'has_next': page_obj.has_next(),
            'prev_page': page - 1,
            'next_page': page + 1,
            'inbox_total': inbox_total,
            'unread': unread,
            'sent_total': sent_total,
            'user': request.user,
        })


class UserSearchAPI(APIView):
    @login_required
    def get(self, request):
        """Search users for messaging."""
        keyword = request.GET.get('keyword', '')
        if not keyword or len(keyword) < 1:
            return self.success({'results': []})

        users = User.objects.filter(is_disabled=False)
        if not request.user.is_admin_role():
            users = users.filter(admin_type__in=[AdminType.ADMIN, AdminType.SUPER_ADMIN])

        users = users.filter(
            Q(username__icontains=keyword) |
            Q(userprofile__real_name__icontains=keyword)
        ).select_related('userprofile')[:10]

        results = []
        for u in users:
            real_name = ''
            try:
                real_name = u.userprofile.real_name or ''
            except:
                pass
            results.append({
                'username': u.username,
                'real_name': real_name,
                'admin_type': u.admin_type,
            })
        return self.success({'results': results})
