# -*- coding: utf-8 -*-
"""题目级开关：覆盖所属实验/题库的「参考题解 / 原题链接」可见性。

- None  = 继承（实验级 contest.show_problem_links，公共题库则用全局默认）
- True  = 强制显示
- False = 强制隐藏

⚠️ 依赖写的是容器/数据库里实际应用的 0015（宿主那批 0016-0019 是未部署的
Course WIP，不在运行时的迁移图里）。
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('problem', '0015_problem_tag_governance'),
    ]

    operations = [
        migrations.AddField(
            model_name='problem',
            name='show_links',
            field=models.NullBooleanField(default=None),
        ),
    ]
