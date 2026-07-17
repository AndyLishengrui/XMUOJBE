from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('message', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='message',
            name='reply_to',
            field=models.ForeignKey(blank=True, null=True, on_delete=models.SET_NULL, related_name='replies', to='message.Message'),
        ),
    ]
