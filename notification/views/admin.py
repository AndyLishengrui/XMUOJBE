from django.shortcuts import render, redirect
from django.db.models import Q
from account.decorators import admin_role_required, super_admin_required
from utils.api import APIView, validate_serializer
from account.models import User
from notification.models import Notification
from notification.serializers import NotificationSerializer, SendNotificationSerializer


class NotificationAdminAPI(APIView):
    @validate_serializer(SendNotificationSerializer)
    @super_admin_required
    def post(self, request):
        """Send notification to one or more recipients. (superadmin only)"""
        data = request.data
        recipients = data["recipients"]
        title = data["title"]
        content = data.get("content", "")
        link = data.get("link", "")

        users = User.objects.filter(username__in=recipients)
        found = set(users.values_list("username", flat=True))
        not_found = [u for u in recipients if u not in found]

        notifications = []
        for user in users:
            notifications.append(Notification(
                sender=request.user,
                recipient=user,
                title=title,
                content=content,
                link=link
            ))
        Notification.objects.bulk_create(notifications)

        return self.success({
            "sent": len(notifications),
            "not_found": not_found
        })

    @super_admin_required
    def get(self, request):
        """List sent notifications (admin view)."""
        qs = Notification.objects.select_related(
            'sender__userprofile', 'recipient__userprofile'
        ).all()
        return self.success(self.paginate_data(request, qs, NotificationSerializer))

    @super_admin_required
    def delete(self, request):
        """Delete a notification by id. (admin only)"""
        nid = request.GET.get("id") or (request.data or {}).get("id")
        if not nid:
            return self.error("Missing id")
        try:
            notification = Notification.objects.get(id=nid)
        except Notification.DoesNotExist:
            return self.error("Notification not found")
        notification.delete()
        return self.success({"deleted": nid})


class NotificationManageView(APIView):
    def get(self, request):
        """Render admin notification management page. Redirects to login if not admin."""
        from django.core.paginator import Paginator

        # Redirect to admin login if not authenticated or not admin
        if not request.user.is_authenticated or not request.user.is_super_admin():
            return redirect('/admin/')

        search = request.GET.get('search', '')
        status = request.GET.get('status', 'all')
        page = int(request.GET.get('page', 1))
        page_size = int(request.GET.get('page_size', 50))
        if page_size not in (30, 50, 100, 200):
            page_size = 50

        qs = Notification.objects.select_related(
            'sender__userprofile', 'recipient__userprofile'
        ).all()

        if status == 'active':
            qs = qs.filter(is_deleted=False)
        elif status == 'deleted':
            qs = qs.filter(is_deleted=True)
        elif status == 'unread':
            qs = qs.filter(is_read=False, is_deleted=False)

        if search:
            qs = qs.filter(
                Q(title__icontains=search) |
                Q(recipient__username__icontains=search)
            )

        qs = qs.order_by('-create_time')

        paginator = Paginator(qs, page_size)
        page_obj = paginator.get_page(page)

        notifications = []
        for n in page_obj.object_list:
            sender_name = ''
            if n.sender:
                try:
                    sender_name = n.sender.userprofile.real_name or n.sender.username
                except:
                    sender_name = n.sender.username
            recipient_name = ''
            if n.recipient:
                try:
                    recipient_name = n.recipient.userprofile.real_name or n.recipient.username
                except:
                    recipient_name = n.recipient.username
            notifications.append({
                'id': n.id,
                'title': n.title,
                'sender_name': sender_name,
                'recipient_name': recipient_name,
                'is_read': n.is_read,
                'is_deleted': n.is_deleted,
                'create_time': n.create_time,
            })

        total = Notification.objects.count()
        unread = Notification.objects.filter(is_read=False, is_deleted=False).count()
        deleted = Notification.objects.filter(is_deleted=True).count()

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

        return render(request, 'admin_notifications.html', {
            'notifications': notifications,
            'total': total,
            'unread': unread,
            'deleted': deleted,
            'search': search,
            'status': status,
            'page': page,
            'page_size': page_size,
            'page_size_options': [30, 50, 100, 200],
            'total_pages': total_pages,
            'page_range': page_range,
            'has_prev': page_obj.has_previous(),
            'has_next': page_obj.has_next(),
            'prev_page': page - 1,
            'next_page': page + 1,
        })

class NotificationNoteView(APIView):
    def get(self, request):
        """Show single notification detail with back-to-list button, for admin use."""
        import re as _re
        from notification.utils import get_notification_absolute_url, resolve_problem_url

        if not request.user.is_authenticated or not request.user.is_super_admin():
            return redirect('/admin/')

        nid = request.GET.get('id', '')
        if not nid:
            return render(request, 'note_detail.html', {'error': '缺少 id 参数', 'show_back': True})

        try:
            notification = Notification.objects.select_related(
                'sender', 'sender__userprofile'
            ).get(id=nid)
        except Notification.DoesNotExist:
            return render(request, 'note_detail.html', {'error': '通知不存在', 'show_back': True})

        # Convert markdown-like content
        content = notification.content or ''
        content = content.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        content = _re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', content)
        content = _re.sub(r'^### (.+)$', r'<h3>\1</h3>', content, flags=_re.MULTILINE)
        content = _re.sub(r'^## (.+)$', r'<h2>\1</h2>', content, flags=_re.MULTILINE)
        content = _re.sub(r'^---$', r'<hr>', content, flags=_re.MULTILINE)
        paragraphs = content.split('\n\n')
        content = '\n'.join(f'<p>{p.replace(chr(10), "<br>")}</p>' for p in paragraphs)

        sender_name = (
            notification.sender.userprofile.real_name
            if hasattr(notification.sender, 'userprofile')
            else notification.sender.username
        )

        # Extract problem_id
        problem_id = ''
        title_match = _re.match(r'\[算法教练\]\s+(\S+)\s', notification.title)
        if title_match:
            problem_id = title_match.group(1)

        problem_link = resolve_problem_url(problem_id) if problem_id else ''
        own_link = notification.link.strip() if notification.link else ''

        page_url = get_notification_absolute_url(notification, request=request)

        return render(request, 'note_detail.html', {
            'notification': notification,
            'title': notification.title,
            'sender_name': sender_name,
            'recipient_username': notification.recipient.username,
            'create_time': notification.create_time.strftime('%Y-%m-%d %H:%M'),
            'content_html': content,
            'problem_link': problem_link,
            'problem_id': problem_id,
            'own_link': own_link,
            'page_url': page_url,
            'show_back': True,
        })
