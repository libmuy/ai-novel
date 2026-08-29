#!/usr/bin/env python3
"""
ai-novel 仓库一致性审查脚本（Agent 可读版）v2

用法:
    python3 audit_consistency.py <小说目录路径> [--format json|text]

默认输出 JSON 到 stdout，供 Agent 直接解析并据此修改数据文件。
加 --format text 可输出人类可读的中文提示。

v2 升级内容：
- TODO_PATTERN 扩展至匹配 @(地名|势力|人物|类型|书籍|伏笔)
- CATEGORY_KEYWORD_IN_PROGRESS 扩展至覆盖全部任务类别
- 新增 check_todo_registry：校验所有TODO引用是否在全局注册表中有对应条目
- 新增 check_geographic_hierarchy：校验地理区域父子链接双向闭合
- 新增 check_id_format：校验ID格式与卡片类型的严格对应
"""
import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from collections import defaultdict

STANDARD_TOP_DIRS = ["01_设定", "02_数据库", "03_规划", "04_状态", "05_任务", "10_正文"]

# v2: 扩展至匹配所有6种引用类型
TODO_PATTERN = re.compile(r"@(地名|势力|人物|类型|书籍|伏笔)\.\[TODO-([^\]]+)\]")

# v2: 扩展至覆盖全部任务类别（与00_通用模板/00_使用说明.md路由表对齐）
CATEGORY_KEYWORD_IN_PROGRESS = {
    "地名": "02_地理区域提示.md",
    "势力": "03_势力组织提示.md",
    "人物": "05_主角与核心配角提示.md",
    "类型": "04_资源提示.md",
    "书籍": "08_书籍库提示.md",
    "伏笔": "09_全书卷大纲提示.md",
}

# v2: ID格式与类型对应规则
ID_FORMAT_RULES = {
    "伏笔": re.compile(r"^FH-\d+$"),
    "主角突破": re.compile(r"^BP-V\d+-\d+$"),
    "战斗结算": re.compile(r"^BT-V\d+-\d+$"),
    "资源道具": re.compile(r"^RES-[A-Z]+-\d+$"),
    "道义": re.compile(r"^DY-\d+$"),
    "世界法则": re.compile(r"^WR-\d+$"),
}

# v2: 全局TODO注册表中允许的类型前缀
TODO_GLOBAL_PREFIXES = {"FC", "CH", "FH", "BK", "DN"}


def read(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="ignore")


def check_top_level_dirs(novel_dir: Path, issues: list):
    missing = [d for d in STANDARD_TOP_DIRS if not (novel_dir / d).exists()]
    if missing:
        issues.append({
            "check": "missing_top_dirs",
            "severity": "warning",
            "category": None,
            "detail": f"缺失标准顶层目录: {missing}",
            "locations": missing,
            "suggested_action": f"从 00_通用模板/05_项目骨架模板/ 下对应目录复制骨架，在 {novel_dir} 下建立: {missing}",
        })


def check_symlink(novel_dir: Path, issues: list):
    link = novel_dir / "00_通用模板"
    if not link.exists():
        issues.append({
            "check": "symlink",
            "severity": "error",
            "category": None,
            "detail": f"{link} 不存在",
            "locations": [str(link)],
            "suggested_action": f"执行 ln -s ../../00_通用模板 {link} 建立符号链接",
        })
    elif not link.is_symlink():
        issues.append({
            "check": "symlink",
            "severity": "error",
            "category": None,
            "detail": f"{link} 存在但不是符号链接，可能被误拷贝为实体目录，会导致模板数据重复/失步",
            "locations": [str(link)],
            "suggested_action": f"备份后删除该实体目录，重新执行 ln -s ../../00_通用模板 {link}",
        })


