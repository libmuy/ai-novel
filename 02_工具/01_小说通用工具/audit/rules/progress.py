"""
进度表对账规则 (progress.py)

`00_进度.md` 是全仓 churn 最高的文件，且自己写过「此前本文件长期滞后于实际进度」。
它混着两类信息：成熟度与裁决（人的判断，不可推导）＋ 文件在不在 / 多少字 / 跑了几轮
（可推导）。本规则只做一件事——**当人维护的那一半与可观测事实矛盾时报出来**。

判定逻辑复用 `progress_report.py`（同一套口径，避免两处各写一份而漂移）；
该脚本还能 `--write` 出人可读的派生视图。

- PROGRESS001 error   进度表声明了成熟度的 canonical 产出，文件却不存在
- PROGRESS002 warning 章节细纲 / 正文已落位，进度表却完全没登记（进度表滞后）
- PROGRESS003 warning 声明的成熟度超前于流水线事实（标定稿却缺履历 / 未折叠 / 无冷读记录）
"""
import sys
from pathlib import Path
from typing import List

from ..models import Finding, Severity
from ..engine import AuditRule
from ..context import AuditContext

# progress_report.py 与 audit/ 同在 02_工具/01_小说通用工具/ 下
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

_SEV = {"error": Severity.ERROR, "warning": Severity.WARNING, "info": Severity.INFO}

_SUGGEST = {
    "PROGRESS001": "把 `00_进度.md` 该行的产出路径改成实际路径，或补齐缺失的产出文件；"
                   "路径写错会让下游任务（含 `build_prompt.py` 的前置门禁）查错对象",
    "PROGRESS002": "在 `00_进度.md` 补登该产出与其成熟度；"
                   "产物落位却不登记，正是这个文件历来滞后的方式",
    "PROGRESS003": "要么补齐缺的那一步，要么把成熟度降回实际阶段——"
                   "成熟度是下游任务的前置门禁，超前声明等于伪造前置",
}


class ProgressRule(AuditRule):
    name = "progress"
    code_prefix = "PROGRESS"

    def run(self, context: AuditContext) -> List[Finding]:
        novel_dir = context.novel_dir
        if not (novel_dir / "00_进度.md").exists():
            return []
        try:
            import progress_report
        except ImportError:
            return []

        try:
            rep = progress_report.collect(novel_dir)
        except Exception as e:                      # 采集失败不该拖垮整个审查
            return [Finding(
                severity=Severity.WARNING, rule=self.name, code="PROGRESS000",
                message=f"进度对账未能执行：{e}", file="00_进度.md",
                suggestion="手工跑 `progress_report.py <小说目录>` 看详细报错",
                category="进度", locations=["00_进度.md"])]

        by_code: dict[str, list[str]] = {}
        sev_of: dict[str, str] = {}
        for lv, code, msg in rep.findings:
            by_code.setdefault(code, []).append(msg)
            sev_of[code] = lv

        findings: List[Finding] = []
        for code, msgs in sorted(by_code.items()):
            findings.append(Finding(
                severity=_SEV.get(sev_of[code], Severity.WARNING),
                rule=self.name, code=code,
                message=f"`00_进度.md` 与可观测事实有 {len(msgs)} 处不一致",
                file="00_进度.md",
                suggestion=_SUGGEST.get(code, "核对 `00_进度.md` 与实际产出"),
                category="进度", locations=msgs,
            ))
        return findings
