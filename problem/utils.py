import json
import os
import re
import shutil
from functools import lru_cache

from django.conf import settings
from utils.shortcuts import rand_str


TEMPLATE_BASE = """//PREPEND BEGIN
{}
//PREPEND END

//TEMPLATE BEGIN
{}
//TEMPLATE END

//APPEND BEGIN
{}
//APPEND END"""

PUBLIC_TEST_CASE_DOWNLOAD_KEY = "allow_public_test_case_download"


@lru_cache(maxsize=100)
def parse_problem_template(template_str):
    prepend = re.findall(r"//PREPEND BEGIN\n([\s\S]+?)//PREPEND END", template_str)
    template = re.findall(r"//TEMPLATE BEGIN\n([\s\S]+?)//TEMPLATE END", template_str)
    append = re.findall(r"//APPEND BEGIN\n([\s\S]+?)//APPEND END", template_str)
    return {"prepend": prepend[0] if prepend else "",
            "template": template[0] if template else "",
            "append": append[0] if append else ""}


@lru_cache(maxsize=100)
def build_problem_template(prepend, template, append):
    return TEMPLATE_BASE.format(prepend, template, append)


def get_test_case_info_path(test_case_id):
    return os.path.join(settings.TEST_CASE_DIR, test_case_id, "info")


def load_test_case_info(test_case_id):
    if not test_case_id:
        return None
    info_path = get_test_case_info_path(test_case_id)
    if not os.path.isfile(info_path):
        return None
    with open(info_path, "r", encoding="utf-8") as info_file:
        return json.load(info_file)


def save_test_case_info(test_case_id, info):
    with open(get_test_case_info_path(test_case_id), "w", encoding="utf-8") as info_file:
        info_file.write(json.dumps(info, indent=4))


def set_public_test_case_download_flag(test_case_id, enabled):
    info = load_test_case_info(test_case_id)
    if info is None:
        raise FileNotFoundError("Test case info does not exist")
    info[PUBLIC_TEST_CASE_DOWNLOAD_KEY] = bool(enabled)
    save_test_case_info(test_case_id, info)
    return info


def get_public_test_case_download_flag(test_case_id, fallback=None):
    info = load_test_case_info(test_case_id)
    if info is None or PUBLIC_TEST_CASE_DOWNLOAD_KEY not in info:
        return fallback
    return bool(info[PUBLIC_TEST_CASE_DOWNLOAD_KEY])


def is_problem_public_test_case_download_enabled(problem):
    # 考试题目强制不允许下载测试数据
    if problem.contest_id and problem.contest.is_exam:
        return False
    # 需要显式开启 allow_public_test_case_download 才允许下载
    fallback = False
    return bool(get_public_test_case_download_flag(problem.test_case_id, fallback=fallback))


def clone_test_case(test_case_id):
    if not test_case_id:
        raise FileNotFoundError("Test case does not exist")
    source_dir = os.path.join(settings.TEST_CASE_DIR, test_case_id)
    if not os.path.isdir(source_dir):
        raise FileNotFoundError("Test case does not exist")
    new_test_case_id = rand_str()
    target_dir = os.path.join(settings.TEST_CASE_DIR, new_test_case_id)
    shutil.copytree(source_dir, target_dir)
    os.chmod(target_dir, 0o710)
    return new_test_case_id
