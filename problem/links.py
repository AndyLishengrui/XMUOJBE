# -*- coding: utf-8 -*-
"""题目提示（hint）里外部链接的可见性控制。

背景
----
题目的「参考题解」「原题链接」以及 Y总讲解 / Y总代码 / B站视频等链接，都写在
``Problem.hint`` 的 HTML 里，**不是独立字段**。老师要求：后台开关关闭时，这些
链接对学生不显示，但**提示正文必须保留**（例如「注意输出格式。」）。

⚠️ 绝不能整块隐藏 hint：全库 5822 条非空 hint 里，1321 条根本没有链接（是纯讲解
文字），另有 3180 条是「链接 + 正文」混合。所以只能**摘掉链接本身及其残留**。

图片也一起摘：提示里 96 处 <img>（78 张是上传的截图，多半是参考代码截图），
老师 2026-09-23 确认「一起藏」——只藏链接等于没藏。

对外只有两样东西
----------------
    LinksPolicy.for_user(user, contest=..., problem=...).hint(problem)   # 该用户应看到的 hint
    strip_problem_links(text)                                            # 纯函数，便于自测

三级开关（见 Contest.show_problem_links / Problem.show_links）
    题目级显式设置  >  所属比赛设置  >  兜底默认
管理员与任课老师**永远**看到原文。
"""

import re
from functools import lru_cache


def _opt(name, default):
    """读 settings.PROBLEM_LINKS（可选，缺省不影响运行）。"""
    try:
        from django.conf import settings
        cfg = getattr(settings, "PROBLEM_LINKS", None) or {}
        return cfg.get(name, default)
    except Exception:
        return default


# 未显式设置时的兜底值（老师 2026-09-23 定：默认隐藏）。
# 真正的全局设置存在 SysOptions 里，后台「网站配置」页可改（见 global_default()）。
DEFAULT_VISIBLE_FOR_UNSET = _opt("default_visible", False)


def global_default():
    """公共题库的全局设置：后台 → 网站配置 → 「公共题库：显示参考题解/原题链接」。

    读的是 SysOptions（@my_property 无 ttl → 不缓存），所以后台一改就生效。
    """
    try:
        from options.options import SysOptions
        return bool(SysOptions.show_problem_links)
    except Exception:
        return DEFAULT_VISIBLE_FOR_UNSET
# 管理员 / 任课老师是否永远看得到原文
ADMIN_ALWAYS_SEES = _opt("admin_always_sees", True)

# ---------------------------------------------------------------------------
# 摘链接
# ---------------------------------------------------------------------------
_SENTINEL = "\x00"                      # 先替换成哨兵，才能清理它的前后边界
ANCHOR_RE = re.compile(r"<a\b[^>]*>.*?</a>", re.S | re.I)
IMG_RE = re.compile(r"<img\b[^>]*?/?>", re.I)
URL_RE = re.compile(r"https?://[^\s<>\"']+", re.I)

_EMOJI = "\U0001f4d6\U0001f4d5\U0001f517⭐\U0001f4a1\U0001f50d\U0001f4da"
_WHITESPACE = " \t\r\n 　"
_SEPARATORS = "|｜·•,，、;；:：-—–~～"
_BRACKETS = "()（）[]【】{}「」《》<>"

# 纯链接文案：整段只剩它（可带冒号）时清掉
ORPHAN_LABELS = (
    "参考题解", "题解参考", "原题链接", "原题连接", "原文链接", "题目链接",
    "参考代码", "链接", "题解", "Y总讲解", "y总讲解", "Y总代码", "y总代码",
    "ACWing讲解", "Andy的讲解", "Guowei讲解", "视频讲解", "讲解视频",
)
_LABEL_ONLY_RE = re.compile(
    r"\A[" + _WHITESPACE + r"]*(" + "|".join(re.escape(x) for x in ORPHAN_LABELS)
    + r")[" + _WHITESPACE + r"]*[:：]?[" + _WHITESPACE + r"]*\Z"
)

# 被摘空的块（只剩空白 / <br /> / emoji）
EMPTY_BLOCK_RE = re.compile(
    r"<(p|div|li|td|h[1-6]|span)\b[^>]*>(?:\s|&nbsp;|<br\s*/?>|[" + _EMOJI + r"])*</\1>",
    re.I,
)

_TAIL_TEXT_RE = re.compile(r"([^<>]*)\Z")
_HEAD_TEXT_RE = re.compile(r"([^<>]*)")


