import re
import json
import os
from datetime import datetime
from django.shortcuts import render
from django.http import Http404

from account.decorators import login_required
from utils.api import APIView
from notification.models import Notification
from notification.serializers import NotificationSerializer
from notification.utils import (
    get_notification_absolute_url,
    get_notification_relative_url,
    resolve_problem_url,
)

# Load coach submission mapping (problem_id -> [submissions])
_coach_submissions_path = '/data/coach_submissions.json'

def _load_coach_submissions():
    """Load the cached coach submission mapping."""
    if os.path.exists(_coach_submissions_path):
        with open(_coach_submissions_path, 'r') as f:
            return json.load(f)
    return {}

def _get_submission_links(problem_id):
    """Return a list of {sid, user, url} dicts for a given problem_id."""
    mapping = _load_coach_submissions()
    entries = mapping.get(problem_id, [])
    links = []
    for entry in entries:
        sid = entry.get('sid', '')
        user = entry.get('user', '')
        links.append({
            'sid': sid,
            'user': user,
            'url': f'/status/{sid}',
        })
    return links


class NotificationListAPI(APIView):
    @login_required
    def get(self, request):
        """Return paginated notifications for current user, excluding soft-deleted."""
        qs = Notification.objects.filter(
            recipient=request.user,
            is_deleted=False
        ).select_related('sender', 'sender__userprofile')
        return self.success(self.paginate_data(request, qs, NotificationSerializer))


class UnreadCountAPI(APIView):
    @login_required
    def get(self, request):
        """Return unread notification count for current user."""
        count = Notification.objects.filter(
            recipient=request.user,
            is_read=False,
            is_deleted=False
        ).count()
        return self.success({"unread_count": count})


class NotificationReadAPI(APIView):
    @login_required
    def post(self, request):
        """Mark a single notification as read. Requires id param."""
        nid = request.GET.get("id") or (request.data or {}).get("id")
        if not nid:
            return self.error("Missing id")
        try:
            notification = Notification.objects.get(
                id=nid,
                recipient=request.user,
                is_deleted=False
            )
        except Notification.DoesNotExist:
            return self.error("Notification not found")
        if not notification.is_read:
            notification.is_read = True
            notification.read_time = datetime.now()
            notification.save(update_fields=["is_read", "read_time"])
        return self.success(NotificationSerializer(notification).data)


class NotificationDeleteAPI(APIView):
    @login_required
    def delete(self, request):
        """Soft-delete a notification. Requires id param."""
        nid = request.GET.get("id") or (request.data or {}).get("id")
        if not nid:
            return self.error("Missing id")
        try:
            notification = Notification.objects.get(
                id=nid,
                recipient=request.user,
                is_deleted=False
            )
        except Notification.DoesNotExist:
            return self.error("Notification not found")
        notification.is_deleted = True
        notification.save(update_fields=["is_deleted"])
        return self.success({"deleted": nid})


class BatchReadAPI(APIView):
    @login_required
    def post(self, request):
        """Mark all unread notifications as read for current user."""
        updated = Notification.objects.filter(
            recipient=request.user,
            is_read=False,
            is_deleted=False
        ).update(is_read=True, read_time=datetime.now())
        return self.success({"marked_read": updated})


class NotificationDetailView(APIView):
    @login_required
    def get(self, request, notification_id):
        try:
            notification = Notification.objects.select_related(
                'sender', 'sender__userprofile'
            ).get(id=notification_id) if request.user.is_admin_role() else Notification.objects.select_related("sender", "sender__userprofile").get(id=notification_id, recipient=request.user, is_deleted=False)
        except Notification.DoesNotExist:
            return self.error("Notification not found")

        # Mark as read if not already
        if not notification.is_read:
            notification.is_read = True
            notification.read_time = datetime.now()
            notification.save(update_fields=['is_read', 'read_time'])

        # Convert markdown-like content to basic HTML
        content = notification.content or ''
        content = content.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        # Bold
        content = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', content)
        # Headers
        content = re.sub(r'^### (.+)$', r'<h3>\1</h3>', content, flags=re.MULTILINE)
        content = re.sub(r'^## (.+)$', r'<h2>\1</h2>', content, flags=re.MULTILINE)
        # Separator lines
        content = re.sub(r'^---$', r'<hr>', content, flags=re.MULTILINE)
        # Newlines -> <br> or <p>
        paragraphs = content.split('\n\n')
        content = '\n'.join(f'<p>{p.replace(chr(10), "<br>")}</p>' for p in paragraphs)

        sender_name = (
            notification.sender.userprofile.real_name
            if hasattr(notification.sender, 'userprofile')
            else notification.sender.username
        )

        # Extract problem_id from title: [算法教练] PROBLEM_ID TITLE
        problem_id = ''
        title_match = re.match(r'\[算法教练\]\s+(\S+)\s', notification.title)
        if title_match:
            problem_id = title_match.group(1)

        # Use the centralized URL resolver (handles standalone vs contest)
        problem_link = resolve_problem_url(problem_id) if problem_id else ''

        # Look up coach submission links from the mapping
        submission_links = _get_submission_links(problem_id) if problem_id else []

        # Always include the notification's own link as the primary submission link
        own_link = notification.link.strip() if notification.link else ''
        if own_link and not any(s.get('url') == own_link for s in submission_links):
            submission_links.insert(0, {
                'sid': own_link.rstrip('/').split('/')[-1],
                'user': notification.recipient.username,
                'url': own_link,
            })

        # Build canonical absolute URL for this page
        page_url = get_notification_absolute_url(notification, request=request)

        return render(request, 'coach_report.html', {
            'title': notification.title,
            'sender_name': sender_name,
            'create_time': notification.create_time.strftime('%Y-%m-%d %H:%M'),
            'content_html': content,
            'problem_link': problem_link,
            'submission_links': submission_links,
            'page_url': page_url,
            'recipient_username': notification.recipient.username,
            'coach_problem_id': problem_id,
            'notification_id': notification.id,
        })
