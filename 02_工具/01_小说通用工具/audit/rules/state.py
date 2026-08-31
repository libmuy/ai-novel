"""
状态检查规则 (state.py)
校验 05_工作区/ 履历语法、字段词表匹配、全局状态树、级联冲突、状态漂移
"""
import re
import sys
from pathlib import Path
from collections import defaultdict
from typing import List, Dict, Any, Optional
from ..models import Finding, Severity
from ..engine import AuditRule
from ..context import AuditContext

VALID_MERGE_TYPES = {"运算-数值", "运算-枚举", "运算-列表", "描述"}
STATE_OBJECT_PREFIX_CATEGORY = {
    "角色": "01_角色", "物品": "02_物品", "势力": "03_势力",
    "财务": "04_财务", "世界": "05_世界",
}
CHAR_DEAD_STATES = {"死亡", "退场"}
ITEM_DEAD_STATES = {"损毁", "易主"}


def _load_state_helpers():
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent / "00_系统级"))
        import state_tree
        return state_tree
    except Exception:
        return None


def _load_field_vocab(novel_dir: Path, context: AuditContext) -> Dict[str, str]:
    vocab_rel = "00_通用模板/03_字段词表.md"
    fi = context.file_map.get(vocab_rel)
    if not fi:
        vocab_path = novel_dir / vocab_rel
        if not vocab_path.exists():
            vocab_path = novel_dir.parent.parent / vocab_rel
        if not vocab_path.exists():
            return {}
        text = vocab_path.read_text(encoding="utf-8", errors="ignore")
    else:
        text = fi.content

    fields = {}
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("|") or "---" in line or "字段名" in line:
            continue
        parts = [p.strip() for p in line.split("|")[1:-1]]
        if len(parts) >= 2:
            raw_fname = parts[0]
            clean_fname = raw_fname.replace("**", "").strip()
            ftype = parts[1]
            fields[clean_fname] = ftype
    return fields


