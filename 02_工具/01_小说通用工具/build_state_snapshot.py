#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
只读状态快照工具 (build_state_snapshot.py)

从冻结基线 `05_工作区/00_全局/00_基线状态/` 折叠履历，产出只读的单张 4 列状态表。
**只读、永不调 LLM**：折叠范围内若有未合并的描述字段变更 -> 报错、退出码 2
（提示先对那些章跑 merge_chapter_state.py）。

三种模式
--------
    # 卷末快照：折叠到该卷最后一章（含），写 99_卷末状态快照.md
    python3 02_工具/01_小说通用工具/build_state_snapshot.py --volume-dir <卷目录> [--output PATH]

    # 某章开篇状态：折叠到该章之前（不含），默认打印 stdout
    #   —— 回溯改旧章时，滑动窗口任务需要「第 N 章开篇时的世界状态」
    python3 02_工具/01_小说通用工具/build_state_snapshot.py --at-chapter <章目录> [--output PATH]

    # 逐章开篇状态物化（W4.1）：为每章生成/刷新 03_本章开篇状态.md（值拷贝、派生视图）
    #   按各章单章细纲的「## 出场对象」小节裁剪；清单缺失则写全量并告警。
    #   merge_chapter_state.py / rebuild_global_state.py 写完最新状态后会自动调用本模式。
    python3 02_工具/01_小说通用工具/build_state_snapshot.py --write-chapter-openers <小说目录>
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "00_系统级"))

import state_tree as st  # noqa: E402
from state_tree import render_md_table, StateMergeError, CHANGELOG_FILENAME  # noqa: E402


def _fold(novel_dir, changelog_paths):
    baseline = st.baseline_dir(novel_dir)
    if not os.path.isdir(baseline):
        print(f"错误: 冻结基线不存在: {baseline}")
        sys.exit(1)
    cache = st.load_merge_cache(novel_dir)
    try:
        records, _wb = st.fold_all(baseline, changelog_paths, cache=cache, resolver=None)
    except StateMergeError as e:
        print(f"\n[阻断] {e}\n先对涉及章运行 merge_chapter_state.py 合并描述字段后再生成快照。")
        sys.exit(2)
    return records


def write_chapter_openers(novel_dir, *, verbose=True):
    """为每个已建履历的章生成/刷新 03_本章开篇状态.md（派生视图）。
    第 N 章开篇状态 = 基线 ⊕ 折叠「排在第 N 章之前」的全部章履历，再按第 N 章
    单章细纲的「## 出场对象」清单裁剪。返回写入路径列表。

    供 CLI（--write-chapter-openers）与 merge/rebuild 收尾自动调用。
    基线不存在时静默跳过（返回 []）。"""
    baseline = st.baseline_dir(novel_dir)
    if not os.path.isdir(baseline):
        if verbose:
            print("跳过逐章开篇状态刷新：冻结基线尚未初始化")
        return []
    changelogs = st.iter_workspace_changelogs(novel_dir)
    prot = st.protagonist_state_id(novel_dir)
    written = []
    for i, cl in enumerate(changelogs):
        records = _fold(novel_dir, changelogs[:i])   # 严格早于本章的全部章
        chap_dir = os.path.dirname(cl)
        chap_name = st.chapter_rel_name(cl, novel_dir)
        cast = st.parse_chapter_cast(st.plan_path_for_chapter(chap_dir, novel_dir), prot)
        if cast is None:
            shown, missing = records, None
        else:
            present = {r["object_id"] for r in records}
            shown = [r for r in records if st.cast_contains(cast, r["object_id"])]
            missing = cast - present
        out = os.path.join(chap_dir, st.CHAPTER_OPENER_FILENAME)
        st._atomic_write(out, st.render_chapter_opener(shown, chap_name, cast, missing))
        written.append(out)
        if verbose:
            tag = "全量(无出场对象清单)" if cast is None else f"{len(shown)}/{len(records)} 对象"
            print(f"  开篇状态: {chap_name}  [{tag}]")
    return written


def main():
    ap = argparse.ArgumentParser(description="只读状态快照（从基线折叠履历，不调 LLM）")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--volume-dir", help="卷目录：折叠到该卷最后一章（含）")
    g.add_argument("--at-chapter", help="章目录：折叠到该章之前（不含）= 该章开篇状态")
    g.add_argument("--write-chapter-openers", metavar="小说目录",
                   help="为每章生成/刷新 03_本章开篇状态.md（按细纲「## 出场对象」裁剪）")
    ap.add_argument("--novel-dir", help="小说根目录（缺省自动定位）")
    ap.add_argument("--output", help="输出文件路径")
    args = ap.parse_args()

    if args.write_chapter_openers:
        nd = os.path.abspath(args.write_chapter_openers)
        nd = nd if os.path.isdir(os.path.join(nd, "05_工作区")) else st.find_novel_dir(nd)
        if not nd:
            print("错误: 无法定位小说根目录")
            sys.exit(1)
        w = write_chapter_openers(nd)
        print(f"共刷新 {len(w)} 个 03_本章开篇状态.md")
        return

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
