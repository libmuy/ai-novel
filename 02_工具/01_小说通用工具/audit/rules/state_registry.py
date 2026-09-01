"""
状态对象/字段注册一致性 (state_registry.py) — W4.3

履历里写 `角色.苏彦`（笔误），旧流程静默新建一个影子对象、零告警——千万字尺度下
这是第一大漂移源。本规则把「状态里出现的对象/字段」与「数据库卡片 + 动态字段清单」
对齐：

- STATE022 state_object_unregistered (error)：状态对象在对应数据库分类找不到同名卡片，
  且不在 05_工作区/00_全局/00_状态对象白名单.md 里。
- STATE023 state_field_undeclared (error)：状态字段未在该对象卡片的「## 动态字段清单」里声明。
- STATE024 state_object_stateless (info)：卡片声明了动态字段清单、但对象从未进入任何状态
  （多数是尚未登场的对象，属正常；聚合成一条）。

只检查 角色 / 势力（有动态字段清单的两类）+ 物品/财务的对象名存在性。
"""
import re
from pathlib import Path
from typing import List, Dict, Set
from ..models import Finding, Severity
from ..engine import AuditRule
from ..context import AuditContext

PROTAGONIST_NAME = "苏砚"
WHITELIST_REL = "05_工作区/00_全局/00_状态对象白名单.md"

STATE_TREES = [
    "05_工作区/00_全局/00_基线状态",
    "05_工作区/00_全局/01_最新状态",
]
CHANGELOG_NAME = "04_本章状态履历.md"

_DYNSEC_RE = re.compile(r"^#{1,4}\s*动态字段清单\s*$")


def _parse_dynamic_fields(text: str):
    """卡片正文里的「## 动态字段清单」→ set(字段名)。无该小节返回 None。"""
    in_sec = False
    fields: Set[str] = set()
    for line in text.splitlines():
        st = line.strip()
        if _DYNSEC_RE.match(st):
            in_sec = True
            continue
        if not in_sec:
            continue
        if st.startswith("#"):
            break
        if not st.startswith("|"):
            continue
        parts = [p.strip() for p in st.split("|")[1:-1]]
        if len(parts) < 2:
            continue
        head = parts[0].replace("*", "")
        if head in ("字段", "字段名") or head.startswith(":-") or head.startswith("---"):
            continue
        fields.add(head)
    return fields if in_sec else None


def _iter_state_rows(context: AuditContext):
    """yield (rel_path, lineno, object_id, field) over 基线/最新状态树 + 全部履历。"""
    for fi in context.files:
        rel = fi.relative_path
        is_tree = any(rel.startswith(t + "/") for t in STATE_TREES)
        is_changelog = rel.startswith("05_工作区/") and rel.endswith(CHANGELOG_NAME)
        if not (is_tree or is_changelog):
            continue
        if rel.rsplit("/", 1)[-1].startswith("00_"):
            continue  # 说明 / 同步状态 / 索引前缀
        for i, line in enumerate(fi.content.splitlines(), 1):
            st = line.strip()
            if not st.startswith("|"):
                continue
            parts = [p.strip() for p in st.split("|")[1:-1]]
            if len(parts) < 4:
                continue
            oid, field = parts[0], parts[1]
            if oid in ("对象ID", "对象 ID", "ID") or oid.startswith(":-") or oid.startswith("---"):
                continue
            yield rel, i, oid, field


