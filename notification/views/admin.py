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

    @admin_role_required
    def get(self, request):
        """List sent notifications (admin view)."""
        qs = Notification.objects.select_related(
            'sender__userprofile', 'recipient__userprofile'
        ).all()
        return self.success(self.paginate_data(request, qs, NotificationSerializer))

    @super_admin_required
    def delete(self, request):
        """Delete a notification by id. (superadmin only)"""
        nid = request.GET.get("id") or (request.data or {}).get("id")
        if not nid:
            return self.error("Missing id")
        try:
            notification = Notification.objects.get(id=nid)
        except Notification.DoesNotExist:
            return self.error("Notification not found")
        notification.delete()
        return self.success({"deleted": nid})
