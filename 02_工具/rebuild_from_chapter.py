#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
级联重放工具 (rebuild_from_chapter.py)

用于「修改早期章节情节」的场景：某一章的 04_本章状态履历.md 被人工改动后，
该章之后所有章节的 03_本章初始状态.md 都需要按新的履历链重新计算。

流程：
  1. 输入起始章节目录（被人工修改履历的那一章）。
  2. 自动发现同一 05_工作区/ 下、按完整路径排序在其之后的全部章节目录。
  3. 从起始章节的 03 + （已人工修改的）04 开始，依次 merge_states，
     覆盖写入下一章的 03_本章初始状态.md，逐章级联。
  4. 全部重放完成后，对最终合并终态执行一次 --sync-global，刷新 04_全局状态/。

安全网：
  - 默认 --review-descriptive 开启（描述类字段变更逐条打印 旧值 vs 新值）。
  - **必须先跑 --dry-run**：打印每章 03 的 diff，人工确认后再去掉 --dry-run 正式执行。
    级联覆盖不可逆，这一步比单章合并更关键。

用法:
    python3 02_工具/rebuild_from_chapter.py <起始章节目录> --dry-run
    python3 02_工具/rebuild_from_chapter.py <起始章节目录>            # 正式执行（交互确认）
    python3 02_工具/rebuild_from_chapter.py <起始章节目录> --yes --backup
"""

import os
import sys
import argparse
import shutil

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from merge_chapter_state import (  # noqa: E402
    parse_md_table,
    merge_states,
    render_md_table,
    records_diff,
    sync_global_state,
    locate_global_state_dir,
    _atomic_write,
    StateMergeError,
)

STATE_FILE = "03_本章初始状态.md"
CHANGELOG_FILE = "04_本章状态履历.md"
WORKSPACE_DIRNAME = "05_工作区"


def find_workspace_root(chapter_dir):
    cur = os.path.normpath(os.path.abspath(chapter_dir))
    while True:
        if os.path.basename(cur) == WORKSPACE_DIRNAME:
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            return None
        cur = parent


def discover_chapter_dirs(workspace_root):
    dirs = []
    for dirpath, _dirnames, filenames in os.walk(workspace_root):
        if STATE_FILE in filenames:
            dirs.append(os.path.normpath(dirpath))
    dirs.sort()
    return dirs


def main():
    ap = argparse.ArgumentParser(description="级联重放工具（改早期章节后重算后续 03）")
    ap.add_argument("start_chapter_dir", help="起始章节目录（履历被人工修改的那一章）")
    ap.add_argument("--dry-run", action="store_true",
                    help="仅打印每章 03 的 diff 与全局同步计划，不写任何文件")
    ap.add_argument("--no-review-descriptive", action="store_true",
                    help="关闭描述类字段逐条复核（默认开启）")
    ap.add_argument("--global-state-dir",
                    help="04_全局状态/ 目录路径（缺省自动定位）")
    ap.add_argument("--backup", action="store_true",
                    help="覆盖每个 03 前备份为 .bak")
    ap.add_argument("--yes", action="store_true",
                    help="跳过正式写入前的交互确认（非 dry-run 时）")
    args = ap.parse_args()

    start = os.path.normpath(os.path.abspath(args.start_chapter_dir))
    if not os.path.isfile(os.path.join(start, STATE_FILE)):
        print(f"错误: 起始章节缺少 {STATE_FILE}: {start}")
        sys.exit(1)

    ws_root = find_workspace_root(start)
    if not ws_root:
        print(f"错误: 未能从 {start} 向上定位 {WORKSPACE_DIRNAME}/ 目录")
        sys.exit(1)

    all_chaps = discover_chapter_dirs(ws_root)
    if start not in all_chaps:
        print(f"错误: 起始章节不在工作区章节列表中: {start}")
        sys.exit(1)

    idx = all_chaps.index(start)
    replay = all_chaps[idx:]

    print("=== 级联重放 ===")
    print(f"工作区根: {ws_root}")
    print(f"起始章节: {os.path.relpath(start, ws_root)}")
    print(f"待重放章节数: {len(replay)}（含起始章）")
    for c in replay:
        print(f"  - {os.path.relpath(c, ws_root)}")
    print()

    review = not args.no_review_descriptive
    interactive = not args.dry_run

    curr = parse_md_table(os.path.join(start, STATE_FILE))
    planned = []  # (target_path, merged_records, diff_lines)

    for i, chap in enumerate(replay):
        cl_path = os.path.join(chap, CHANGELOG_FILE)
        cl = parse_md_table(cl_path) if os.path.exists(cl_path) else []
        rel = os.path.relpath(chap, ws_root)
        print(f"--- 重放 {rel}（履历 {len(cl)} 条）---")
        try:
            curr, diff_logs = merge_states(
                curr, cl, review_descriptive=review, interactive=interactive,
            )
        except StateMergeError as e:
            print(f"\n[阻断] {rel} 履历合并失败，级联中止，未写入任何文件：{e}")
            print("请修正该章 04_本章状态履历.md 后重跑。")
            sys.exit(2)
        for log in diff_logs:
            print(f"    * {log}")

        if i + 1 < len(replay):
            nxt = replay[i + 1]
            target = os.path.join(nxt, STATE_FILE)
            old = parse_md_table(target) if os.path.exists(target) else []
            dlines = records_diff(old, curr)
            planned.append((target, list(curr), dlines))
            nxt_rel = os.path.relpath(nxt, ws_root)
            if dlines:
                print(f"    => {nxt_rel}/{STATE_FILE} 将变更:")
                for dl in dlines:
                    print(f"    {dl}")
            else:
                print(f"    => {nxt_rel}/{STATE_FILE} 无变化")
        print()

    global_dir = args.global_state_dir or locate_global_state_dir(ws_root)
    if not global_dir:
        print("警告: 无法自动定位 04_全局状态/ 目录，将跳过 --sync-global。可用 --global-state-dir 指定。")

    if args.dry_run:
        print("--- --sync-global 计划（基于最终合并终态）---")
        if global_dir:
            for l in sync_global_state(curr, global_dir, dry_run=True):
                print(f"  {l}")
        print(f"\n[DRY-RUN] 未写入任何文件。共 {len(planned)} 个 {STATE_FILE} 待覆盖。")
        print("人工确认每章 diff 无误后，去掉 --dry-run 重新执行。")
        return

    if not args.yes and sys.stdin.isatty():
        ans = input(
            f"\n将覆盖 {len(planned)} 个 {STATE_FILE} 并同步 04_全局状态/，确认执行? [y/N] "
        ).strip().lower()
        if ans != 'y':
            print("已取消，未写入任何文件。")
            sys.exit(1)

    for target, recs, _ in planned:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        if args.backup and os.path.exists(target):
            shutil.copyfile(target, target + ".bak")
        _atomic_write(target, render_md_table(recs))
        print(f"已覆盖 {os.path.relpath(target, ws_root)}")

    if global_dir:
        print("\n--- --sync-global：覆盖写入 04_全局状态/ ---")
        for l in sync_global_state(curr, global_dir, dry_run=False):
            print(f"  {l}")

    print("\n级联重放完成。请立即运行 audit_consistency.py 复查一致性。")


if __name__ == "__main__":
    main()
