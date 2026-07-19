"""
Import contest-271 problems into local Django DB.
Run: python import_contest271.py
"""
import os, sys, json, glob, re

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'oj.settings')
os.environ['OJ_ENV'] = 'dev'

import django
django.setup()

from problem.models import Problem, ProblemTag
from contest.models import Contest

CONTEST_DIR = os.path.expanduser("~/Documents/xmuojCodes/contest-271")

def parse_md(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        text = f.read()
    lines = text.split('\n')
    title = lines[0].replace('# ', '').strip() if lines[0].startswith('#') else 'Unknown'

    desc_lines = []
    input_lines = []
    output_lines = []
    hint_lines = []
    samples = []
    current = None
    in_code = False
    code_buf = []
    sample_input = None

    for line in lines[1:]:
        stripped = line.strip()

        if stripped.startswith('```') and not in_code:
            in_code = True
            code_buf = []
            continue
        if stripped == '```' and in_code:
            in_code = False
            if sample_input is not None and code_buf:
                if sample_input is True:
                    sample_input = '\n'.join(code_buf)
                else:
                    samples.append({"input": sample_input if sample_input else "", "output": '\n'.join(code_buf)})
                    sample_input = None
            code_buf = []
            continue
        if in_code:
            code_buf.append(line)
            continue

        if '题目描述' in stripped:
            current = 'desc'
        elif '输入描述' in stripped or '输入格式' in stripped:
            current = 'input'
        elif '输出描述' in stripped or '输出格式' in stripped:
            current = 'output'
        elif '提示' in stripped:
            current = 'hint'
        elif '样例' in stripped:
            current = 'sample'
        elif '输入' in stripped and current == 'sample':
            if samples and 'output' in str(samples[-1]) and samples[-1].get('output'):
                pass
            sample_input = ''
        elif '输出' in stripped and current == 'sample':
            sample_input = False  # flag: next code block is output
        elif current == 'desc':
            desc_lines.append(line)
        elif current == 'input':
            input_lines.append(line)
        elif current == 'output':
            output_lines.append(line)
        elif current == 'hint':
            hint_lines.append(line)

    desc = '\n'.join(desc_lines).strip()
    inp = '\n'.join(input_lines).strip()
    outp = '\n'.join(output_lines).strip()
    hint = '\n'.join(hint_lines).strip()

    if not samples:
        samples = [{"input": "", "output": ""}]

    return {
        "title": title,
        "description": desc,
        "input_description": inp,
        "output_description": outp,
        "hint": hint,
        "samples": samples,
    }


def main():
    # Create or get contest
    contest, _ = Contest.objects.get_or_create(
        title="程序设计实践 李胜睿班级",
        defaults={
            "description": "2026年程序设计实践课程例题",
            "real_time_rank": True,
            "rule_type": "ACM",
            "start_time": "2026-01-01T00:00:00Z",
            "end_time": "2026-12-31T23:59:59Z",
            "created_by_id": 1,
        },
    )
    print(f"Contest: {contest.title} (id={contest.id})")

    # Get or create tags
    tags_map = {}
    for tag_name in ["入门", "基础", "进阶", "综合", "程序设计实践"]:
        tag, _ = ProblemTag.objects.get_or_create(name=tag_name)
        tags_map[tag_name] = tag

    count = 0
    for d in sorted(glob.glob(os.path.join(CONTEST_DIR, "*/"))):
        pid = os.path.basename(d.rstrip('/'))
        md_path = os.path.join(d, 'problem.md')

        if not os.path.exists(md_path):
            continue

        # Check if already exists
        if Problem.objects.filter(_id=pid).exists():
            print(f"  SKIP {pid}: already exists")
            continue

        data = parse_md(md_path)

        if pid.startswith('A'):
            cat = '入门'
        elif pid.startswith('B'):
            cat = '基础'
        elif pid.startswith('C'):
            cat = '进阶'
        else:
            cat = '综合'

        problem = Problem.objects.create(
            _id=pid,
            contest=contest,
            is_public=True,
            title=f"{pid} {data['title']}",
            description=data['description'] or data['title'],
            input_description=data['input_description'] or "见题目描述",
            output_description=data['output_description'] or "见题目描述",
            samples=data['samples'],
            hint=data['hint'] or "",
            test_case_id=pid,
            test_case_score=[{"score": 0}],
            languages=["C", "C++", "Java"],
            template={},
            time_limit=1000,
            memory_limit=256,
            created_by_id=1,
        )

        # Add tags
        if cat in tags_map:
            problem.tags.add(tags_map[cat])
            problem.tags.add(tags_map["程序设计实践"])

        count += 1
        print(f"  ✓ {pid} {data['title']} [{cat}]")

    print(f"\n✅ Imported {count} problems")

    # Summary
    total = Problem.objects.filter(contest=contest).count()
    print(f"Total in contest: {total}")


if __name__ == "__main__":
    main()
