#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
状态树共享库 (state_tree.py)

本模块承载「按对象拆分、按类目分层」的全局状态存储的全部纯逻辑，供
merge_chapter_state.py / rebuild_global_state.py / build_state_snapshot.py /
audit_consistency.py 共用。**纯 Python 标准库、无网络、无 LLM。**

数据模型
--------
- 唯一权威状态存储 = 小说目录下 `04_全局状态/`，按对象类别分子目录：
    04_全局状态/
      ├── 00_说明.md            手写
      ├── 00_同步状态.md         本模块写（manifest，非权威）
      ├── 01_角色/  01_角色.md（索引）  01_角色_<名>.md ...
      ├── 02_物品/  ...
      ├── 03_势力/  ...
      ├── 04_财务/  ...
      ├── 05_世界/  ...
      └── 99_其他/  ...          前缀不属五大类的兜底
- 冻结基线 = `05_工作区/00_全局/00_基线状态/`，布局同上，只读不可变。
- `04_全局状态/` == 基线 ⊕（按章节路径顺序折叠全部 `04_本章状态履历.md`）。
- 每章目录只有 `04_本章状态履历.md`（7 列：前 4 列权威 + 章节号/变更时间/变更类型 元数据）。
"""

import os
import re
import hashlib
from collections import OrderedDict
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

GLOBAL_STATE_DIRNAME = "04_全局状态"
WORKSPACE_DIRNAME = "05_工作区"
BASELINE_SUBPATH = os.path.join("05_工作区", "00_全局", "00_基线状态")
CHANGELOG_FILENAME = "04_本章状态履历.md"
LEGACY_CHAPTER_STATE_FILENAME = "03_本章初始状态.md"
MANIFEST_FILENAME = "00_同步状态.md"
NONE_MARKER = "__none__"

# 04_本章状态履历.md 在标准 4 列之后追加的元数据列（读入但不参与合并计算）
CHANGELOG_META_COLUMNS = ['章节号', '变更时间', '变更类型']

# 描述字段变更被 LLM 合并并冻结后，元数据「变更类型」列写这个值；
# 之后重放/审计一律按字面整体覆盖，不再触发 LLM。
DESCRIPTIVE_MERGED_MARK = '描述合并'

# 运算-数值字段允许的「空值」写法，一律按 0 处理（不视为解析失败）
NUMERIC_EMPTY_VALUES = {'', '无', '空', '-', '—', '~', 'null', 'N/A', '/'}

# 对象 ID 前缀 -> 04_全局状态/ 下的类目目录名
CATEGORY_BY_PREFIX = {
    "角色": "01_角色",
    "物品": "02_物品",
    "势力": "03_势力",
    "财务": "04_财务",
    "世界": "05_世界",
}
OTHER_CATEGORY = "99_其他"
CATEGORY_ORDER = ["01_角色", "02_物品", "03_势力", "04_财务", "05_世界", OTHER_CATEGORY]
CATEGORY_LABELS = {
    "01_角色": "角色", "02_物品": "物品", "03_势力": "势力",
    "04_财务": "财务", "05_世界": "世界", OTHER_CATEGORY: "其他",
}

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

def parse_md_table(file_path):
    """
    解析 Markdown 状态表格文件。
    返回 [{'object_id', 'field', 'type', 'value', 'meta': {...}}, ...]

    兼容 4 列（对象文件 / 基线文件 / 卷末快照）与 7 列（04_本章状态履历.md）。
    第 5 列及之后作为元数据读入 record['meta']，不参与合并计算。
    """
    if not os.path.exists(file_path):
        return []

    records = []
    with open(file_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    for line in lines:
        stripped = line.strip()
        if not stripped.startswith('|'):
            continue

        parts = [p.strip() for p in stripped.split('|')[1:-1]]
        if len(parts) < 4:
            continue

        if (parts[0] in ['对象ID', '对象 ID', 'ID', '---']
                or parts[0].startswith(':-') or parts[0].startswith('---')):
            continue

        obj_id, field, ftype, val = parts[0], parts[1], parts[2], parts[3]

        meta = {}
        for idx, col_name in enumerate(CHANGELOG_META_COLUMNS):
            src_idx = 4 + idx
            if src_idx < len(parts):
                meta[col_name] = parts[src_idx]

        records.append({
            'object_id': obj_id,
            'field': field,
            'type': ftype,
            'value': val,
            'meta': meta,
        })

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
        - 否则 -> 字面整体覆盖（首次出现 / 已冻结的 描述合并 行 / 调用方选择不智能合并）。
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
                    if op not in current_items:
                        current_items.append(op)
            new_val = format_list(current_items)

        elif ftype == '描述':
            if key in resolution:
                new_val = resolution[key]
            else:
                new_val = change_val.strip()

        else:
            new_val = change_val.strip()

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


def pending_descriptive(base_records, changelog_records):
    """
    找出本章履历里「需要 LLM 智能合并」的描述字段变更。
    返回 [(object_id, field, old_text, new_text), ...]。

    跳过：类型非「描述」；元数据 变更类型 == 描述合并（已冻结）；
          旧值为空/不存在（首次出现，字面写入即可）；新旧文本相同。
    """
    cur = {(r['object_id'], r['field']): r['value'] for r in base_records}
    out = []
    for rec in changelog_records:
        if rec['type'] != '描述':
            continue
        if rec.get('meta', {}).get('变更类型') == DESCRIPTIVE_MERGED_MARK:
            continue
        key = (rec['object_id'], rec['field'])
        old_val = cur.get(key)
        if old_val is None or old_val.strip() in NUMERIC_EMPTY_VALUES:
            continue
        new_val = rec['value'].strip()
        if new_val == old_val.strip():
            continue
        out.append((rec['object_id'], rec['field'], old_val, new_val))
    return out


def rewrite_changelog_text(original_text, resolution):
    """
    把 04_本章状态履历.md 文本里已被 LLM 合并的描述行：
      - 「值」列换成合并文本；
      - 「变更类型」元数据列改成 描述合并。
    其余行、表头、注释块原样保留。返回新文本（不落盘）。
    resolution: dict {(object_id, field): merged_text}
    """
    out_lines = []
    for line in original_text.splitlines():
        stripped = line.strip()
        if stripped.startswith('|'):
            parts = [p.strip() for p in stripped.split('|')[1:-1]]
            if len(parts) >= 4 and (parts[0], parts[1]) in resolution:
                merged = resolution[(parts[0], parts[1])]
                cols = parts[:]
                cols[3] = merged
                # 补齐到 7 列
                while len(cols) < 7:
                    cols.append('-')
                cols[6] = DESCRIPTIVE_MERGED_MARK
                out_lines.append("| " + " | ".join(cols) + " |")
                continue
        out_lines.append(line)
    text = "\n".join(out_lines)
    if original_text.endswith("\n") and not text.endswith("\n"):
        text += "\n"
    return text


def fold_all(baseline_dir, changelog_paths, resolver=None):
    """
    从冻结基线出发，按给定顺序折叠 changelog_paths 里的每个 04_本章状态履历.md。

    resolver: 可选 callable(pending_list) -> {(obj,field): merged_text}
      pending_list 为 pending_descriptive() 的返回。仅当某章存在待合并描述变更时调用。
      resolver 为 None 且存在待合并描述变更 -> 抛 StateMergeError。

    返回 (records, writebacks)：
      records    折叠终态（list[record]）
      writebacks dict {changelog_path(str): 新文本}  —— 调用方负责原子写回冻结
    """
    records = load_state_tree(baseline_dir)
    writebacks = {}
    for cl_path in changelog_paths:
        cl_path = str(cl_path)
        cl = parse_md_table(cl_path)
        pend = pending_descriptive(records, cl)
        resolution = None
        if pend:
            if resolver is None:
                raise StateMergeError(
                    f"{cl_path} 有 {len(pend)} 处未冻结的描述字段变更，需要 LLM 合并。"
                    f"请对该章运行 merge_chapter_state.py，或用 rebuild_global_state.py --merge-pending。"
                )
            resolution = resolver(pend)
            with open(cl_path, 'r', encoding='utf-8') as f:
                writebacks[cl_path] = rewrite_changelog_text(f.read(), resolution)
        records, _diff = merge_states(records, cl, descriptive_resolution=resolution)
    return records, writebacks


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
    """`角色.叶云生` -> `01_角色/01_角色_叶云生.md`（不含碰撞后缀）。"""
    cat = category_for_object(object_id)
    name = object_id.split(".", 1)[1] if "." in object_id else object_id
    return f"{cat}/{cat}_{sanitize_name(name)}.md"


# ---------------------------------------------------------------------------
# 目录发现
# ---------------------------------------------------------------------------

def find_novel_dir(hint_path):
    """从给定路径向上查找含 04_全局状态/ 或 02_数据库/ 的小说根目录。找不到返回 None。"""
    if not hint_path:
        return None
    cur = os.path.abspath(hint_path)
    if not os.path.isdir(cur):
        cur = os.path.dirname(cur)
    while True:
        if (os.path.isdir(os.path.join(cur, GLOBAL_STATE_DIRNAME))
                or os.path.isdir(os.path.join(cur, "02_数据库"))):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            return None
        cur = parent


def global_state_dir(novel_dir):
    return os.path.join(novel_dir, GLOBAL_STATE_DIRNAME)


def baseline_dir(novel_dir):
    return os.path.join(novel_dir, BASELINE_SUBPATH)


def iter_workspace_changelogs(novel_dir):
    """返回 05_工作区/ 下所有 04_本章状态履历.md 的绝对路径，按完整路径排序。"""
    ws = os.path.join(novel_dir, WORKSPACE_DIRNAME)
    found = []
    if not os.path.isdir(ws):
        return found
    for dirpath, _dirs, files in os.walk(ws):
        if CHANGELOG_FILENAME in files:
            found.append(os.path.normpath(os.path.join(dirpath, CHANGELOG_FILENAME)))
    found.sort()
    return found


def chapter_rel_name(changelog_path, novel_dir):
    """把 .../05_工作区/01_第01部/01_卷01/01_章0001/04_本章状态履历.md
    表示成 `01_第01部/01_卷01/01_章0001`（供 manifest 与报告用）。"""
    chap_dir = os.path.dirname(os.path.abspath(changelog_path))
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
        "# 04_全局状态 · 同步状态",
        "",
        "> 由状态脚本自动写入，供人工查看与审计参考，非权威数据。",
        "",
        f"- 折叠至章: {folded_chapter or NONE_MARKER}",
        "- 基线: 05_工作区/00_全局/00_基线状态/",
        f"- 最后运行工具: {tool}",
        f"- 最后运行时间: {now}",
        f"- 对象总数: {n_objects}",
        f"- 记录总数: {n_records}",
        "",
    ])


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
    把 records 写成 04_全局状态/ 的对象树：逐对象文件原子写、重建类目索引、
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
                    os.remove(os.path.join(cat_dir, fn))
                os.rmdir(cat_dir)
                logs.append(f"prune 空类目目录 {cat}/")

    if manifest:
        _atomic_write(
            os.path.join(state_dir, MANIFEST_FILENAME),
            render_manifest(folded_chapter, tool, len(by_obj), len(records)),
        )

    logs.append(f"写入 {len(by_obj)} 个对象文件 / {len(records)} 条记录 -> {state_dir}")
    return logs
