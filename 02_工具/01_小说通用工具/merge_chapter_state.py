#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
章级状态合并工具 (merge_chapter_state.py)

把某一章的 `04_本章状态履历.md` 折叠进小说的唯一权威状态存储 `05_工作区/00_全局/01_最新状态/`
（每对象一文件、按类目分层的目录树）。

模型
----
- `05_工作区/00_全局/01_最新状态/` == 冻结基线 `05_工作区/00_全局/00_基线状态/` ⊕（按章节路径顺序
  折叠全部 `04_本章状态履历.md`）。
- 本工具**始终从基线全量重折**到 `--chapter-dir` 对应的那一章（不做增量）：
  基线不可变 → 重折幂等自愈；每个履历极小、纯 Python，重放不调 LLM。
- 运算-数值 / 运算-枚举 / 运算-列表：纯 Python，确定性。
- 「描述」类字段发生实质变更时：把本次全部待合并描述字段打包成**一次** LLM 调用
  （旧文本 + 新文本合并不丢信息），合并结果写进独立的 append-only 缓存
  `00_描述合并缓存.jsonl`，**永不修改履历原文**。
- LLM 不可用（无配置 / 无 key / 网络失败）且本章确有待合并描述变更 → 中止合并、
  退出码 2、**不写任何文件**。纯运算章节无需 key。

用法
----
    python3 02_工具/01_小说通用工具/merge_chapter_state.py --chapter-dir <章目录> [选项]

      --chapter-dir PATH   必填，要并入 05_工作区/00_全局/01_最新状态/ 的那一章
      --novel-dir PATH     可选，缺省从 --chapter-dir 向上自动定位
      --dry-run            计算（含调一次 LLM 预览合并文本）+ 打印 diff，不写任何文件
      --backup/--no-backup 写前是否备份（默认开启备份）
      --no-llm             本章若有待合并描述变更 -> 直接中止、退出码 2（不调 LLM）
      --force              允许并入非「工作区最新章」
