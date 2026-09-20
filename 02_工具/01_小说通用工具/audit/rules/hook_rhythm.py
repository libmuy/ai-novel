"""
卷大纲钩子节奏校验 (hook_rhythm.py)

钩子类型由卷大纲【章节节拍表】的「钩子类型」列决定，单章细纲的章末钩子必须照它写。
本规则守的是**读者留存**，不是机械配额（`00_通用写作规则` 3.3「结尾钩子规则」是文字权威）：

- HOOK001 (warning)：钩子类型不在 {重钩, 轻钩, 无钩}；或标「无钩」的章**不在卷末两章内**
  （「无钩」仅用于卷末重大情感释放后的喘息章，见 `06_卷大纲模板`）。
- HOOK002 (warning)：卷内不少于 10 章时，**前 10 章重钩少于 4 个**（开篇留存关键期）。
- HOOK003 (warning)：**连续 ≥4 章同一强度**（重钩或轻钩；「无钩」按轻钩计，不能用它打断轻钩长跑）——读者疲劳。
- HOOK004 (warning)：核心事件类型含「战斗」「危机」的章标了「轻钩」——钩子强度应对齐事件类型。
  已定稿章的命中仅供参考（不能回头改）。
- HOOK005 (warning)：全卷重钩占比不在 35%~60%（目标约 40–55%）。

只读卷大纲 `规划_卷NN.md`（不含单章细纲），首轮 warning；启发式，作者可裁决保留。
"""
import re
from typing import List

from ..models import Finding, Severity
from ..engine import AuditRule
from ..context import AuditContext
from .plan_beat import VOL_OUTLINE_RE, CHAP_RE, _iter_md_tables

_VALID = ("重钩", "轻钩", "无钩")


def _hook_rows(fi):
    """返回 [(章号, 核心事件类型, 钩子类型, 行号)]，取【章节节拍表】表头含「钩子类型」的表。"""
    out = []
    for _start, header, rows in _iter_md_tables(fi.content.splitlines()):
        if "钩子类型" not in header or "章节" not in header:
            continue
        ci, ei, hi = header.index("章节"), None, header.index("钩子类型")
        for k, h in enumerate(header):
            if "事件类型" in h:
                ei = k
        for ln, cells in rows:
            if len(cells) <= max(ci, hi):
                continue
            m = CHAP_RE.search(cells[ci])
            if not m:
                continue
            out.append((int(m.group(1)), cells[ei] if ei is not None and ei < len(cells) else "",
                        cells[hi].strip(), ln))
        break
    return sorted(out)


class HookRhythmRule(AuditRule):
    name = "hook_rhythm"
    code_prefix = "HOOK"

    def run(self, context: AuditContext) -> List[Finding]:
        findings: List[Finding] = []
        for fi in context.files:
            if fi.data_domain != "03_规划" or not VOL_OUTLINE_RE.search(fi.relative_path):
                continue
            rows = _hook_rows(fi)
            if not rows:
                continue
            rel = fi.relative_path
            kinds = [(c, ev, next((v for v in _VALID if v in h), None), h, ln) for c, ev, h, ln in rows]

            def add(code, msg, sug, line=None):
                findings.append(Finding(severity=Severity.WARNING, rule=self.name, code=code,
                                        message=msg, file=rel, line=line, suggestion=sug, category="03_规划"))

            last = max(c for c, *_ in kinds)
            for c, ev, k, raw, ln in kinds:
                if k is None:
                    add("HOOK001", f"第{c:02d}章钩子类型「{raw}」不在 重钩/轻钩/无钩 之内",
                        "改为枚举值之一（见 `06_卷大纲模板` 钩子类型枚举）", ln)
                elif k == "无钩" and c < last - 1:
                    add("HOOK001", f"第{c:02d}章标「无钩」，但不在卷末两章内",
                        "「无钩」仅用于卷末重大情感释放后的喘息章；改为轻钩（未说破的一句话 / 反常细节 / 自我疑惑）", ln)
                if k == "轻钩" and re.search(r"战斗|危机", ev):
                    add("HOOK004", f"第{c:02d}章核心事件类型「{ev}」却标轻钩",
                        "钩子强度应对齐事件类型；已定稿章仅供参考，未写章复审是否升重钩", ln)

            seq = [(c, k) for c, _e, k, _r, _l in kinds]
            # 连续判定里「无钩」按轻钩计（读者体验上它不是重钩，不能靠它「打断」轻钩长跑）
            run_seq = [(c, "轻钩" if k == "无钩" else k) for c, k in seq]
            if len(seq) >= 10:
                heavy10 = sum(1 for c, k in seq if c <= 10 and k == "重钩")
                if heavy10 < 4:
                    add("HOOK002", f"前 10 章重钩仅 {heavy10} 个（<4）", "开篇留存关键期宜有至少 4 个重钩，复审节奏")
            run_kind, run_start, prev = None, None, None
            def flush(kind, start, end):
                if kind in ("重钩", "轻钩") and end - start + 1 >= 4:
                    add("HOOK003", f"第{start:02d}~{end:02d}章连续 {end - start + 1} 章同为{kind}",
                        "连续同强度 ≤3 章；复审其中是否有章可升/降一档（不为凑序列而扭曲事件）")
            for c, k in run_seq:
                if k == run_kind and prev is not None and c == prev + 1:
                    prev = c
                    continue
                if run_kind is not None:
                    flush(run_kind, run_start, prev)
                run_kind, run_start, prev = k, c, c
            if run_kind is not None:
                flush(run_kind, run_start, prev)
            total = len(seq)
            heavy = sum(1 for _c, k in seq if k == "重钩")
            ratio = heavy / total if total else 0
            if total >= 10 and not (0.35 <= ratio <= 0.60):
                add("HOOK005", f"全卷重钩占比 {ratio:.0%}（{heavy}/{total}），不在 35%~60%",
                    "目标约 40–55%；过高则每章都是危机、读者钝化，过低则缺推动力")
        return findings
