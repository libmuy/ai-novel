#!/usr/bin/env python3
"""
ai-novel 仓库一致性审查脚本（Agent 可读版）

用法:
    python3 audit_consistency.py <小说目录路径> [--format json|text]

默认输出 JSON 到 stdout，供 Agent 直接解析并据此修改数据文件。
加 --format text 可输出人类可读的中文提示。

路径说明：<小说目录路径> 完全由调用方传入（如 01_小说数据/00_苍玄、
01_小说数据/01_xxx），脚本本身不含任何具体某本书的硬编码路径。
唯一的耦合点是「通用模板自身的命名规范」——见下方 CATEGORY_KEYWORD_IN_PROGRESS
（任务提示词文件名）与 STANDARD_TOP_DIRS（标准骨架目录名）：这两处按当前
00_通用模板 的编号规则写死，如果模板任务编号/骨架目录名以后调整，需要
同步改这里，脚本不会自动感知、也不会报错提示。

JSON 输出结构:
{
  "novel_dir": "...",
  "generated_at": "...",
  "issues": [
    {
      "check": "missing_top_dirs" | "symlink" | "index_consistency" |
                "stale_placeholder" | "id_frequency_signal",
      "severity": "error" | "warning" | "info",
      "category": "<涉及的分类目录名，若适用>",
      "detail": "<具体问题描述>",
      "locations": ["<相对路径>", ...],
      "suggested_action": "<给 Agent 的具体修改建议>"
    },
    ...
  ]
}
"""
import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from collections import defaultdict

STANDARD_TOP_DIRS = ["01_设定", "02_数据库", "03_规划", "04_状态", "05_任务", "10_正文"]

TODO_PATTERN = re.compile(r"@(地名|势力|人物|类型)\.\[TODO-([^\]]+)\]")

# 耦合点：任务提示词文件名 -> 00_进度.md 中对应分类关键字
CATEGORY_KEYWORD_IN_PROGRESS = {
    "地名": "02_地理区域提示.md",
    "势力": "03_势力组织提示.md",
    "人物": "05_主角与核心配角提示.md",
}


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
            issues.append({
                "check": "index_consistency",
                "severity": "info",
                "category": sub.name,
                "detail": "多级层级目录（世界/区域/地名），深层文件的父子链接完整性未做递归校验，需人工/扩展脚本单独核对",
                "locations": [],
                "suggested_action": "如需完整校验，逐级检查父文件是否链接了其全部子文件",
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
        text = read(f)
        for m in TODO_PATTERN.finditer(text):
            typ, num = m.group(1), m.group(2)
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


def run_all_checks(novel_dir: Path) -> dict:
    issues = []
    check_top_level_dirs(novel_dir, issues)
    check_symlink(novel_dir, issues)
    check_index_consistency(novel_dir, issues)
    check_stale_placeholders(novel_dir, issues)
    check_id_frequency(novel_dir, issues)
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
    ap = argparse.ArgumentParser(description="ai-novel 仓库一致性审查（Agent 可读输出）")
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
