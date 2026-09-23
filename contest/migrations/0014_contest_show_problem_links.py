# -*- coding: utf-8 -*-
"""实验级开关：是否向学生展示题目里的「参考题解 / 原题链接」。

老师 2026-09-23 要求：「后台对实验点击开启提示，实验内的题目的参考题解和原题
链接才会显示出来」，默认关闭。题目级可用 problem.show_links 覆盖（见 0020）。
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('contest', '0013_add_is_exam_field'),
    ]

    operations = [
        migrations.AddField(
            model_name='contest',
            name='show_problem_links',
            field=models.BooleanField(default=False),
        ),
    ]
