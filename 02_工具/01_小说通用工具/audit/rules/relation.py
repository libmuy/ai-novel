"""
关系对象一致性规则 (relation.py) — W5

对称关系（敌对/结盟/血亲/同门…）升为一等对象 `关系.<甲>&<乙>`：
- 分隔符必须是 ASCII `&`，恰好一个；
- 两端非空、互不相同；
- 两端名字必须按 Unicode 码位排序（`关系.<小>&<大>`）——排序保证同一对关系
  物理上只有一个文件，双边不一致从结构上不可能；
- 两端都必须是已注册对象（人物卡 / 势力卡，或状态对象白名单）。

- RELATION001 relation_id_malformed (error)：ID 不符合上述规范；报错给出规范形式。
- RELATION002 relation_dangling (warning)：关系一端在最新状态里已 `对象终态 ∈ {死亡, 退场}`，
  但关系对象自身未标 `对象终态 = 终结`——关系没跟着人一起了断。
"""
import re
import sys
from pathlib import Path
from typing import List, Set, Dict
from ..models import Finding, Severity
from ..engine import AuditRule
from ..context import AuditContext

WHITELIST_REL = "05_工作区/00_全局/00_状态对象白名单.md"
PROTAGONIST_NAME = "苏砚"
STATE_TREES = [
    "05_工作区/00_全局/00_基线状态",
    "05_工作区/00_全局/01_最新状态",
]
CHANGELOG_NAME = "04_本章状态履历.md"
CHAR_TERMINAL = {"死亡", "退场"}
REL_TERMINAL_OK = {"终结"}


def _load_state_tree_helpers():
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "00_系统级"))
        import state_tree
        return state_tree
    except Exception:
        return None


def _iter_state_rows(context: AuditContext):
    """yield (rel_path, lineno, object_id, field, value) over 基线/最新状态树 + 全部履历。"""
    for fi in context.files:
        rel = fi.relative_path
        is_tree = any(rel.startswith(t + "/") for t in STATE_TREES)
        is_changelog = rel.startswith("05_工作区/") and rel.endswith(CHANGELOG_NAME)
        if not (is_tree or is_changelog):
            continue
        if rel.rsplit("/", 1)[-1].startswith("00_"):
            continue
        for i, line in enumerate(fi.content.splitlines(), 1):
            st = line.strip()
            if not st.startswith("|"):
                continue
            parts = [p.strip() for p in st.split("|")[1:-1]]
            if len(parts) < 4:
                continue
            oid, field, _ftype, val = parts[0], parts[1], parts[2], parts[3]
            if oid in ("对象ID", "对象 ID", "ID") or oid.startswith(":-") or oid.startswith("---"):
                continue
            yield rel, i, oid, field, val