class StateRule(AuditRule):
    name = "state"
    code_prefix = "STATE"

    def run(self, context: AuditContext) -> List[Finding]:
        findings: List[Finding] = []
        novel_dir = context.novel_dir
        vocab = _load_field_vocab(novel_dir, context)

        # 1. 检查状态树
        self._check_state_tree(context, novel_dir / "05_工作区" / "00_全局" / "01_最新状态", "全局状态", vocab, findings)
        self._check_state_tree(context, novel_dir / "05_工作区" / "00_全局" / "00_基线状态", "基线", vocab, findings)

        # 2. 检查章级状态履历语法
        workspace_dir = novel_dir / "05_工作区"
        if workspace_dir.exists():
            invalid_fields, invalid_syntax, type_mismatches = [], [], []
            for fi in context.files:
                if fi.relative_path.startswith("05_工作区/") and fi.relative_path.endswith("04_本章状态履历.md"):
                    rel = fi.relative_path
                    lines = fi.content.splitlines()
                    for i, line in enumerate(lines):
                        line = line.strip()
                        if not line.startswith("|") or "---" in line or "对象ID" in line:
                            continue
                        parts = [p.strip() for p in line.split("|")[1:-1]]
                        if len(parts) >= 4:
                            obj_id, field, ftype, val = parts[0], parts[1], parts[2], parts[3]
                            if vocab and field not in vocab:
                                invalid_fields.append(f"{rel}:{i+1} ({field})")
                            canon_type = vocab.get(field) if vocab else None
                            if (canon_type in VALID_MERGE_TYPES and ftype in VALID_MERGE_TYPES and ftype != canon_type):
                                type_mismatches.append(f"{rel}:{i+1} ({field}: 表内写「{ftype}」，词表登记「{canon_type}」)")
                            if ftype == "运算-数值":
                                if not (val.startswith("+") or val.startswith("-") or val.isdigit()):
                                    invalid_syntax.append(f"{rel}:{i+1} (数值履历缺乏 +/- 前缀: '{val}')")
                            elif ftype == "运算-列表":
                                if not (val.startswith("+") or val.startswith("-") or val in ["无", "空"]):
                                    invalid_syntax.append(f"{rel}:{i+1} (列表履历格式非 +X,-Y: '{val}')")
                                elif "," in val:
                                    items = [x.strip() for x in val.split(",") if x.strip()]
                                    for item in items:
                                        if not item.startswith("+") and not item.startswith("-"):
                                            invalid_syntax.append(f"{rel}:{i+1} (列表元素缺少 +/- 前缀: '{item}')")

            if invalid_fields:
                findings.append(Finding(
                    severity=Severity.ERROR, rule=self.name, code="STATE001",
                    message=f"发现 {len(invalid_fields)} 处章级状态使用了未在 03_字段词表.md 中登记的字段",
                    file=None, suggestion="对照 00_通用模板/03_字段词表.md 修正字段名，或在词表中补充注册",
                    category="05_工作区", locations=invalid_fields
                ))
            if invalid_syntax:
                findings.append(Finding(
                    severity=Severity.ERROR, rule=self.name, code="STATE002",
                    message=f"发现 {len(invalid_syntax)} 处章级状态履历未遵循固定计算语法",
                    file=None, suggestion="修正履历表中的值语法：运算-数值须写 +N/-N，运算-列表须写 +X,-Y",
                    category="05_工作区", locations=invalid_syntax
                ))
            if type_mismatches:
                findings.append(Finding(
                    severity=Severity.WARNING, rule=self.name, code="STATE003",
                    message=f"发现 {len(type_mismatches)} 处章级状态的「类型」列与 03_字段词表.md 登记的权威类型不符",
                    file=None, suggestion="以 00_通用模板/03_字段词表.md 为准修正表内「类型」列；若词表登记本身有误，先订正词表。",
                    category="05_工作区", locations=type_mismatches
                ))

        # 3. 解析性与高级比对 (使用 state_tree 模块)
        stmod = _load_state_helpers()
        if stmod:
            self._check_changelog_parseable(context, novel_dir, stmod, findings)
            self._check_no_legacy_state_files(context, novel_dir, findings)
            self._check_state_drift(context, novel_dir, stmod, findings)
            self._check_state_unmerged_chapters(context, novel_dir, stmod, findings)
            self._check_cascade_terminal_conflicts(context, novel_dir, stmod, findings)
            self._check_stale_descriptive_merge(context, novel_dir, stmod, findings)

        return findings

    def _check_state_tree(self, context: AuditContext, state_dir: Path, label: str, vocab: dict, findings: list):
        if not state_dir.exists():
            return
        unparse, header_bad, field_bad, type_bad, misfiled, unknown_prefix = [], [], [], [], [], []
        index_bad = []
        for cat_dir in sorted(p for p in state_dir.iterdir() if p.is_dir()):
            cat = cat_dir.name
            seen_objs = set()
            for f in sorted(cat_dir.glob("*.md")):
                if f.name == f"{cat}.md" or f.name.startswith("00_"):
                    continue
                rel = f.relative_to(context.novel_dir).as_posix()
                fi = context.file_map.get(rel)
                text = fi.content if fi else f.read_text(encoding="utf-8", errors="ignore")
                m = re.search(r"^>\s*对象ID:\s*(\S.*?)\s*$", text, re.M)
                header_id = m.group(1) if m else None
                rows = []
                for i, line in enumerate(text.splitlines()):
                    line = line.strip()
                    if not line.startswith("|") or "---" in line or "对象ID" in line:
                        continue
                    parts = [p.strip() for p in line.split("|")[1:-1]]
                    if len(parts) < 4:
                        continue
                    rows.append((i + 1, parts[0], parts[1], parts[2]))
                if not rows:
                    unparse.append(rel)
                    continue
                for ln, obj_id, field, ftype in rows:
                    seen_objs.add(obj_id)
                    if header_id and obj_id != header_id:
                        header_bad.append(f"{rel}:{ln} (行对象ID「{obj_id}」≠ 头「{header_id}」)")
                    if vocab and field not in vocab:
                        field_bad.append(f"{rel}:{ln} ({field})")
                    canon = vocab.get(field) if vocab else None
                    if canon in VALID_MERGE_TYPES and ftype in VALID_MERGE_TYPES and ftype != canon:
                        type_bad.append(f"{rel}:{ln} ({field}: 「{ftype}」≠ 词表「{canon}」)")
                    prefix = obj_id.split(".", 1)[0].strip()
                    expect_cat = STATE_OBJECT_PREFIX_CATEGORY.get(prefix)
                    if expect_cat is None:
                        if cat != "99_其他":
                            unknown_prefix.append(f"{rel}:{ln} (前缀「{prefix}」不属五大类且不在 99_其他/)")
                    elif expect_cat != cat:
                        misfiled.append(f"{rel}:{ln} (对象「{obj_id}」应在 {expect_cat}/，实际在 {cat}/)")
            idx_file = cat_dir / f"{cat}.md"
            if idx_file.exists() and seen_objs:
                idx_rel = idx_file.relative_to(context.novel_dir).as_posix()
                idx_fi = context.file_map.get(idx_rel)
                idx_text = idx_fi.content if idx_fi else idx_file.read_text(encoding="utf-8", errors="ignore")
                listed = set(re.findall(r"^\|\s*([^|]+?)\s*\|\s*\d+\s*\|", idx_text, re.M))
                if listed and listed != seen_objs:
                    index_bad.append(f"{idx_rel} (索引对象集与实际不符: 缺 {sorted(seen_objs - listed)} 多 {sorted(listed - seen_objs)})")

        def _add(code, sev, items, detail, action):
            if items:
                findings.append(Finding(
                    severity=sev, rule=self.name, code=code,
                    message=f"[{label}] " + detail.format(n=len(items)),
                    file=None, suggestion=action, category="01_最新状态", locations=items
                ))

        _add("STATE004", Severity.ERROR, unparse, "{n} 个对象文件没有可解析的状态表", "确认文件被脚本正常写入；必要时重跑 rebuild_global_state.py")
        _add("STATE005", Severity.ERROR, header_bad, "{n} 处对象文件表格行的对象ID与文件头 `> 对象ID:` 不一致", "该文件疑似被手改；重跑 rebuild_global_state.py")
        _add("STATE006", Severity.ERROR, field_bad, "{n} 处使用了未在 03_字段词表.md 登记的字段", "对照词表修正，或在词表补充注册")
        _add("STATE007", Severity.WARNING, type_bad, "{n} 处「类型」列与 03_字段词表.md 登记的权威类型不符", "以词表为准修正来源履历/基线的类型列")
        _add("STATE008", Severity.ERROR, misfiled, "{n} 处对象文件放错了类目目录", "重跑 rebuild_global_state.py 让脚本按前缀归位")
        _add("STATE009", Severity.WARNING, unknown_prefix, "{n} 处对象前缀不属于 角色/物品/势力/财务/世界", "确认对象ID前缀书写；非五大类对象归入 99_其他/")
        _add("STATE010", Severity.WARNING, index_bad, "{n} 个类目索引与实际对象文件不同步", "重跑 rebuild_global_state.py 重建索引")

    def _check_changelog_parseable(self, context: AuditContext, novel_dir: Path, stmod: Any, findings: list):
        unparseable = []
        ws = novel_dir / "05_工作区"
        if ws.exists():
            for fi in context.files:
                if fi.relative_path.startswith("05_工作区/") and fi.relative_path.endswith("04_本章状态履历.md"):
                    try:
                        stmod.parse_md_table(str(fi.file_path), strict=True)
                    except stmod.StateMergeError as e:
                        unparseable.append(f"{fi.relative_path}: {e}")
        for state_dir_rel in ["05_工作区/00_全局/00_基线状态", "05_工作区/00_全局/01_最新状态"]:
            state_dir = novel_dir / state_dir_rel
            if not state_dir.exists():
                continue
            for fi in context.files:
                if fi.relative_path.startswith(state_dir_rel):
                    try:
                        stmod.parse_md_table(str(fi.file_path), strict=True)
                    except stmod.StateMergeError as e:
                        unparseable.append(f"{fi.relative_path}: {e}")
        if unparseable:
            findings.append(Finding(
                severity=Severity.ERROR, rule=self.name, code="STATE011",
                message=f"发现 {len(unparseable)} 个状态文件存在无法解析的行（列数不符或类型非法）",
                file=None, suggestion="修正对应文件中的非法行：检查列数是否为 4 或 7，类型列是否为合法值",
                category="05_工作区", locations=unparseable
            ))

    def _check_no_legacy_state_files(self, context: AuditContext, novel_dir: Path, findings: list):
        legacy_03 = [fi.relative_path for fi in context.files if fi.relative_path.startswith("05_工作区/") and fi.relative_path.endswith("03_本章初始状态.md")]
        if legacy_03:
            findings.append(Finding(
                severity=Severity.ERROR, rule=self.name, code="STATE012",
                message=f"发现 {len(legacy_03)} 个已废弃的逐章 03_本章初始状态.md（新模型无逐章初始状态）",
                file=None, suggestion="删除这些文件；状态起点统一读 01_最新状态/，改旧章用 build_state_snapshot.py --at-chapter",
                category="05_工作区", locations=legacy_03
            ))
        gdir = novel_dir / "05_工作区" / "00_全局" / "01_最新状态"
        flat = sorted(p.name for p in gdir.glob("0[1-5]_*状态.md")) if gdir.exists() else []
        if flat:
            findings.append(Finding(
                severity=Severity.ERROR, rule=self.name, code="STATE013",
                message=f"01_最新状态/ 下有 {len(flat)} 个已废弃的扁平分类文件: {flat}",
                file=None, suggestion="运行 migrate_state_layout.py 迁移为每对象一文件的目录树",
                category="01_最新状态", locations=[f"01_最新状态/{n}" for n in flat]
            ))

    def _check_state_drift(self, context: AuditContext, novel_dir: Path, stmod: Any, findings: list):
        baseline = novel_dir / "05_工作区" / "00_全局" / "00_基线状态"
        live = novel_dir / "05_工作区" / "00_全局" / "01_最新状态"
        if not baseline.exists():
            findings.append(Finding(
                severity=Severity.WARNING, rule=self.name, code="STATE014",
                message="冻结基线 05_工作区/00_全局/00_基线状态/ 不存在，无法校验全局状态一致性",
                file=None, suggestion="新书用技能 08_基线状态初始化 生成基线；旧书用 migrate_state_layout.py 迁移",
                category="01_最新状态", locations=[]
            ))
            return
        if not live.exists():
            return

        all_paths = stmod.iter_workspace_changelogs(str(novel_dir))
        folded = stmod.parse_manifest_folded_chapter(str(live))
        if folded is None:
            paths = []
        else:
            names = [stmod.chapter_rel_name(p, str(novel_dir)) for p in all_paths]
            paths = all_paths if folded not in names else all_paths[: names.index(folded) + 1]

        try:
            expected, _wb = stmod.fold_all(str(baseline), paths, resolver=None)
        except stmod.StateMergeError as e:
            findings.append(Finding(
                severity=Severity.ERROR, rule=self.name, code="STATE015",
                message=f"从基线折叠已并入的履历时失败: {e}",
                file=None, suggestion="已标记「折叠至章」却有未冻结的描述行，属异常；对涉及章重跑 merge_chapter_state.py",
                category="01_最新状态", locations=[]
            ))
            return

        diff = stmod.records_diff(stmod.load_state_tree(str(live)), expected)
        if diff:
            findings.append(Finding(
                severity=Severity.WARNING, rule=self.name, code="STATE016",
                message=f"01_最新状态/ 与「基线 ⊕ 已并入履历（折叠至 {folded}）」的折叠结果不一致，共 {len(diff)} 处",
                file=None, suggestion="运行 python3 02_工具/01_小说通用工具/rebuild_global_state.py <小说目录>（先 --dry-run 复核）",
                category="01_最新状态", locations=diff
            ))

    def _check_state_unmerged_chapters(self, context: AuditContext, novel_dir: Path, stmod: Any, findings: list):
        all_paths = stmod.iter_workspace_changelogs(str(novel_dir))
        if not all_paths:
            return
        folded = stmod.parse_manifest_folded_chapter(str(novel_dir / "05_工作区" / "00_全局" / "01_最新状态"))
        names = [stmod.chapter_rel_name(p, str(novel_dir)) for p in all_paths]
        if folded is None:
            unmerged = names
        elif folded in names:
            unmerged = names[names.index(folded) + 1:]
        else:
            unmerged = names
        if unmerged:
            findings.append(Finding(
                severity=Severity.INFO, rule=self.name, code="STATE017",
                message=f"{len(unmerged)} 章的履历尚未并入 01_最新状态/",
                file=None, suggestion="对每章按顺序运行 python3 02_工具/01_小说通用工具/merge_chapter_state.py --chapter-dir <章目录>",
                category="01_最新状态", locations=unmerged
            ))

    def _check_cascade_terminal_conflicts(self, context: AuditContext, novel_dir: Path, stmod: Any, findings: list):
        baseline = novel_dir / "05_工作区" / "00_全局" / "00_基线状态"
        changelogs = stmod.iter_workspace_changelogs(str(novel_dir))
        if not baseline.exists() or len(changelogs) < 2:
            return

        curr = stmod.load_state_tree(str(baseline))
        dead, item_term = {}, {}
        char_conflicts, item_conflicts = [], []

        for cl_path in changelogs:
            rel = stmod.chapter_rel_name(cl_path, str(novel_dir))
            cl = stmod.parse_md_table(cl_path)

            for r in cl:
                oid, field = r["object_id"], r["field"]
                if oid in dead:
                    char_conflicts.append(f"{rel}: {oid} 已于「{dead[oid]}」标记终态，仍变更字段「{field}」")
                if oid in item_term:
                    term_state, ch = item_term[oid]
                    item_conflicts.append(f"{rel}: {oid} 已于「{ch}」标记「{term_state}」，仍变更字段「{field}」")
                if field == "持有物品" and str(r.get("type", "")).startswith("运算-列表"):
                    for op in r["value"].split(","):
                        op = op.strip()
                        if op.startswith("+"):
                            key = f"物品.{op[1:].strip()}"
                            if key in item_term:
                                term_state, ch = item_term[key]
                                item_conflicts.append(f"{rel}: {oid} 重新持有「{op[1:].strip()}」，但该物品已于「{ch}」标记「{term_state}」")

            try:
                curr, _ = stmod.merge_states(curr, cl)
            except Exception as e:
                findings.append(Finding(
                    severity=Severity.ERROR, rule=self.name, code="STATE018",
                    message=f"{rel} 履历合并失败，级联检查中断: {e}",
                    file=rel, suggestion="修正该章 04_本章状态履历.md 中的非法值后重跑。",
                    category="05_工作区", locations=[rel]
                ))
                return

            chap_name = rel
            for rec in curr:
                oid = rec["object_id"]
                if rec["field"] == "对象终态":
                    if rec["value"] in CHAR_DEAD_STATES:
                        dead.setdefault(oid, chap_name)
                    elif oid in dead:
                        del dead[oid]
                elif rec["field"] == "物品状态":
                    if rec["value"] in ITEM_DEAD_STATES:
                        item_term.setdefault(oid, (rec["value"], chap_name))
                    elif oid in item_term:
                        del item_term[oid]

        if char_conflicts:
            findings.append(Finding(
                severity=Severity.ERROR, rule=self.name, code="STATE019",
                message=f"发现 {len(char_conflicts)} 处：角色终态（死亡/退场）之后仍有履历变更",
                file=None, suggestion="核对被修改的早期章节情节，修正对应 04 履历后重跑 rebuild_global_state.py",
                category="05_工作区", locations=char_conflicts
            ))
        if item_conflicts:
            findings.append(Finding(
                severity=Severity.ERROR, rule=self.name, code="STATE020",
                message=f"发现 {len(item_conflicts)} 处：物品终态（损毁/易主）之后仍被变更或被原持有者使用",
                file=None, suggestion="核对物品损毁/易主的章节，修正对应 04 履历后重跑 rebuild_global_state.py",
                category="05_工作区", locations=item_conflicts
            ))

    def _check_stale_descriptive_merge(self, context: AuditContext, novel_dir: Path, stmod: Any, findings: list):
        baseline = novel_dir / "05_工作区" / "00_全局" / "00_基线状态"
        if not baseline.exists():
            return
        changelogs = stmod.iter_workspace_changelogs(str(novel_dir))
        if not changelogs:
            return
        cache = stmod.load_merge_cache(str(novel_dir))
        try:
            stmod.fold_all(str(baseline), changelogs, cache=cache, resolver=None)
        except stmod.StateMergeError as e:
            findings.append(Finding(
                severity=Severity.ERROR, rule=self.name, code="STATE021",
                message=f"存在未命中合并缓存的描述字段变更: {e}",
                file=None, suggestion="运行 python3 02_工具/01_小说通用工具/rebuild_global_state.py --merge-pending <小说目录>",
                category="01_最新状态", locations=[]
            ))
