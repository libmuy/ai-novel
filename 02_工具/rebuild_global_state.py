#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
全局状态重折工具 (rebuild_global_state.py)

用于「改早期章节」的场景：某一章的 `04_本章状态履历.md` 被人工改动后，
`04_全局状态/` 需要重新计算。

做法：从冻结基线 `05_工作区/00_全局/00_基线状态/` 出发，按章节路径顺序
折叠**全部** `04_本章状态履历.md`，覆盖写入 `04_全局状态/`。
不需要「起始章」参数——基线不可变，全量重折即可。

用法
----
    python3 02_工具/rebuild_global_state.py <小说目录> [--dry-run] [--merge-pending] [--backup]

      --dry-run         打印将对 04_全局状态/ 造成的 diff 与计划冻结回写的履历，不写文件
      --merge-pending   对存在未冻结描述变更的章，各调一次 LLM 合并并冻结回写
                        （缺省则遇到这类章直接中止、退出码 2）
      --backup          写前把 04_全局状态/ 整目录备份为 .bak

必须先 `--dry-run` 复核。
"""

import argparse
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import state_tree as st  # noqa: E402
from state_tree import records_diff, load_state_tree, StateMergeError, CHANGELOG_FILENAME  # noqa: E402
from merge_chapter_state import _make_llm_resolver  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="从基线全量重折 04_全局状态/")
    ap.add_argument("novel_dir", help="小说根目录")
    ap.add_argument("--dry-run", action="store_true", help="打印 diff 与计划，不写文件")
    ap.add_argument("--merge-pending", action="store_true",
                    help="对有未冻结描述变更的章各调一次 LLM 合并并冻结回写")
    ap.add_argument("--backup", action="store_true", help="写前把 04_全局状态/ 整目录备份为 .bak")
    args = ap.parse_args()

    novel_dir = os.path.abspath(args.novel_dir)
    baseline = st.baseline_dir(novel_dir)
    live = st.global_state_dir(novel_dir)
    tools_dir = os.path.dirname(os.path.abspath(__file__))

    if not os.path.isdir(baseline):
        print(f"错误: 冻结基线不存在: {baseline}")
        sys.exit(1)

    changelogs = st.iter_workspace_changelogs(novel_dir)
    print("=== 全局状态重折 ===")
    print(f"小说根: {novel_dir}")
    print(f"基线: {baseline}")
    print(f"待折叠章数: {len(changelogs)}")
    for p in changelogs:
        print(f"  - {st.chapter_rel_name(p, novel_dir)}")

    resolver = _make_llm_resolver(tools_dir) if args.merge_pending else None

    try:
        records, writebacks = st.fold_all(baseline, changelogs, resolver=resolver)
    except StateMergeError as e:
        print(f"\n[阻断] 重折中止，未写入任何文件：{e}")
        if not args.merge_pending:
            print("提示: 对上述涉及章逐一运行 merge_chapter_state.py，或本命令加 --merge-pending。")
        sys.exit(2)

    diff = records_diff(load_state_tree(live), records)
    print(f"\n折叠终态记录数: {len(records)}")
    if diff:
        print("\n--- 04_全局状态/ 将变更 ---")
        for line in diff:
            print(line)
    else:
        print("\n04_全局状态/ 无变化。")

    if writebacks:
        print("\n--- 将冻结回写以下履历（描述字段已由 LLM 合并）---")
        for p in writebacks:
            print(f"  {st.chapter_rel_name(p, novel_dir)}/{CHANGELOG_FILENAME}")

    if args.dry_run:
        print("\n[Dry-run] 未写入任何文件。人工复核 diff 无误后去掉 --dry-run 重跑。")
        return

    if args.backup and os.path.isdir(live):
        bak = live.rstrip("/") + ".bak"
        if os.path.isdir(bak):
            shutil.rmtree(bak)
        shutil.copytree(live, bak)
        print(f"已备份 {live} -> {bak}")

    for path, text in writebacks.items():
        st._atomic_write(path, text)
        print(f"已冻结回写 {st.chapter_rel_name(path, novel_dir)}/{CHANGELOG_FILENAME}")

    folded = st.chapter_rel_name(changelogs[-1], novel_dir) if changelogs else None
    for line in st.write_state_tree(live, records, folded_chapter=folded,
                                   tool="rebuild_global_state.py"):
        print(f"  {line}")

    print("\n重折完成。请立即运行 audit_consistency.py 复查一致性。")


if __name__ == "__main__":
    main()
