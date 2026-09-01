"""
卷大纲章节归属单一权威校验 (plan_beat.py)

见 00_系统架构规范.md §二·A。【章节节拍表】的「一句话剧情摘要」（含 @引用）是本卷
所有事件章节归属的唯一权威。其它区块只描述「是什么」，不带「章节」列。

- PLAN_BEAT001 (warning)：卷大纲的 角色/关系/资源 区块表带「章节」列，或存在
  【出场对象ID清单】等按章节的对象索引区块。
- PLAN_BEAT002 (warning)：【埋设/回收伏笔列表】某 FH 的埋设/回收章节 N，但第 N 章
  节拍表摘要未 @引用 该 FH。
- PLAN_BEAT003 (warning)：卷大纲其它区块的某行同时出现「第N章」和某对象
  （@人物/@势力/@伏笔/FH-号），但第 N 章节拍表摘要没有对应 @引用。
"""
import re
from typing import List, Dict, Set, Tuple
from ..models import Finding, Severity
from ..engine import AuditRule
from ..context import AuditContext

# 卷大纲文件：规划_卷NN.md（不含 规划_卷NN_章XXXX.md）
VOL_OUTLINE_RE = re.compile(r"规划_卷\d+\.md$")

CHAP_RE = re.compile(r"第0*(\d+)章")
# 摘要/行文里的对象引用
REF_RE = re.compile(
    r"@(主角|人物|势力|地名|区域|资源|物品|伏笔|关系|道义|古籍)"
    r"(?:\.\[([^\]]+)\]|\.(FH-\d+|DY-\d+|[A-Z]{2}-[\w-]+))?"
)
FH_BARE_RE = re.compile(r"(?<![\w-])FH-\d+")

# 允许带章节列的区块（关键词）
CHAPTER_COL_OK_HEADINGS = ("伏笔",)
# 章节列的表头写法
CHAPTER_COL_RE = re.compile(r"(^|[·\s])章节$|章节$|^章$|出场章节|退场章节|关键章节|获取章节|失去章节|出现章节|变化章节")
BANNED_SECTION_RE = re.compile(r"出场对象ID清单|章节.对象索引|按章节.*对象")


def _iter_md_tables(lines):
    """yield (start_lineno, header_cells, [(row_lineno, cells), ...])."""
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i].strip()
        if line.startswith("|") and i + 1 < n and re.match(r"^\|[\s:|-]+\|?\s*$", lines[i + 1].strip()):
            header = [c.strip() for c in line.strip("|").split("|")]
            rows = []
            j = i + 2
            while j < n and lines[j].strip().startswith("|"):
                rows.append((j + 1, [c.strip() for c in lines[j].strip().strip("|").split("|")]))
                j += 1
            yield (i + 1, header, rows)
            i = j
        else:
            i += 1


def _refs_in(text: str) -> Set[str]:
    """返回文本里的对象标识集合：人名/势力名/地名…（方括号内），FH-号，DY-号，'主角'。"""
    out: Set[str] = set()
    for m in REF_RE.finditer(text):
        typ, br, ident = m.group(1), m.group(2), m.group(3)
        if typ == "主角":
            out.add("@主角")
        elif br:
            out.add(br.strip())
        elif ident:
            out.add(ident.strip())
    for m in FH_BARE_RE.finditer(text):
        out.add(m.group(0))
    return out


