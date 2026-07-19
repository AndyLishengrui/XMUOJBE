from django.db import migrations


class Migration(migrations.Migration):

	dependencies = [
		('problem', '0017_auto_20260604_0312'),
	]

	operations = [
		migrations.RemoveField(
			model_name='problem',
			name='group',
		),
		migrations.DeleteModel(
			name='ProblemGroup',
		),
	]