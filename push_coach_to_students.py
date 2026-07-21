#!/usr/bin/env python3
"""Push algo coach reports as notifications to students."""
import os, sys, sqlite3, re
from datetime import datetime

sys.path.insert(0, '/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'oj.settings')
import django
django.setup()

from account.models import User
from notification.models import Notification

# Connect to coach reports DB
conn = sqlite3.connect('/opt/algo-coach/reports.db')
cur = conn.cursor()
cur.execute("""
    SELECT id, username, problem_id, problem_title, submission_id,
           code_analysis, hints, common_pitfall, status, created_at
    FROM review_reports
    WHERE status = 'done'
    ORDER BY created_at ASC
""")
reports = cur.fetchall()
conn.close()

print(f"Total done reports: {len(reports)}")

# Get existing notification (recipient_id, title) pairs for dedup
existing_pairs = set(
    Notification.objects.filter(title__startswith='[算法教练]')
    .values_list('recipient_id', 'title')
)
print(f"Existing coach notifications: {len(existing_pairs)}")

# Get system user (sender)
try:
    sender = User.objects.get(username='andy')
except User.DoesNotExist:
    sender = User.objects.filter(is_super_admin=True).first()
    if not sender:
        print("ERROR: No sender user found!")
        sys.exit(1)

notifications = []
skipped = 0
users_not_found = set()

for r in reports:
    rid, username, problem_id, problem_title, submission_id, \
        code_analysis, hints, common_pitfall, status, created_at = r

    title = f'[算法教练] {problem_id} {problem_title}'

    # Find student user
    try:
        student = User.objects.get(username=username)
    except User.DoesNotExist:
        users_not_found.add(username)
        skipped += 1
        continue

    # Dedup by (recipient, title) pair — each student gets their own notification per problem
    if (student.id, title) in existing_pairs:
        skipped += 1
        continue

    # Build content
    parts = []
    if code_analysis:
        parts.append(f'【代码分析】\n{code_analysis.strip()[:500]}')
    if hints:
        parts.append(f'【提示】\n{hints.strip()[:300]}')
    if common_pitfall:
        parts.append(f'【常见陷阱】\n{common_pitfall.strip()[:200]}')
    content = '\n\n'.join(parts) if parts else '(无内容)'

    # Link to submission status page (where coach card shows)
    link = f'/status/{submission_id}'

    n = Notification(
        sender=sender,
        recipient=student,
        title=title,
        content=content,
        link=link,
        is_read=False,
        is_deleted=False,
    )
    notifications.append(n)
    existing_pairs.add((student.id, title))

print(f"Students not found: {len(users_not_found)}")
for u in sorted(users_not_found)[:10]:
    print(f"  {u}")
if len(users_not_found) > 10:
    print(f"  ... and {len(users_not_found)-10} more")

print(f"\nNew notifications to create: {len(notifications)}")
print(f"Skipped (dup or no user): {skipped}")

# Bulk create in batches of 100
batch_size = 100
created = 0
for i in range(0, len(notifications), batch_size):
    batch = notifications[i:i+batch_size]
    objs = Notification.objects.bulk_create(batch)
    created += len(objs)
    print(f"  Batch {i//batch_size + 1}: {len(objs)} created")

print(f"\nDone! Total notifications created: {created}")
