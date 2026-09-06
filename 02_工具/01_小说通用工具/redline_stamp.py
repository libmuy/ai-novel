#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
常驻红线包上游指纹戳 (redline_stamp.py)

`00_系统架构规范.md` §二·A：一个事实的第二份呈现只准三种形态——不重复 /
脚本派生的只读视图 / 被 audit 交叉校验。`01_设定/00_红线包.md` 是**人工蒸馏**
的第二份呈现（从主角档案 / 文风 / 法宝卡 / 经济卡 / 禁用词表 / 通用写作规则
生成版里摘出「逐章不变的那半份约束」），长期是被漏掉的第四种：无脚本派生、
无审计守护。后果与当年的规则切片一模一样——上游改了、红线包没跟，而红线包
是正文提示词【必读规则】段的打头件、整段内联，它一漂移，每一章正文都跟着漂。

本工具给红线包 §八【刷新触发】表补一列「上次核对指纹」：把「我已按当前上游
核对过本包」这件事**戳**下来。之后上游任一文件变更，指纹对不上，
`audit_consistency.py` 的 `REDLINE001` 就报出来，逼一次重核。

指纹口径与 W6.1 状态树三方指纹一致（`02_工具/00_系统级/state_tree.py`
`value_fingerprint`——sha256 前 16 位）。

用法
----
    redline_stamp.py <小说目录> [--check | --write | --list]

    --check   （默认）逐行比对上游当前指纹与表里记录，不一致打印差异并返回 1
    --write   按上游当前内容回写指纹列（只动这一列，不碰表格其余内容）
    --list    只列出 §八 表解析到的 (上游文件 → 重核小节) 映射
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

REDLINE_REL = "01_设定/00_红线包.md"
SECTION_TITLE_RE = re.compile(r"^##\s+八[、.]")
ANY_H2_RE = re.compile(r"^##\s+")
MD_REF_RE = re.compile(r"`([^`\n]+?\.md)`")
TABLE_ROW_RE = re.compile(r"^\s*\|(.+)\|\s*$")
SEP_ROW_RE = re.compile(r"^\s*\|[\s:|\-]+\|\s*$")
NO_FILE = "—"          # 该行没有可解析的上游文件（如「进卷」行）
STAMP_HEADER = "上次核对指纹"


def value_fingerprint(text: str) -> str:
    """内容指纹。与 state_tree.value_fingerprint 同口径：sha256 前 16 位。"""
    return hashlib.sha256((text or "").strip().encode("utf-8")).hexdigest()[:16]


@dataclass
class RefreshRow:
    lineno: int                       # 红线包里的 1-based 行号
    cells: list[str]                   # 原始单元格（strip 后）
    refs: list[str] = field(default_factory=list)   # col1 里按序抽出的 .md 引用


@dataclass
class ParsedTable:
    header: list[str]                  # 表头单元格
    sep_lineno: int
    rows: list[RefreshRow]
    has_stamp_col: bool


class RedlineError(Exception):
    pass


def _split_row(inner: str) -> list[str]:
    return [c.strip() for c in inner.split("|")]


def locate_section(lines: list[str]) -> tuple[int, int]:
    """→ §八【刷新触发】的 (起始行 idx, 结束行 idx)，含头不含尾。找不到抛 RedlineError。"""
    start = None
    for i, line in enumerate(lines):
        if SECTION_TITLE_RE.match(line):
            start = i
            break
    if start is None:
        raise RedlineError("红线包里找不到 `## 八、…` 小节——【刷新触发】表缺失，守护无从落地")
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if ANY_H2_RE.match(lines[j]):
            end = j
            break
    return start, end


def parse_table(text: str) -> ParsedTable:
    """解析红线包 §八 的 Markdown 表。"""
    lines = text.splitlines()
    start, end = locate_section(lines)

    header = None
    sep_lineno = None
    rows: list[RefreshRow] = []
    for i in range(start, end):
        line = lines[i]
        m = TABLE_ROW_RE.match(line)
        if not m:
            continue
        if SEP_ROW_RE.match(line):
            sep_lineno = i
            continue
        cells = _split_row(m.group(1))
        if header is None:
            header = cells
            continue
        row = RefreshRow(lineno=i, cells=cells)
        row.refs = MD_REF_RE.findall(cells[0]) if cells else []
        rows.append(row)

    if header is None or sep_lineno is None:
        raise RedlineError("红线包 §八 小节里没有可解析的 Markdown 表")

    has_stamp = any(STAMP_HEADER in h for h in header)
    return ParsedTable(header=header, sep_lineno=sep_lineno, rows=rows, has_stamp_col=has_stamp)


def resolve_upstream(novel_dir: Path, ref: str) -> Path | None:
    """把红线包 §八 里的 `路径.md` 解析成磁盘文件。

    小说目录下 `00_通用模板` 是指向仓库根的软链，故 `01_设定/…` 与
    `00_通用模板/…` 两类路径都能直接落到 novel_dir 下。软链断了则返回 None
    （审计会据此报「上游文件不存在」）。
    """
    cand = novel_dir / ref
    if cand.is_file():
        return cand
    # 兜底：向上找真实仓库根（含真实 00_通用模板 目录）
    cur = novel_dir
    for _ in range(6):
        if (cur / "00_通用模板").is_dir() and not (cur / "00_通用模板").is_symlink():
            alt = cur / ref
            return alt if alt.is_file() else None
        if cur.parent == cur:
            break
        cur = cur.parent
    return None


