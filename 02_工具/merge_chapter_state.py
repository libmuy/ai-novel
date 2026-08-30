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


def parse_md_table(file_path):
    """
    解析 Markdown 状态表格文件。
    返回列表 [{'object_id': ..., 'field': ..., 'type': ..., 'value': ...}, ...]
    及表头/其他非表格元数据（如果存在）。
    """
    if not os.path.exists(file_path):
        return []

    records = []
    with open(file_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    in_table = False
    header_found = False

    for line in lines:
        stripped = line.strip()
        if not stripped.startswith('|'):
            continue

        parts = [p.strip() for p in stripped.split('|')[1:-1]]
        if len(parts) < 4:
            continue

        # 检查是否为表头或分割线
        if parts[0] in ['对象ID', '对象 ID', 'ID', '---'] or parts[0].startswith(':-') or parts[0].startswith('---'):
            header_found = True
            continue

        # 正常数据行
        obj_id, field, ftype, val = parts[0], parts[1], parts[2], parts[3]
        records.append({
            'object_id': obj_id,
            'field': field,
            'type': ftype,
            'value': val
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


def merge_states(initial_records, changelog_records):
    """
    合并初始状态与本章履历。
    返回: (merged_records, diff_logs)
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
            # 整体覆盖
            new_val = change_val.strip()

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


def render_md_table(records, title="本章初始状态表"):
    """将记录渲染为标准 Markdown 表格字符串"""
    lines = [
        f"# {title}",
        "",
        "> 由 merge_chapter_state.py 自动计算合并产出",
        "",
        "| 对象ID | 字段 | 类型 | 值 |",
        "| --- | --- | --- | --- |"
    ]
    for r in records:
        lines.append(f"| {r['object_id']} | {r['field']} | {r['type']} | {r['value']} |")
    lines.append("")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="章级状态自动合并工具")
    parser.add_argument("--initial", help="初始状态文件路径 (03_本章初始状态.md)")
    parser.add_argument("--changelog", help="本章履历文件路径 (04_本章状态履历.md)")
    parser.add_argument("--output", help="合并后输出的初始状态文件路径")
    parser.add_argument("--chapter-dir", help="章目录路径 (包含 03_本章初始状态.md 与 04_本章状态履历.md)")
    parser.add_argument("--next-chapter-dir", help="下一章目录路径 (合并结果写入其 03_本章初始状态.md)")
    parser.add_argument("--dry-run", action="store_true", help="仅计算并打印 diff，不写入文件")
    parser.add_argument("--backup", action="store_true", help="写入前备份现有目标文件为 .bak")

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

    if not initial_path or not changelog_path:
        print("错误: 必须指定 --initial 与 --changelog 路径，或提供 --chapter-dir")
        sys.exit(1)

    if not os.path.exists(initial_path):
        print(f"错误: 初始状态文件不存在: {initial_path}")
        sys.exit(1)

    if not os.path.exists(changelog_path):
        print(f"警告: 本章履历文件不存在: {changelog_path}，将直接继承初始状态")
        changelog_records = []
    else:
        changelog_records = parse_md_table(changelog_path)

    initial_records = parse_md_table(initial_path)
    merged_records, diff_logs = merge_states(initial_records, changelog_records)

    print(f"=== 合并摘要 [{os.path.basename(os.path.dirname(initial_path))}] ===")
    print(f"初始记录数: {len(initial_records)} | 履历变更数: {len(changelog_records)} | 合并终态记录数: {len(merged_records)}")
    if diff_logs:
        print("\n--- 状态变更 Diff 清单 ---")
        for log in diff_logs:
            print(f"  * {log}")
    else:
        print("\n无状态变更。")

    if args.dry_run:
        print("\n[Dry-run 模式] 未写入文件。")
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


if __name__ == "__main__":
    main()
