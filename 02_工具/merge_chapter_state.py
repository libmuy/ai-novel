#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
章级状态自动合并工具 (merge_chapter_state.py)

读取 第N章/03_本章初始状态.md 与 第N章/04_本章状态履历.md，
按字段类型（运算-数值 / 运算-枚举 / 运算-列表 / 描述）进行结构化合并，
并将结果输出为 第N+1章/03_本章初始状态.md（或指定输出文件）。

特性：
1. 纯 Python 标准库实现，无需第三方依赖；
2. 保证合并过程幂等（对同一章重复计算输出结果完全一致）；
3. 支持 --dry-run 模式（仅对比打印 diff，不修改文件）；
4. 支持自动生成 .bak 备份文件；
5. 输出清晰的合并 Log。
"""

import sys
import os
import re
import argparse
import shutil


# 04_本章状态履历.md 允许在标准 4 列之后追加的元数据列（只读，不参与 merge 计算）
CHANGELOG_META_COLUMNS = ['章节号', '变更时间', '变更类型']


def parse_md_table(file_path):
    """
    解析 Markdown 状态表格文件。
    返回列表 [{'object_id': ..., 'field': ..., 'type': ..., 'value': ..., 'meta': {...}}, ...]

    兼容两种表格形态：
    - 4 列：| 对象ID | 字段 | 类型 | 值 |          （03_本章初始状态.md / 全局状态文件）
    - 7 列：| 对象ID | 字段 | 类型 | 值 | 章节号 | 变更时间 | 变更类型 |  （04_本章状态履历.md）

    第 5 列及之后一律作为元数据读入 record['meta']，供审计/展示使用，
    但**不参与 merge_states 的类型合并计算**。
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

        # 检查是否为表头或分割线
        if parts[0] in ['对象ID', '对象 ID', 'ID', '---'] or parts[0].startswith(':-') or parts[0].startswith('---'):
            continue

        # 前 4 列为合并计算所需的权威列
        obj_id, field, ftype, val = parts[0], parts[1], parts[2], parts[3]

        # 第 5 列及之后：元数据列，读入但不参与合并
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


def parse_number(val_str):
    """将字符串转换为 int 或 float"""
    val_str = val_str.strip()
    try:
        if '.' in val_str:
            return float(val_str)
        return int(val_str)
    except ValueError:
        return 0


def _review_descriptive_change(obj_id, field, old_val, new_val, interactive):
    """
    描述类字段变更复核 hook。
    打印「旧值 vs 新值」供人工/Agent 判断是否存在信息丢失。
    返回 True 表示「保留旧值」（放弃本次覆盖，需人工合并），False 表示「按新值覆盖」。
    """
    print("\n[描述字段变更复核] --review-descriptive")
    print(f"  对象: {obj_id} | 字段: {field}")
    print(f"  旧值: {old_val}")
    print(f"  新值: {new_val}")
    if interactive and sys.stdin.isatty():
        resp = input("  直接覆盖为新值? [Y/n]（n = 保留旧值，稍后人工合并两段描述）: ").strip().lower()
        if resp == 'n':
            print("  -> 已保留旧值。请人工合并后再更新本章履历，重跑合并。")
            return True
        print("  -> 按新值覆盖。")
        return False
    print("  -> 非交互模式：默认按新值覆盖。请复查上方对比，确认无信息丢失。")
    return False


