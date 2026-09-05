"""
细纲内部标识校验 (outline_leak.py)

依据 `00_通用模板/04_提示词/00_云端提示词生成器.md`「示例去污染规则」第 6 条：
凡出现在【任务】【修改项】**【已有数据】**等会被模型转写成正文的段落里，一律禁止
出现章节 ID、卷部编号、文件名与仓库路径、`场景N`、文件代称等内部标识。

单章细纲是**逐字内联进【已有数据】**的，且是模型转写正文的主要依据，因此细纲本身
必须干净。此前没有任何检查覆盖它，代价是：

- ch0002 修订1：正文出现「章0001 买的止咳散」（源头在修改项）
- ch0003 首版：正文出现「比章0001 沟壁那几道更深更密」（源头在细纲场景内容简述）

拼装期 `build_prompt.py` 已会就同一问题报警，但那时提示词已经拼好、往往已经发给
云端。本规则把拦截点前移到**细纲落位时**。

- OUTLINE_LEAK001 (warning)：细纲的叙述性字段里出现内部标识。

判定逻辑**直接复用** `prompt_build.leak.scan_source`——两处各写一份必然漂移，
而「规则说的范围」与「实现扫的范围」对不上，正是本规则要防的那类问题本身。
"""
from pathlib import Path
from typing import List

from ..models import Finding, Severity
from ..engine import AuditRule
from ..context import AuditContext

OUTLINE_GLOB = "03_规划/**/规划_卷*_章*.md"


def _scan_source(text: str):
    """借 prompt_build 的实现；拿不到就安静跳过（该包只在拼装机器上必备）。"""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    try:
        from prompt_build import leak
    except Exception:
        return None
    return leak.scan_source("细纲", text)


class OutlineLeakRule(AuditRule):
    name = "outline_leak"
    code_prefix = "OUTLINE_LEAK"

    def run(self, context: AuditContext) -> List[Finding]:
        findings: List[Finding] = []
        for path in sorted(context.novel_dir.glob(OUTLINE_GLOB)):
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            hits = _scan_source(text)
            if hits is None:          # prompt_build 不可用，本规则跳过
                return []
            if not hits:
                continue
            kinds: dict[str, int] = {}
            for h in hits:
                kinds[h.kind] = kinds.get(h.kind, 0) + 1
            head = "、".join(f"{k} {v} 处" for k, v in sorted(kinds.items()))
            sample = "；".join(f"第{h.line_no}行「{h.hit}」" for h in hits[:3])
            rel = path.relative_to(context.novel_dir).as_posix()
            findings.append(Finding(
                severity=Severity.WARNING,
                rule=self.name,
                code="OUTLINE_LEAK001",
                message=(f"细纲叙述性字段里有 {len(hits)} 处仓库内部标识（{head}）——"
                         f"细纲会逐字内联进正文提示词的【已有数据】，模型会照抄进正文。"
                         f"例：{sample}"),
                file=rel,
                suggestion=("改成故事内说法（`章0001 买的止咳散` →「昨日换回的那半包」；"
                            "`场景2 的钩子` →「这一场收在……」）。结构字段（涉及伏笔/出场对象表等）"
                            "与文末台账小节不在此列，已自动跳过。"),
            ))
        return findings
