"""
卷大纲章节归属单一权威校验 (plan_beat.py)

见 00_系统架构规范.md §二·A。【章节节拍表】的「一句话剧情摘要」（含 @引用）是本卷
所有事件章节归属的唯一权威。其它区块只描述「是什么」，不带「章节」列。

- PLAN_BEAT001 (warning)：卷大纲的 角色/关系/资源 区块表带「章节」列，或存在
  【出场对象ID清单】等按章节的对象索引区块。
- PLAN_BEAT002 (warning)：小节标题含「伏笔」的表（【埋设/回收伏笔列表】等）某 FH 的
  埋设/回收章节 N，但第 N 章节拍表摘要未 @引用 该 FH。
- PLAN_BEAT003 (warning)：卷大纲其它区块的某行同时出现「第N章」和某对象
  （@人物/@势力/@伏笔/FH-号），但第 N 章节拍表摘要没有对应 @引用；
  或该行提到「第N章」而节拍表根本没有第 N 章行。
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

# PLAN_BEAT004：退场/死亡关键词（配角在其退场章摘要里应带这类字眼）
EXIT_KW_RE = re.compile(
    r"死|亡|殁|殒|亡故|身死|遇害|杀|斩|戮|诛|牺牲|就义|处死|问斩|伏诛|同归于尽|自尽|殉|"
    r"临终|遗志|遗物|退场|离去|离开|远走|出走|背叛|叛离|叛逃|投敌|失踪|下落不明|被贬|贬为|沦为")
# PLAN_BEAT005：寿元倒计时类关键词（人物卡或配角行里出现 → 本卷须交代其存殁）
DEATHCLOCK_KW_RE = re.compile(
    r"寿元(不足|将尽|将至|无多|耗尽)|时日无多|命不久|大限(将至|已到|将近)|"
    r"死亡倒计时|倒计时|病危|垂危|绝症|药断|肺痨|尘肺|命悬一线")
# 配角行「关联卡」列里的卡片文件名
CARD_FILE_RE = re.compile(r"(0?7[_-]人物[_-][^\s`（）()]+\.md)")
BRACKET_NAME_RE = re.compile(r"@(?:主角|人物|势力)(?:\.\[([^\]]+)\])?")


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
            findings.extend(self._check_one(fi, context))
        return findings

    def _check_one(self, fi, context: AuditContext) -> List[Finding]:
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

        # --- PLAN_BEAT002（伏笔小节）+ 003（其它区块）：「第N章 + 对象」是否与节拍表摘要一致 ---
        missing_fh: List[str] = []      # 小节标题含「伏笔」→ 002
        missing_other: List[str] = []   # 其它区块 → 003
        for start, header, rows in _iter_md_tables(lines):
            if beat_table_span[0] <= start <= beat_table_span[1]:
                continue
            sec = heading_at.get(start - 1, "")
            bucket = missing_fh if "伏笔" in sec else missing_other
            for rlineno, cells in rows:
                rowtext = " ".join(cells)
                chaps = {int(m.group(1)) for m in CHAP_RE.finditer(rowtext)}
                if not chaps:
                    continue
                refs = _refs_in(rowtext)
                for ch in sorted(chaps):
                    if ch not in beat_refs:
                        bucket.append(f"{rel}:{rlineno}（小节「{sec}」提到第{ch}章，但节拍表无第{ch}章行）")
                        continue
                    for r in refs:
                        if r == "@主角":
                            continue
                        if r not in beat_refs[ch]:
                            bucket.append(
                                f"{rel}:{rlineno}（小节「{sec}」：第{ch}章 × 「{r}」，但第{ch}章节拍表摘要未 @引用 它）")
        missing_fh = sorted(set(missing_fh))
        missing_other = sorted(set(missing_other))
        if missing_fh:
            out.append(Finding(
                severity=Severity.WARNING, rule=self.name, code="PLAN_BEAT002",
                message=f"{len(missing_fh)} 处：伏笔列表声明「第N章埋设/回收某 FH」，但第N章节拍表摘要未 @引用 该 FH",
                file=None,
                suggestion=("以【章节节拍表】摘要为准：把该 FH @引用 补进第N章摘要，或修正伏笔列表里的章节号。"),
                category="03_规划", locations=missing_fh,
            ))
        if missing_other:
            out.append(Finding(
                severity=Severity.WARNING, rule=self.name, code="PLAN_BEAT003",
                message=f"{len(missing_other)} 处：卷大纲区块声明「第N章发生某事」，但第N章节拍表摘要未 @引用 对应对象/伏笔",
                file=None,
                suggestion=("以【章节节拍表】摘要为准：要么把该对象/伏笔 @引用 补进第N章摘要，"
                            "要么修正区块里写错的章节号。节拍表摘要是章节归属的唯一权威。"),
                category="03_规划", locations=missing_other,
            ))

        # --- 公共视图：节拍表全部对象名 / 全部摘要文本 / 各章退场字眼 ---
        all_beat_names: Set[str] = set()
        for s in beat_refs.values():
            all_beat_names |= s
        all_beat_text = " ".join(beat_summary.values())
        exit_chapters = {ch for ch, summ in beat_summary.items() if EXIT_KW_RE.search(summ)}

        def _rows_under(pred):
            for start, header, rows in _iter_md_tables(lines):
                if beat_table_span[0] <= start <= beat_table_span[1]:
                    continue
                sec = heading_at.get(start - 1, "")
                if pred(sec):
                    for rlineno, cells in rows:
                        yield sec, rlineno, cells

        def _text_under(kw: str) -> str:
            """kw 命中的 ## 级小节（含其下所有 ### 子节）的正文文本。"""
            buf: List[str] = []
            active = False
            for ln in lines:
                s = ln.strip()
                if s.startswith("## ") or (s.startswith("#") and not s.startswith("###")):
                    active = kw in s
                elif active:
                    buf.append(ln)
            return "\n".join(buf)

        def _names_in(text: str) -> Set[str]:
            return {m.group(1) for m in BRACKET_NAME_RE.finditer(text) if m.group(1)}

        # 退场配角小节里点名的角色
        exit_section_names: Set[str] = set()
        for _sec, _ln, cells in _rows_under(lambda s: "退场配角" in s or ("退场" in s and "配角" in s)):
            exit_section_names |= _names_in(" ".join(cells))
        volend_text = _text_under("卷末状态")

        out.extend(self._check_exit_cast(rel, _rows_under, all_beat_names, beat_refs, exit_chapters))
        out.extend(self._check_deathclock(rel, fi, context, _rows_under, all_beat_names,
                                          exit_section_names, volend_text))
        out.extend(self._check_relation_arc(rel, _rows_under, all_beat_names, all_beat_text))
        return out

    # --- PLAN_BEAT004：退场配角完整性 ---
    def _check_exit_cast(self, rel, rows_under, all_beat_names, beat_refs, exit_chapters) -> List[Finding]:
        bad: List[str] = []
        for sec, rlineno, cells in rows_under(lambda s: "退场配角" in s or ("退场" in s and "配角" in s)):
            names = {m.group(1) for m in BRACKET_NAME_RE.finditer(" ".join(cells)) if m.group(1)}
            for nm in sorted(names):
                appears = [ch for ch, refs in beat_refs.items() if nm in refs]
                if not appears:
                    bad.append(f"{rel}:{rlineno}（退场配角「{nm}」未在任何节拍表摘要 @引用）")
                elif not (set(appears) & exit_chapters):
                    bad.append(f"{rel}:{rlineno}（退场配角「{nm}」出现在第{sorted(appears)}章摘要，"
                               f"但这些摘要都没有退场/死亡字眼——退场章的摘要须 @引用 此人并写明退场）")
        if not bad:
            return []
        return [Finding(
            severity=Severity.WARNING, rule=self.name, code="PLAN_BEAT004",
            message=f"{len(bad)} 处：【退场配角】的角色，其退场未落到某章节拍表摘要（配角死亡/离去漏进节拍表）",
            file=None,
            suggestion="把该配角的退场写进对应章的节拍表摘要：@引用 此人并含死/亡/牺牲/处死/退场/背叛/失踪 等字眼。",
            category="03_规划", locations=bad,
        )]

    # --- PLAN_BEAT005：寿元倒计时配角须交代本卷存殁 ---
    def _check_deathclock(self, rel, fi, context, rows_under, all_beat_names,
                          exit_section_names, volend_text) -> List[Finding]:
        # 预索引人物卡
        card_text: Dict[str, str] = {}
        for cf in context.files:
            m = CARD_FILE_RE.search(cf.relative_path)
            if m and cf.relative_path.endswith(m.group(1)):
                card_text[m.group(1)] = cf.content

        bad: List[str] = []
        seen: Set[str] = set()
        for sec, rlineno, cells in rows_under(lambda s: "配角" in s and "退场" not in s):
            rowtext = " ".join(cells)
            names = {m.group(1) for m in BRACKET_NAME_RE.finditer(rowtext) if m.group(1)}
            cardfiles = CARD_FILE_RE.findall(rowtext)
            blob = rowtext + " " + " ".join(card_text.get(c, "") for c in cardfiles)
            if not DEATHCLOCK_KW_RE.search(blob):
                continue
            for nm in sorted(names):
                if nm in seen:
                    continue
                seen.add(nm)
                if nm not in all_beat_names:
                    bad.append(f"{rel}:{rlineno}（寿元倒计时角色「{nm}」未在任何节拍表摘要出现）")
                elif nm not in exit_section_names and nm not in volend_text:
                    bad.append(f"{rel}:{rlineno}（寿元倒计时角色「{nm}」：本卷【退场配角】和【卷末状态】"
                               f"都没交代其本卷结束时的存殁——即便决定「不死、留到下一卷」也要在【卷末状态】写明）")
        if not bad:
            return []
        return [Finding(
            severity=Severity.WARNING, rule=self.name, code="PLAN_BEAT005",
            message=f"{len(bad)} 处：寿元/续命倒计时类配角，本卷未明确交代其存殁",
            file=None,
            suggestion="在【退场配角】或【卷末状态】对该角色本卷是否退场给出明确说法（规则不替你决定生死，只要求不沉默）。",
            category="03_规划", locations=bad,
        )]

    # --- PLAN_BEAT006：关系变化弧线可追溯 ---
    def _check_relation_arc(self, rel, rows_under, all_beat_names, all_beat_text) -> List[Finding]:
        bad: List[str] = []
        for sec, rlineno, cells in rows_under(lambda s: "关系变化" in s):
            names = {m.group(1) for m in BRACKET_NAME_RE.finditer(" ".join(cells)) if m.group(1)}
            for nm in sorted(names):
                if nm not in all_beat_names and nm not in all_beat_text:
                    bad.append(f"{rel}:{rlineno}（关系变化弧线涉及「{nm}」，但节拍表任何一章摘要都没 @引用 它）")
        if not bad:
            return []
        return [Finding(
            severity=Severity.WARNING, rule=self.name, code="PLAN_BEAT006",
            message=f"{len(bad)} 处：【关系变化】弧线涉及的对象，在节拍表摘要里追溯不到",
            file=None,
            suggestion="每次关系变化发生的那一章，其节拍表摘要须 @引用 关系双方并体现该变化。",
            category="03_规划", locations=bad,
        )]