def merge_states(initial_records, changelog_records, review_descriptive=False, interactive=True):
    """
    合并初始状态与本章履历。
    返回: (merged_records, diff_logs)

    review_descriptive: 开启后，遇到「描述」类字段发生实际变更时，先打印旧值/新值对比，
                        在交互式 TTY 下询问是否覆盖（级联重放 rebuild_from_chapter.py 默认开启）。
    interactive:        是否允许 input() 交互（dry-run / 非 TTY 场景传 False，仅打印对比不阻塞）。
    """
    # 建立对象与字段索引：state_map[(obj_id, field)] = {'type': ..., 'value': ...}
    state_map = {}
    obj_order = []  # 保持对象出现顺序
    field_order = {}  # obj_id -> list of fields

    for rec in initial_records:
        obj_id = rec['object_id']
        field = rec['field']
        ftype = rec['type']
        val = rec['value']

        if obj_id not in obj_order:
            obj_order.append(obj_id)
            field_order[obj_id] = []
        if field not in field_order[obj_id]:
            field_order[obj_id].append(field)

        state_map[(obj_id, field)] = {
            'type': ftype,
            'value': val
        }

    diff_logs = []

    # 逐条应用履历
    for rec in changelog_records:
        obj_id = rec['object_id']
        field = rec['field']
        ftype = rec['type']
        change_val = rec['value']

        if obj_id not in obj_order:
            obj_order.append(obj_id)
            field_order[obj_id] = []
        if field not in field_order[obj_id]:
            field_order[obj_id].append(field)

        key = (obj_id, field)
        old_val = state_map[key]['value'] if key in state_map else '无'

        new_val = old_val

        if ftype == '运算-数值':
            # 增量运算 +N 或 -N
            old_num = parse_number(old_val)
            change_str = change_val.strip()
            if change_str.startswith('+') or change_str.startswith('-'):
                delta = parse_number(change_str)
                calc_num = old_num + delta
            else:
                # 若未带符号，作为设定值直接覆盖
                calc_num = parse_number(change_str)

            # 保留整数格式或浮点格式
            if isinstance(calc_num, float) and calc_num.is_integer():
                calc_num = int(calc_num)
            new_val = str(calc_num)

        elif ftype == '运算-枚举':
            # 直接覆盖枚举
            new_val = change_val.strip()

        elif ftype == '运算-列表':
            # +X,-Y
            current_items = parse_list(old_val)
            ops = [op.strip() for op in change_val.split(',') if op.strip()]
            for op in ops:
                if op.startswith('+'):
                    item_to_add = op[1:].strip()
                    if item_to_add and item_to_add not in current_items:
                        current_items.append(item_to_add)
                elif op.startswith('-'):
                    item_to_remove = op[1:].strip()
                    if item_to_remove in current_items:
                        current_items.remove(item_to_remove)
                else:
                    # 无 + / - 前缀，直接添加
                    if op not in current_items:
                        current_items.append(op)
            new_val = format_list(current_items)

        elif ftype == '描述':
            # 整体覆盖（可选 --review-descriptive 复核 hook）
            proposed = change_val.strip()
            if review_descriptive and proposed != old_val and old_val != '无':
                keep_old = _review_descriptive_change(obj_id, field, old_val, proposed, interactive)
                new_val = old_val if keep_old else proposed
            else:
                new_val = proposed

        else:
            # 默认覆盖
            new_val = change_val.strip()

        state_map[key] = {
            'type': ftype,
            'value': new_val
        }

        diff_logs.append(f"[{obj_id}] {field} ({ftype}): '{old_val}' -> '{new_val}' (履历: '{change_val}')")

    # 构建最终记录列表
    merged_records = []
    for obj_id in obj_order:
        for field in field_order[obj_id]:
            key = (obj_id, field)
            data = state_map[key]
            merged_records.append({
                'object_id': obj_id,
                'field': field,
                'type': data['type'],
                'value': data['value']
            })

    return merged_records, diff_logs


def render_md_table(records, title="本章初始状态表", note="> 由 merge_chapter_state.py 自动计算合并产出"):
    """将记录渲染为标准 4 列 Markdown 表格字符串（初始状态 / 全局状态形态）"""
    lines = [
        f"# {title}",
        "",
        note,
        "",
        "| 对象ID | 字段 | 类型 | 值 |",
        "| --- | --- | --- | --- |"
    ]
    for r in records:
        lines.append(f"| {r['object_id']} | {r['field']} | {r['type']} | {r['value']} |")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 全局状态同步 (--sync-global)
# ---------------------------------------------------------------------------

# 对象前缀 -> (04_全局状态/ 目标文件名, 分类中文名)
GLOBAL_PREFIX_FILES = [
    ("角色", "01_角色状态.md", "角色"),
    ("物品", "02_物品状态.md", "物品"),
    ("势力", "03_势力状态.md", "势力"),
    ("财务", "04_财务状态.md", "财务"),
    ("世界", "05_世界状态.md", "世界"),
]

GLOBAL_STATE_DIRNAME = "04_全局状态"
GLOBAL_NOTE = "> 由 merge_chapter_state.py --sync-global 自动覆盖写入，请勿手工编辑。"


def locate_global_state_dir(hint_path):
    """
    从给定路径向上查找小说根目录（含 02_数据库/ 或已存在 04_全局状态/），
    返回其中的 04_全局状态 目录路径（可能尚未创建）。找不到返回 None。
    """
    if not hint_path:
        return None
    cur = os.path.abspath(hint_path)
    if not os.path.isdir(cur):
        cur = os.path.dirname(cur)
    while True:
        if (os.path.isdir(os.path.join(cur, "02_数据库"))
                or os.path.isdir(os.path.join(cur, GLOBAL_STATE_DIRNAME))):
            return os.path.join(cur, GLOBAL_STATE_DIRNAME)
        parent = os.path.dirname(cur)
        if parent == cur:
            return None
        cur = parent


def split_records_by_prefix(records):
    """按对象前缀分组。返回 (groups: dict[prefix->list], unknown: list[object_id])"""
    groups = {prefix: [] for prefix, _, _ in GLOBAL_PREFIX_FILES}
    unknown = []
    for r in records:
        prefix = r['object_id'].split('.', 1)[0].strip()
        if prefix in groups:
            groups[prefix].append(r)
        else:
            unknown.append(r['object_id'])
    return groups, unknown


