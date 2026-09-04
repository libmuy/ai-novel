"""
伏笔进度规则 (foreshadow_schedule.py)

`foreshadow.py` 只管「FH 号有没有在总纲登记」。**伏笔到期没回收**这件事至今是纯人工的
（技能 `05_伏笔回收校验.md`），而伏笔册里明明写着「埋设位置」「拟回收卷/章」
「本卷回收章节」「执行状态」——到期与否是可算的。70 个 FH 且还在长，
靠人记得就是第 50 章还在交第 1 章的学费。

三条规则（都按「正文是否已落位」判断进度，不看成熟度）：

- FS003 warning 埋设逾期 —— 伏笔册说埋在某章，那章正文已写完，
                该章细纲里却找不到这个 FH
- FS004 warning 回收逾期 —— 拟回收的卷/章已经写完，卷册回收表里
                该 FH 的执行状态却不含「已回收」
- FS005 warning 无回收计划 —— 本卷新埋的 FH，「拟回收卷/章」空着

**刻意不做**「总纲状态 vs 卷册执行状态」对照：阶段性回收是合法的
（FH-067 卷册记「已回收（阶段性：入道机制坐实，深层来历仍悬）」而总纲记「活跃」，
两者都对）。把它写成规则只会制造假阳性。
"""
import re
from pathlib import Path
from typing import List, Optional

from ..models import Finding, Severity
from ..engine import AuditRule
from ..context import AuditContext

FH_RE = re.compile(r"FH-\d+")
# 「卷01章0001」「章0003」「卷02」「卷03~卷04」「卷04+」都要能解析
_VOL_CH_RE = re.compile(r"卷\s*0*(\d+)\s*章\s*0*(\d+)")
_CH_ONLY_RE = re.compile(r"章\s*0*(\d+)")
_VOL_ONLY_RE = re.compile(r"卷\s*0*(\d+)")
_EMPTY = {"", "-", "—", "–", "待定", "未定", "TBD", "N/A", "无", "?", "？"}
_RECOVERED = ("已回收", "已触发", "已闭合")


def _cells(line: str) -> Optional[List[str]]:
    s = line.strip()
    if not s.startswith("|"):
        return None
    c = [x.strip() for x in s.strip("|").split("|")]
    if not c or all(set(x) <= set(":- ") for x in c):
        return None
    return c


def _tables(text: str):
    """产出 (表头单元格, [数据行…])，按空行/非表格行切分连续表块。

    注意：`| :--- | :--- |` 分隔行也以 `|` 开头，但不是数据行。它必须被**跳过**，
    不能当成「表结束」——否则表头会被丢掉、第一条数据行被当成表头，整张表解析错位
    （这条规则第一版就栽在这里，真实数据与构造用例都静默返回零发现）。
    """
    header, rows = None, []
    for line in text.splitlines():
        if not line.strip().startswith("|"):
            if header is not None and rows:
                yield header, rows
            header, rows = None, []
            continue
        c = _cells(line)
        if c is None:
            continue                      # 分隔行：跳过，但表还没结束
        if header is None:
            header = c
        else:
            rows.append(c)
    if header is not None and rows:
        yield header, rows


def _col(header: List[str], *names: str) -> Optional[int]:
    for i, h in enumerate(header):
        for n in names:
            if n in h:
                return i
    return None


def _points(text: str, default_volume: Optional[int] = None):
    """把「卷01章0001（发热）/章0003」这类写法解析成 [(卷, 章|None), …]。

    `章0003` 这种省略卷号的写法沿用上一处出现的卷号——伏笔册里普遍这么写。
    """
    out: List[tuple] = []
    cur_vol = default_volume
    pos = 0
    for m in re.finditer(r"卷\s*0*(\d+)\s*章\s*0*(\d+)|卷\s*0*(\d+)|章\s*0*(\d+)", text):
        if m.group(1):
            cur_vol = int(m.group(1))
            out.append((cur_vol, int(m.group(2))))
        elif m.group(3):
            cur_vol = int(m.group(3))
            out.append((cur_vol, None))
        elif m.group(4) and cur_vol is not None:
            out.append((cur_vol, int(m.group(4))))
        pos = m.end()
    return out


