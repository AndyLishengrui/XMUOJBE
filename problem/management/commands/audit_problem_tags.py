import json

from django.core.management.base import BaseCommand

from problem.tag import serialize_problem_tag_audit


class Command(BaseCommand):
    help = "Audit problem tags and print duplicate, zero-usage, and low-frequency tag groups."

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true", dest="as_json")
        parser.add_argument("--low-frequency-threshold", type=int, default=2)

    def handle(self, *args, **options):
        threshold = options["low_frequency_threshold"]
        if threshold < 1:
            threshold = 1
        report = serialize_problem_tag_audit(low_frequency_threshold=threshold)
        if options["as_json"]:
            self.stdout.write(json.dumps(report, indent=2, ensure_ascii=False))
            return

        summary = report["summary"]
        self.stdout.write("Problem Tag Audit")
        self.stdout.write("- total tags: {}".format(summary["total_tags"]))
        self.stdout.write("- active tags: {}".format(summary["active_tags"]))
        self.stdout.write("- duplicate groups: {}".format(summary["duplicate_groups"]))
        self.stdout.write("- zero problem tags: {}".format(summary["zero_problem_tags"]))
        self.stdout.write("- low frequency tags: {}".format(summary["low_frequency_tags"]))

        if report["duplicates"]:
            self.stdout.write("\nDuplicate groups:")
            for duplicate in report["duplicates"]:
                tag_names = [item["name"] for item in duplicate["tags"]]
                self.stdout.write("- {} => {}".format(duplicate["normalized_name"], ", ".join(tag_names)))

        if report["zero_problem_tags"]:
            self.stdout.write("\nZero problem tags:")
            for tag in report["zero_problem_tags"][:20]:
                self.stdout.write("- {} ({})".format(tag["name"], tag["id"]))

        if report["low_frequency_tags"]:
            self.stdout.write("\nLow frequency tags:")
            for tag in report["low_frequency_tags"][:20]:
                self.stdout.write("- {} ({})".format(tag["name"], tag["problem_count"]))