def sync_global_state(merged_records, global_dir, dry_run=False):
    """
    将合并终态按对象前缀拆分，覆盖写入 04_全局状态/ 下 5 个分类文件。
    返回日志行列表。
    """
    logs = []
    groups, unknown = split_records_by_prefix(merged_records)

    if not dry_run:
        os.makedirs(global_dir, exist_ok=True)

    for prefix, fname, label in GLOBAL_PREFIX_FILES:
        target = os.path.join(global_dir, fname)
        text = render_md_table(groups[prefix], title=f"全局状态 · {label}", note=GLOBAL_NOTE)
        if dry_run:
            logs.append(f"[dry-run] {target} <- {len(groups[prefix])} 条记录")
        else:
            with open(target, 'w', encoding='utf-8') as f:
                f.write(text)
            logs.append(f"已写入 {target}（{len(groups[prefix])} 条记录）")

    if unknown:
        uniq = sorted(set(unknown))
        logs.append(f"警告: {len(uniq)} 个对象前缀不属于五大类（角色/物品/势力/财务/世界），未同步至全局状态: {uniq}")

    return logs


def main():
    parser = argparse.ArgumentParser(description="章级状态自动合并工具")
    parser.add_argument("--initial", help="初始状态文件路径 (03_本章初始状态.md)")
    parser.add_argument("--changelog", help="本章履历文件路径 (04_本章状态履历.md)")
    parser.add_argument("--output", help="合并后输出的初始状态文件路径")
    parser.add_argument("--chapter-dir", help="章目录路径 (包含 03_本章初始状态.md 与 04_本章状态履历.md)")
    parser.add_argument("--next-chapter-dir", help="下一章目录路径 (合并结果写入其 03_本章初始状态.md)")
    parser.add_argument("--dry-run", action="store_true", help="仅计算并打印 diff，不写入文件")
    parser.add_argument("--backup", action="store_true", help="写入前备份现有目标文件为 .bak")
    parser.add_argument("--sync-global", action="store_true",
                        help="将合并终态按对象前缀拆分，覆盖写入 04_全局状态/ 五个分类文件")
    parser.add_argument("--global-state-dir",
                        help="04_全局状态/ 目录路径（缺省则从 --output/--initial 向上自动定位）")
    parser.add_argument("--review-descriptive", action="store_true",
                        help="描述类字段变更时先打印 旧值 vs 新值 供复核，交互式下询问是否覆盖")

    args = parser.parse_args()

    initial_path = args.initial
    changelog_path = args.changelog
    output_path = args.output

    if args.chapter_dir:
        if not initial_path:
            initial_path = os.path.join(args.chapter_dir, "03_本章初始状态.md")
        if not changelog_path:
            changelog_path = os.path.join(args.chapter_dir, "04_本章状态履历.md")

    if args.next_chapter_dir and not output_path:
        output_path = os.path.join(args.next_chapter_dir, "03_本章初始状态.md")

    if not initial_path:
        print("错误: 必须指定 --initial 路径，或提供 --chapter-dir")
        sys.exit(1)

    if not os.path.exists(initial_path):
        print(f"错误: 初始状态文件不存在: {initial_path}")
        sys.exit(1)

    if not changelog_path or not os.path.exists(changelog_path):
        if changelog_path:
            print(f"警告: 本章履历文件不存在: {changelog_path}，将直接继承初始状态")
        else:
            print("提示: 未指定 --changelog，按空履历处理（仅继承初始状态）")
        changelog_records = []
    else:
        changelog_records = parse_md_table(changelog_path)

    initial_records = parse_md_table(initial_path)
    merged_records, diff_logs = merge_states(
        initial_records, changelog_records,
        review_descriptive=args.review_descriptive,
        interactive=not args.dry_run,
    )

    print(f"=== 合并摘要 [{os.path.basename(os.path.dirname(initial_path))}] ===")
    print(f"初始记录数: {len(initial_records)} | 履历变更数: {len(changelog_records)} | 合并终态记录数: {len(merged_records)}")
    if diff_logs:
        print("\n--- 状态变更 Diff 清单 ---")
        for log in diff_logs:
            print(f"  * {log}")
    else:
        print("\n无状态变更。")

    # 全局状态同步目标目录
    global_dir = None
    if args.sync_global:
        global_dir = args.global_state_dir
        if not global_dir:
            for hint in (initial_path, args.chapter_dir, output_path):
                global_dir = locate_global_state_dir(hint)
                if global_dir:
                    break
        if not global_dir:
            print("\n错误: --sync-global 无法自动定位 04_全局状态/ 目录，请用 --global-state-dir 指定")
            sys.exit(1)

    if args.dry_run:
        print("\n[Dry-run 模式] 未写入文件。")
        if args.sync_global:
            print("\n--- --sync-global 计划 ---")
            for l in sync_global_state(merged_records, global_dir, dry_run=True):
                print(f"  {l}")
        return

    if output_path:
        output_dir = os.path.dirname(output_path)
        if output_dir and not os.path.exists(output_dir):
            os.makedirs(output_dir, exist_ok=True)

        if args.backup and os.path.exists(output_path):
            bak_path = output_path + ".bak"
            shutil.copyfile(output_path, bak_path)
            print(f"已备份原有目标文件至: {bak_path}")

        rendered_text = render_md_table(merged_records)
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(rendered_text)
        print(f"\n成功写入合并状态至: {output_path}")

    if args.sync_global:
        print("\n--- --sync-global：覆盖写入 04_全局状态/ ---")
        for l in sync_global_state(merged_records, global_dir, dry_run=False):
            print(f"  {l}")


if __name__ == "__main__":
    main()
