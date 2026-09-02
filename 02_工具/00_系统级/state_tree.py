#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
状态树共享库 (state_tree.py)

本模块承载「按对象拆分、按类目分层」的全局状态存储的全部纯逻辑，供
merge_chapter_state.py / rebuild_global_state.py / build_state_snapshot.py /
audit_consistency.py 共用。**纯 Python 标准库、无网络、无 LLM。**

数据模型
--------
- 唯一权威状态存储 = 小说目录下 `05_工作区/02_状态/01_最新状态/`，按对象类别分子目录：
    05_工作区/02_状态/01_最新状态/
      ├── 00_说明.md            手写
      ├── 00_同步状态.md         本模块写（manifest，非权威）
      ├── 01_角色/  01_角色.md（索引）  01_角色_<名>.md ...
      ├── 02_物品/  ...
      ├── 03_势力/  ...
      ├── 04_财务/  ...
      ├── 05_世界/  ...
      └── 99_其他/  ...          前缀不属五大类的兜底
- 冻结基线 = `05_工作区/02_状态/00_基线状态/`，布局同上，只读不可变。
- `05_工作区/02_状态/01_最新状态/` == 基线 ⊕（按章节路径顺序折叠全部 `01_状态履历.md`）。
- 每章目录只有 `01_状态履历.md`（7 列：前 4 列权威 + 章节号/变更时间/变更类型 元数据）。
"""

import os
import re
import hashlib
import json
from collections import OrderedDict
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

WORKSPACE_DIRNAME = "05_工作区"
BASELINE_SUBPATH = os.path.join("05_工作区", "02_状态", "00_基线状态")
LATEST_STATE_SUBPATH = os.path.join("05_工作区", "02_状态", "01_最新状态")
CHANGELOG_FILENAME = "01_状态履历.md"
LEGACY_CHAPTER_STATE_FILENAME = "03_本章初始状态.md"
MANIFEST_FILENAME = "00_同步状态.md"
NONE_MARKER = "__none__"

# 01_状态履历.md 在标准 4 列之后追加的元数据列（读入但不参与合并计算）
CHANGELOG_META_COLUMNS = ['章节号', '变更时间', '变更类型']

# 运算-数值字段允许的「空值」写法，一律按 0 处理（不视为解析失败）
NUMERIC_EMPTY_VALUES = {'', '无', '空', '-', '—', '~', 'null', 'N/A', '/'}

# 合并脚本认可的 4 种字段细分类型（与 audit_consistency.VALID_MERGE_TYPES 保持一致）
VALID_MERGE_TYPES = {"运算-数值", "运算-枚举", "运算-列表", "描述"}

# 描述合并缓存
MERGE_CACHE_FILENAME = "04_描述合并缓存.jsonl"

# 对象 ID 前缀 -> 05_工作区/02_状态/01_最新状态/ 下的类目目录名
CATEGORY_BY_PREFIX = {
    "角色": "01_角色",
    "物品": "02_物品",
    "势力": "03_势力",
    "财务": "04_财务",
    "世界": "05_世界",
    "关系": "06_关系",
}
OTHER_CATEGORY = "99_其他"
CATEGORY_ORDER = ["01_角色", "02_物品", "03_势力", "04_财务", "05_世界", "06_关系", OTHER_CATEGORY]
CATEGORY_LABELS = {
    "01_角色": "角色", "02_物品": "物品", "03_势力": "势力",
    "04_财务": "财务", "05_世界": "世界", "06_关系": "关系", OTHER_CATEGORY: "其他",
}

# 对称关系一等对象 `关系.<甲>&<乙>` 的分隔符。必须是 ASCII `&`：
# 文件系统安全、Markdown 无义、不在 _UNSAFE_NAME_CHARS 里、不与运算-列表语法
# （+ - ,）冲突、不与文件名碰撞后缀（~）冲突、不与表格分隔符（| 及会被 NFKC
# 折成 | 的全角 ｜）冲突。两端名字按 Unicode 码位排序后拼接——排序保证同一对
# 关系物理上只有一个文件，双边不一致从结构上不可能。
RELATION_SEP = "&"
RELATION_PREFIX = "关系"


class RelationIdError(ValueError):
    """关系对象 ID 不符合 `关系.<甲>&<乙>`（恰一个 &、两端非空、已按 Unicode 序）规范。"""
    pass


def split_relation_id(object_id):
    """`关系.柳禾&苏砚` -> ('柳禾', '苏砚')。不合规抛 RelationIdError。
    两端顺序按输入原样返回，不做排序（排序检查交给 normalize_relation_id 比对）。"""
    if "." not in object_id:
        raise RelationIdError(f"{object_id}: 缺少 `关系.` 前缀")
    prefix, rest = object_id.split(".", 1)
    if prefix.strip() != RELATION_PREFIX:
        raise RelationIdError(f"{object_id}: 前缀应为「{RELATION_PREFIX}」")
    if rest.count(RELATION_SEP) != 1:
        raise RelationIdError(
            f"{object_id}: 关系 ID 必须恰好含一个 ASCII `{RELATION_SEP}`（当前 {rest.count(RELATION_SEP)} 个）")
    a, b = (s.strip() for s in rest.split(RELATION_SEP))
    if not a or not b:
        raise RelationIdError(f"{object_id}: `{RELATION_SEP}` 两端都不能为空")
    if a == b:
        raise RelationIdError(f"{object_id}: 关系两端不能是同一对象")
    return a, b


def normalize_relation_id(object_id):
    """把关系对象 ID 规范化：两端按 Unicode 码位排序、`关系.<小>&<大>`。
    输入不含 `关系.` 前缀时按裸的 `甲&乙` 处理。用于 audit 报错时给出规范形式。"""
    body = object_id.split(".", 1)[1] if object_id.startswith(RELATION_PREFIX + ".") else object_id
    a, b = split_relation_id(RELATION_PREFIX + "." + body)
    lo, hi = sorted((a, b))
    return f"{RELATION_PREFIX}.{lo}{RELATION_SEP}{hi}"


def relation_endpoints_as_object_ids(object_id, char_names, fac_names):
    """关系两端 -> 状态对象 ID 列表。端名在人物卡集里 -> `角色.X`，在势力卡集里 -> `势力.X`，
    都不在则原样返回 `角色.X`（让 state_registry 的存在性检查去报错）。"""
    out = []
    for end in split_relation_id(object_id):
        if end in fac_names and end not in char_names:
            out.append(f"势力.{end}")
        else:
            out.append(f"角色.{end}")
    return out

OBJECT_NOTE = "> 由状态脚本自动写入，请勿手工编辑。"
BASELINE_NOTE = "> 创世基线快照 · 只读不可变（写第 1 章前一次性生成）。"

_UNSAFE_NAME_CHARS = set('/\\:*?"<>|')


class StateMergeError(Exception):
    """合并过程中的阻断性数据错误：应中止本次合并、不写任何文件。"""
    pass


# ---------------------------------------------------------------------------
# 原子写
# ---------------------------------------------------------------------------

def _atomic_write(path, text):
    """先写临时文件再 os.replace 原子替换，避免写到一半崩溃留下损坏文件。"""
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write(text)
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Markdown 表格 解析 / 渲染
# ---------------------------------------------------------------------------

def parse_md_table(file_path, *, strict=True):
    """
    解析 Markdown 状态表格。兼容 4 列（状态文件）与 7 列（履历）。
    strict=True 时，表格区内任何列数不符或类型非法的行都抛 StateMergeError，
    绝不静默丢弃——静默丢行是状态腐烂的头号来源。
    值内的竖线写作 \\| ，解析时还原。
    """
    if not os.path.exists(file_path):
        return []

    records = []
    with open(file_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    for lineno, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped.startswith('|'):
            continue

        # 先把转义竖线换成哨兵，切分后再还原
        protected = stripped.replace('\\|', '\x00')
        parts = [p.strip().replace('\x00', '|') for p in protected.split('|')[1:-1]]
        if not parts:
            continue

        head = parts[0]
        if (head in ('对象ID', '对象 ID', 'ID')
                or head.startswith(':-') or head.startswith('---')):
            continue

        if len(parts) not in (4, 7):
            if strict:
                raise StateMergeError(
                    f"{file_path}:{lineno} 状态表格行应为 4 列或 7 列，实际 {len(parts)} 列。\n"
                    f"  行内容: {stripped}\n"
                    f"  若某列的值里本身含竖线，请写成 \\| "
                )
            continue

        obj_id, field, ftype, val = parts[0], parts[1], parts[2], parts[3]

        if strict and ftype not in VALID_MERGE_TYPES:
            raise StateMergeError(
                f"{file_path}:{lineno} 未知的类型列「{ftype}」。\n"
                f"  合法值: {sorted(VALID_MERGE_TYPES)}\n"
                f"  常见原因: 用了全角破折号「—」而非半角「-」"
            )

        meta = {}
        for idx, col_name in enumerate(CHANGELOG_META_COLUMNS):
            src_idx = 4 + idx
            if src_idx < len(parts):
                meta[col_name] = parts[src_idx]

        records.append({
            'object_id': obj_id, 'field': field,
            'type': ftype, 'value': val, 'meta': meta,
        })

    return records

    return records


def parse_list(val_str):
    """解析逗号分隔的列表元素"""
    if not val_str or val_str in ['无', '空', '[]', '-']:
        return []
    return [x.strip() for x in val_str.split(',') if x.strip()]


def format_list(item_list):
    """格式化列表为逗号分隔字符串"""
    if not item_list:
        return '无'
    return ','.join(item_list)


def parse_number(val_str, obj_id=None, field=None):
    """
    将字符串转换为 int 或 float。

    - 空值写法（无 / 空 / - 等，见 NUMERIC_EMPTY_VALUES）按 0 处理；
    - 真正无法解析的垃圾值（如 `八十`、`+2O`）抛 StateMergeError，
      由上层中止合并、不写文件，绝不静默归零。
    """
    s = (val_str or '').strip()
    core = s[1:].strip() if s[:1] in ('+', '-') else s
    if not core or core in NUMERIC_EMPTY_VALUES or s in NUMERIC_EMPTY_VALUES:
        return 0
    try:
        return float(s) if '.' in s else int(s)
    except ValueError:
        raise StateMergeError(
            f"数值解析失败: [{obj_id or '?'}].{field or '?'} = '{val_str}'，"
            f"运算-数值字段的值应为 +N / -N 或纯数字"
        )


def render_md_table(records, title="状态表", note="> 由状态脚本自动写入，请勿手工编辑。"):
    """将记录渲染为标准 4 列 Markdown 表格字符串。"""
    lines = [
        f"# {title}",
        "",
        note,
        "",
        "| 对象ID | 字段 | 类型 | 值 |",
        "| --- | --- | --- | --- |",
    ]
    for r in records:
        lines.append(f"| {r['object_id']} | {r['field']} | {r['type']} | {r['value']} |")
    lines.append("")
    return "\n".join(lines)


def records_diff(old_records, new_records):
    """
    对比两组状态记录，返回人类可读的 diff 行列表（无差异则空列表）。
    按 (对象ID, 字段) 键比对，忽略记录/文件顺序。
    """
    old_map = {(r['object_id'], r['field']): r['value'] for r in old_records}
    new_map = {(r['object_id'], r['field']): r['value'] for r in new_records}
    lines = []
    for k in sorted(set(old_map) | set(new_map)):
        o = old_map.get(k)
        n = new_map.get(k)
        if o == n:
            continue
        label = f"{k[0]} | {k[1]}"
        if o is None:
            lines.append(f"  + [{label}] 新增: {n}")
        elif n is None:
            lines.append(f"  - [{label}] 移除（原值: {o}）")
        else:
            lines.append(f"  ~ [{label}] {o}  ->  {n}")
    return lines


# ---------------------------------------------------------------------------
# 核心合并（纯函数，永不调用 LLM / 网络 / 文件）
# ---------------------------------------------------------------------------

def merge_states(base_records, changelog_records, *, descriptive_resolution=None):
    """
    把本章履历折叠进 base_records（当前全量状态）。

    descriptive_resolution: 可选 dict {(object_id, field): merged_text}。
      「描述」类履历行的取值：
        - (obj,field) 在 descriptive_resolution 中 -> 用合并文本；
        - 否则 -> 字面整体覆盖（首次出现 / 调用方选择不智能合并）。
    运算-数值 / 运算-枚举 / 运算-列表 的逻辑与历史版本完全一致。

    返回: (merged_records, diff_logs)
    """
    resolution = descriptive_resolution or {}

    state_map = {}
    obj_order = []
    field_order = {}

    def _touch(obj_id, field):
        if obj_id not in obj_order:
            obj_order.append(obj_id)
            field_order[obj_id] = []
        if field not in field_order[obj_id]:
            field_order[obj_id].append(field)

    for rec in base_records:
        _touch(rec['object_id'], rec['field'])
        state_map[(rec['object_id'], rec['field'])] = {
            'type': rec['type'], 'value': rec['value'],
        }

    diff_logs = []

    for rec in changelog_records:
        obj_id = rec['object_id']
        field = rec['field']
        ftype = rec['type']
        change_val = rec['value']
        _touch(obj_id, field)

        key = (obj_id, field)
        old_val = state_map[key]['value'] if key in state_map else '无'
        new_val = old_val

        if ftype == '运算-数值':
            old_num = parse_number(old_val, obj_id, field)
            change_str = change_val.strip()
            if change_str.startswith('+') or change_str.startswith('-'):
                calc_num = old_num + parse_number(change_str, obj_id, field)
            else:
                calc_num = parse_number(change_str, obj_id, field)
            if isinstance(calc_num, float) and calc_num.is_integer():
                calc_num = int(calc_num)
            new_val = str(calc_num)

        elif ftype == '运算-枚举':
            new_val = change_val.strip()

        elif ftype == '运算-列表':
            current_items = parse_list(old_val)
            ops = [op.strip() for op in change_val.split(',') if op.strip()]
            for op in ops:
                if op.startswith('+'):
                    item = op[1:].strip()
                    if item and item not in current_items:
                        current_items.append(item)
                elif op.startswith('-'):
                    item = op[1:].strip()
                    if item in current_items:
                        current_items.remove(item)
                else:
                    raise StateMergeError(
                        f"[{obj_id}].{field} 运算-列表 的每个元素必须带 +/- 前缀，"
                        f"收到 '{op}'（完整值: '{change_val}'）。\n"
                        f"  不支持整表替换写法：要移除请逐项写 -X。"
                    )
            new_val = format_list(current_items)

        elif ftype == '描述':
            if key in resolution:
                new_val = resolution[key]
            else:
                new_val = change_val.strip()

        else:
            raise StateMergeError(
                f"[{obj_id}].{field} 未知类型「{ftype}」。"
                f"合法: 运算-数值 / 运算-枚举 / 运算-列表 / 描述"
            )

        state_map[key] = {'type': ftype, 'value': new_val}
        diff_logs.append(
            f"[{obj_id}] {field} ({ftype}): '{old_val}' -> '{new_val}' (履历: '{change_val}')"
        )

    merged_records = []
    for obj_id in obj_order:
        for field in field_order[obj_id]:
            data = state_map[(obj_id, field)]
            merged_records.append({
                'object_id': obj_id, 'field': field,
                'type': data['type'], 'value': data['value'],
                'meta': {},
            })
    return merged_records, diff_logs


def split_descriptive(base_records, changelog_records, cache):
    """
    把本章履历的描述类变更分成「缓存命中」与「待合并」两拨。
    返回 (resolved, pending)：
      resolved  {(obj, field): merged_text}   —— 可直接用，不调 LLM
      pending   [(obj, field, old, new), ...] —— 需要 LLM

    跳过条件：类型非「描述」；旧值为空/不存在（首次出现，字面写入即可）；新旧文本相同。
    """
    cur = {(r['object_id'], r['field']): r['value'] for r in base_records}
    resolved, pending = {}, []
    for rec in changelog_records:
        if rec['type'] != '描述':
            continue
        key = (rec['object_id'], rec['field'])
        old_val = cur.get(key)
        if old_val is None or old_val.strip() in NUMERIC_EMPTY_VALUES:
            continue
        new_val = rec['value'].strip()
        old_val = old_val.strip()
        if new_val == old_val:
            continue
        ck = (rec['object_id'], rec['field'],
              value_fingerprint(old_val), value_fingerprint(new_val))
        if ck in cache:
            resolved[key] = cache[ck]
        else:
            pending.append((rec['object_id'], rec['field'], old_val, new_val))
    return resolved, pending


def fold_all(baseline_dir, changelog_paths, *, cache=None, resolver=None, chapter_names=None):
    """
    从冻结基线出发，按给定顺序折叠各章履历。

    cache:        load_merge_cache() 的结果；None 视为空
    resolver:     callable(pending) -> {(obj, field): merged_text}
                  None 且存在未命中 -> 抛 StateMergeError
    chapter_names: 与 changelog_paths 等长的章名列表，仅用于写进缓存条目做溯源

    返回 (records, new_cache_entries)。调用方负责 append_merge_cache。
    本函数永不修改任何履历文件。
    """
    records = load_state_tree(baseline_dir)
    cache = cache or {}
    new_entries = []
    today = datetime.now(timezone.utc).date().isoformat()

    for i, cl_path in enumerate(changelog_paths):
        cl = parse_md_table(str(cl_path))
        resolved, pending = split_descriptive(records, cl, cache)

        if pending:
            if resolver is None:
                names = ", ".join(f"{o}.{f}" for o, f, _, _ in pending)
                raise StateMergeError(
                    f"{cl_path} 有 {len(pending)} 处描述字段变更未命中合并缓存: {names}\n"
                    f"  原因：首次合并，或上游章节被改动导致前提变化。\n"
                    f"  处理：对该章跑 merge_chapter_state.py，"
                    f"或 rebuild_global_state.py --merge-pending。"
                )
            merged = resolver(pending)
            for (obj, field, old, new) in pending:
                text = merged[(obj, field)]
                resolved[(obj, field)] = text
                entry = {
                    "对象": obj, "字段": field,
                    "旧值sha": value_fingerprint(old),
                    "新值sha": value_fingerprint(new),
                    "合并文本": text,
                    "章": chapter_names[i] if chapter_names else "",
                    "时间": today,
                }
                new_entries.append(entry)
                cache[(obj, field, entry["旧值sha"], entry["新值sha"])] = text

        records, _diff = merge_states(records, cl, descriptive_resolution=resolved)

    return records, new_entries


# ---------------------------------------------------------------------------
# 对象 ID <-> 路径
# ---------------------------------------------------------------------------

def category_for_object(object_id):
    prefix = object_id.split(".", 1)[0].strip()
    return CATEGORY_BY_PREFIX.get(prefix, OTHER_CATEGORY)


def sanitize_name(name):
    """把对象名里文件系统不安全的字符转成 %XX（大写十六进制），结果人类可读。"""
    out = []
    for ch in name:
        if ch in _UNSAFE_NAME_CHARS or ord(ch) < 0x20:
            out.append(f"%{ord(ch):02X}")
        else:
            out.append(ch)
    s = "".join(out).strip()
    if s.startswith("."):
        s = "%2E" + s[1:]
    if s.endswith("."):
        s = s[:-1] + "%2E"
    return s or "未命名"


def object_id_to_relpath(object_id):
    """`角色.示例角色` -> `01_角色/01_角色_示例角色.md`（不含碰撞后缀）。"""
    cat = category_for_object(object_id)
    name = object_id.split(".", 1)[1] if "." in object_id else object_id
    return f"{cat}/{cat}_{sanitize_name(name)}.md"


# ---------------------------------------------------------------------------
# 单章细纲「## 出场对象」<-> 状态对象 ID（供 build_state_snapshot / audit 共用）
# ---------------------------------------------------------------------------

CHAPTER_OPENER_FILENAME = "00_开篇状态.md"

# @引用前缀 -> 状态对象 ID 前缀
_REF_PREFIX_TO_STATE = {"人物": "角色", "势力": "势力", "物品": "物品",
                       "财务": "财务", "世界": "世界", "关系": "关系"}
_CAST_CELL_RE = re.compile(r"@(主角|人物|势力|物品|财务|世界|关系)(?:\.\[([^\]]+)\])?")
_CHAPTER_DIR_RE = re.compile(r"^(\d+_第\d+部)/(\d+_卷(\d+))/(\d+_章\d+|章\d+)$")


def protagonist_state_id(novel_dir):
    """从 01_设定/00_主角档案.md 的「姓名」字段推导主角状态对象 ID（`角色.<姓名>`）。找不到返回 None。"""
    card = os.path.join(novel_dir, "01_设定", "00_主角档案.md")
    try:
        with open(card, encoding="utf-8") as f:
            txt = f.read()
    except OSError:
        return None
    m = re.search(r"^\|\s*姓名\s*\|[^|\n]*\|\s*([^|\n]+?)\s*\|", txt, re.M)
    return f"角色.{m.group(1).strip()}" if m else None


def plan_path_for_chapter(chapter_dir, novel_dir):
    """章目录 `05_工作区/03_第01部/03_卷01/03_章0001` -> 单章细纲
    `03_规划/01_第01部/01_卷01/规划_卷01_章0001.md` 的绝对路径。不匹配返回 None。"""
    chapter_dir_abs = os.path.abspath(chapter_dir)
    if os.path.basename(chapter_dir_abs) == "02_状态":
        chapter_dir_abs = os.path.dirname(chapter_dir_abs)
    ws = os.path.join(os.path.abspath(novel_dir), WORKSPACE_DIRNAME)
    rel = os.path.relpath(chapter_dir_abs, ws).replace(os.sep, "/")
    m = _CHAPTER_DIR_RE.match(rel)
    if not m:
        return None
    part_dir, vol_dir, vol_num, chap_raw = m.group(1), m.group(2), m.group(3), m.group(4)
    chap = chap_raw.split("_")[-1] if "_" in chap_raw else chap_raw
    part_clean = re.sub(r"^\d+_", "", part_dir)
    vol_clean = re.sub(r"^\d+_", "", vol_dir)
    # 尝试匹配 03_规划 下的目录
    plan_root = os.path.join(os.path.abspath(novel_dir), "03_规划")
    if os.path.isdir(plan_root):
        for p_entry in os.listdir(plan_root):
            if p_entry == part_dir or p_entry == part_clean or re.sub(r"^\d+_", "", p_entry) == part_clean:
                p_path = os.path.join(plan_root, p_entry)
                if os.path.isdir(p_path):
                    for v_entry in os.listdir(p_path):
                        if v_entry == vol_dir or v_entry == vol_clean or re.sub(r"^\d+_", "", v_entry) == vol_clean:
                            candidate = os.path.join(p_path, v_entry, f"规划_卷{vol_num}_{chap}.md")
                            if os.path.exists(candidate):
                                return candidate
    return os.path.join(plan_root, part_dir, vol_dir, f"规划_卷{vol_num}_{chap}.md")


def parse_chapter_cast(plan_path, protagonist_id=None):
    """解析单章细纲的「## 出场对象」小节，返回状态对象 ID 集合。
    文件不存在 / 无该小节 -> None（调用方据此退化为「全量」并告警）。"""
    if not plan_path or not os.path.isfile(plan_path):
        return None
    with open(plan_path, encoding="utf-8") as f:
        txt = f.read()
    m = re.search(r"^#{1,4}\s*出场对象\s*$(.*?)(?=^#{1,4}\s|\Z)", txt, re.M | re.S)
    if not m:
        return None
    ids = set()
    for line in m.group(1).splitlines():
        s = line.strip()
        if not s.startswith("|"):
            continue
        cells = s.split("|")
        cell = cells[1].strip().strip("`") if len(cells) > 1 else ""
        cm = _CAST_CELL_RE.match(cell)
        if not cm:
            continue
        typ, name = cm.group(1), cm.group(2)
        if typ == "主角":
            if protagonist_id:
                ids.add(protagonist_id)
            continue
        if not name:
            continue
        pref = _REF_PREFIX_TO_STATE.get(typ)
        if not pref:
            continue
        if pref == "关系":
            try:
                ids.add(normalize_relation_id(name.strip()))
            except RelationIdError:
                ids.add(f"关系.{name.strip()}")  # 交给 audit 报格式错
        else:
            ids.add(f"{pref}.{name.strip()}")
    return ids or None


def cast_contains(cast_ids, object_id):
    """本章出场对象集 cast_ids 是否「覆盖」object_id。
    - 普通对象：直接 in。
    - 关系对象 `关系.甲&乙`：**至少一端**的对象 ID 在 cast_ids 内即算覆盖
      （主角对不在场仇人的敌意同样影响本章行为——不是「两端都在」）。"""
    if object_id in cast_ids:
        return True
    if object_id.startswith(RELATION_PREFIX + "."):
        try:
            a, b = split_relation_id(object_id)
        except RelationIdError:
            return False
        return any(f"{p}.{e}" in cast_ids for e in (a, b) for p in ("角色", "势力"))
    return False


def render_chapter_opener(records, chap_name, cast_ids=None, missing_ids=None):
    """渲染 00_开篇状态.md（派生视图 · 禁止手工编辑）。"""
    lines = [
        f"# 本章开篇状态 · {chap_name}",
        "",
        "> 派生视图 · 由 build_state_snapshot.py --write-chapter-openers 生成 · **禁止手工编辑**",
        "> 权威数据 = 05_工作区/02_状态/00_基线状态/ + 各章 02_状态/01_状态履历.md",
        "> 要修改本章起点，请去改上游章节的履历，然后重折。",
    ]
    if cast_ids is None:
        lines.append("> ⚠ 未找到单章细纲的「## 出场对象」小节——本文件为**全量**开篇状态。")
    else:
        lines.append(f"> 出场对象清单（{len(cast_ids)}）：{'、'.join(sorted(cast_ids))}")
        if missing_ids:
            lines.append(f"> 清单中本章开篇尚无状态的对象（应在本章履历以「新建」引入）：{'、'.join(sorted(missing_ids))}")
    lines += ["", "| 对象ID | 字段 | 类型 | 值 |", "| --- | --- | --- | --- |"]
    for r in records:
        lines.append(f"| {r['object_id']} | {r['field']} | {r['type']} | {r['value']} |")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 目录发现
# ---------------------------------------------------------------------------

def find_novel_dir(hint_path):
    """从给定路径向上查找含 02_数据库/ 和 05_工作区/ 的小说根目录。找不到返回 None。"""
    if not hint_path:
        return None
    cur = os.path.abspath(hint_path)
    if not os.path.isdir(cur):
        cur = os.path.dirname(cur)
    while True:
        if (os.path.isdir(os.path.join(cur, "02_数据库"))
                and os.path.isdir(os.path.join(cur, WORKSPACE_DIRNAME))):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            return None
        cur = parent


def latest_state_dir(novel_dir):
    return os.path.join(novel_dir, LATEST_STATE_SUBPATH)


def merge_cache_path(novel_dir):
    return os.path.join(novel_dir, WORKSPACE_DIRNAME, "02_状态", MERGE_CACHE_FILENAME)


def value_fingerprint(text):
    """描述文本的内容指纹。取 sha256 前 16 位，足够避免碰撞且便于人眼比对。"""
    return hashlib.sha256((text or "").strip().encode("utf-8")).hexdigest()[:16]


def load_merge_cache(novel_dir):
    """返回 {(对象, 字段, 旧值sha, 新值sha): 合并文本}。文件不存在返回 {}。"""
    path = merge_cache_path(novel_dir)
    cache = {}
    if not os.path.exists(path):
        return cache
    with open(path, encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                e = json.loads(line)
                cache[(e["对象"], e["字段"], e["旧值sha"], e["新值sha"])] = e["合并文本"]
            except (json.JSONDecodeError, KeyError) as ex:
                raise StateMergeError(f"{path}:{lineno} 描述合并缓存行损坏: {ex}")
    return cache


def append_merge_cache(novel_dir, entries):
    """追加写。缓存是派生物，只追加不重写、不删除。"""
    if not entries:
        return
    path = merge_cache_path(novel_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")


def baseline_dir(novel_dir):
    return os.path.join(novel_dir, BASELINE_SUBPATH)


CHAPTER_REL_RE = re.compile(r"^(\d+)_第(\d+)部/(\d+)_卷(\d+)/(?:(\d+)_)?章(\d+)$")


def chapter_sort_key(changelog_path, novel_dir):
    """从履历路径解析 (部号, 卷号, 章号)。不符合规范即抛错，绝不静默排序。"""
    # changelog_path 可能是 .../03_章0001/02_状态/01_状态履历.md 或 .../03_章0001/01_状态履历.md
    parent_dir = os.path.dirname(os.path.abspath(changelog_path))
    if os.path.basename(parent_dir) == "02_状态":
        chap_dir = os.path.dirname(parent_dir)
    else:
        chap_dir = parent_dir

    ws = os.path.join(os.path.abspath(novel_dir), WORKSPACE_DIRNAME)
    rel = os.path.relpath(chap_dir, ws).replace(os.sep, "/")
    m = CHAPTER_REL_RE.match(rel)
    if not m:
        raise StateMergeError(
            f"章目录路径不符合规范: {WORKSPACE_DIRNAME}/{rel}\n"
            f"应为 NN_第NN部/NN_卷NN/NN_章NNNN（例: 03_第01部/03_卷01/03_章0001）"
        )
    _pp, part, _vp, vol, _cp, chap = m.groups()
    return (int(part), int(vol), int(chap))


def iter_workspace_changelogs(novel_dir):
    """返回 05_工作区/ 下所有 01_状态履历.md 的绝对路径，按 (部, 卷, 章) 排序。"""
    ws = os.path.join(novel_dir, WORKSPACE_DIRNAME)
    found = []
    if not os.path.isdir(ws):
        return found
    for dirpath, _dirs, files in os.walk(ws):
        if CHANGELOG_FILENAME in files:
            found.append(os.path.normpath(os.path.join(dirpath, CHANGELOG_FILENAME)))
    found.sort(key=lambda p: chapter_sort_key(p, novel_dir))
    return found


def chapter_rel_name(changelog_path, novel_dir):
    """把 .../05_工作区/03_第01部/03_卷01/03_章0001/02_状态/01_状态履历.md
    表示成 `03_第01部/03_卷01/03_章0001`（供 manifest 与报告用）。"""
    parent_dir = os.path.dirname(os.path.abspath(changelog_path))
    if os.path.basename(parent_dir) == "02_状态":
        chap_dir = os.path.dirname(parent_dir)
    else:
        chap_dir = parent_dir
    ws = os.path.join(os.path.abspath(novel_dir), WORKSPACE_DIRNAME)
    try:
        return os.path.relpath(chap_dir, ws)
    except ValueError:
        return os.path.basename(chap_dir)


# ---------------------------------------------------------------------------
# 状态树 读 / 写
# ---------------------------------------------------------------------------

def load_state_tree(state_dir):
    """遍历状态树，返回全部记录（跳过 NN_类目.md 索引与 00_*.md）。"""
    records = []
    if not os.path.isdir(state_dir):
        return records
    for cat in CATEGORY_ORDER:
        cat_dir = os.path.join(state_dir, cat)
        if not os.path.isdir(cat_dir):
            continue
        for fn in sorted(f for f in os.listdir(cat_dir) if f.endswith(".md")):
            if fn == f"{cat}.md" or fn.startswith("00_"):
                continue
            records.extend(parse_md_table(os.path.join(cat_dir, fn)))
    return records


def render_object_file(object_id, records, note=OBJECT_NOTE):
    lines = [
        f"# 全局状态 · {object_id}",
        "",
        note,
        f"> 对象ID: {object_id}",
        "",
        "| 对象ID | 字段 | 类型 | 值 |",
        "| --- | --- | --- | --- |",
    ]
    for r in records:
        lines.append(f"| {r['object_id']} | {r['field']} | {r['type']} | {r['value']} |")
    lines.append("")
    return "\n".join(lines)


def render_category_index(category, obj_paths, obj_counts, folded_chapter):
    label = CATEGORY_LABELS.get(category, category)
    lines = [
        f"# 全局状态 · {label} · 索引",
        "",
        "> 由状态脚本自动生成，请勿手工编辑。",
        f"> 折叠至章: {folded_chapter or NONE_MARKER}",
        "",
        "| 对象ID | 字段数 | 对象文件 |",
        "| --- | --- | --- |",
    ]
    for obj_id in sorted(obj_paths):
        fname = os.path.basename(obj_paths[obj_id])
        lines.append(f"| {obj_id} | {obj_counts[obj_id]} | {fname} |")
    lines.append("")
    return "\n".join(lines)


def render_manifest(folded_chapter, tool, n_objects, n_records):
    now = datetime.now(timezone.utc).isoformat()
    return "\n".join([
        "# 05_工作区/02_状态/01_最新状态 · 同步状态",
        "",
        "> 由状态脚本自动写入，供人工查看与审计参考，非权威数据。",
        "",
        f"- 折叠至章: {folded_chapter or NONE_MARKER}",
        "- 基线: 05_工作区/02_状态/00_基线状态/",
        f"- 最后运行工具: {tool}",
        f"- 最后运行时间: {now}",
        f"- 对象总数: {n_objects}",
        f"- 记录总数: {n_records}",
        "",
    ])


HOLDINGS_REVERSE_FILENAME = "00_持有物品反查.md"
_HOLDER_REF_RE = re.compile(r"@?(?:角色|势力)\.\[?([^\]\s]+?)\]?$")


def render_holdings_reverse_index(records, folded_chapter=None):
    """派生视图：扫全部 `物品.* 的「持有者」` 字段，反查出「某角色/势力 持有哪些物品」。
    这是唯一权威 `物品.X 的「持有者」` 的只读反向索引——**不是权威数据**。"""
    holders = OrderedDict()   # holder_display -> [item_name, ...]
    unowned = []
    for r in records:
        oid = r.get("object_id", "")
        if not oid.startswith("物品.") or r.get("field") != "持有者":
            continue
        item = oid.split(".", 1)[1]
        raw = (r.get("value") or "").strip()
        m = _HOLDER_REF_RE.match(raw)
        if raw in ("", "无", "无人", "-", "—"):
            unowned.append(item)
        elif m:
            holders.setdefault(m.group(1), []).append(item)
        else:
            holders.setdefault(raw, []).append(item)
    lines = [
        "# 持有物品 · 反查视图",
        "",
        "> 派生视图 · 由状态脚本从 `物品.* 的「持有者」` 字段反查生成 · **禁止手工编辑、非权威**。",
        "> 权威数据 = 各 `物品.X` 对象文件的「持有者」字段；此处仅供「某人身上有哪些物品」查阅。",
        f"> 折叠至章: {folded_chapter or NONE_MARKER}",
        "",
        "| 持有者 | 持有物品 |",
        "| --- | --- |",
    ]
    for holder in sorted(holders):
        lines.append(f"| {holder} | {'、'.join(sorted(holders[holder]))} |")
    if unowned:
        lines.append(f"| （无主 / 无人持有） | {'、'.join(sorted(unowned))} |")
    lines.append("")
    return "\n".join(lines)


def parse_manifest_folded_chapter(state_dir):
    """读 00_同步状态.md 的「折叠至章」；无文件或无该行返回 None。"""
    path = os.path.join(state_dir, MANIFEST_FILENAME)
    if not os.path.exists(path):
        return None
    m = re.search(r"折叠至章:\s*(\S+)", open(path, encoding='utf-8').read())
    if not m or m.group(1) == NONE_MARKER:
        return None
    return m.group(1)


def write_state_tree(state_dir, records, *, folded_chapter=None, tool="state_tree.py",
                     prune=True, manifest=True, note=OBJECT_NOTE):
    """
    把 records 写成 05_工作区/02_状态/01_最新状态/ 的对象树：逐对象文件原子写、重建类目索引、
    可选写 manifest、可选 prune 掉不再存在的对象文件与空类目目录。
    返回日志行列表。
    """
    os.makedirs(state_dir, exist_ok=True)
    logs = []

    by_obj = OrderedDict()
    for r in records:
        by_obj.setdefault(r['object_id'], []).append(r)

    # 路径映射（消毒后碰撞则追加 ~<sha1[:6]>）
    obj_paths = {}
    used = {}
    for obj_id in by_obj:
        rel = object_id_to_relpath(obj_id)
        if rel in used and used[rel] != obj_id:
            cat, base = rel.split("/", 1)
            h = hashlib.sha1(obj_id.encode("utf-8")).hexdigest()[:6]
            rel = f"{cat}/{base[:-3]}~{h}.md"
        used[rel] = obj_id
        obj_paths[obj_id] = rel

    cats_objs = {}          # cat -> {obj_id: field_count}
    for obj_id, recs in by_obj.items():
        rel = obj_paths[obj_id]
        cat = rel.split("/", 1)[0]
        cats_objs.setdefault(cat, {})[obj_id] = len(recs)
        abspath = os.path.join(state_dir, rel)
        os.makedirs(os.path.dirname(abspath), exist_ok=True)
        _atomic_write(abspath, render_object_file(obj_id, recs, note=note))

    for cat, objmap in cats_objs.items():
        cat_paths = {o: obj_paths[o] for o in objmap}
        _atomic_write(
            os.path.join(state_dir, cat, f"{cat}.md"),
            render_category_index(cat, cat_paths, objmap, folded_chapter),
        )

    if prune:
        for cat in CATEGORY_ORDER:
            cat_dir = os.path.join(state_dir, cat)
            if not os.path.isdir(cat_dir):
                continue
            keep = {os.path.basename(obj_paths[o]) for o in cats_objs.get(cat, {})}
            for fn in list(os.listdir(cat_dir)):
                if not fn.endswith(".md") or fn.startswith("00_") or fn == f"{cat}.md":
                    continue
                if fn not in keep:
                    os.remove(os.path.join(cat_dir, fn))
                    logs.append(f"prune 对象文件 {cat}/{fn}")
            leftover_objs = [
                f for f in os.listdir(cat_dir)
                if f.endswith(".md") and not f.startswith("00_") and f != f"{cat}.md"
            ]
            if not leftover_objs:
                for fn in os.listdir(cat_dir):
                    if fn.startswith("00_"):
                        continue  # 永不触碰 00_* 说明文件
                    os.remove(os.path.join(cat_dir, fn))
                # 只有目录完全空了才 rmdir
                remaining = os.listdir(cat_dir)
                if not remaining:
                    os.rmdir(cat_dir)
                logs.append(f"prune 空类目目录 {cat}/")

    if manifest:
        _atomic_write(
            os.path.join(state_dir, MANIFEST_FILENAME),
            render_manifest(folded_chapter, tool, len(by_obj), len(records)),
        )
        # 持有物品反查（派生视图，仅在权威最新状态树生成）
        _atomic_write(
            os.path.join(state_dir, HOLDINGS_REVERSE_FILENAME),
            render_holdings_reverse_index(records, folded_chapter),
        )

    logs.append(f"写入 {len(by_obj)} 个对象文件 / {len(records)} 条记录 -> {state_dir}")
    return logs
