import json

from django.core.management.base import BaseCommand

from options.options import SysOptions, build_runtime_languages


class Command(BaseCommand):
    help = "Sync SysOptions.languages from judge.languages and optionally preserve hidden legacy languages."

    def add_arguments(self, parser):
        parser.add_argument(
            "--preserve-hidden",
            action="append",
            dest="preserve_hidden_names",
            default=[],
            help="Keep a legacy language in runtime config but mark it hidden from public language lists.",
        )
        parser.add_argument("--dry-run", action="store_true", help="Print the target config without saving it.")
        parser.add_argument("--json", action="store_true", dest="as_json", help="Print target config as JSON.")

    def handle(self, *args, **options):
        preserve_hidden_names = options["preserve_hidden_names"]
        target_languages = build_runtime_languages(
            current_languages=SysOptions.languages,
            preserve_hidden_names=preserve_hidden_names,
        )

        if options["as_json"]:
            self.stdout.write(json.dumps(target_languages, ensure_ascii=False, indent=2))
        else:
            visible_names = [item["name"] for item in target_languages if item.get("visible", True)]
            hidden_names = [item["name"] for item in target_languages if not item.get("visible", True)]
            self.stdout.write("Target runtime languages: {}".format(", ".join(item["name"] for item in target_languages)))
            self.stdout.write("Visible languages: {}".format(", ".join(visible_names) if visible_names else "<none>"))
            self.stdout.write("Hidden languages: {}".format(", ".join(hidden_names) if hidden_names else "<none>"))

        if options["dry_run"]:
            self.stdout.write(self.style.WARNING("Dry run only, SysOptions.languages was not changed."))
            return

        SysOptions.languages = target_languages
        self.stdout.write(self.style.SUCCESS("SysOptions.languages synchronized"))