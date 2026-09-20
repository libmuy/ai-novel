"""
冷读记录「封印」与校验 (record_seal.py)

背景：进度门禁原先只数校验记录里有几个 `## 冷读…` 标题——手写一节（含占位符指纹、
「0 条发现」、并不存在的评审器）也能通过。章0007 正文就出现过这种「记录说做了、实际没做」。

做法：`review_manuscript.py` 写冷读记录时，在该节末尾追加一行
`> 记录校验码：<10 位十六进制>`——它是「本节从 `## 冷读…` 标题行到该行之前」全部文本的
sha256 前 10 位。`progress_report.py` 计冷读轮次时重算校验码：
- 校验通过 ＋ 含脚本行 / 评审器行 / 12 位指纹 → 计入；
- 手写、篡改、占位符指纹、缺评审器 → 不计入，并报 `PROGRESS007`。
封印之前脚本写的历史记录（无校验码）按「遗留」处理：只有章号不超过各自的宽限上限
（见 `progress_report.LEGACY_COLD_MAX_CHAPTER_*`）才继续计数，新章必须是封印过的。

局限（有意为之）：这是防「模板式伪造 / 偷懒手写」，不是密码学防伪——有人读了本文件再刻意
重算校验码是能绕过的。它挡的是最常见的失效方式：没跑脚本、把记录写成看起来完整的样子。
"""
import hashlib
import re

SEAL_RE = re.compile(r"^>\s*记录校验码：([0-9a-f]{10})\s*$", re.M)
_HEAD_RE = re.compile(r"^##\s*冷读", re.M)
_ANY_H2_RE = re.compile(r"^##\s", re.M)
_SCRIPT_RE = re.compile(r"^>\s*脚本：`review_manuscript\.py`", re.M)
_CRITICS_RE = re.compile(r"^>\s*评审器：\s*(.+?)\s*$", re.M)
_FP_RE = re.compile(r"^>\s*目标文件指纹：([0-9a-f]{12})\s*$", re.M)
_PLACEHOLDER_FP = re.compile(r"^(?:([0-9a-f])\1{11}|0123456789ab|a1b2c3d4e5f6|123456789abc|abcdef012345|deadbeef0000)$")


def seal_digest(body: str) -> str:
    """本节正文（标题行起、校验码行之前）的校验码。忽略末尾空白差异。"""
    return hashlib.sha256(body.rstrip().encode("utf-8")).hexdigest()[:10]


def seal_line(body: str) -> str:
    return f"> 记录校验码：{seal_digest(body)}"


def split_cold_sections(text: str) -> list[str]:
    """把记录文件切成若干「## 冷读…」节（到下一个 `## ` 标题或文件末尾为止）。"""
    starts = [m.start() for m in _HEAD_RE.finditer(text)]
    heads = [m.start() for m in _ANY_H2_RE.finditer(text)]
    out = []
    for s in starts:
        nxt = next((h for h in heads if h > s), len(text))
        out.append(text[s:nxt])
    return out


def check_section(section: str) -> tuple[str, str]:
    """返回 (状态, 原因)：'sealed' 有效封印 / 'legacy' 无校验码（封印前的历史记录，或手写）/
    'invalid' 有校验码但对不上（篡改）或字段不合格。"""
    m = SEAL_RE.search(section)
    if not m:
        return "legacy", "无「记录校验码」行"
    body = section[:m.start()]
    if seal_digest(body) != m.group(1):
        return "invalid", "记录校验码与本节内容不符（被改过或手写）"
    if not _SCRIPT_RE.search(body):
        return "invalid", "缺「> 脚本：`review_manuscript.py`」行"
    cm = _CRITICS_RE.search(body)
    if not cm or cm.group(1) in ("（无）", "无", "-"):
        return "invalid", "评审器为空——没有评审器成功运行的冷读不算冷读"
    fm = _FP_RE.search(body)
    if not fm:
        return "invalid", "缺 12 位「目标文件指纹」"
    if _PLACEHOLDER_FP.match(fm.group(1)):
        return "invalid", f"指纹 {fm.group(1)} 是占位符"
    return "sealed", "ok"


def count_cold_rounds(text: str, chapter_no: int, legacy_max_chapter: int) -> tuple[int, list[str]]:
    """返回 (计入的冷读节数, 未计入的节标题列表)。"""
    rounds, rejected = 0, []
    for sec in split_cold_sections(text):
        status, reason = check_section(sec)
        head = sec.splitlines()[0].strip()
        if status == "sealed":
            rounds += 1
        elif status == "legacy" and chapter_no <= legacy_max_chapter:
            rounds += 1
        else:
            rejected.append(f"{head}（{reason if status == 'invalid' else '无校验码，不是脚本封印的记录'}）")
    return rounds, rejected