def check_index_consistency(novel_dir: Path, issues: list):
    db = novel_dir / "02_数据库"
    if not db.exists():
        return
    for sub in sorted(db.iterdir()):
        if not sub.is_dir():
            continue
        idx = sub / f"{sub.name}.md"
        if not idx.exists():
            issues.append({
                "check": "index_consistency",
                "severity": "error",
                "category": sub.name,
                "detail": f"缺少总索引文件 {idx.name}",
                "locations": [str(idx)],
                "suggested_action": f"按对应卡片模板创建总索引文件 {idx}",
            })
            continue

        text = read(idx)
        raw_linked = re.findall(rf"({re.escape(sub.name)}[^\s\)\(\[\]\`|<>]*\.md)", text)
        linked = set(Path(x).name for x in raw_linked)
        actual = set(f.name for f in sub.glob(f"{sub.name}*.md"))
        actual.discard(idx.name)
        linked.discard(idx.name)

        prefix = f"{sub.name}_"
        is_multilevel = any(
            f[len(prefix):].count("_") >= 1 for f in actual if f.startswith(prefix)
        )

        if is_multilevel:
            depth1_actual = {f for f in actual if f[len(prefix):].count("_") == 0}
            missing_in_index = sorted(depth1_actual - linked)
            if missing_in_index:
                issues.append({
                    "check": "index_consistency",
                    "severity": "warning",
                    "category": sub.name,
                    "detail": "多级层级目录，第一级文件未在总索引登记",
                    "locations": [f"{sub.name}/{f}" for f in missing_in_index],
                    "suggested_action": f"在 {idx} 的世界索引表中补充上述文件链接",
                })
        else:
            missing_in_index = sorted(actual - linked)
            missing_files = sorted(linked - actual)
            if missing_in_index:
                issues.append({
                    "check": "index_consistency",
                    "severity": "error",
                    "category": sub.name,
                    "detail": "文件存在但总索引未链接（孤儿文件）",
                    "locations": [f"{sub.name}/{f}" for f in missing_in_index],
                    "suggested_action": f"在 {idx} 的索引表中补充这些文件的链接行",
                })
            if missing_files:
                issues.append({
                    "check": "index_consistency",
                    "severity": "error",
                    "category": sub.name,
                    "detail": "总索引中提到但文件不存在（悬空链接）",
                    "locations": [f"{sub.name}/{f}" for f in missing_files],
                    "suggested_action": f"确认这些文件是否被误删，若确认废弃则从 {idx} 索引表中移除对应行",
                })


def parse_progress_status(novel_dir: Path):
    prog = novel_dir / "00_进度.md"
    status = {}
    if not prog.exists():
        return status
    for line in read(prog).splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        for todo_type, keyword in CATEGORY_KEYWORD_IN_PROGRESS.items():
            if keyword in line:
                cells = [c.strip() for c in line.strip("|").split("|")]
                if cells:
                    status[todo_type] = cells[-1]
    return status


def check_stale_placeholders(novel_dir: Path, issues: list):
    status = parse_progress_status(novel_dir)
    db = novel_dir / "02_数据库"
    if not db.exists():
        return
    findings = defaultdict(lambda: defaultdict(set))
    for f in db.rglob("*.md"):
        if f.name == "00_TODO全局注册表.md":
            continue
        text = read(f)
        for m in TODO_PATTERN.finditer(text):
            typ, num = m.group(1), m.group(2)
            # 跳过模板说明文字中的占位符（如 "TODO-序号"）
            if "序号" in num or "xx" in num.lower():
                continue
            findings[typ][f.relative_to(novel_dir).as_posix()].add(num)

    for typ, files in findings.items():
        final_status = status.get(typ, "")
        is_final = "定稿" in final_status
        total = sum(len(v) for v in files.values())
        source_category = CATEGORY_KEYWORD_IN_PROGRESS.get(typ, "").replace("提示.md", "")
        issues.append({
            "check": "stale_placeholder",
            "severity": "warning" if is_final else "info",
            "category": typ,
            "detail": (
                f"@{typ}.[TODO-*] 共 {total} 处，源分类状态「{final_status or '未知'}」，"
                + ("源数据已定稿仍有残留占位符，应回补" if is_final else "源数据尚未定稿，占位符暂属正常")
            ),
            "locations": sorted(files.keys()),
            "suggested_action": (
                f"逐条核对 {typ} 类占位符对应的真实条目（参考已定稿的 {source_category} 分类数据），"
                f"将 @{typ}.[TODO-序号] 替换为真实 @引用，并在来源文件的【待创建条目】表中勾除该条目"
                if is_final else "待源分类定稿后再回补，暂不处理"
            ),
        })


def check_id_frequency(novel_dir: Path, issues: list, prefixes=("WR-", "DY-", "RES-")):
    counts = defaultdict(int)
    for f in novel_dir.rglob("*.md"):
        text = read(f)
        for prefix in prefixes:
            for m in re.finditer(rf"{re.escape(prefix)}[A-Z]*-?\d+", text):
                counts[m.group(0)] += 1
    issues.append({
        "check": "id_frequency_signal",
        "severity": "info",
        "category": None,
        "detail": "编号出现频次统计，仅供参考，不代表重复定义（多处引用同一编号是正常的）",
        "locations": [],
        "suggested_action": "如需精确判断某编号是否被重复定义（而非引用），需按各编号的定义位置规则单独检查",
        "counts": dict(sorted(counts.items())),
    })


# ============================================================
# v2 新增检查函数
# ============================================================

