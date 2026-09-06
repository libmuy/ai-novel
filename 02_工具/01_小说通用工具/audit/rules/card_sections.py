"""
出场对象卡区块守护 (card_sections.py)

正文提示词把出场人物卡**只取几个区块**内联（`00_通用模板/04_提示词/任务输入清单.toml`
里 `resolver:cast_cards_outline` 的 `sections = [...]`）。取材走 `prompt_build/extract.py`
的 `read_sections`，它对**找不到的区块名静默跳过**——某张人物卡把 `### 【角色内核】`
改了名 / 漏填了，那一段就从提示词里无声消失，云端照写不误，审计全绿。

`audit_rules.py` 的 `RULE009` 只校验这些区块名在**模板** `02_卡片模板/04_人物模板.md`
里存在；本规则补另一半——校验它们在 `02_数据库/07_人物/` 下**实际会被内联的卡片**里存在。

- CARDSEC001 (warning)：某张人物卡缺清单声明的内联区块。
  warning 而非 error——迁移期不阻断 `check.sh`；清单口径本身有争议时也不该硬拦。

判定与 `assemble.py:_add_cast_cards` 一致：只查人物卡（势力/地理卡结构不同、整份内联）。
**匹配用精确相等**，刻意跟 `extract.read_section`（`title == want`）一模一样——如果这里放宽成
子串匹配，就会出现「审计说区块在、`read_sections` 精确匹配取不到、内容照样静默消失」，
正是本规则要堵的洞。模板里 `【角色内核】（三要素）` 带后缀，但实例卡片用的是干净的
`### 【角色内核】`（已核 19 张苍玄人物卡），清单口径也照实例写。

**豁免**：某张卡确实不该有某个区块时，在卡片任意位置写一行
`<!-- CARDSEC-ok: 【区块名】 理由 -->`——注释里带上区块名，本规则就对这张卡跳过那个区块
（罕用；正常做法是补齐区块，或改清单口径）。
"""
import re
from pathlib import Path
from typing import List

from ..models import Finding, Severity
from ..engine import AuditRule
from ..context import AuditContext

# assemble.py 只对人物卡切片
_PERSON_CARD_DIR = "02_数据库/07_人物"
_PERSON_CARD_GLOB = "07_人物_*.md"
_CAST_RESOLVERS = {"cast_cards_outline", "cast_cards_beat"}
_WAIVER = re.compile(r"<!--\s*CARDSEC-ok:([^>]*)-->")
_HEADING = re.compile(r"^\s*#{1,6}\s+(.+?)\s*$")


class CardSectionsRule(AuditRule):
    name = "card_sections"
    code_prefix = "CARDSEC"

    def run(self, context: AuditContext) -> List[Finding]:
        findings: List[Finding] = []

        declared = self._declared_sections(context)
        if not declared:
            return findings

        card_dir = context.novel_dir / _PERSON_CARD_DIR
        if not card_dir.is_dir():
            return findings

        for card in sorted(card_dir.glob(_PERSON_CARD_GLOB)):
            text = card.read_text(encoding="utf-8")
            heads = {m.group(1) for line in text.splitlines()
                     if (m := _HEADING.match(line))}
            waived_text = " ".join(m.group(1) for m in _WAIVER.finditer(text))
            missing = [s for s in declared
                       if s not in heads and s not in waived_text]
            if not missing:
                continue
            rel = card.relative_to(context.novel_dir).as_posix()
            findings.append(Finding(
                severity=Severity.WARNING,
                rule=self.name,
                code="CARDSEC001",
                message=(
                    f"人物卡缺正文提示词要内联的区块：{'、'.join(missing)}"
                    "——这几段会从提示词里静默消失"
                ),
                file=rel,
                line=1,
                suggestion=(
                    "按 `00_通用模板/02_卡片模板/04_人物模板.md` 的区块结构补齐这张卡；"
                    "若某区块确不适用，改 `04_提示词/任务输入清单.toml` 的 "
                    "`cast_cards_outline` sections（那是全书口径，慎改）。"
                    "个别卡确要豁免：卡里加一行 `<!-- CARDSEC-ok: 【区块名】 理由 -->`"
                ),
                category="02_数据库",
                locations=[f"{rel} 缺 {s}" for s in missing],
            ))

        return findings

    def _declared_sections(self, context: AuditContext) -> List[str]:
        """任务输入清单里 cast_cards 步骤声明的区块名（去重、保序）。"""
        try:
            from prompt_build import manifest as M
        except ImportError:
            import sys
            sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
            from prompt_build import manifest as M

        toml_path = context.novel_dir / M.MANIFEST_REL
        if not toml_path.is_file():
            return []
        try:
            man = M.parse(toml_path.read_text(encoding="utf-8"))
        except M.ManifestError:
            return []  # 清单本身坏了是 RULE009 的事，这里不重复报

        out: List[str] = []
        for task in man.tasks.values():
            for step in task.steps:
                if step.kind == "resolver" and step.ref in _CAST_RESOLVERS:
                    for s in step.sections:
                        if s not in out:
                            out.append(s)
        return out
