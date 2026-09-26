#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
进度数据一次性迁移 (migrate_progress_json.py)

把 <小说目录>/00_进度.md 转成 00_进度.json，语义对齐迁移前 progress_report.py 的
declared_status()/lookup()（commit b185e8b 版本，下面 _legacy_* 是那个版本的独立
拷贝，不 import 已经切到读 JSON 的当前代码，保证转换可核对）。

用法：
    migrate_progress_json.py <小说目录>            # dry-run，只打印候选/丢弃项
    migrate_progress_json.py <小说目录> --apply    # 写 JSON、重新读回校验、删旧 MD

一次性工具，迁移完确认无误后应在单独一次提交里删掉本文件（历史见 git log）。
"""
import argparse
import json
import re
import sys
from pathlib import Path

_TOOLS_DIR = Path(__file__).resolve().parent.parent / "01_小说通用工具"
sys.path.insert(0, str(_TOOLS_DIR))
import progress_store  # noqa: E402

# ────────────────────────────────────── 旧版解析逻辑的独立拷贝（commit b185e8b）

_LEGACY_CODE_PATH_RE = re.compile(r"`([^`\n]+?\.md)`")
_LEGACY_STATUS_RE = re.compile(r"(定稿|待校验|草稿)")


def legacy_declared_status(md_text: str) -> dict[str, str]:
    """commit b185e8b 的 progress_report.declared_status() 原样搬过来。"""
    out: dict[str, str] = {}
    for line in md_text.splitlines():
        s = line.strip()
        if not s.startswith("|"):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if len(cells) < 2 or all(set(c) <= set(":- ") for c in cells):
            continue
        paths = [p for c in cells for p in _LEGACY_CODE_PATH_RE.findall(c)]
        status = next((m.group(1) for c in cells
                       for m in [_LEGACY_STATUS_RE.search(c)] if m), None)
        if not paths or status is None:
            continue
        for p in paths:
            out.setdefault(p.strip(), status)
    return out


def legacy_lookup(declared: dict[str, str], path: Path, novel_dir: Path) -> str | None:
    """commit b185e8b 的 progress_report.lookup() 原样搬过来。"""
    try:
        want = path.relative_to(novel_dir).as_posix()
    except ValueError:
        want = path.as_posix()
    for recorded, st in declared.items():
        r = recorded.lstrip("./")
        if want == r or want.endswith("/" + r):
            return st
    return None


def legacy_progress_index_status_of(declared: dict[str, str], rel: str) -> str | None:
    """prompt_build/progress.py 旧版 ProgressIndex.status_of 的双向匹配拷贝。"""
    for recorded, st in declared.items():
        r = recorded.lstrip("./")
        if rel == r or rel.endswith("/" + r) or r.endswith("/" + rel):
            return st
    return None


_CATEGORY_KEYWORD_IN_PROGRESS = {
    "地名": "02_数据库/02_地理区域/",
    "势力": "02_数据库/03_势力组织/",
    "人物": "02_数据库/07_人物/",
    "类型": "02_数据库/04_资源/",
    "书籍": "02_数据库/06_书籍/",
    "伏笔": "规划_卷01.md",
}
_MATURITY_MARKERS = ("定稿", "待校验", "草稿")


def legacy_todo_progress_status(md_text: str) -> dict[str, str]:
    """audit/resolver/todo_resolver.py 旧版 _parse_progress_status() 的拷贝。"""
    status: dict[str, str] = {}
    for line in md_text.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        for todo_type, keyword in _CATEGORY_KEYWORD_IN_PROGRESS.items():
            if keyword in line:
                cells = [c.strip() for c in line.strip("|").split("|")]
                if not cells:
                    continue
                cell = next((c for c in cells if any(m in c for m in _MATURITY_MARKERS)),
                            cells[-1])
                status[todo_type] = cell
    return status


# ────────────────────────────────────── 迁移逻辑

_DIR_RE = re.compile(r"`((?:01_设定|02_数据库|03_规划|10_正文)/[^`\n]*?/)`")
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_PLACEHOLDER_RE = re.compile(r"0N|NN|XX|[*《]")


def legacy_dir_status(md_text: str) -> dict[str, tuple[str, int]]:
    """目录 key（如 `02_数据库/04_资源/`）→ (成熟度, 首次命中行号)。
    和 legacy_declared_status 同样的 first-row-wins（setdefault）语义，
    只是抓的是目录反引号路径而不是 .md 文件路径——declared_status 的
    _CODE_PATH_RE 只认 `.md` 结尾，目录行对它完全不可见。"""
    out: dict[str, tuple[str, int]] = {}
    for i, line in enumerate(md_text.splitlines(), 1):
        s = line.strip()
        if not s.startswith("|"):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if len(cells) < 2 or all(set(c) <= set(":- ") for c in cells):
            continue
        dirs = [d for c in cells for d in _DIR_RE.findall(c)]
        status = next((m.group(1) for c in cells
                       for m in [_LEGACY_STATUS_RE.search(c)] if m), None)
        if not dirs or status is None:
            continue
        for d in dirs:
            out.setdefault(d.strip(), (status, i))
    return out


def first_date_near(md_text: str, needle: str) -> str | None:
    lines = md_text.splitlines()
    for i, line in enumerate(lines):
        if needle in line:
            m = _DATE_RE.search(line)
            if m:
                return m.group(0)
    return None


def build_candidates(md_text: str, novel_dir: Path):
    """返回 (entries, dropped, promoted)。
    entries: {key: {"status":.., "date":..}} —— 最终要写进 JSON 的
    dropped: [(旧key, status, 原因)]
    promoted: [(旧key, 新key, status)]
    """
    legacy = legacy_declared_status(md_text)
    entries: dict[str, dict] = {}
    dropped: list[tuple[str, str, str]] = []
    promoted: list[tuple[str, str, str]] = []

    # 1) 直接是 canonical 文件路径的
    for key, status in legacy.items():
        k = key.lstrip("./")
        if k.startswith(progress_store.CANONICAL_PREFIXES) and not _PLACEHOLDER_RE.search(k):
            date = first_date_near(md_text, key)
            entries[k] = {"status": status, "date": date} if date else {"status": status}

    # 2) 裸名/半路径 → 在磁盘上按后缀唯一匹配，提升为全路径（且全路径本身没有
    #    已经被 (1) 直接收录——已收录的以 (1) 为准，不用裸名覆盖）
    remaining = {k: v for k, v in legacy.items() if k.lstrip("./") not in entries}
    for key, status in remaining.items():
        k = key.lstrip("./")
        if _PLACEHOLDER_RE.search(k):
            dropped.append((key, status, "占位符路径"))
            continue
        hits = []
        for prefix in progress_store.CANONICAL_PREFIXES:
            root = novel_dir / prefix.rstrip("/")
            if not root.is_dir():
                continue
            for p in root.rglob("*"):
                if p.is_file() and p.as_posix().endswith("/" + k.split("/")[-1]) \
                        and p.relative_to(novel_dir).as_posix().endswith(k):
                    hits.append(p.relative_to(novel_dir).as_posix())
        hits = sorted(set(hits))
        if len(hits) == 1 and hits[0] not in entries:
            date = first_date_near(md_text, key)
            entries[hits[0]] = {"status": status, "date": date} if date else {"status": status}
            promoted.append((key, hits[0], status))
        elif len(hits) == 1 and hits[0] in entries:
            dropped.append((key, status, f"全路径 {hits[0]} 已经以自己的状态收录，裸名丢弃"))
        elif len(hits) > 1:
            dropped.append((key, status, f"后缀匹配到 {len(hits)} 个文件，不唯一，跳过"))
        else:
            dropped.append((key, status, "非 canonical 路径 / 磁盘上找不到对应文件"))

    # 3) 目录 key
    for dkey, (status, lineno) in legacy_dir_status(md_text).items():
        k = dkey.lstrip("./")
        if _PLACEHOLDER_RE.search(k):
            continue
        if k in entries:
            continue  # 理论上不会撞（文件 key 以 .md 结尾），保险起见跳过
        entries[k] = {"status": status}

    return entries, dropped, promoted


def render_report(entries, dropped, promoted) -> str:
    lines = [f"候选 JSON key 数：{len(entries)}", ""]
    for k in sorted(entries):
        lines.append(f"  {k}  ->  {entries[k]}")
    lines.append("")
    lines.append(f"裸名提升为全路径（{len(promoted)} 条）：")
    for old, new, status in promoted:
        lines.append(f"  {old!r} -> {new!r}  ({status})")
    lines.append("")
    lines.append(f"丢弃（{len(dropped)} 条）：")
    for key, status, reason in dropped:
        lines.append(f"  {key!r} ({status})  原因：{reason}")
    return "\n".join(lines)


def _queried_paths(novel_dir: Path) -> list[Path]:
    """所有真正会被代码 lookup 的路径：01_设定/*.md + 每卷大纲 + 每章细纲/正文。"""
    out = list((novel_dir / "01_设定").glob("*.md"))
    plan_root = novel_dir / "03_规划"
    if plan_root.is_dir():
        out += sorted(plan_root.glob("*/*/规划_卷*.md"))
        out += sorted(plan_root.glob("*/*/规划_卷*_章*.md"))
    text_root = novel_dir / "10_正文"
    if text_root.is_dir():
        out += sorted(text_root.glob("*/*/正文_卷*_章*.md"))
    return out