class StateRegistryRule(AuditRule):
    name = "state_registry"
    code_prefix = "STATE"

    def run(self, context: AuditContext) -> List[Finding]:
        findings: List[Finding] = []
        novel_dir = context.novel_dir

        # ---- 建卡片索引 ----
        char_cards: Dict[str, Set] = {}   # 名 -> 声明字段集 (或 None = 无清单)
        fac_cards: Dict[str, Set] = {}
        item_names: Set[str] = set()

        def card_text(rel: str):
            fi = context.file_map.get(rel)
            if fi:
                return fi.content
            p = novel_dir / rel
            return p.read_text(encoding="utf-8", errors="ignore") if p.exists() else ""

        # 主角
        prot = card_text("01_设定/00_主角档案.md")
        if prot:
            char_cards[PROTAGONIST_NAME] = _parse_dynamic_fields(prot)

        for fi in context.files:
            rel = fi.relative_path
            fn = rel.rsplit("/", 1)[-1]
            if rel.startswith("02_数据库/07_人物/") and fn.startswith("07_人物_") and fn.endswith(".md"):
                name = fn[len("07_人物_"):-3]
                if name:
                    char_cards[name] = _parse_dynamic_fields(fi.content)
            elif rel.startswith("02_数据库/03_势力组织/") and fn.startswith("03_势力组织_") and fn.endswith(".md"):
                name = fn[len("03_势力组织_"):-3]
                if name:
                    fac_cards[name] = _parse_dynamic_fields(fi.content)
            elif rel.startswith("02_数据库/04_资源/") and fn.startswith("04_资源_") and fn.endswith(".md"):
                for m in re.finditer(r"【物品卡】\s*名称[:：]\s*([^/\n]+?)\s*/", fi.content):
                    item_names.add(m.group(1).strip())

        # ---- 白名单 ----
        whitelist: Set[str] = set()
        wl_text = card_text(WHITELIST_REL)
        for line in wl_text.splitlines():
            st = line.strip()
            if not st.startswith("|"):
                continue
            first = st.split("|")[1].strip() if len(st.split("|")) > 1 else ""
            if "." in first and not first.startswith((":-", "---")):
                whitelist.add(first)

        char_names = set(char_cards)
        fac_names = set(fac_cards)
        fin_names = char_names | fac_names

        # ---- 扫状态行 ----
        unregistered: List[str] = []
        undeclared: List[str] = []
        seen_objects: Set[str] = set()

        for rel, ln, oid, field in _iter_state_rows(context):
            if oid in whitelist:
                seen_objects.add(oid)
                continue
            if "." not in oid:
                continue
            prefix, name = oid.split(".", 1)
            prefix, name = prefix.strip(), name.strip()
            seen_objects.add(oid)

            ok = True
            declared = None
            if prefix == "角色":
                ok = name in char_names
                declared = char_cards.get(name)
            elif prefix == "势力":
                ok = name in fac_names
                declared = fac_cards.get(name)
            elif prefix == "物品":
                ok = name in item_names
            elif prefix == "财务":
                ok = name in fin_names
            elif prefix == "关系":
                # 关系对象：两端都必须是已注册对象（格式错误由 relation 规则的 RELATION001 报）
                if name.count("&") == 1:
                    ends = [e.strip() for e in name.split("&")]
                    ok = all(e in fin_names for e in ends if e)
                else:
                    continue  # 格式非法，交给 RELATION001
            else:
                continue  # 世界/其他前缀：交给 state.py 的 STATE009

            if not ok:
                unregistered.append(f"{rel}:{ln} ({oid})")
                continue
            if declared is not None and field not in declared:
                undeclared.append(f"{rel}:{ln} ({oid} 的「{field}」未在动态字段清单声明)")

        if unregistered:
            findings.append(Finding(
                severity=Severity.ERROR, rule=self.name, code="STATE022",
                message=f"发现 {len(unregistered)} 处状态对象在数据库分类里找不到同名卡片",
                file=None,
                suggestion=("确认对象ID拼写；若确为群像/临时/背景对象，登记到 "
                            "05_工作区/00_全局/00_状态对象白名单.md 放行"),
                category="05_工作区", locations=unregistered,
            ))
        if undeclared:
            findings.append(Finding(
                severity=Severity.ERROR, rule=self.name, code="STATE023",
                message=f"发现 {len(undeclared)} 处状态字段未在对象卡片的「## 动态字段清单」里声明",
                file=None,
                suggestion="在该卡片的动态字段清单补声明该字段（字段名/类型/基线初值），或修正履历里的字段名",
                category="05_工作区", locations=undeclared,
            ))

        # ---- STATE024 聚合 ----
        stateless = []
        for name, declared in char_cards.items():
            if declared and f"角色.{name}" not in seen_objects:
                stateless.append(f"角色.{name}")
        for name, declared in fac_cards.items():
            if declared and f"势力.{name}" not in seen_objects:
                stateless.append(f"势力.{name}")
        if stateless:
            findings.append(Finding(
                severity=Severity.INFO, rule=self.name, code="STATE024",
                message=(f"{len(stateless)} 个对象声明了动态字段清单、但从未进入任何状态"
                         "（多为尚未登场的对象，属正常）"),
                file=None, suggestion="无需操作；若某对象本应已有状态，检查其首次登场章履历是否漏写「新建」",
                category="05_工作区", locations=sorted(stateless),
            ))

        return findings
