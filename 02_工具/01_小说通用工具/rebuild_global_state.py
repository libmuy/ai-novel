#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
全局状态重折工具 (rebuild_global_state.py)

用于「改早期章节」的场景：某一章的 `01_状态履历.md` 被人工改动后，
`01_最新状态/` 需要重新计算。

做法：从冻结基线 `05_工作区/02_状态/00_基线状态/` 出发，按章节路径顺序
折叠**全部** `01_状态履历.md`，覆盖写入 `01_最新状态/`。
不需要「起始章」参数——基线不可变，全量重折即可。

用法
----
    python3 02_工具/01_小说通用工具/rebuild_global_state.py <小说目录> [--dry-run] [--merge-pending] [--backup]

      --dry-run         打印将对 01_最新状态/ 造成的 diff 与计划追加的缓存条目，不写文件
      --merge-pending   对存在未合并描述变更的章，各调一次 LLM 合并并追加缓存
                        （缺省则遇到这类章直接中止、退出码 2）
      --backup          写前把 01_最新状态/ 整目录备份为 .bak

必须先 `--dry-run` 复核。
"""

import argparse
import os
import shutil
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "00_系统级"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import state_tree as st  # noqa: E402
from state_lock import acquire_until_exit, StateLockError  # noqa: E402
from state_tree import records_diff, load_state_tree, StateMergeError, CHANGELOG_FILENAME  # noqa: E402
from merge_chapter_state import _make_llm_resolver  # noqa: E402


def main():
    """入口。真正的重折在 _run() 里，外面接住写锁失败（W6.2）。"""
    try:
        return _run()
    except StateLockError as e:
        print(f"\n错误：{e}", file=sys.stderr)
        sys.exit(3)


def _run():
    ap = argparse.ArgumentParser(description="从基线全量重折 01_最新状态/")
    ap.add_argument("novel_dir", help="小说根目录")
    ap.add_argument("--dry-run", action="store_true", help="打印 diff 与计划，不写文件")
    ap.add_argument("--merge-pending", action="store_true",
                    help="对有未合并描述变更的章各调一次 LLM 合并并追加缓存")
    ap.add_argument("--backup", action="store_true", help="写前把 01_最新状态/ 整目录备份为 .bak")
    args = ap.parse_args()

    novel_dir = os.path.abspath(args.novel_dir)
    baseline = st.baseline_dir(novel_dir)
    live = st.latest_state_dir(novel_dir)

    # W6.2 写锁：整个「读全量 → 算 → 全量重写」都在锁内；拿不到就中止、不写文件。
    if not args.dry_run:
        acquire_until_exit(os.path.dirname(live.rstrip("/")), tool="rebuild_global_state.py")
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
    cache = st.load_merge_cache(novel_dir)
    chapter_names = [st.chapter_rel_name(p, novel_dir) for p in changelogs]

    try:
        records, new_entries = st.fold_all(baseline, changelogs, cache=cache,
                                           resolver=resolver, chapter_names=chapter_names)
    except StateMergeError as e:
        print(f"\n[阻断] 重折中止，未写入任何文件：{e}")
        if not args.merge_pending:
            print("提示: 对上述涉及章逐一运行 merge_chapter_state.py，或本命令加 --merge-pending。")
        sys.exit(2)

    diff = records_diff(load_state_tree(live), records)
    print(f"\n折叠终态记录数: {len(records)}")
    if diff:
        print("\n--- 01_最新状态/ 将变更 ---")
        for line in diff:
            print(line)
    else:
        print("\n01_最新状态/ 无变化。")

    if new_entries:
        print(f"\n--- 描述字段合并缓存将追加 {len(new_entries)} 条 ---")

    if args.dry_run:
        print("\n[Dry-run] 未写入任何文件。人工复核 diff 无误后去掉 --dry-run 重跑。")
        return

    if args.backup and os.path.isdir(live):
        bak = live.rstrip("/") + ".bak"
        if os.path.isdir(bak):
            shutil.rmtree(bak)
        shutil.copytree(live, bak)
        print(f"已备份 {live} -> {bak}")

    # 写序：先缓存落盘 → 再写状态树
    st.append_merge_cache(novel_dir, new_entries)

    folded = st.chapter_rel_name(changelogs[-1], novel_dir) if changelogs else None
    for line in st.write_state_tree(live, records, folded_chapter=folded,
                                   tool="rebuild_global_state.py"):
        print(f"  {line}")

    # 刷新逐章开篇状态派生视图（W4.1）
    try:
        from build_state_snapshot import write_chapter_openers
        print("\n刷新逐章开篇状态 00_开篇状态.md ...")
        write_chapter_openers(novel_dir)
    except Exception as e:  # noqa: BLE001 —— 开篇状态刷新失败不应中断重折
        print(f"  警告: 逐章开篇状态刷新失败（{e}）；请手动跑 build_state_snapshot.py --write-chapter-openers")

    print("\n重折完成。请立即运行 audit_consistency.py 复查一致性。")


if __name__ == "__main__":
    main()
