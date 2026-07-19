# Generated migration for adding ProblemGroup model and group field to Problem

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('problem', '0015_problem_tag_governance'),
    ]

    operations = [
        migrations.CreateModel(
            name='ProblemGroup',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(db_index=True, max_length=255)),
                ('course_name', models.CharField(db_index=True, max_length=255)),
                ('description', models.TextField(blank=True, null=True)),
                ('order', models.IntegerField(default=0)),
                ('created_time', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'db_table': 'problem_group',
                'ordering': ('course_name', 'order', 'name'),
            },
        ),
        migrations.AddField(
            model_name='problem',
            name='group',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='problem.ProblemGroup'),
        ),
        migrations.AlterUniqueTogether(
            name='problemgroup',
            unique_together={('name', 'course_name')},
        ),
    ]
