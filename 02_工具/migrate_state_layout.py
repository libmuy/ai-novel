#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
状态布局迁移工具 (migrate_state_layout.py) —— 一次性

把旧模型的状态布局迁移到新模型：
  - 旧：05_工作区/00_全局/01_最新状态/ 下 5 个扁平分类文件（01_角色状态.md … 05_世界状态.md）
        + 每章 03_本章初始状态.md（链式）+ 卷末 99_卷末状态快照.md
  - 新：05_工作区/00_全局/01_最新状态/ 每对象一文件的目录树（唯一权威）
        + 冻结基线 05_工作区/00_全局/00_基线状态/
        + 每章只留 04_本章状态履历.md

**只改结构，不改内容**：5 个扁平文件里的记录逐字复制成基线；已知的内容不一致
（对象名、数值对不上等）不在本工具职责内。

用法:
    python3 02_工具/migrate_state_layout.py <小说目录> [--dry-run]

迁移后请对最新章运行 merge_chapter_state.py 让全局状态推进到该章之后的终态。
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import state_tree as st  # noqa: E402

FLAT_FILES = ["01_角色状态.md", "02_物品状态.md", "03_势力状态.md",
              "04_财务状态.md", "05_世界状态.md"]

NEW_EXPLAIN = """\
# 05_工作区/00_全局/01_最新状态/ 说明

> 全局状态 = 全书唯一权威的对象状态存储。每个对象一个文件，按类目分子目录。

## 目录结构

```
05_工作区/00_全局/01_最新状态/
├── 00_说明.md          本文件（手写）
├── 00_同步状态.md       脚本写：manifest（折叠至章 / 时间 / 计数），非权威
├── 01_角色/  01_角色.md（索引）  01_角色_<名>.md ...
├── 02_物品/  ...
├── 03_势力/  ...
├── 04_财务/  ...
├── 05_世界/  ...
└── 99_其他/  ...        前缀不属五大类的兜底
```

对象 ID 前缀 -> 类目：`角色.→01_角色` `物品.→02_物品` `势力.→03_势力`
`财务.→04_财务` `世界.→05_世界`。对象名里文件系统不安全的字符按 `%XX` 编码进文件名；
读取以文件内表格第 1 列「对象ID」为准，文件名只是派生物。

## 与基线的关系

`05_工作区/00_全局/01_最新状态/` == 冻结基线 `05_工作区/00_全局/00_基线状态/`
⊕（按章节路径顺序折叠全部 `04_本章状态履历.md`）。

## 维护（全部脚本覆盖写入，禁止手工编辑）

- 章末推进：`python3 02_工具/merge_chapter_state.py --chapter-dir <本章>`
- 改早期章后重折：`python3 02_工具/rebuild_global_state.py <小说目录>`（先 --dry-run）
- 某章开篇状态：`python3 02_工具/build_state_snapshot.py --at-chapter <章目录>`

字段名与类型的权威定义见 `00_通用模板/03_字段词表.md`。
"""


def main():
    ap = argparse.ArgumentParser(description="状态布局迁移（扁平 -> 每对象一文件的树）")
    ap.add_argument("novel_dir")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    novel = os.path.abspath(args.novel_dir)
    gdir = os.path.join(novel, "05_工作区/00_全局/01_最新状态")
    baseline = st.baseline_dir(novel)

    if not os.path.isdir(gdir):
        print(f"错误: 找不到 {gdir}")
        sys.exit(1)

    flat_present = [f for f in FLAT_FILES if os.path.isfile(os.path.join(gdir, f))]
    tree_present = any(os.path.isdir(os.path.join(gdir, c)) for c in st.CATEGORY_ORDER)

    if not flat_present and (tree_present or os.path.isdir(baseline)):
        print("已是新布局（无扁平文件），无需迁移。")
        return
    if not flat_present:
        print(f"错误: {gdir} 下既没有扁平分类文件，也不是已迁移的树布局。")
        sys.exit(1)

    records = []
    for f in flat_present:
        records.extend(st.parse_md_table(os.path.join(gdir, f)))
    print(f"从 {len(flat_present)} 个扁平文件读到 {len(records)} 条记录。")

    legacy_03 = list(_rglob(os.path.join(novel, "05_工作区"), "03_本章初始状态.md"))
    legacy_snap = list(_rglob(os.path.join(novel, "05_工作区"), "99_卷末状态快照.md"))

    plan = [
        f"写基线树   -> {baseline}",
        f"写全局状态树 -> {gdir}（折叠至章: {st.NONE_MARKER}）",
        f"重写        -> {os.path.join(gdir, '00_说明.md')}",
    ]
    plan += [f"删扁平文件   -> 05_工作区/00_全局/01_最新状态/{f}" for f in flat_present]
    plan += [f"删逐章初始态 -> {os.path.relpath(p, novel)}" for p in legacy_03]
    plan += [f"删卷末快照   -> {os.path.relpath(p, novel)}" for p in legacy_snap]

    print("\n=== 迁移计划 ===")
    for line in plan:
        print("  " + line)

    if args.dry_run:
        print("\n[Dry-run] 未写盘。")
        return

    st.write_state_tree(baseline, records, folded_chapter=None, tool="migrate_state_layout.py",
                        manifest=False, note=st.BASELINE_NOTE)
    st.write_state_tree(gdir, records, folded_chapter=None, tool="migrate_state_layout.py")
    st._atomic_write(os.path.join(gdir, "00_说明.md"), NEW_EXPLAIN)
    for f in flat_present:
        os.remove(os.path.join(gdir, f))
    for p in legacy_03 + legacy_snap:
        os.remove(p)

    print("\n迁移完成。下一步：")
    print("  python3 02_工具/audit_consistency.py " + os.path.relpath(novel) + " --format text")
    print("  python3 02_工具/merge_chapter_state.py --chapter-dir <最新章目录>   # 让全局状态推进到该章之后")


def _rglob(root, name):
    if not os.path.isdir(root):
        return
    for dp, _d, fs in os.walk(root):
        if name in fs:
            yield os.path.join(dp, name)


if __name__ == "__main__":
    main()