class RelationRule(AuditRule):
    name = "relation"
    code_prefix = "RELATION"

    def run(self, context: AuditContext) -> List[Finding]:
        findings: List[Finding] = []
        novel_dir = context.novel_dir
        st = _load_state_tree_helpers()

        # ---- 已注册对象名（人物卡 stem + 主角 + 势力卡 stem + 白名单端名）----
        char_names: Set[str] = {PROTAGONIST_NAME}
        fac_names: Set[str] = set()
        for fi in context.files:
            rel = fi.relative_path
            fn = rel.rsplit("/", 1)[-1]
            if rel.startswith("02_数据库/07_人物/") and fn.startswith("07_人物_") and fn.endswith(".md"):
                char_names.add(fn[len("07_人物_"):-3])
            elif rel.startswith("02_数据库/03_势力组织/") and fn.startswith("03_势力组织_") and fn.endswith(".md"):
                fac_names.add(fn[len("03_势力组织_"):-3])

        whitelist_ends: Set[str] = set()
        wl_fi = context.file_map.get(WHITELIST_REL)
        if wl_fi:
            for line in wl_fi.content.splitlines():
                s = line.strip()
                if not s.startswith("|"):
                    continue
                first = s.split("|")[1].strip() if len(s.split("|")) > 1 else ""
                if "." in first and not first.startswith((":-", "---")):
                    whitelist_ends.add(first.split(".", 1)[1])

        known_ends = char_names | fac_names | whitelist_ends

        # ---- RELATION001：扫所有 关系. 对象 ----
        malformed: List[str] = []
        seen_rel_ids: Set[str] = set()
        for rel, ln, oid, _field, _val in _iter_state_rows(context):
            if not oid.startswith("关系."):
                continue
            seen_rel_ids.add(oid)
            reason = None
            canon = None
            if st is not None:
                try:
                    a, b = st.split_relation_id(oid)
                    canon = st.normalize_relation_id(oid)
                    if canon != oid:
                        reason = f"两端未按 Unicode 序排列，规范形式应为 `{canon}`"
                    else:
                        missing = [e for e in (a, b) if e not in known_ends]
                        if missing:
                            reason = f"端「{'、'.join(missing)}」不是已注册对象（人物卡/势力卡/白名单）"
                except st.RelationIdError as e:
                    reason = str(e).split(":", 1)[-1].strip()
            else:
                body = oid.split(".", 1)[1]
                if body.count("&") != 1:
                    reason = "关系 ID 必须恰好含一个 ASCII `&`"
            if reason:
                malformed.append(f"{rel}:{ln} （{oid}）：{reason}")

        if malformed:
            findings.append(Finding(
                severity=Severity.ERROR, rule=self.name, code="RELATION001",
                message=f"发现 {len(malformed)} 处关系对象 ID 不符合 `关系.<甲>&<乙>` 规范",
                file=None,
                suggestion=("关系 ID：恰一个 ASCII `&`、两端非空且互不相同、两端按 Unicode 序排列、"
                            "两端均为已注册对象。对称关系（敌对/结盟/血亲/同门）才建关系对象；"
                            "非对称（持有/师承/隶属）记在从属方字段上。"),
                category="05_工作区", locations=malformed,
            ))

        # ---- RELATION002：关系悬挂（一端已终态、关系未标终结）----
        if st is not None:
            latest = novel_dir / "05_工作区" / "00_全局" / "01_最新状态"
            if latest.exists():
                recs = st.load_state_tree(str(latest))
                dead_ends: Set[str] = set()
                for r in recs:
                    if r["field"] == "对象终态" and r["value"] in CHAR_TERMINAL:
                        oid = r["object_id"]
                        if "." in oid:
                            dead_ends.add(oid.split(".", 1)[1])
                rel_term: Dict[str, str] = {}
                rel_ids: Set[str] = set()
                for r in recs:
                    if not r["object_id"].startswith("关系."):
                        continue
                    rel_ids.add(r["object_id"])
                    if r["field"] == "对象终态":
                        rel_term[r["object_id"]] = r["value"]
                dangling = []
                for rid in sorted(rel_ids):
                    try:
                        a, b = st.split_relation_id(rid)
                    except st.RelationIdError:
                        continue
                    hit = {e for e in (a, b) if e in dead_ends}
                    if hit and rel_term.get(rid) not in REL_TERMINAL_OK:
                        dangling.append(f"{rid}（端「{'、'.join(sorted(hit))}」已终态，关系未标「终结」）")
                if dangling:
                    findings.append(Finding(
                        severity=Severity.WARNING, rule=self.name, code="RELATION002",
                        message=f"发现 {len(dangling)} 处关系对象的一端已死亡/退场，但关系未标终态",
                        file=None,
                        suggestion=("在该端角色死亡/退场那一章的履历里，为对应关系对象补一行 "
                                    "`关系.甲&乙 | 对象终态 | 运算-枚举 | 终结`（或说明关系为何仍活跃）。"),
                        category="05_工作区", locations=dangling,
                    ))

        return findings
