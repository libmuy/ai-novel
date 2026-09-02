#!/usr/bin/env python3
"""测试辅助工具：造合成小说目录结构、写状态表、LLM 桩。"""

import hashlib
import os
import sys

# 确保能导入 state_tree / _llm
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

NOVEL_STRUCTURE = {
    "01_设定": {},
    "02_数据库": {},
    "03_规划": {},
    "05_工作区/02_状态/01_最新状态": {
        "00_说明.md": "# 全局状态\n\n> 由状态脚本自动管理。\n",
        "00_同步状态.md": "# 05_工作区/02_状态/01_最新状态 · 同步状态\n\n> 折叠至章: __none__\n",
    },
    "05_工作区": {
        "02_状态": {
            "00_基线状态": {
                "00_说明.md": "# 基线状态\n\n> 创世基线快照 · 只读不可变。\n",
            },
        },
    },
    "10_正文": {},
}


def write_table(path, rows, cols=4):
    """写标准 Markdown 状态表到 path。rows 为 list[list[str]]。"""
    header = "| " + " | ".join(["对象ID", "字段", "类型", "值"][:cols]) + " |"
    sep = "| " + " | ".join(["---"] * cols) + " |"
    lines = [header, sep]
    for row in rows:
        lines.append("| " + " | ".join(row[:cols]) + " |")
    lines.append("")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def make_novel(tmpdir, baseline_records=None, chapters=None):
    """
    造合成小说的完整目录结构。

    tmpdir:     临时根目录（如 /tmp/test_xxx）
    baseline_records: list[list[str]]，写入 05_工作区/00_全局/01_最新状态/01_角色/01_角色_示例.md
    chapters:   dict {章目录名: list[list[str]]}，每项写入对应的 04_本章状态履历.md

    返回小说根路径。
    """
    novel = os.path.join(tmpdir, "test_novel")
    os.makedirs(novel, exist_ok=True)

    # 造顶层目录
    for d in ("01_设定", "02_数据库", "03_规划", "05_工作区/02_状态/01_最新状态", "10_正文"):
        os.makedirs(os.path.join(novel, d), exist_ok=True)

    # 造 05_工作区
    ws = os.path.join(novel, "05_工作区")
    os.makedirs(os.path.join(ws, "00_提示词"), exist_ok=True)
    os.makedirs(os.path.join(ws, "01_模型输出"), exist_ok=True)
    os.makedirs(os.path.join(ws, "02_状态"), exist_ok=True)

    # 基线
    bl = os.path.join(ws, "02_状态", "00_基线状态")
    os.makedirs(bl, exist_ok=True)
    with open(os.path.join(bl, "00_说明.md"), "w", encoding="utf-8") as f:
        f.write("# 基线状态\n\n> 创世基线快照 · 只读不可变。\n")

    # 05_工作区/02_状态/01_最新状态/
    gs = os.path.join(novel, "05_工作区/02_状态/01_最新状态")
    os.makedirs(gs, exist_ok=True)
    with open(os.path.join(gs, "00_说明.md"), "w", encoding="utf-8") as f:
        f.write("# 全局状态\n\n> 由状态脚本自动管理。\n")
    with open(os.path.join(gs, "00_同步状态.md"), "w", encoding="utf-8") as f:
        f.write("# 05_工作区/02_状态/01_最新状态 · 同步状态\n\n> 折叠至章: __none__\n")

    # 基线数据：按 object_id 分组写入类目目录
    if baseline_records:
        _write_records_to_dir(bl, baseline_records)

    # 各章履历
    if chapters:
        for chap_name, rows in chapters.items():
            chap_dir = os.path.join(ws, chap_name, "02_状态")
            os.makedirs(chap_dir, exist_ok=True)
            cl_path = os.path.join(chap_dir, "01_状态履历.md")
            _write_changelog(cl_path, rows)

    return novel


def _write_records_to_dir(state_dir, records):
    """把 records (list[list[str]]) 按 object_id 分组写入 state_dir 的类目目录。"""
    from state_tree import CATEGORY_BY_PREFIX, OTHER_CATEGORY
    by_obj = {}
    for row in records:
        obj_id = row[0]
        by_obj.setdefault(obj_id, []).append(row)

    for obj_id, rows in by_obj.items():
        prefix = obj_id.split(".", 1)[0]
        cat = CATEGORY_BY_PREFIX.get(prefix, OTHER_CATEGORY)
        cat_dir = os.path.join(state_dir, cat)
        os.makedirs(cat_dir, exist_ok=True)
        name = obj_id.split(".", 1)[1] if "." in obj_id else obj_id
        fname = f"{cat}_{name}.md"
        path = os.path.join(cat_dir, fname)
        write_table(path, rows)


def _write_changelog(path, rows):
    """写 04_本章状态履历.md（7 列：4 基础 + 章节号/变更时间/变更类型）。"""
    header = "| 对象ID | 字段 | 类型 | 值 | 章节号 | 变更时间 | 变更类型 |"
    sep = "| --- | --- | --- | --- | --- | --- | --- |"
    lines = [
        "# 本章状态履历",
        "",
        "> 本文件是权威数据，记录本章所有状态变更。append-only。",
        "",
        header, sep,
    ]
    for row in rows:
        # 补齐到 7 列
        r = list(row)
        while len(r) < 7:
            r.append("-")
        lines.append("| " + " | ".join(r) + " |")
    lines.append("")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


class StubLlm:
    """可替换 _llm.merge_descriptive_fields 的桩。

    记录被调用次数，返回可预测拼接（不用 | 避免表格冲突）。
    """

    def __init__(self):
        self.call_count = 0

    def __call__(self, pending):
        self.call_count += 1
        result = {}
        for obj_id, field, old, new in pending:
            # 可预测拼接：旧值 + '～' + 新值
            result[(obj_id, field)] = f"{old}～{new}"
        return result


def sha256_file(path):
    """返回文件内容的 sha256 hexdigest。"""
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()
