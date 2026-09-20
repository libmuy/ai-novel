"""
人物卡设定层边界校验 (card_plot.py)

`00_系统架构规范.md` §二「核心判定原则」/§七 + `04_人物模板`「设定层边界」：人物卡属**设定层**，
只写「他是谁、要什么、能做什么、大致走向哪里」的**定性**，不写「谁对谁做了什么、几次、多少」
这类事件/状态——那是规划层（卷纲【章节节拍表】【关系变化】【本卷退场配角】）与状态层的事实，
写进卡里就成了第二份、会漂移，还会剧透并被下游当成硬约束。

- CARDPLOT001 (warning)：人物卡出现**已取消的字段行**——「当前进度」（动态状态归状态层）、
  「主角对其的影响」（主角对他做了什么，归卷纲【关系变化】）。
- CARDPLOT002 (warning)：定性字段（「高光时刻设计」「预计退场方式」「关系演变轨迹」
  「对主角的影响」）里出现**事件特征**：数量+量词（`三年`/`十枚`/`两次`…）、计次（`第一次`/`首次`…）、
  以主角为施动者的动作句（`主角……了` / `被主角……`）。启发式，只报 warning，须人工复核
  （定性表述里合理出现的数字，用逐行豁免）。

首轮 warning——存量卡（如 苍玄 07_人物）迁移期间不阻断 `check.sh`；存量清零后可升 error。
只查 `02_数据库/07_人物/07_人物_*.md`（不含总索引）。主角姓名从主角档案推导
（`audit/novel_meta.py`），规则代码里不写任何一本书的专名。

**逐行豁免**：确需保留时在该行任意位置加 `<!-- CARDPLOT-ok: 理由 -->`。
"""
import re
from typing import List, Optional

from ..models import Finding, Severity
from ..engine import AuditRule
from ..context import AuditContext
from ..novel_meta import protagonist_name

_CARD_DIR = "02_数据库/07_人物/"
_CARD_PREFIX = "07_人物_"

REMOVED_FIELDS = {
    "当前进度": "动态状态（进度 / 阻碍）归状态层，人物卡不写；长期愿望写进「人生目标」",
    "主角对其的影响": "「主角对他做了什么」是事件描述，归卷纲【关系变化】与状态层，人物卡不写",
}
QUALITATIVE_FIELDS = ("高光时刻设计", "预计退场方式", "关系演变轨迹", "对主角的影响")

_NUM = r"[一二三四五六七八九十百千两半\d]+"
_QUANTITY_RE = re.compile(_NUM + r"\s*(?:个月|年|枚|次|层|天|日|颗|块)")
_COUNT_RE = re.compile(r"第[一二三四五六七八九十\d]+次|首次|再次|又一次")
_WAIVER = re.compile(r"<!--\s*CARDPLOT-ok:")


def _card_owner(rel: str) -> str:
    """`07_人物_<姓名>.md` → 姓名；扫描前从文本里剥掉，避免名字里的数字/量词字被当成事件特征。"""
    base = rel.split("/")[-1]
    return base[len(_CARD_PREFIX):-len(".md")] if base.startswith(_CARD_PREFIX) and base.endswith(".md") else ""


AGENT_EXEMPT_FIELDS = ("关系演变轨迹",)  # 关系形态里「被主角保护」之类是角色形态，不按施动者判


def _field_and_text(line: str):
    s = line.strip()
    if not s.startswith("|"):
        return None, ""
    cells = [c.strip() for c in s.strip("|").split("|")]
    if len(cells) < 2:
        return None, ""
    field = cells[0].strip("*` ")
    return field, " ".join(cells[1:])


def _agent_re(name: Optional[str]):
    """以主角为施动者的动作句：`@主角……了` / `主角……了` / `被主角……`（主角姓名从档案推导）。"""
    tokens = ["@主角", "主角"]
    if name:
        tokens.append(re.escape(name))
    alt = "|".join(tokens)
    return re.compile(rf"(?:{alt})[^|，。；;、\n「」（）()]{{0,8}}了|被\s*(?:{alt})")


class CardPlotRule(AuditRule):
    name = "card_plot"
    code_prefix = "CARDPLOT"

    def run(self, context: AuditContext) -> List[Finding]:
        findings: List[Finding] = []
        agent_re = _agent_re(protagonist_name(context))
        removed_hits: dict = {}
        event_hits: dict = {}

        for fi in context.files:
            rel = fi.relative_path
            if fi.data_domain != "02_数据库" or fi.file_type != "markdown":
                continue
            if not rel.startswith(_CARD_DIR) or not rel.split("/")[-1].startswith(_CARD_PREFIX):
                continue
            for idx, line in enumerate(fi.content.splitlines(), 1):
                if _WAIVER.search(line):
                    continue
                field, text = _field_and_text(line)
                if not field:
                    continue
                if field in REMOVED_FIELDS:
                    removed_hits.setdefault(rel, []).append((idx, field))
                elif field in QUALITATIVE_FIELDS:
                    owner = _card_owner(rel)
                    if owner:
                        text = text.replace(owner, "")
                    m = (_QUANTITY_RE.search(text) or _COUNT_RE.search(text)
                         or (None if field in AGENT_EXEMPT_FIELDS else agent_re.search(text)))
                    if m:
                        event_hits.setdefault(rel, []).append((idx, f"{field}：{m.group(0)}"))

        for rel, occ in sorted(removed_hits.items()):
            fields = sorted({f for _, f in occ})
            findings.append(Finding(
                severity=Severity.WARNING, rule=self.name, code="CARDPLOT001",
                message=f"人物卡含已取消字段行：{'、'.join(fields)}",
                file=rel, line=occ[0][0],
                suggestion="；".join(f"「{f}」{REMOVED_FIELDS[f]}" for f in fields)
                           + "。见 `04_人物模板`「设定层边界」。确需保留加 `<!-- CARDPLOT-ok: 理由 -->`",
                category="02_数据库",
                locations=[f"{rel}:第{ln}行（{f}）" for ln, f in occ],
            ))
        for rel, occ in sorted(event_hits.items()):
            findings.append(Finding(
                severity=Severity.WARNING, rule=self.name, code="CARDPLOT002",
                message=f"人物卡定性字段里出现事件特征（数量 / 计次 / 主角为施动者），{len(occ)} 处",
                file=rel, line=occ[0][0],
                suggestion="改写成弧线**定性**（类别 + 情感基调），具体经过归卷纲【章节节拍表】【关系变化】。"
                           "见 `04_人物模板`「设定层边界」对照表。合理保留加 `<!-- CARDPLOT-ok: 理由 -->`",
                category="02_数据库",
                locations=[f"{rel}:第{ln}行（{frag}）" for ln, frag in occ],
            ))
        return findings
