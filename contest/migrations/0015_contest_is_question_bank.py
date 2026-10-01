# -*- coding: utf-8 -*-
"""把「题库」和「课堂实验」分成两个列表。

老师 2026-10-01 要求：导航栏加「题库」菜单只显示题库；原 /contest 只显示非题库。
是不是题库由**后台比赛编辑页的显式开关**决定，不靠标题关键词猜。

本次一次性回填 17 个存量题库 id（已逐个与老师核对过标题，
见 /home/ubuntu/.claude/projects/-home-ubuntu/memory/SESSION_STATE.md）。

⚠️ 不要改用标题前缀判断：全库 0 个比赛标题以 `[教材]` 开头（已实测），
前端那句 `filter(!title.startsWith('[教材]'))` 一直是死的。
"""
from django.db import migrations, models


# 老师确认的 17 个题库（按 id 升序）
QUESTION_BANK_IDS = [
    88, 89, 90, 91,        # 《算法基础与在线编程实验教程》配套题库(1)~(4)
    207,                   # 南强100题库
    261, 264, 265,         # 蓝桥杯集训队 动态规划 / 搜索 / 图论题库
    263,                   # 2025年秋数据结构题库
    271, 278,              # 蓝桥杯集训队语法入门题 / 2
    280,                   # 蓝桥杯集训队数学知识题库
    325,                   # 蓝桥杯集训营题库(2025年-2027年)
    365,                   # 剑道试炼 · 仗剑走江湖【预览版】
    382,                   # 算法竞赛进阶指南 · 提高题库
    398,                   # 2026年数据结构实验题库
    474,                   # 《基于VSCode的程序设计实践教程》配套题库
]


def mark_question_banks(apps, schema_editor):
    Contest = apps.get_model("contest", "Contest")
    Contest.objects.filter(id__in=QUESTION_BANK_IDS).update(is_question_bank=True)


def unmark_question_banks(apps, schema_editor):
    Contest = apps.get_model("contest", "Contest")
    Contest.objects.filter(id__in=QUESTION_BANK_IDS).update(is_question_bank=False)


class Migration(migrations.Migration):

    dependencies = [
        ('contest', '0014_contest_show_problem_links'),
    ]

    operations = [
        migrations.AddField(
            model_name='contest',
            name='is_question_bank',
            field=models.BooleanField(default=False),
        ),
        migrations.RunPython(mark_question_banks, reverse_code=unmark_question_banks),
    ]
