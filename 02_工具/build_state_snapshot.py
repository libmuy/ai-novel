#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
卷末只读状态快照构建工具 (build_state_snapshot.py)

在某一卷结束时，顺序重放该卷全部章节的 03_本章初始状态.md 与 04_本章状态履历.md，
进行结构化重放合并，生成只读的 99_卷末状态快照.md，供审计与 consistency 自检工具查阅。
"""

import sys
import os
import re
import argparse
from merge_chapter_state import parse_md_table, merge_states, render_md_table


def find_chapter_dirs(volume_dir):
    """查找卷目录下所有的章级工作区目录 (如 01_章0001)"""
    if not os.path.exists(volume_dir):
        return []

    dirs = []
    for entry in sorted(os.listdir(volume_dir)):
        full_path = os.path.join(volume_dir, entry)
        if os.path.isdir(full_path) and ("章" in entry or entry.startswith("0") or entry.startswith("1")):
            # 校验是否包含章级状态文件
            init_file = os.path.join(full_path, "03_本章初始状态.md")
            if os.path.exists(init_file):
                dirs.append(full_path)
    return dirs


def build_snapshot(volume_dir, output_file=None):
    chapter_dirs = find_chapter_dirs(volume_dir)
    if not chapter_dirs:
        print(f"警告: 卷目录 [{volume_dir}] 下未找到有效的章级工作区。")
        return None

    print(f"找到 {len(chapter_dirs)} 个章级工作区，开始顺序重放状态...")

    # 第 1 章初始状态为起点
    first_chap = chapter_dirs[0]
    curr_records = parse_md_table(os.path.join(first_chap, "03_本章初始状态.md"))

    total_changes = 0
    for chap_dir in chapter_dirs:
        chap_name = os.path.basename(chap_dir)
        changelog_file = os.path.join(chap_dir, "04_本章状态履历.md")
        if os.path.exists(changelog_file):
            ch_records = parse_md_table(changelog_file)
            if ch_records:
                curr_records, diffs = merge_states(curr_records, ch_records)
                total_changes += len(ch_records)
                print(f"  * {chap_name}: 应用了 {len(ch_records)} 条状态履历")

    vol_name = os.path.basename(volume_dir)
    rendered = render_md_table(curr_records, title=f"卷末状态快照 · {vol_name} (只读物化视图)")

    if not output_file:
        output_file = os.path.join(volume_dir, "99_卷末状态快照.md")

    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(rendered)

    print(f"\n卷末状态快照生成完毕 (全卷累计应用 {total_changes} 条履历): {output_file}")
    return output_file


def main():
    parser = argparse.ArgumentParser(description="卷末只读状态快照构建工具")
    parser.add_argument("--volume-dir", required=True, help="卷工作区目录路径 (如 05_工作区/01_第01部/01_卷01)")
    parser.add_argument("--output", help="产出的 99_卷末状态快照.md 文件路径")

    args = parser.parse_args()
    build_snapshot(args.volume_dir, args.output)


if __name__ == "__main__":
    main()