class PlanBeatRule(AuditRule):
    name = "plan_beat"
    code_prefix = "PLAN_BEAT"

    def run(self, context: AuditContext) -> List[Finding]:
        findings: List[Finding] = []
        for fi in context.files:
            if fi.data_domain != "03_规划" or not VOL_OUTLINE_RE.search(fi.relative_path):
                continue
            findings.extend(self._check_one(fi))
        return findings

    def _check_one(self, fi) -> List[Finding]:
        out: List[Finding] = []
        lines = fi.content.splitlines()
        rel = fi.relative_path

        # 当前所在小节标题
        heading_at: Dict[int, str] = {}
        cur = ""
        for idx, ln in enumerate(lines):
            s = ln.strip()
            if s.startswith("#") or (s.startswith("【") and s.endswith("】")) or s.startswith("### "):
                cur = s.strip("# ").strip()
            heading_at[idx] = cur

        # --- 定位节拍表，建 {章号: 摘要refs} ---
        beat_refs: Dict[int, Set[str]] = {}
        beat_summary: Dict[int, str] = {}
        beat_table_span: Tuple[int, int] = (-1, -1)
        for start, header, rows in _iter_md_tables(lines):
            hdr_join = " ".join(header)
            if ("章节" in header or "章" in header) and ("一句话剧情摘要" in hdr_join or "剧情摘要" in hdr_join or "摘要" in hdr_join):
                last = rows[-1][0] if rows else start
                beat_table_span = (start, last)
                for rlineno, cells in rows:
                    if len(cells) < 2:
                        continue
                    cm = CHAP_RE.search(cells[0])
                    if not cm:
                        continue
                    ch = int(cm.group(1))
                    summ = cells[1]
                    beat_summary[ch] = summ
                    beat_refs[ch] = _refs_in(summ)
                break

        if not beat_refs:
            return out  # 没有可解析的节拍表，交给其它规则

        # --- PLAN_BEAT001：其它表带章节列 / 存在废弃索引区块 ---
        bad_cols: List[str] = []
        for start, header, rows in _iter_md_tables(lines):
            if beat_table_span[0] <= start <= beat_table_span[1]:
                continue
            sec = heading_at.get(start - 1, "")
            if any(k in sec for k in CHAPTER_COL_OK_HEADINGS):
                continue
            for h in header:
                if CHAPTER_COL_RE.search(h):
                    bad_cols.append(f"{rel}:{start}（小节「{sec}」表头含「{h}」列）")
                    break
        for idx, ln in enumerate(lines, 1):
            if BANNED_SECTION_RE.search(ln) and (ln.strip().startswith(("#", "【", "###", "**")) or "：" in ln[:12]):
                bad_cols.append(f"{rel}:{idx}（疑似按章节的对象索引区块「{ln.strip()[:40]}」）")
        if bad_cols:
            out.append(Finding(
                severity=Severity.WARNING, rule=self.name, code="PLAN_BEAT001",
                message=f"{len(bad_cols)} 处卷大纲区块把「章节」当字段记（违反单一权威原则，见 00_系统架构规范.md §二·A）",
                file=None,
                suggestion=("删除【角色与关系】【资源与伏笔规划】各表的「章节」列与【出场对象ID清单】区块；"
                            "某事在第几章只记在【章节节拍表】摘要（用 @引用 点名该章对象/伏笔/资源）"),
                category="03_规划", locations=bad_cols,
            ))

        # --- PLAN_BEAT002 + 003：其它表里的「第N章 + 对象」是否与节拍表摘要一致 ---
        missing: List[str] = []
        for start, header, rows in _iter_md_tables(lines):
            if beat_table_span[0] <= start <= beat_table_span[1]:
                continue
            sec = heading_at.get(start - 1, "")
            for rlineno, cells in rows:
                rowtext = " ".join(cells)
                chaps = {int(m.group(1)) for m in CHAP_RE.finditer(rowtext)}
                if not chaps:
                    continue
                refs = _refs_in(rowtext)
                # 该行涉及的 FH（伏笔区块重点查）
                for ch in sorted(chaps):
                    if ch not in beat_refs:
                        missing.append(f"{rel}:{rlineno}（小节「{sec}」提到第{ch}章，但节拍表无第{ch}章行）")
                        continue
                    for r in refs:
                        if r == "@主角":
                            continue
                        if r not in beat_refs[ch]:
                            missing.append(
                                f"{rel}:{rlineno}（小节「{sec}」：第{ch}章 × 「{r}」，但第{ch}章节拍表摘要未 @引用 它）")
        # 去重、限量
        missing = sorted(set(missing))
        if missing:
            out.append(Finding(
                severity=Severity.WARNING, rule=self.name, code="PLAN_BEAT002",
                message=f"{len(missing)} 处：卷大纲区块声明「第N章发生某事」，但第N章节拍表摘要未 @引用 对应对象/伏笔",
                file=None,
                suggestion=("以【章节节拍表】摘要为准：要么把该对象/伏笔 @引用 补进第N章摘要，"
                            "要么修正区块里写错的章节号。节拍表摘要是章节归属的唯一权威。"),
                category="03_规划", locations=missing,
            ))

        return out
