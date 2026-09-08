"""
细纲待确认清单门禁 (outline_pending.py)

`04_单章质量验收.md` 细纲循环门禁明写：「若某条必改项确实需要用户拍板才能定，标
『待裁决』挂起并上报，**不得带着它进正文阶段**」。但这条一直只靠人自觉——ch4 细纲
带着 4 条『待作者过目』就转了定稿、正文也跟着出稿。本规则把它变成确定性检查。

- PLAN020 error   细纲【待确认清单】仍有未裁决项，而该章正文已声明 定稿 / 待校验
- PLAN021 warning 细纲【待确认清单】仍有未裁决项，而细纲自身已声明 定稿（还没到正文阶段，但已越线）
- PLAN022 info    有【待确认清单】小节、但结构上判断不出是否已全部裁决 —— 请人工确认

判定复用 `progress_report.collect`（成熟度口径与 `progress` 规则一致）。
"""
import re
import sys
from pathlib import Path
from typing import List

from ..models import Finding, Severity
from ..engine import AuditRule
from ..context import AuditContext

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

_SECTION_HEADINGS = ("【待确认清单】", "待确认清单", "【待裁决】", "【遗留待裁决】", "遗留待裁决")
_TRIGGER_RE = re.compile(r"(遗留|待|需|请)[^。\n]{0,8}(作者|用户|过目|裁决|确认|复核|拍板|定夺)")
_RESOLVED_RE = re.compile(r"已(全部)?(确认|裁决|复核|落定|定|处理)|均已|全部裁决|无(待|遗留|需)")
_NUM_ITEM_RE = re.compile(r"^\s*\d+[\.、)]\s+\S")
_OPEN_BOX_RE = re.compile(r"^\s*[-*]\s*\[\s\]\s*\S")
_DONE_BOX_RE = re.compile(r"^\s*[-*]\s*\[[xX]\]")


def _section_text(text: str, headings) -> str:
    lines = text.splitlines()
    start = level = None
    for i, line in enumerate(lines):
        m = re.match(r"^(#{1,6})\s+(.*?)\s*$", line)
        if not m:
            continue
        title = m.group(2).strip()
        if start is None and any(h in title for h in headings):
            start, level = i, len(m.group(1))
            continue
        if start is not None and len(m.group(1)) <= level:
            return "\n".join(lines[start + 1:i])
    if start is None:
        return ""
    return "\n".join(lines[start + 1:])


def _open_items(section: str) -> list[str]:
    items: list[str] = []
    pending = False
    for raw in section.splitlines():
        s = raw.strip()
        if not s:
            continue
        if _OPEN_BOX_RE.match(s):
            items.append(s[:90])
            continue
        if _DONE_BOX_RE.match(s):
            continue
        if _TRIGGER_RE.search(s) and not _NUM_ITEM_RE.match(s):
            pending = not bool(_RESOLVED_RE.search(s))
            continue
        if pending and _NUM_ITEM_RE.match(s):
            if not _RESOLVED_RE.search(s):
                items.append(s[:90])
            continue
        # 触发段后遇到实质散文（非列表/引用/表格）→ 认为列表已结束
        if pending and not s.startswith(("|", ">", "-", "*", "#")) and not _NUM_ITEM_RE.match(s):
            pending = False
    return items


class OutlinePendingRule(AuditRule):
    name = "outline_pending"
    code_prefix = "PLAN"

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
        except Exception as e:  # noqa: BLE001
            return [Finding(
                severity=Severity.WARNING, rule=self.name, code="PLAN000",
                message=f"待确认清单门禁未能执行：{e}", file="00_进度.md",
                suggestion="手工跑 `progress_report.py <小说目录>` 看详细报错",
                category="规划", locations=["00_进度.md"])]

        block, warn, info = [], [], []
        for c in rep.chapters:
            if c.outline is None or not c.outline.exists():
                continue
            text = c.outline.read_text(encoding="utf-8", errors="ignore")
            section = _section_text(text, _SECTION_HEADINGS)
            if not section.strip():
                continue
            rel = c.outline.relative_to(novel_dir).as_posix()
            items = _open_items(section)
            if items:
                where = f"{rel}（{c.cid}）：" + "；".join(i[:50] for i in items[:4])
                if c.declared_manuscript in ("定稿", "待校验"):
                    block.append(where)
                elif c.declared_outline == "定稿":
                    warn.append(where)
                else:
                    warn.append(where)  # 细纲还没定稿也提醒，别让它悄悄溜过
            elif "待" in section and not _RESOLVED_RE.search(section):
                info.append(f"{rel}（{c.cid}）：有【待确认清单】小节，结构上判断不出是否已全部裁决")

        out: List[Finding] = []
        if block:
            out.append(Finding(
                severity=Severity.ERROR, rule=self.name, code="PLAN020",
                message=f"{len(block)} 章的正文已声明定稿/待校验，但细纲【待确认清单】仍有未裁决项",
                file=None,
                suggestion="先让作者逐条裁决、把结论写进细纲（清掉『待作者过目』列表），再转正文定稿",
                category="规划", locations=block))
        if warn:
            out.append(Finding(
                severity=Severity.WARNING, rule=self.name, code="PLAN021",
                message=f"{len(warn)} 章的细纲【待确认清单】仍有未裁决项（不得带进正文阶段）",
                file=None,
                suggestion="按 04_单章质量验收.md 细纲循环门禁：待裁决项清零才能去拼正文提示词",
                category="规划", locations=warn))
        if info:
            out.append(Finding(
                severity=Severity.INFO, rule=self.name, code="PLAN022",
                message=f"{len(info)} 章的【待确认清单】需人工确认是否已全部裁决",
                file=None, suggestion="人工过一眼；确认已裁决就把措辞改成『已确认/已裁决』",
                category="规划", locations=info))
        return out
