#!/usr/bin/env python3
"""
02_工具/01_小说通用工具/auto_link_placeholders.py

自动化 Agent 占位符回补与拓扑修复工具 (落地计划 5)

用法:
    python3 02_工具/01_小说通用工具/auto_link_placeholders.py <小说目录路径> [--dry-run]

功能:
1. 读取小说目录下的 02_数据库/00_TODO全局注册表.md 以及 02_数据库/ 各分类下的实体定稿文件，
   建立 @类型.[TODO-xxx] 到 @类型.[实名] 的映射字典；
2. 遍历 02_数据库/、03_规划/、05_工作区/ 中所有 .md 文件，批量将占位符替换为实名；
3. 检查文件尾部 【待创建条目】 列表/表格，将 `- [ ]` 自动勾选更新为 `- [x]`；
4. 输出替换日志与统计报告。
"""

import argparse
import os
import re
import sys
from pathlib import Path

# 定义引用类型与目录前缀映射
CATEGORY_DIR_MAP = {
    "势力": ("02_数据库/03_势力组织", "03_势力组织_"),
    "人物": ("02_数据库/07_人物", "07_人物_"),
    "书籍": ("02_数据库/06_书籍", "06_书籍_"),
    "体系": ("02_数据库/01_修炼体系", "01_修炼体系_"),
    "区域": ("02_数据库/02_地理区域", "02_地理区域_"),
    "地名": ("02_数据库/02_地理区域", "02_地理区域_"),
    "资源": ("02_数据库/04_资源", "04_资源_"),
    "类型": ("02_数据库/04_资源", "04_资源_"),
}

# TODO 编号前缀映射
TODO_PREFIX_CAT = {
    "FC": "势力",
    "CH": "人物",
    "FH": "伏笔",
    "BK": "书籍",
    "DN": "地名",
}


def build_entity_map(novel_dir: Path) -> dict:
    """从数据库定稿卡片以及 TODO全局注册表中搜集实名映射"""
    mapping = {}

    # 1. 搜集数据库各分类下已存在的实名实体
    # 注意：只解析文件本身作为定义卡片时声明的 TODO-ID（例如在【基础档案】或头部定义的全局ID），
    # 避免将文件中引用的其他第三方 TODO-ID 错挂到当前文件名上。
    for cat, (rel_dir, prefix) in CATEGORY_DIR_MAP.items():
        target_dir = novel_dir / rel_dir
        if not target_dir.exists():
            continue
        for f in target_dir.rglob("*.md"):
            if f.name.startswith("00_") or f.name == f"{target_dir.name}.md":
                continue
            stem = f.stem
            if stem.startswith(prefix):
                name = stem[len(prefix):]
            else:
                name = stem
            # 处理多级地理文件名，如 苍玄界_灰壤凡域_枯港矿城 -> 枯港矿城
            if "_" in name:
                short_name = name.split("_")[-1]
            else:
                short_name = name

            content = f.read_text(encoding="utf-8", errors="ignore")
            # 仅匹配该文件作为实体定义卡片声明自身 TODO-ID 的情况（如【全局ID】或定义表格首列）
            for line in content.splitlines():
                if "全局ID" in line or "【基础档案】" in line or "卡片名称" in line:
                    for m in re.finditer(r"(TODO-[A-Z]{2}-\d+)", line):
                        todo_id = m.group(1)
                        mapping[(cat, todo_id)] = f"@{cat}.[{short_name}]"

    # 2. 解析 02_数据库/ 下各个卡片末尾的 ## 【TODO状态更新】 表格
    db_dir = novel_dir / "02_数据库"
    if db_dir.exists():
        for f in db_dir.rglob("*.md"):
            if f.name == "00_TODO全局注册表.md":
                continue
            text = f.read_text(encoding="utf-8", errors="ignore")
            if "TODO状态更新" in text or "TODO" in text:
                in_table = False
                for line in text.splitlines():
                    line_s = line.strip()
                    if "TODO" in line_s and "状态" in line_s and "|" in line_s:
                        in_table = True
                        continue
                    if in_table:
                        if not line_s.startswith("|"):
                            in_table = False
                            continue
                        if "---" in line_s:
                            continue
                        parts = [p.strip() for p in line_s.split("|")[1:-1]]
                        if len(parts) >= 3:
                            todo_id, new_name, status = parts[0], parts[1], parts[2]
                            if todo_id.startswith("TODO-") and "已创建" in status:
                                prefix_match = re.search(r"TODO-([A-Z]{2})-\d+", todo_id)
                                if prefix_match:
                                    prefix = prefix_match.group(1)
                                    cat = TODO_PREFIX_CAT.get(prefix)
                                    if cat:
                                        mapping[(cat, todo_id)] = f"@{cat}.[{new_name}]"

    # 3. 读取 00_TODO全局注册表.md（如果有显式关联）
    registry_file = novel_dir / "02_数据库" / "00_TODO全局注册表.md"
    if registry_file.exists():
        reg_text = registry_file.read_text(encoding="utf-8", errors="ignore")
        # 匹配 表格行中包含 全局ID 与 实名/状态 的条目
        # 例如 | TODO-FC-001 | 控制矿区的低阶家族 | ... | 已创建 (陈家 / @势力.[铁砂陈家])
        for line in reg_text.splitlines():
            if not line.startswith("|") or "全局ID" in line or "---" in line:
                continue
            parts = [p.strip() for p in line.split("|")[1:-1]]
            if len(parts) >= 2:
                todo_id = parts[0]
                m_target = re.search(r"@(\w+)\.\[([^\]]+)\]", line)
                if m_target:
                    c_type, c_name = m_target.group(1), m_target.group(2)
                    mapping[(c_type, todo_id)] = f"@{c_type}.[{c_name}]"

    return mapping


