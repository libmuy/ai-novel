#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
全局状态初始化迁移工具 (migrate_global_state.py)

把某本小说「当前最新章节」的 03_本章初始状态.md，按对象前缀
（角色./物品./势力./财务./世界.）拆分，写入 04_全局状态/ 下的
5 个分类文件，作为全局状态的初始化基线。

用法:
    python3 02_工具/migrate_global_state.py <小说目录>            # 实际写入
    python3 02_工具/migrate_global_state.py <小说目录> --dry-run  # 仅打印计划
    python3 02_工具/migrate_global_state.py <小说目录> --chapter-dir <指定章目录>

「最新章节」= 05_工作区/ 下按完整路径排序、最后一个含 03_本章初始状态.md 的目录。
后续章节推进时不再需要本脚本，改用 merge_chapter_state.py --sync-global 持续覆盖。
"""

import os
import sys
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from merge_chapter_state import (  # noqa: E402
    parse_md_table,
    sync_global_state,
    GLOBAL_STATE_DIRNAME,
)

STATE_FILE = "03_本章初始状态.md"


def discover_chapter_dirs(workspace_root):
    dirs = []
    for dirpath, _dirnames, filenames in os.walk(workspace_root):
        if STATE_FILE in filenames:
            dirs.append(os.path.normpath(dirpath))
    dirs.sort()
    return dirs


def main():
    ap = argparse.ArgumentParser(description="全局状态初始化迁移工具")
    ap.add_argument("novel_dir", help="小说数据目录路径 (含 05_工作区/ 与 02_数据库/)")
    ap.add_argument("--chapter-dir", help="显式指定作为基线的章目录（缺省取最新章）")
    ap.add_argument("--dry-run", action="store_true", help="仅打印将写入的内容概要，不落盘")
    args = ap.parse_args()

    novel_dir = os.path.abspath(args.novel_dir)
    if not os.path.isdir(novel_dir):
        print(f"错误: 小说目录不存在: {novel_dir}")
        sys.exit(1)

    if args.chapter_dir:
        base_chapter = os.path.abspath(args.chapter_dir)
    else:
        ws_root = os.path.join(novel_dir, "05_工作区")
        if not os.path.isdir(ws_root):
            print(f"错误: 未找到 05_工作区/ : {ws_root}")
            sys.exit(1)
        chapters = discover_chapter_dirs(ws_root)
        if not chapters:
            print(f"错误: 05_工作区/ 下未找到任何含 {STATE_FILE} 的章目录")
            sys.exit(1)
        base_chapter = chapters[-1]

    state_path = os.path.join(base_chapter, STATE_FILE)
    if not os.path.isfile(state_path):
        print(f"错误: 基线章节缺少 {STATE_FILE}: {state_path}")
        sys.exit(1)

    records = parse_md_table(state_path)
    global_dir = os.path.join(novel_dir, GLOBAL_STATE_DIRNAME)

    print(f"=== 全局状态迁移 ===")
    print(f"基线章节: {base_chapter}")
    print(f"来源文件: {state_path}（{len(records)} 条记录）")
    print(f"目标目录: {global_dir}")
    print()

    for l in sync_global_state(records, global_dir, dry_run=args.dry_run):
        print(f"  {l}")

    if args.dry_run:
        print("\n[Dry-run] 未写入文件。")


if __name__ == "__main__":
    main()
