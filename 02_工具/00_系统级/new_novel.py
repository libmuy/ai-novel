#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
新建小说脚手架 (new_novel.py) —— 纯文件操作，无 LLM

    python3 02_工具/00_系统级/new_novel.py <小说名> [--dry-run]

步骤：
  1. 算目录编号 NN（01_小说数据/ 下现有 `NN_*` 目录最大值 +1，两位，从 00 起）
  2. 从 00_通用模板/05_项目骨架模板/ 复制骨架到 01_小说数据/<NN>_<小说名>/
  3. 建相对符号链接 00_通用模板 -> ../../00_通用模板
  4. 由 00_通用模板/00_小说级AGENTS模板.md 生成 AGENTS.md（去用法说明段、替换占位符）
  5. 改 00_进度.md 标题
  6. 打印目录树与自检结果

后续云端规划由技能 07_新建小说.md 交接。
"""

import argparse
import os
import re
import shutil
import sys

# 本脚本位于 02_工具/00_系统级/，仓库根需向上三级
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SKELETON = os.path.join(REPO_ROOT, "00_通用模板", "05_项目骨架模板")
AGENTS_TEMPLATE = os.path.join(REPO_ROOT, "00_通用模板", "00_小说级AGENTS模板.md")
DATA_DIR = os.path.join(REPO_ROOT, "01_小说数据")

TOP_DIRS = ["01_设定", "02_数据库", "03_规划", "05_工作区", "10_正文"]


def next_nn():
    nums = []
    if os.path.isdir(DATA_DIR):
        for name in os.listdir(DATA_DIR):
            m = re.match(r"^(\d{2})_", name)
            if m and os.path.isdir(os.path.join(DATA_DIR, name)):
                nums.append(int(m.group(1)))
    return f"{(max(nums) + 1) if nums else 0:02d}"


def render_agents(name, nn):
    text = open(AGENTS_TEMPLATE, encoding="utf-8").read()
    # 去掉首个 '---' 之前的「用法说明」段
    if "\n---\n" in text:
        text = text.split("\n---\n", 1)[1].lstrip("\n")
    return text.replace("{{小说名}}", name).replace("{{NN}}", nn)


def tree(root, prefix="", depth=0, max_depth=3):
    if depth > max_depth:
        return
    try:
        entries = sorted(e for e in os.listdir(root) if not e.startswith(".git"))
    except OSError:
        return
    for i, e in enumerate(entries):
        p = os.path.join(root, e)
        last = i == len(entries) - 1
        mark = "└── " if last else "├── "
        if os.path.islink(p):
            print(f"{prefix}{mark}{e} -> {os.readlink(p)}")
        elif os.path.isdir(p):
            print(f"{prefix}{mark}{e}/")
            tree(p, prefix + ("    " if last else "│   "), depth + 1, max_depth)
        else:
            print(f"{prefix}{mark}{e}")


def main():
    ap = argparse.ArgumentParser(description="新建小说脚手架（无 LLM）")
    ap.add_argument("name", help="小说名")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    name = args.name.strip()
    if not name or "/" in name or name.startswith("."):
        print(f"错误: 小说名非法: {name!r}")
        sys.exit(1)
    if not os.path.isdir(SKELETON):
        print(f"错误: 骨架模板不存在: {SKELETON}")
        sys.exit(1)

    nn = next_nn()
    dest = os.path.join(DATA_DIR, f"{nn}_{name}")
    if os.path.exists(dest):
        print(f"错误: 目标已存在: {dest}")
        sys.exit(1)

    print(f"小说名: {name}")
    print(f"目录编号: {nn}")
    print(f"目标目录: {dest}")
    print("计划步骤:")
    print(f"  1. copytree {os.path.relpath(SKELETON, REPO_ROOT)} -> {os.path.relpath(dest, REPO_ROOT)}")
    print(f"  2. symlink  {os.path.relpath(dest, REPO_ROOT)}/00_通用模板 -> ../../00_通用模板")
    print(f"  3. 生成     {os.path.relpath(dest, REPO_ROOT)}/AGENTS.md（占位符 {{{{小说名}}}}->{name} / {{{{NN}}}}->{nn}）")
    print(f"  4. 改标题   {os.path.relpath(dest, REPO_ROOT)}/00_进度.md")

    if args.dry_run:
        print("\n[Dry-run] 未写盘。")
        return

    shutil.copytree(SKELETON, dest)
    os.symlink("../../00_通用模板", os.path.join(dest, "00_通用模板"))

    agents_path = os.path.join(dest, "AGENTS.md")
    with open(agents_path, "w", encoding="utf-8") as f:
        f.write(render_agents(name, nn))

    prog_path = os.path.join(dest, "00_进度.md")
    if os.path.exists(prog_path):
        txt = open(prog_path, encoding="utf-8").read()
        txt = re.sub(r"^# .*?· 进度追踪", f"# {name} · 进度追踪", txt, count=1, flags=re.M)
        with open(prog_path, "w", encoding="utf-8") as f:
            f.write(txt)

    print("\n=== 目录树 ===")
    print(f"{nn}_{name}/")
    tree(dest, "")

    print("\n=== 自检 ===")
    checks = []
    link = os.path.join(dest, "00_通用模板")
    checks.append(("符号链接可解析", os.path.islink(link) and os.path.isdir(link)))
    for d in TOP_DIRS:
        checks.append((f"顶层目录 {d}", os.path.isdir(os.path.join(dest, d))))
    checks.append(("AGENTS.md 无残留 {{", "{{" not in open(agents_path, encoding="utf-8").read()))
    checks.append(("05_工作区/02_状态/01_最新状态/00_说明.md", os.path.isfile(os.path.join(dest, "05_工作区/02_状态/01_最新状态", "00_说明.md"))))
    checks.append(("05_工作区/02_状态/01_最新状态/00_同步状态.md", os.path.isfile(os.path.join(dest, "05_工作区/02_状态/01_最新状态", "00_同步状态.md"))))
    checks.append(("00_基线状态/00_说明.md", os.path.isfile(os.path.join(dest, "05_工作区", "02_状态", "00_基线状态", "00_说明.md"))))
    checks.append(("无逐章 03_本章初始状态.md", not any(
        "03_本章初始状态.md" in fs for _r, _d, fs in os.walk(dest))))
    ok = True
    for label, passed in checks:
        print(f"  [{'x' if passed else ' '}] {label}")
        ok = ok and passed

    print("\n下一步：进入云端提示词管线（见技能 00_通用模板/03_任务技能/02_小说级/07_新建小说.md）。")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
