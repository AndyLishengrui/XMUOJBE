from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True
    dependencies = [
        ('account', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='Message',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('title', models.CharField(max_length=256)),
                ('content', models.TextField(default='')),
                ('is_read', models.BooleanField(default=False)),
                ('is_deleted', models.BooleanField(default=False)),
                ('create_time', models.DateTimeField(auto_now_add=True)),
                ('read_time', models.DateTimeField(blank=True, null=True)),
                ('recipient', models.ForeignKey(on_delete=models.CASCADE, related_name='received_messages', to='account.User')),
                ('reply_to', models.ForeignKey(blank=True, null=True, on_delete=models.SET_NULL, related_name='replies', to='message.Message')),
                ('sender', models.ForeignKey(null=True, on_delete=models.SET_NULL, related_name='sent_messages', to='account.User')),
            ],
            options={
                'db_table': 'message',
                'ordering': ['-create_time'],
            },
        ),
        migrations.AddIndex(
            model_name='message',
            index=models.Index(fields=['recipient', '-create_time'], name='msg_recip_time'),
        ),
        migrations.AddIndex(
            model_name='message',
            index=models.Index(fields=['recipient', 'is_read'], name='msg_recip_read'),
        ),
        migrations.AddIndex(
            model_name='message',
            index=models.Index(fields=['sender', '-create_time'], name='msg_sender_time'),
        ),
    ]