def verify_equivalence(md_text: str, entries: dict, novel_dir: Path) -> list[str]:
    """核对 A/B/C，返回不一致清单（空表示全部一致）。"""
    problems = []
    legacy = legacy_declared_status(md_text)
    new_statuses = {k: v["status"] for k, v in entries.items()}

    # A) 所有会被查询到的路径，旧 lookup / 旧 ProgressIndex.status_of / 新 status_of 一致
    for p in _queried_paths(novel_dir):
        rel = p.relative_to(novel_dir).as_posix()
        old_a = legacy_lookup(legacy, p, novel_dir)
        old_b = legacy_progress_index_status_of(legacy, rel)
        new = new_statuses.get(rel)
        if old_a != new or old_b != new:
            problems.append(f"A) {rel}: legacy_lookup={old_a!r} legacy_ProgressIndex={old_b!r} new={new!r}")

    # B) PROGRESS001 候选集合：声明了但文件不存在
    def declared_missing(statuses: dict[str, str]) -> set[str]:
        missing = set()
        for k in statuses:
            kk = k.lstrip("./")
            if not kk.startswith(progress_store.CANONICAL_PREFIXES):
                continue
            if _PLACEHOLDER_RE.search(kk):
                continue
            if not (novel_dir / kk).exists():
                missing.add(kk)
        return missing

    old_missing = declared_missing(legacy)
    new_missing = declared_missing(new_statuses)
    if old_missing != new_missing:
        problems.append(f"B) PROGRESS001 候选集合不同：旧={old_missing} 新={new_missing}")

    # C) TODO004 每个分类。已知且刻意接受的例外：旧版 _parse_progress_status()
    # 有「同一行命中即覆盖」的副作用，把「类型」判成了「定稿」，但那一行自己写的
    # 其实是「待校验」（04_资源 分类行），是被后面一行顺带提到关联文件时覆盖的
    # bug（见迁移计划 §四·3）。这次按行内容如实写「待校验」，属于刻意订正，
    # 不是回归——核对时豁免这一条，其余分类仍要求逐一一致。
    _KNOWN_ACCEPTED_DIFF = {"类型"}
    old_todo = legacy_todo_progress_status(md_text)
    for typ, keyword in _CATEGORY_KEYWORD_IN_PROGRESS.items():
        old_cell = old_todo.get(typ, "")
        old_final = "定稿" in old_cell
        new_status = progress_store.category_status(new_statuses, keyword)
        new_final = new_status == "定稿"
        if old_final != new_final and typ not in _KNOWN_ACCEPTED_DIFF:
            problems.append(
                f"C) 分类 {typ}: 旧 cell={old_cell!r}（'定稿' in cell={old_final}）"
                f" 新 status={new_status!r}（=='定稿'：{new_final}）")
        elif old_final != new_final:
            print(f"（已知例外，接受）分类 {typ}: 旧={old_final} 新={new_final} —— "
                  f"当前小说数据里没有任何 @{typ}.[TODO-实际编号] 占位符，零影响")

    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("novel_dir", type=Path)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    novel_dir = args.novel_dir.resolve()
    legacy_path = novel_dir / progress_store.LEGACY_REL
    if not legacy_path.exists():
        print(f"没有找到 {legacy_path}，无需迁移", file=sys.stderr)
        return 1

    md_text = legacy_path.read_text(encoding="utf-8")
    entries, dropped, promoted = build_candidates(md_text, novel_dir)
    print(render_report(entries, dropped, promoted))
    print()

    problems = verify_equivalence(md_text, entries, novel_dir)
    if problems:
        print(f"核对发现 {len(problems)} 处不一致，不会写入：")
        for p in problems:
            print(f"  {p}")
        return 2
    print("核对通过：A（逐路径 lookup）/ B（PROGRESS001 候选集合）/ C（TODO004 分类）三项均一致。")

    if not args.apply:
        print("\n（dry-run，未写入。加 --apply 正式执行）")
        return 0

    store_entries = {k: progress_store.Entry(status=v["status"], date=v.get("date")) for k, v in entries.items()}
    progress_store.save(novel_dir, store_entries)
    reread = progress_store.load(novel_dir)
    if {k: e.status for k, e in reread.items()} != {k: v["status"] for k, v in entries.items()}:
        print("写入后重新读回校验失败，不删除旧文件！", file=sys.stderr)
        return 3
    legacy_path.unlink()
    print(f"\n已写入 {progress_store.path(novel_dir)}（{len(entries)} 条），已删除 {legacy_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