def compute_row_stamp(novel_dir: Path, row: RefreshRow) -> tuple[str, list[str]]:
    """→ (该行应写入指纹列的字符串, 缺失文件列表)。"""
    if not row.refs:
        return NO_FILE, []
    parts: list[str] = []
    missing: list[str] = []
    for ref in row.refs:
        p = resolve_upstream(novel_dir, ref)
        if p is None:
            missing.append(ref)
            parts.append("缺失")
        else:
            parts.append(value_fingerprint(p.read_text(encoding="utf-8", errors="ignore")))
    return ", ".join(parts), missing


def _rebuild_row_line(row: RefreshRow, stamp: str, width: int) -> str:
    """按现有单元格 + 新指纹重排一行（补/换第 `width` 列）。"""
    cells = list(row.cells)
    # 去掉尾部空串（split('|') 常在行尾多出一个）
    while cells and cells[-1] == "":
        cells.pop()
    while len(cells) < width - 1:
        cells.append("")
    if len(cells) >= width:
        cells[width - 1] = stamp
    else:
        cells.append(stamp)
    return "| " + " | ".join(cells) + " |"


def write_stamps(novel_dir: Path) -> tuple[str, list[str]]:
    """回写指纹列。→ (新全文, 变更说明列表)。"""
    path = novel_dir / REDLINE_REL
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=False)
    table = parse_table(text)

    width = max(3, len(table.header) if table.has_stamp_col else len(table.header) + 1)
    notes: list[str] = []

    # 表头 + 分隔行
    if not table.has_stamp_col:
        hdr_line = lines[table.sep_lineno - 1]
        m = TABLE_ROW_RE.match(hdr_line)
        hcells = _split_row(m.group(1))
        while hcells and hcells[-1] == "":
            hcells.pop()
        hcells.append(STAMP_HEADER)
        lines[table.sep_lineno - 1] = "| " + " | ".join(hcells) + " |"
        lines[table.sep_lineno] = "|" + "|".join(["---"] * len(hcells)) + "|"
        notes.append(f"§八 表补列「{STAMP_HEADER}」")
    else:
        lines[table.sep_lineno] = "|" + "|".join(["---"] * width) + "|"

    for row in table.rows:
        stamp, missing = compute_row_stamp(novel_dir, row)
        lines[row.lineno] = _rebuild_row_line(row, stamp, width)
        label = row.refs[0] if row.refs else row.cells[1] if len(row.cells) > 1 else "?"
        if missing:
            notes.append(f"第 {row.lineno+1} 行：上游缺失 {'、'.join(missing)}")
        else:
            notes.append(f"第 {row.lineno+1} 行（{label}）→ {stamp}")

    return "\n".join(lines) + ("\n" if text.endswith("\n") else ""), notes


def check_stamps(novel_dir: Path) -> list[str]:
    """→ 不一致项的人读描述列表（空 = 干净）。"""
    path = novel_dir / REDLINE_REL
    table = parse_table(path.read_text(encoding="utf-8"))
    problems: list[str] = []

    if not table.has_stamp_col:
        return [f"§八 表还没有「{STAMP_HEADER}」列——跑 `redline_stamp.py <小说> --write` 建列并落戳"]

    stamp_idx = next(i for i, h in enumerate(table.header) if STAMP_HEADER in h)
    for row in table.rows:
        recorded = row.cells[stamp_idx] if len(row.cells) > stamp_idx else ""
        expected, missing = compute_row_stamp(novel_dir, row)
        loc = f"红线包 §八 第 {row.lineno+1} 行"
        if missing:
            problems.append(f"{loc}：上游文件不存在 —— {'、'.join(missing)}（软链断了？）")
            continue
        if not recorded:
            problems.append(f"{loc}（{row.refs[0] if row.refs else '—'}）：指纹列为空，从未核对过")
            continue
        if recorded != expected:
            sect = row.cells[1] if len(row.cells) > 1 else "对应小节"
            problems.append(
                f"{loc}：{'、'.join(row.refs)} 自上次核对后已变更"
                f"（记录 {recorded} → 现在 {expected}）——重核{sect}后跑 `redline_stamp.py --write`"
            )
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description="给红线包 §八 上游表补/校指纹列")
    ap.add_argument("novel_dir", help="小说目录（含 01_设定/00_红线包.md）")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--check", action="store_true", help="（默认）校验指纹，不一致返回 1")
    g.add_argument("--write", action="store_true", help="按上游当前内容回写指纹列")
    g.add_argument("--list", action="store_true", help="只列出 上游文件 → 重核小节")
    args = ap.parse_args()

    novel_dir = Path(args.novel_dir).resolve()
    redline = novel_dir / REDLINE_REL
    if not redline.is_file():
        print(f"跳过：{args.novel_dir} 下没有 {REDLINE_REL}", file=sys.stderr)
        return 0

    try:
        if args.list:
            table = parse_table(redline.read_text(encoding="utf-8"))
            for row in table.rows:
                sect = row.cells[1] if len(row.cells) > 1 else "?"
                print(f"  {sect:<8} ← {'、'.join(row.refs) or NO_FILE}")
            return 0

        if args.write:
            new_text, notes = write_stamps(novel_dir)
            redline.write_text(new_text, encoding="utf-8")
            print(f"  ↻ {REDLINE_REL} §八 指纹列已更新：")
            for n in notes:
                print(f"      {n}")
            return 0

        problems = check_stamps(novel_dir)
        if not problems:
            print(f"  ✔ {REDLINE_REL} §八 指纹与上游一致")
            return 0
        print(f"  ✘ {REDLINE_REL} §八 有 {len(problems)} 处对不上：", file=sys.stderr)
        for p in problems:
            print(f"      {p}", file=sys.stderr)
        return 1
    except RedlineError as e:
        print(f"错误：{e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