def _is_content_free(node):
    """这段文本节点里除了空白/emoji/分隔符/括号，还有没有别的东西。"""
    for ch in node:
        if (ch not in _WHITESPACE and ch not in _EMOJI
                and ch not in _SEPARATORS and ch not in _BRACKETS):
            return False
    return True


def _strip_boundaries(text):
    """清理哨兵两侧**已经空了**的残留。

    ⚠️ 只在这段文本节点「完全没有正文」时才动手 —— 否则像
    ``<p>《参考：<a>hdu 2034</a>》</p>`` 会把正文里的「参考：」一起误删。
    """
    pieces = text.split(_SENTINEL)
    last = len(pieces) - 1
    for i, piece in enumerate(pieces):
        if i < last:                                   # 右边有被摘掉的链接 → 看尾部节点
            m = _TAIL_TEXT_RE.search(piece)
            if m and _is_content_free(m.group(1)):
                piece = piece[:m.start(1)]
        if i > 0:                                      # 左边有被摘掉的链接 → 看头部节点
            m = _HEAD_TEXT_RE.match(piece)
            if m and _is_content_free(m.group(1)):
                piece = piece[m.end(1):]
        pieces[i] = piece
    return "".join(pieces)


def _drop_empty_blocks(text):
    prev = None
    while prev != text:                    # 嵌套块可能要清几轮
        prev = text
        text = EMPTY_BLOCK_RE.sub("", text)
    return text


def _drop_label_nodes(text):
    """把「整段只剩『参考题解』『链接』这类文案」的文本节点清掉。"""
    def repl(m):
        inner = m.group(1)                 # ⚠️ 必须用 group(1)：group(0) 含两侧的 < >
        return ">" + ("" if _LABEL_ONLY_RE.match(inner) else inner) + "<"

    # 只处理标签之间的文本节点，不碰标签属性
    return re.sub(r">([^<>]*)<", repl, text)


@lru_cache(maxsize=8192)
def strip_problem_links(text):
    """摘掉提示里的所有外部链接，**保留正文**。

    - ``None`` / 空串原样返回。
    - 不含任何链接的提示走快路径，**逐字节原样返回**（全库 1321 条，机械保证不误伤）。
    - 幂等：``strip(strip(x)) == strip(x)``。
    """
    if not text:
        return text
    low = text.lower()
    if "<a" not in low and "<img" not in low and "http" not in low:
        return text                        # 快路径：无损

    out = ANCHOR_RE.sub(_SENTINEL, text)
    out = IMG_RE.sub(_SENTINEL, out)
    out = URL_RE.sub(_SENTINEL, out)
    out = _strip_boundaries(out)
    out = out.replace(_SENTINEL, "")
    out = _drop_label_nodes(out)
    out = _drop_empty_blocks(out)
    return out.strip()


# ---------------------------------------------------------------------------
# 三级策略
# ---------------------------------------------------------------------------
def user_can_always_see(user, problem=None):
    """管理员、任课老师永远看得到原文。"""
    if not ADMIN_ALWAYS_SEES:
        return False
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    try:
        if user.is_admin_role():
            return True
        if problem is not None and getattr(problem, "contest_id", None):
            return user.is_contest_admin(problem.contest)
    except Exception:
        return False
    return False


class LinksPolicy(object):
    """某个用户对某道题的链接可见性。

    ``contest`` 参数用于避免列表接口上的 N+1（调用方把已经取到的 contest 传进来）。
    """

    def __init__(self, bypass=False, default_visible=None, contest_override=None):
        self.bypass = bypass
        # 每次构造策略时读一次全局设置（不缓存，后台改完立刻生效）
        self.default_visible = global_default() if default_visible is None else default_visible
        self.contest_override = contest_override

    @classmethod
    def for_user(cls, user, contest=None, problem=None):
        return cls(bypass=user_can_always_see(user, problem), contest_override=contest)

    # -- 生效规则：题目级 > 比赛级 > 兜底 -----------------------------------
    def visible(self, problem):
        if self.bypass:
            return True
        v = getattr(problem, "show_links", None)
        if v is not None:
            return bool(v)
        if getattr(problem, "contest_id", None) is None:
            return self.default_visible
        contest = self.contest_override
        if contest is None or getattr(contest, "id", None) != problem.contest_id:
            contest = getattr(problem, "contest", None)
        if contest is not None:
            return bool(getattr(contest, "show_problem_links", self.default_visible))
        return self.default_visible

    def hint(self, problem):
        """返回该用户应该看到的 hint（隐藏时已摘掉链接）。"""
        h = problem.hint
        if not h or self.visible(problem):
            return h
        return strip_problem_links(h)