def process_file(file_path: Path, mapping: dict, dry_run: bool = False) -> tuple:
    """对单个文件执行占位符替换与待创建条目勾选"""
    try:
        content = file_path.read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        return 0, False, str(e)

    original_content = content
    replacements_made = 0

    # 1. 匹配并替换 @类型.[TODO-xxx]
    todo_pattern = re.compile(r"@(\w+)\.\[(TODO-[^\]]+)\]")

    def replacer(match):
        nonlocal replacements_made
        c_type, todo_id = match.group(1), match.group(2)
        key = (c_type, todo_id)
        if key in mapping:
            replacements_made += 1
            return mapping[key]
        return match.group(0)

    content = todo_pattern.sub(replacer, content)

    # 2. 自动勾选 【待创建条目】 清单（- [ ] 改为 - [x]）
    # 当被引用的 TODO 项已经在 mapping 中被替换了或者对应的 TODO ID 已被处理
    todo_check_pattern = re.compile(r"(-\s*\[\s*\]\s*@?(\w+)\.\[(TODO-[^\]]+)\])")

    def check_replacer(match):
        nonlocal replacements_made
        full_match, c_type, todo_id = match.group(1), match.group(2), match.group(3)
        if (c_type, todo_id) in mapping:
            replacements_made += 1
            return full_match.replace("[ ]", "[x]")
        return match.group(0)

    content = todo_check_pattern.sub(check_replacer, content)

    # 如果有修改且非 dry_run，写回文件
    changed = content != original_content
    if changed and not dry_run:
        file_path.write_text(content, encoding="utf-8")

    return replacements_made, changed, None


def run_auto_link(novel_dir: Path, dry_run: bool = False) -> dict:
    """运行占位符自动回补全流程"""
    mapping = build_entity_map(novel_dir)
    target_dirs = ["01_设定", "02_数据库", "03_规划", "05_工作区"]

    total_replacements = 0
    modified_files = []

    for sub in target_dirs:
        sub_path = novel_dir / sub
        if not sub_path.exists():
            continue
        for root, _, files in os.walk(sub_path):
            for file in files:
                if not file.endswith(".md"):
                    continue
                file_path = Path(root) / file
                if file == "00_TODO全局注册表.md":
                    continue

                count, changed, err = process_file(file_path, mapping, dry_run)
                if count > 0 or changed:
                    total_replacements += count
                    modified_files.append(str(file_path.relative_to(novel_dir)))

    return {
        "novel_dir": str(novel_dir),
        "mapping_count": len(mapping),
        "total_replacements": total_replacements,
        "modified_files_count": len(modified_files),
        "modified_files": modified_files,
        "dry_run": dry_run,
    }


def main():
    parser = argparse.ArgumentParser(description="自动化 Agent 占位符回补与拓扑修复工具")
    parser.add_argument("novel_dir", help="小说数据目录路径（如 01_小说数据/00_苍玄）")
    parser.add_argument("--dry-run", action="store_true", help="演练模式，只输出 diff 与统计不实际修改文件")
    args = parser.parse_args()

    novel_dir = Path(args.novel_dir)
    if not novel_dir.exists():
        print(f"错误: 目录不存在 {novel_dir}", file=sys.stderr)
        sys.exit(1)

    result = run_auto_link(novel_dir, dry_run=args.dry_run)
    print(f"=== 占位符自动回补执行完成 {'(演练模式)' if result['dry_run'] else ''} ===")
    print(f"匹配规则条数: {result['mapping_count']}")
    print(f"已替换/勾选占位符: {result['total_replacements']} 处")
    print(f"涉及修改文件数: {result['modified_files_count']} 个")
    if result["modified_files"]:
        print("修改文件列表:")
        for f in result["modified_files"]:
            print(f"  - {f}")


if __name__ == "__main__":
    main()