"""

import argparse
import os
import shutil
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "00_系统级"))

import state_tree as st  # noqa: E402
import _llm  # noqa: E402

from state_tree import (  # noqa: E402,F401
    parse_md_table,
    merge_states,
    render_md_table,
    records_diff,
    fold_all,
    load_state_tree,
    write_state_tree,
    parse_number,
    _atomic_write,
    StateMergeError,
    CHANGELOG_META_COLUMNS,
    NUMERIC_EMPTY_VALUES,
    CHANGELOG_FILENAME,
    load_merge_cache,
    append_merge_cache,
    value_fingerprint,
)

_REMOVED_ARGS = [
    "--initial", "--changelog", "--output", "--next-chapter-dir",
    "--sync-global", "--global-state-dir", "--review-descriptive",
]


def _make_llm_resolver(tools_dir):
    """
    返回 resolver(pending) -> {(obj,field): merged_text}。
    首次被调用时才加载 LLM 配置（纯运算章节永不加载、无需 key）。
    LlmError 一律转成 StateMergeError（中止合并、不写文件）。
    """
    cache = {}

    def resolver(pending):
        try:
            if "cfg" not in cache:
                cache["cfg"] = _llm.load_llm_config(tools_dir)
            return _llm.merge_descriptive_fields(cache["cfg"], pending)
        except _llm.LlmError as e:
            raise StateMergeError(f"描述字段智能合并失败：{e}")

    return resolver


def main():
    ap = argparse.ArgumentParser(description="章级状态合并工具（把本章履历折叠进 01_最新状态/）")
    ap.add_argument("--chapter-dir", required=True, help="要并入 01_最新状态/ 的那一章目录")
    ap.add_argument("--novel-dir", help="小说根目录（缺省从 --chapter-dir 向上自动定位）")
    ap.add_argument("--dry-run", action="store_true",
                    help="计算 + 调一次 LLM 预览合并文本 + 打印 diff，不写任何文件")
    ap.add_argument("--backup", action="store_true", default=True,
                    help="写前备份（默认开启）")
    ap.add_argument("--no-backup", action="store_true",
                    help="关闭写前备份")
    ap.add_argument("--no-llm", action="store_true",
                    help="本章若有待合并描述变更则直接中止（退出码 2），不调 LLM")
    ap.add_argument("--force", action="store_true", help="允许并入非工作区最新章")
    for old in _REMOVED_ARGS:
        ap.add_argument(old, help=argparse.SUPPRESS)

    args = ap.parse_args()

    for old in _REMOVED_ARGS:
        if getattr(args, old.lstrip("-").replace("-", "_")) is not None:
            print(f"错误: 参数 {old} 已移除。新模型下本工具只接受 --chapter-dir；"
                  f"回溯重算用 rebuild_global_state.py，卷末快照用 build_state_snapshot.py。")
            sys.exit(2)

    tools_dir = os.path.dirname(os.path.abspath(__file__))
    chapter_dir = os.path.abspath(args.chapter_dir)
    target_cl = os.path.join(chapter_dir, CHANGELOG_FILENAME)
    if not os.path.isfile(target_cl):
        print(f"错误: 章目录缺少 {CHANGELOG_FILENAME}: {chapter_dir}")
        sys.exit(1)

    novel_dir = os.path.abspath(args.novel_dir) if args.novel_dir else st.find_novel_dir(chapter_dir)
    if not novel_dir:
        print("错误: 无法定位小说根目录（需含 02_数据库/ 和 05_工作区/），请用 --novel-dir 指定")
        sys.exit(1)

    baseline = st.baseline_dir(novel_dir)
    live = st.latest_state_dir(novel_dir)
    if not os.path.isdir(baseline):
        print(f"错误: 冻结基线不存在: {baseline}\n"
              f"先用技能 08_基线状态初始化 生成基线（新书），或 migrate_state_layout.py 迁移（旧书）。")
        sys.exit(1)

    changelogs = st.iter_workspace_changelogs(novel_dir)
    target_norm = os.path.normpath(target_cl)
    if target_norm not in changelogs:
        print(f"错误: {target_norm} 不在工作区履历清单中")
        sys.exit(1)
    idx = changelogs.index(target_norm)
    if idx != len(changelogs) - 1 and not args.force:
        later = [st.chapter_rel_name(p, novel_dir) for p in changelogs[idx + 1:]]
        print(f"错误: 目标章不是工作区最新章，其后还有: {later}\n"
              f"正常应逐章按顺序 merge；确需并入旧章请加 --force，或用 rebuild_global_state.py 全量重折。")
        sys.exit(1)

    paths = changelogs[:idx + 1]
    resolver = None if args.no_llm else _make_llm_resolver(tools_dir)
    cache = st.load_merge_cache(novel_dir)
    chapter_names = [st.chapter_rel_name(p, novel_dir) for p in paths]

    try:
        records, new_entries = st.fold_all(baseline, paths, cache=cache,
                                           resolver=resolver, chapter_names=chapter_names)
    except StateMergeError as e:
        print(f"\n[阻断] 合并中止，未写入任何文件：{e}")
        sys.exit(2)

    folded_chapter = st.chapter_rel_name(target_cl, novel_dir)
    live_before = st.load_state_tree(live)
    diff = records_diff(live_before, records)

    print(f"=== 合并摘要 [{folded_chapter}] ===")
    print(f"折叠章数: {len(paths)} | 折叠终态记录数: {len(records)} | 01_最新状态/ 现有记录数: {len(live_before)}")
    if diff:
        print("\n--- 01_最新状态/ 将变更 ---")
        for line in diff:
            print(line)
    else:
        print("\n01_最新状态/ 无变化。")

    if new_entries:
        print(f"\n--- 描述字段合并缓存将追加 {len(new_entries)} 条 ---")

    if args.dry_run:
        print("\n[Dry-run] 未写入任何文件。")
        return

    do_backup = not args.no_backup
    if do_backup:
        if os.path.isdir(live):
            bak = live.rstrip("/") + ".bak"
            if os.path.isdir(bak):
                shutil.rmtree(bak)
            shutil.copytree(live, bak)
            print(f"已备份 {live} -> {bak}")
        if os.path.exists(target_cl):
            shutil.copyfile(target_cl, target_cl + ".bak")

    # 写序：先缓存落盘 → 再写状态树
    st.append_merge_cache(novel_dir, new_entries)
    for line in st.write_state_tree(live, records, folded_chapter=folded_chapter,
                                   tool="merge_chapter_state.py"):
        print(f"  {line}")

    print("\n合并完成。请运行 audit_consistency.py 复查一致性。")


if __name__ == "__main__":
    main()
