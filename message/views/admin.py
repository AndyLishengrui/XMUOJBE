from django.shortcuts import render, redirect
from django.db.models import Q
from django.core.paginator import Paginator
from datetime import datetime
from utils.api import APIView, CSRFExemptAPIView, validate_serializer
from account.decorators import admin_role_required, super_admin_required
from account.models import User
from message.models import Message
from message.serializers import MessageSerializer, SendMessageSerializer


class MessageAdminAPI(CSRFExemptAPIView):
    @validate_serializer(SendMessageSerializer)
    @super_admin_required
    def post(self, request):
        """Admin broadcast message to multiple users."""
        data = request.data
        recipients = data['recipients']
        title = data['title']
        content = data.get('content', '')
        reply_to_id = data.get('reply_to')

        # Verify reply_to if provided
        reply_to_msg = None
        if reply_to_id:
            try:
                reply_to_msg = Message.objects.get(id=reply_to_id)
            except Message.DoesNotExist:
                pass

        users = User.objects.filter(username__in=recipients)
        found = set(users.values_list('username', flat=True))
        not_found = [u for u in recipients if u not in found]

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
            'not_found': not_found
        })

    @admin_role_required
    def get(self, request):
        """List all messages (admin view)."""
        qs = Message.objects.select_related(
            'sender__userprofile', 'recipient__userprofile'
        ).all()
        return self.success(self.paginate_data(request, qs, MessageSerializer))

    @super_admin_required
    def delete(self, request):
        """Hard delete a message by id."""
        mid = request.GET.get('id') or (request.data or {}).get('id')
        if not mid:
            return self.error('Missing id')
        try:
            message = Message.objects.get(id=mid)
        except Message.DoesNotExist:
            return self.error('Message not found')
        message.delete()
        return self.success({'deleted': mid})


class MessageManageView(APIView):
    def get(self, request):
        """Render admin message management page."""
        # Redirect to admin login if not authenticated or not admin
        if not request.user.is_authenticated or not request.user.is_admin_role():
            return redirect('/admin/')

        tab = request.GET.get('tab', 'inbox')
        search = request.GET.get('search', '')
        page = int(request.GET.get('page', 1))
        page_size = int(request.GET.get('page_size', 50))
        if page_size not in (30, 50, 100, 200):
            page_size = 50

        qs = Message.objects.select_related(
            'sender__userprofile', 'recipient__userprofile'
        ).all()

        if tab == 'inbox':
            qs = qs.filter(recipient=request.user, is_deleted=False)
        elif tab == 'sent':
            qs = qs.filter(sender=request.user, is_deleted=False)
        elif tab == 'all':
            pass  # admin sees all
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
                'is_deleted': m.is_deleted,
                'create_time': m.create_time,
                'reply_to_id': m.reply_to_id,
            })

        # Stats for current user
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

        return render(request, 'admin_messages.html', {
            'messages': messages,
            'tab': tab,
            'search': search,
            'page': page,
            'page_size': page_size,
            'page_size_options': [30, 50, 100, 200],
            'total_pages': total_pages,
            'page_range': page_range,
            'has_prev': page_obj.has_previous(),
            'has_next': page_obj.has_next(),
            'prev_page': page - 1,
            'next_page': page + 1,
            'inbox_total': inbox_total,
            'unread': unread,
            'sent_total': sent_total,
        })
