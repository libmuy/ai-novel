"""
常驻红线包蒸馏视图守护 (redline.py)

`01_设定/00_红线包.md` 是**人工蒸馏**的第二份呈现：从主角档案 / 文风 / 法宝卡 /
经济卡 / 禁用词表 / 通用写作规则生成版里摘出「逐章不变的那半份约束」，正文提示词
【必读规则】段整段内联。它与规则切片（`build_rule_slices.py` 脚本派生 + `RULE008`
逐字守护）的差别是——红线包既非脚本派生、又长期没有任何审计。上游改了、红线包
没跟，每一章正文都跟着漂（`00_系统架构规范.md` §二·A 反复描述的病根）。

本规则给它补上确定性守护：

- REDLINE000 (info)   ：本书未建 `01_设定/00_红线包.md`，本规则跳过。
- REDLINE001 (warning)：§八【刷新触发】表记录的上游指纹与上游当前内容对不上
                        （或上游文件不存在）——上游变过、红线包未重核。
                        指纹戳由 `redline_stamp.py --write` 落。
- REDLINE002 (warning)：§六 禁用词摘录里的词不在 `01_设定/00_禁用词表.md` 的
                        **启用区**（权威清单里没有 / 在「待确认」区尚未启用）。
- REDLINE003 (warning)：红线包在，但 §八 表缺失 / 无法解析 / 没有指纹列——守护形同虚设。
- REDLINE004 (warning)：红线包正文出现**章节级**编号（`第N章`/`章NNNN`/`chNN`）。
                        红线包按定义是卷级视图，`卷 1`/`本卷` 合法、逐章编号不合法；
                        且它整段内联进提示词，`prompt_build/leak.py` 明确不扫内联源文件，
                        章节 ID 会被云端模型照抄进正文（ch0002 修订1「章0001买的止咳散」）。
                        逐行豁免：该行加 `<!-- REDLINE-ok: 理由 -->`（如压抑周期通则里的
                        「第 3 章」指周期内序数、非小说章号）。
"""
import re
from pathlib import Path
from typing import List

from ..models import Finding, Severity
from ..engine import AuditRule
from ..context import AuditContext
from .manuscript_lexicon import _parse_lexicon, LEXICON_REL
from .db_chapter import CHAPTER_LEVEL_PATTERNS

REDLINE_REL = "01_设定/00_红线包.md"

# 章节级编号——唯一权威定义在 `db_chapter.CHAPTER_LEVEL_PATTERNS`，这里直接复用、
# 不重新维护一份正则（曾经两处独立定义，图省事各写各的，是要防的那类漂移本身）。
# 红线包是**卷级**视图，"卷1"/"本卷"合法——因此只借用不含卷级/场景级判定的这个子集，
# db_chapter 自己额外拦的卷级/场景级模式（数据库卡片不该有任何粒度的编号）不适用于此。
_CHAPTER_ID_PATTERNS = CHAPTER_LEVEL_PATTERNS

_WAIVER_RE = re.compile(r"<!--\s*REDLINE-ok:")
_SECTION_SIX_RE = re.compile(r"^##\s+六[、.]")
_ANY_H2_RE = re.compile(r"^##\s+")
_LIST_BACKTICK_RE = re.compile(r"`([^`\n]+?)`")
_LEX_SECTION_RE = re.compile(r"^#{2,}\s*(包含|子串|正则|待确认|待定)\s*$")


def _iter_section(lines: List[str], head_re) -> List[str]:
    start = None
    for i, line in enumerate(lines):
        if head_re.match(line):
            start = i
            break
    if start is None:
        return []
    out = []
    for j in range(start + 1, len(lines)):
        if _ANY_H2_RE.match(lines[j]):
            break
        out.append(lines[j])
    return out


def _lexicon_terms_by_zone(text: str) -> dict:
    """禁用词表原文 → {词: 区名}；区名 ∈ {包含/子串/正则/待确认/待定}。"""
    zone = "包含"
    out = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = _LEX_SECTION_RE.match(line)
        if m:
            zone = m.group(1)
            continue
        if line.startswith("#") or line.startswith(">"):
            continue
        term = re.split(r"\s+//\s*|\s+——\s*", line, maxsplit=1)[0].strip()
        if term:
            out.setdefault(term, zone)
    return out