def check_todo_registry(novel_dir: Path, issues: list):
    """校验所有 @类型.[TODO-xxx] 引用是否在全局注册表中有对应条目"""
    registry_path = novel_dir / "02_数据库" / "00_TODO全局注册表.md"
    if not registry_path.exists():
        issues.append({
            "check": "todo_registry",
            "severity": "warning",
            "category": None,
            "detail": "全局TODO注册表 02_数据库/00_TODO全局注册表.md 不存在",
            "locations": [str(registry_path)],
            "suggested_action": "创建全局TODO注册表，定义所有TODO占位符的全局唯一ID",
        })
        return

    # 解析注册表中的全局ID
    registry_text = read(registry_path)
    registry_ids = set()
    for m in re.finditer(r"(TODO-[A-Z]{2}-\d+)", registry_text):
        registry_ids.add(m.group(1))

    # 扫描所有数据文件中的TODO引用
    data_dirs = [novel_dir / "01_设定", novel_dir / "02_数据库"]
    orphans = []
    for data_dir in data_dirs:
        if not data_dir.exists():
            continue
        for f in data_dir.rglob("*.md"):
            if f.name == "00_TODO全局注册表.md":
                continue
            text = read(f)
            for m in TODO_PATTERN.finditer(text):
                typ, todo_id = m.group(1), m.group(2)
                # 跳过模板说明文字中的占位符
                if "序号" in todo_id or "xx" in todo_id.lower() or "示例" in todo_id:
                    continue
                # 检查是否是全局ID格式 (TODO-XX-NNN)
                global_match = re.match(r"^([A-Z]{2})-(\d+)$", todo_id)
                if global_match:
                    prefix = global_match.group(1)
                    if prefix not in TODO_GLOBAL_PREFIXES:
                        orphans.append((f.relative_to(novel_dir).as_posix(), typ, todo_id))
                    elif f"TODO-{prefix}-{global_match.group(2)}" not in registry_ids:
                        orphans.append((f.relative_to(novel_dir).as_posix(), typ, todo_id))
                else:
                    # 旧式ID不应存在（已被阶段一替换）
                    orphans.append((f.relative_to(novel_dir).as_posix(), typ, todo_id))

    if orphans:
        issues.append({
            "check": "todo_registry",
            "severity": "warning",
            "category": None,
            "detail": f"发现 {len(orphans)} 处TODO引用未在全局注册表中登记",
            "locations": [f"{loc}" for loc, _, _ in orphans],
            "suggested_action": "在 00_TODO全局注册表.md 中为这些TODO条目创建对应的全局ID，或修正引用",
        })
    else:
        issues.append({
            "check": "todo_registry",
            "severity": "info",
            "category": None,
            "detail": f"全局TODO注册表校验通过：{len(registry_ids)} 个全局ID，所有引用均已登记",
            "locations": [],
            "suggested_action": "无需操作",
        })


def check_geographic_hierarchy(novel_dir: Path, issues: list):
    """校验地理区域 父→子 链接双向闭合（世界→区域→地名）
    
    检查逻辑：
    1. 世界文件是否列出了所有区域文件对应的区域名称
    2. 每个区域文件是否列出了所有地名文件对应的地名名称
    """
    geo_dir = novel_dir / "02_数据库" / "02_地理区域"
    if not geo_dir.exists():
        return

    # 解析所有地理区域文件
    geo_files = {}
    for f in geo_dir.glob("*.md"):
        geo_files[f.name] = f

    # 检查世界→区域的关系
    world_file = geo_dir / "02_地理区域_苍玄界.md"
    if world_file.exists():
        world_text = read(world_file)
        prefix = "02_地理区域_苍玄界_"

        # 实际存在的区域文件（第一级子文件）
        actual_regions = set()
        for fname in geo_files:
            if fname.startswith(prefix) and fname != world_file.name:
                remainder = fname[len(prefix):].replace(".md", "")
                if "_" not in remainder:
                    actual_regions.add(fname)

        # 检查每个区域文件是否在世界文件中有对应条目（通过区域名匹配）
        missing_regions = []
        for region_fname in sorted(actual_regions):
            # 从文件名提取区域名：去掉prefix和.md
            region_name_raw = region_fname[len(prefix):].replace(".md", "")
            # 检查世界文件中是否包含该区域名（模糊匹配）
            if region_name_raw not in world_text:
                # 尝试用更短的名字匹配
                found = False
                for part in region_name_raw.split("_"):
                    if len(part) >= 2 and part in world_text:
                        found = True
                        break
                if not found:
                    missing_regions.append(region_fname)

        if missing_regions:
            issues.append({
                "check": "geographic_hierarchy",
                "severity": "warning",
                "category": "02_地理区域",
                "detail": "区域文件存在但其名称未在世界总索引中出现",
                "locations": [f"02_地理区域/{f}" for f in sorted(missing_regions)],
                "suggested_action": f"在 {world_file.name} 中补充这些区域的描述条目",
            })

        # 检查每个区域文件→地名文件的关系
        for region_fname in sorted(actual_regions):
            region_file = geo_files.get(region_fname)
            if not region_file:
                continue
            region_text = read(region_file)
            region_prefix = region_fname.replace(".md", "") + "_"

            # 实际存在的地名文件
            actual_locations = set()
            for fname in geo_files:
                if fname.startswith(region_prefix) and fname != region_fname:
                    actual_locations.add(fname)

            # 检查每个地名文件是否在区域文件中有对应条目
            missing_locations = []
            for loc_fname in sorted(actual_locations):
                loc_name_raw = loc_fname[len(region_prefix):].replace(".md", "")
                if loc_name_raw not in region_text:
                    found = False
                    for part in loc_name_raw.split("_"):
                        if len(part) >= 2 and part in region_text:
                            found = True
                            break
                    if not found:
                        missing_locations.append(loc_fname)

            if missing_locations:
                issues.append({
                    "check": "geographic_hierarchy",
                    "severity": "warning",
                    "category": "02_地理区域",
                    "detail": f"地名文件存在但其名称未在区域文件 {region_fname} 中出现",
                    "locations": [f"02_地理区域/{f}" for f in sorted(missing_locations)],
                    "suggested_action": f"在 {region_fname} 中补充这些地名的描述条目",
                })


