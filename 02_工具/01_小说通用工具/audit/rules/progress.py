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
- PROGRESS005 error   落地核对表缺失，或仍有未锚定项（未勾选/占位符/未 waive 的 ❌未落地）
- PROGRESS006 error   落地核对表已勾选，但锚点句在当前正文里找不到逐字匹配（幽灵锚点——
                      核对表生成之后正文又被本地改过，锚点没跟着回核）
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
    "PROGRESS005": "跑 `build_landing_checklist.py <本章目录>` 生成落地核对表，"
                   "逐条在正文里锚定或标 `❌未落地`；清完未锚定项再转「定稿」",
    "PROGRESS006": "核对表生成之后正文又被改动过——回头把该条锚点句换成正文的最新原文，"
                   "或确认改动没有让这条 beat 从正文里消失；锚点是拿来防止「核对表说落地了、"
                   "正文其实早已不是那句话」的，改完正文不回核等于没做这步",
}

_SEV_RANK = {"error": 3, "warning": 2, "info": 1}


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
            # 同一 code 可能同时有 error / warning 实例（不同章）——取最强的
            if _SEV_RANK.get(lv, 0) >= _SEV_RANK.get(sev_of.get(code, ""), 0):
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