class RedlineRule(AuditRule):
    name = "redline"
    code_prefix = "REDLINE"

    def run(self, context: AuditContext) -> List[Finding]:
        findings: List[Finding] = []
        fi = context.file_map.get(REDLINE_REL)
        if fi is None or not fi.content.strip():
            findings.append(Finding(
                severity=Severity.INFO, rule=self.name, code="REDLINE000",
                message="本书未建 `01_设定/00_红线包.md`，红线包守护跳过",
                file=REDLINE_REL, category="01_设定",
            ))
            return findings

        self._check_stamps(context, findings)
        self._check_lexicon_subset(context, fi, findings)
        self._check_chapter_ids(fi, findings)
        return findings

    # ── REDLINE001 / REDLINE003：§八 上游指纹
    def _check_stamps(self, context: AuditContext, findings: List[Finding]) -> None:
        try:
            import redline_stamp as rs
        except ImportError:
            import sys
            sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
            import redline_stamp as rs

        try:
            problems = rs.check_stamps(context.novel_dir)
        except rs.RedlineError as e:
            findings.append(Finding(
                severity=Severity.WARNING, rule=self.name, code="REDLINE003",
                message=f"红线包 §八【刷新触发】表无法解析：{e}",
                file=REDLINE_REL, category="01_设定",
                suggestion="§八 表是红线包唯一的守护落点，必须可解析且带指纹列——"
                           "见 `00_通用模板/02_卡片模板/16_红线包模板.md`",
                locations=[REDLINE_REL],
            ))
            return

        no_col = [p for p in problems if "还没有" in p and "列" in p]
        drift = [p for p in problems if p not in no_col]
        if no_col:
            findings.append(Finding(
                severity=Severity.WARNING, rule=self.name, code="REDLINE003",
                message="红线包 §八 表没有「上次核对指纹」列——上游漂移无从检出",
                file=REDLINE_REL, category="01_设定",
                suggestion="跑 `python3 02_工具/01_小说通用工具/redline_stamp.py <小说目录> --write` 建列并落戳",
                locations=[REDLINE_REL],
            ))
        if drift:
            findings.append(Finding(
                severity=Severity.WARNING, rule=self.name, code="REDLINE001",
                message=f"红线包 §八 上游有 {len(drift)} 处与上次核对时不一致——上游变过、红线包未重核",
                file=REDLINE_REL, category="01_设定",
                suggestion="逐条重核对应小节，改完跑 "
                           "`python3 02_工具/01_小说通用工具/redline_stamp.py <小说目录> --write` 重新落戳",
                locations=drift,
            ))

    # ── REDLINE002：§六 摘录 ⊆ 禁用词表启用区
    def _check_lexicon_subset(self, context: AuditContext, fi, findings: List[Finding]) -> None:
        six = _iter_section(fi.content.splitlines(), _SECTION_SIX_RE)
        if not six:
            return
        excerpt: list[str] = []
        for line in six:
            if line.lstrip().startswith("-"):
                excerpt += _LIST_BACKTICK_RE.findall(line)
        excerpt = [w.strip() for w in excerpt if w.strip() and not w.endswith(".md")]
        if not excerpt:
            return

        lex_fi = context.file_map.get(LEXICON_REL)
        if lex_fi is None:
            findings.append(Finding(
                severity=Severity.WARNING, rule=self.name, code="REDLINE002",
                message="红线包 §六 摘了禁用词，但本书没有 `01_设定/00_禁用词表.md`（权威清单）",
                file=REDLINE_REL, category="01_设定",
                suggestion="建禁用词表，或把 §六 降级为「建议避开」表述",
                locations=[REDLINE_REL],
            ))
            return

        parsed = _parse_lexicon(lex_fi.content)
        enabled_sub = {t for t, m, _r in parsed if m == "sub"}
        enabled_re = [t for t, m, _r in parsed if m == "re"]
        zones = _lexicon_terms_by_zone(lex_fi.content)

        def _covered(w: str) -> bool:
            if w in enabled_sub:
                return True
            # §六 常写裸词「米」，词表用数字锚定的正则「\d\s*米」拦——视为已覆盖
            for pat in enabled_re:
                try:
                    if any(re.search(pat, s) for s in (w, "三" + w, "3 " + w)):
                        return True
                except re.error:
                    pass
            return False

        missing = [w for w in excerpt if not _covered(w) and w not in zones]
        pending = [w for w in excerpt if not _covered(w) and w in zones]
        if missing:
            findings.append(Finding(
                severity=Severity.WARNING, rule=self.name, code="REDLINE002",
                message=f"红线包 §六 有 {len(missing)} 个词权威清单 `00_禁用词表.md` 里根本没有："
                        f"{'、'.join(missing)}",
                file=REDLINE_REL, category="01_设定",
                suggestion="要么补进禁用词表启用区，要么从红线包 §六 删掉——两处口径必须一致（§二·A）",
                locations=[f"{REDLINE_REL} §六 → {w}" for w in missing],
            ))
        if pending:
            findings.append(Finding(
                severity=Severity.WARNING, rule=self.name, code="REDLINE002",
                message=f"红线包 §六 把 {len(pending)} 个词当硬禁写，但禁用词表里它们在「待确认」区、尚未启用："
                        f"{'、'.join(pending)}",
                file=REDLINE_REL, category="01_设定",
                suggestion="用户拍板：把这些词从 `00_禁用词表.md` 的『## 待确认』提到启用区，"
                           "或把红线包 §六 对应行措辞降级为「建议避开（待确认）」",
                locations=[f"{REDLINE_REL} §六 → {w}（待确认区）" for w in pending],
            ))

    # ── REDLINE004：章节级编号
    def _check_chapter_ids(self, fi, findings: List[Finding]) -> None:
        hits: list[str] = []
        for idx, line in enumerate(fi.content.splitlines(), 1):
            if _WAIVER_RE.search(line):
                continue
            for pat in _CHAPTER_ID_PATTERNS:
                m = pat.search(line)
                if m:
                    hits.append(f"{REDLINE_REL}:第{idx}行（{m.group(0)}）")
                    break
        if hits:
            findings.append(Finding(
                severity=Severity.WARNING, rule=self.name, code="REDLINE004",
                message=f"红线包正文出现章节级编号 {len(hits)} 处（{'、'.join(sorted({h.split('（')[-1].rstrip('）') for h in hits}))}）",
                file=REDLINE_REL, line=int(hits[0].split(':第')[1].split('行')[0]),
                category="01_设定",
                suggestion="红线包是卷级视图，改成叙述性表述（「主角入道后」而非「第4章后」）；"
                           "章节归属的唯一权威是节拍表 `@引用`。它整段内联进提示词，"
                           "章节 ID 会被云端照抄进正文",
                locations=hits,
            ))