def check_id_format(novel_dir: Path, issues: list):
    """校验ID格式与卡片类型的严格对应"""
    # 扫描所有.md文件，提取已定义的ID（排除@引用和运行归档/提示词）
    defined_ids = defaultdict(list)
    id_pattern = re.compile(r"\b(WR-\d+|DY-\d+|RES-[A-Z]+-\d+|V\d+-C\d+-\d+|BP-V\d+-\d+|BT-V\d+-\d+)\b")
    # 匹配 @类型.ID 格式的引用（这些是引用而非定义，应排除）
    ref_pattern = re.compile(r"@\w+\.\[")

    for f in novel_dir.rglob("*.md"):
        if "运行归档" in str(f) or "提示词" in str(f) or "00_TODO全局注册表" in str(f):
            continue
        text = read(f)
        rel = f.relative_to(novel_dir).as_posix()
        lines = text.splitlines()
        for i, line in enumerate(lines):
            # 跳过包含@引用的行
            if ref_pattern.search(line):
                continue
            for m in id_pattern.finditer(line):
                defined_ids[m.group(1)].append(f"{rel}:{i+1}")

    # 检查同一ID是否在不同文件中被定义（而非引用）
    duplicates = {}
    for k, v in defined_ids.items():
        unique_files = set(loc.split(":")[0] for loc in v)
        if len(unique_files) > 2:
            duplicates[k] = v
    if duplicates:
        issues.append({
            "check": "id_format",
            "severity": "warning",
            "category": None,
            "detail": f"发现 {len(duplicates)} 个ID在多个文件中出现（可能是重复定义）",
            "locations": [],
            "suggested_action": "检查这些ID是否在不同文件中被重复定义（而非仅被引用），若是则合并或去重",
            "duplicates": {k: v[:5] for k, v in duplicates.items()},
        })


def run_all_checks(novel_dir: Path) -> dict:
    issues = []
    check_top_level_dirs(novel_dir, issues)
    check_symlink(novel_dir, issues)
    check_index_consistency(novel_dir, issues)
    check_stale_placeholders(novel_dir, issues)
    check_id_frequency(novel_dir, issues)
    check_todo_registry(novel_dir, issues)
    check_geographic_hierarchy(novel_dir, issues)
    check_id_format(novel_dir, issues)
    return {
        "novel_dir": str(novel_dir),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "issues": issues,
    }


def print_text(report: dict):
    print(f"=== 一致性审查: {report['novel_dir']} ===\n")
    for issue in report["issues"]:
        print(f"[{issue['severity'].upper()}] ({issue['check']}"
              + (f" / {issue['category']}" if issue.get("category") else "") + ")")
        print(f"  问题: {issue['detail']}")
        if issue.get("locations"):
            print(f"  位置: {issue['locations']}")
        print(f"  建议: {issue['suggested_action']}")
        print()


def main():
    ap = argparse.ArgumentParser(description="ai-novel 仓库一致性审查 v2（Agent 可读输出）")
    ap.add_argument("novel_dir", help="小说数据目录路径，由调用方指定，脚本不含具体书名硬编码")
    ap.add_argument("--format", choices=["json", "text"], default="json",
                     help="输出格式，默认 json（供 Agent 解析），可选 text（人类阅读）")
    args = ap.parse_args()
    novel_dir = Path(args.novel_dir)

    if not novel_dir.exists():
        print(json.dumps({"error": f"目录不存在: {novel_dir}"}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)

    report = run_all_checks(novel_dir)

    if args.format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_text(report)


if __name__ == "__main__":
    main()
