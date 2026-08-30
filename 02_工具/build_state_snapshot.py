#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
只读状态快照工具 (build_state_snapshot.py)

从冻结基线 `05_工作区/00_全局/00_基线状态/` 折叠履历，产出只读的单张 4 列状态表。
**只读、永不调 LLM**：折叠范围内若有未冻结的描述字段变更 -> 报错、退出码 2
（提示先对那些章跑 merge_chapter_state.py）。

两种模式
--------
    # 卷末快照：折叠到该卷最后一章（含），写 99_卷末状态快照.md
    python3 02_工具/build_state_snapshot.py --volume-dir <卷目录> [--output PATH]

    # 某章开篇状态：折叠到该章之前（不含），默认打印 stdout
    #   —— 回溯改旧章时，滑动窗口任务需要「第 N 章开篇时的世界状态」
    python3 02_工具/build_state_snapshot.py --at-chapter <章目录> [--output PATH]
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import state_tree as st  # noqa: E402
from state_tree import render_md_table, StateMergeError, CHANGELOG_FILENAME  # noqa: E402


def _fold(novel_dir, changelog_paths):
    baseline = st.baseline_dir(novel_dir)
    if not os.path.isdir(baseline):
        print(f"错误: 冻结基线不存在: {baseline}")
        sys.exit(1)
    try:
        records, _wb = st.fold_all(baseline, changelog_paths, resolver=None)
    except StateMergeError as e:
        print(f"\n[阻断] {e}\n先对涉及章运行 merge_chapter_state.py 冻结描述字段后再生成快照。")
        sys.exit(2)
    return records


def main():
    ap = argparse.ArgumentParser(description="只读状态快照（从基线折叠履历，不调 LLM）")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--volume-dir", help="卷目录：折叠到该卷最后一章（含）")
    g.add_argument("--at-chapter", help="章目录：折叠到该章之前（不含）= 该章开篇状态")
    ap.add_argument("--novel-dir", help="小说根目录（缺省自动定位）")
    ap.add_argument("--output", help="输出文件路径")
    args = ap.parse_args()

    anchor = os.path.abspath(args.volume_dir or args.at_chapter)
    novel_dir = os.path.abspath(args.novel_dir) if args.novel_dir else st.find_novel_dir(anchor)
    if not novel_dir:
        print("错误: 无法定位小说根目录，请用 --novel-dir 指定")
        sys.exit(1)

    changelogs = st.iter_workspace_changelogs(novel_dir)

    if args.volume_dir:
        vol = os.path.normpath(anchor)
        in_vol = [p for p in changelogs if os.path.normpath(p).startswith(vol + os.sep)]
        if not in_vol:
            print(f"警告: 卷目录下没有任何 {CHANGELOG_FILENAME}: {vol}")
            sys.exit(1)
        last_idx = changelogs.index(in_vol[-1])
        paths = changelogs[:last_idx + 1]
        vol_name = os.path.basename(vol)
        title = f"卷末状态快照 · {vol_name} (只读物化视图)"
        default_out = os.path.join(vol, "99_卷末状态快照.md")
    else:
        target_cl = os.path.normpath(os.path.join(anchor, CHANGELOG_FILENAME))
        if target_cl in changelogs:
            idx = changelogs.index(target_cl)
        else:
            # 该章可能还没建履历——折叠到「排序位置之前」的全部章
            idx = len([p for p in changelogs if p < target_cl])
        paths = changelogs[:idx]
        chap_name = st.chapter_rel_name(target_cl, novel_dir)
        title = f"开篇状态快照 · {chap_name} (只读)"
        default_out = None

    records = _fold(novel_dir, paths)
    rendered = render_md_table(records, title=title)

    out = args.output or default_out
    if out:
        st._atomic_write(out, rendered)
        print(f"已写入: {out}（{len(records)} 条记录，折叠 {len(paths)} 章）")
    else:
        print(rendered)


if __name__ == "__main__":
    main()