class ForeshadowScheduleRule(AuditRule):
    name = "foreshadow_schedule"
    code_prefix = "FS"

    def run(self, context: AuditContext) -> List[Finding]:
        novel_dir = context.novel_dir
        written = self._written_chapters(context)
        vol_last = self._volume_last_chapter(context)
        outline_fh = self._outline_foreshadows(context)

        overdue_bury: List[str] = []
        overdue_recover: List[str] = []
        no_plan: List[str] = []

        for fi in context.files:
            if not (fi.data_domain == "03_规划"
                    and fi.relative_path.rsplit("/", 1)[-1].startswith("00_伏笔册_")):
                continue
            vol_hint = None
            mv = _VOL_ONLY_RE.search(fi.relative_path)
            if mv:
                vol_hint = int(mv.group(1))
            rel = fi.relative_path

            recovered: dict[str, str] = {}
            planted: dict[str, tuple] = {}

            for header, rows in _tables(fi.content):
                i_id = _col(header, "伏笔ID", "FH-ID", "伏笔 ID")
                if i_id is None:
                    continue

                # 先给表分类，再取列。三张表都含「…埋设位置」——回收表与推进表用的是
                # 「**来源**埋设位置」，只按子串找「埋设位置」会把它们误判成新埋表，
                # 于是 FH-068 的「拟回收=卷03」被推进表的空值覆盖，误报 FS005。
                i_state = _col(header, "执行状态")
                if i_state is not None:
                    kind = "recover"
                elif _col(header, "推进", "暗示") is not None:
                    kind = "progress"          # 阶段性推进，不参与排期判断
                elif _col(header, "埋设位置") is not None:
                    kind = "bury"
                else:
                    continue
                if kind == "progress":
                    continue

                i_bury = _col(header, "埋设位置")
                i_plan = _col(header, "拟回收")

                for cells in rows:
                    if i_id >= len(cells):
                        continue
                    m = FH_RE.search(cells[i_id])
                    if not m:
                        continue
                    fh = m.group(0)

                    if kind == "recover":
                        state = cells[i_state] if i_state < len(cells) else ""
                        if any(k in state for k in _RECOVERED):
                            recovered[fh] = state
                        continue

                    if i_bury is not None:
                        bury = cells[i_bury] if i_bury < len(cells) else ""
                        plan = cells[i_plan] if (i_plan is not None and i_plan < len(cells)) else ""
                        planted[fh] = (bury, plan, rel)

                        # FS003：埋设章已写完，但该章细纲没提这个 FH
                        for v, c in _points(bury, vol_hint):
                            if c is None or (v, c) not in written:
                                continue
                            if fh not in outline_fh.get((v, c), set()):
                                overdue_bury.append(
                                    f"{rel}：{fh} 登记埋设在 卷{v:02d}章{c:04d}，"
                                    f"该章正文已落位，但该章细纲里找不到 {fh}")

                        # FS005：埋了却没排回收
                        if plan.strip() in _EMPTY:
                            no_plan.append(f"{rel}：{fh} 已登记埋设（{bury or '位置未写'}），"
                                           f"但「拟回收卷/章」为空")

            # ── FS004：拟回收的点已经过去，却没登记回收
            for fh, (bury, plan, where) in planted.items():
                if fh in recovered or plan.strip() in _EMPTY:
                    continue
                pts = _points(plan, vol_hint)
                if not pts:
                    continue
                v, c = pts[-1]                      # 「卷03~卷04」取最晚那个点
                if c is not None:
                    reached = (v, c) in written
                    label = f"卷{v:02d}章{c:04d}"
                else:
                    last = vol_last.get(v)
                    reached = last is not None and (v, last) in written
                    label = f"卷{v:02d}（末章 {('章%04d' % last) if last else '未知'}）"
                if reached:
                    overdue_recover.append(
                        f"{where}：{fh} 拟回收于 {label}，该处正文已写完，"
                        f"但卷册回收表里它的执行状态不含「已回收」")

        findings: List[Finding] = []
        if overdue_bury:
            findings.append(Finding(
                severity=Severity.WARNING, rule=self.name, code="FS003",
                message=f"{len(overdue_bury)} 个伏笔的埋设章已写完，细纲里却没有它",
                suggestion="要么在该章细纲补上埋设动作并回改正文，要么把伏笔册的"
                           "「埋设位置」改到实际埋设的那一章——埋设位置写错会让"
                           "后续所有回收判断都对不上",
                category="伏笔", locations=overdue_bury))
        if overdue_recover:
            findings.append(Finding(
                severity=Severity.WARNING, rule=self.name, code="FS004",
                message=f"{len(overdue_recover)} 个伏笔已过拟回收点但未登记回收",
                suggestion="在该卷伏笔册「本卷拟回收/触发」表登记回收章节与执行状态；"
                           "若确定推迟，把「拟回收卷/章」改到新的计划点，别让它默默逾期",
                category="伏笔", locations=overdue_recover))
        if no_plan:
            findings.append(Finding(
                severity=Severity.WARNING, rule=self.name, code="FS005",
                message=f"{len(no_plan)} 个已埋伏笔没有回收计划",
                suggestion="补「拟回收卷/章」；埋了不排回收，等于把债记在没有到期日的账上",
                category="伏笔", locations=no_plan))
        return findings

    # ── 进度与索引 ────────────────────────────────────────────

    @staticmethod
    def _written_chapters(context: AuditContext) -> set:
        """已落位的正文 → {(卷, 章)}。用正文存在与否判断进度，不看成熟度。"""
        out = set()
        for fi in context.files:
            if fi.data_domain != "10_正文":
                continue
            mc = _CH_ONLY_RE.search(fi.relative_path.rsplit("/", 1)[-1])
            mv = _VOL_ONLY_RE.search(fi.relative_path)
            if mc and mv:
                out.add((int(mv.group(1)), int(mc.group(1))))
        return out

    @staticmethod
    def _volume_last_chapter(context: AuditContext) -> dict:
        """各卷【章节节拍表】里的最大章号。取不到的卷不参与「整卷写完」判断。"""
        out: dict[int, int] = {}
        for fi in context.files:
            fn = fi.relative_path.rsplit("/", 1)[-1]
            if not re.fullmatch(r"规划_卷\d+\.md", fn):
                continue
            mv = _VOL_ONLY_RE.search(fn)
            if not mv:
                continue
            vol = int(mv.group(1))
            best = 0
            # 找【章节节拍表】那张表。**按结构认，不按列名认**：首列表头带「章」
            # 且数据行首列就是章号。绑死列名（如必须叫「章节」+「摘要」）会在
            # 换本书改了列名时静默失配——末章取不到 → 整卷永远判不成「写完」→
            # FS004 无声失效。宁可结构上宽一点，也不要又一次静默零发现。
            for header, rows in _tables(fi.content):
                if not header or "章" not in header[0]:
                    continue
                hits = [int(m.group(1)) for cells in rows
                        if cells and (m := re.fullmatch(r"第?\s*0*(\d+)\s*章?", cells[0].strip()))]
                if hits:
                    best = max(best, max(hits))
            if best:
                out[vol] = best
        return out

    @staticmethod
    def _outline_foreshadows(context: AuditContext) -> dict:
        """各单章细纲提到的 FH → {(卷, 章): {FH-…}}。

        正文里禁止出现 `@引用`（MANUSCRIPT001），所以「这一章有没有落地某伏笔」
        只能在细纲层判断。
        """
        out: dict[tuple, set] = {}
        for fi in context.files:
            if fi.data_domain != "03_规划":
                continue
            m = re.search(r"规划_卷0*(\d+)_章0*(\d+)\.md$", fi.relative_path)
            if not m:
                continue
            out[(int(m.group(1)), int(m.group(2)))] = set(FH_RE.findall(fi.content))
        return out
