# Generated migration — adds contest_id field to Course model
# This field stores the associated contest ID for each course
# so that submissions made through the course UI can be tracked

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('problem', '0018_remove_problem_group'),
    ]

    operations = [
        migrations.AddField(
            model_name='course',
            name='contest_id',
            field=models.IntegerField(null=True, blank=True, default=None),
        ),
    ]
