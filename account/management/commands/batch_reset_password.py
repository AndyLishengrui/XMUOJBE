from django.core.management.base import BaseCommand
from account.models import User


class Command(BaseCommand):
    help = "Batch reset passwords: provide a file of usernames and a new password string"

    def add_arguments(self, parser):
        parser.add_argument("--file", type=str, required=True,
                            help="Path to file with one username per line")
        parser.add_argument("--password", type=str, required=True,
                            help="New password string for all listed users")

    def handle(self, *args, **options):
        file_path = options["file"]
        new_password = options["password"]

        with open(file_path) as f:
            usernames = [line.strip() for line in f if line.strip()]

        if not usernames:
            self.stdout.write(self.style.ERROR("No usernames found in file"))
            return

        self.stdout.write(f"Found {len(usernames)} usernames. Resetting passwords...")

        ok = 0
        missing = []

        for username in usernames:
            try:
                user = User.objects.get(username=username)
                user.set_password(new_password)
                user.save()
                ok += 1
            except User.DoesNotExist:
                missing.append(username)

        self.stdout.write(self.style.SUCCESS(f"Done: {ok} updated, {len(missing)} not found"))
        if missing:
            self.stdout.write(self.style.WARNING(f"Missing users: {', '.join(missing)}"))
