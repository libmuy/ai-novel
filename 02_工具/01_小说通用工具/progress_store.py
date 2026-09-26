#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
进度存储 (progress_store.py)

`00_进度.json` 的唯一读写实现——之前有三处（`progress_report.py`、
`prompt_build/progress.py`、`audit/resolver/todo_resolver.py`）各自用正则/关键词
扫 `00_进度.md` 的 Markdown 表格，格式脆弱、行为还不完全一致（比如同名路径谁覆盖谁
三处各写各的）。现在只存 `{路径: {status, date?}}` 这类结构化数据，三处改成都调
这个模块，不再各写一份解析器。

`00_进度.md` 里原本混着的人写长叙事「说明」（裁决过程记录）已经确认砍掉、不迁移——
它实际是 Agent 代笔而非人工手写，且脚本从未消费过这部分内容；需要回看某次裁决的
来龙去脉，去 `git log` 找旧版 `00_进度.md`。
"""
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

PROGRESS_REL = "00_进度.json"
LEGACY_REL = "00_进度.md"

# 成熟度三态，越靠前越成熟。「待修改」从来不是机读状态（旧 00_进度.md 图例自己也写明
# 「进度工具只识别三态，登记时须同时写『待校验』字样」），这里不收。
MATURITY = ("定稿", "待校验", "草稿")

# canonical 产出根：只有这些前缀下的路径才是「正式小说数据」
CANONICAL_PREFIXES = ("01_设定/", "02_数据库/", "03_规划/", "10_正文/")

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# key 不允许裸文件名、`..`、反斜杠，也不允许占位/通配写法（`0N`/`NN`/`XX`/`*`/`《`）
_PLACEHOLDER_RE = re.compile(r"0N|NN|XX|[*《]")


class ProgressFormatError(ValueError):
    """`00_进度.json` 不存在合法 JSON，或不符合 schema。"""


@dataclass(frozen=True)
class Entry:
    status: str
    date: Optional[str] = None


def path(novel_dir: Path) -> Path:
    return Path(novel_dir) / PROGRESS_REL


def exists(novel_dir: Path) -> bool:
    return path(novel_dir).exists()


def legacy_exists(novel_dir: Path) -> bool:
    return (Path(novel_dir) / LEGACY_REL).exists()


def _validate_key(key: str) -> None:
    if not isinstance(key, str) or not key:
        raise ProgressFormatError(f"key 必须是非空字符串，收到：{key!r}")
    if key.startswith("/") or key.startswith("./") or ".." in key.split("/") or "\\" in key:
        raise ProgressFormatError(f"key 格式非法（不允许绝对路径/`./`/`..`/反斜杠）：{key!r}")
    if not key.startswith(CANONICAL_PREFIXES):
        raise ProgressFormatError(
            f"key 必须以 {CANONICAL_PREFIXES} 之一开头：{key!r}")
    if not (key.endswith(".md") or key.endswith("/")):
        raise ProgressFormatError(f"key 必须以 `.md`（文件）或 `/`（分类目录）结尾：{key!r}")
    if _PLACEHOLDER_RE.search(key):
        raise ProgressFormatError(f"key 不能含占位/通配写法（0N/NN/XX/*/《）：{key!r}")


def _validate_entry(key: str, raw: dict) -> Entry:
    if not isinstance(raw, dict):
        raise ProgressFormatError(f"{key!r} 的值必须是对象，收到：{raw!r}")
    extra = set(raw) - {"status", "date"}
    if extra:
        raise ProgressFormatError(f"{key!r} 有非法字段 {sorted(extra)}——只允许 status/date，不放长文本")
    status = raw.get("status")
    if status not in MATURITY:
        raise ProgressFormatError(f"{key!r} 的 status 必须是 {MATURITY} 之一，收到：{status!r}")
    date = raw.get("date")
    if date is not None and not _DATE_RE.match(str(date)):
        raise ProgressFormatError(f"{key!r} 的 date 必须是 YYYY-MM-DD，收到：{date!r}")
    return Entry(status=status, date=date)


def validate(obj: object) -> dict[str, Entry]:
    """校验 `load()`/`save()` 共用的 schema：`{"version": 1, "files": {...}}`。"""
    if not isinstance(obj, dict):
        raise ProgressFormatError("顶层必须是对象")
    extra = set(obj) - {"version", "files"}
    if extra:
        raise ProgressFormatError(f"顶层有未知字段 {sorted(extra)}（只允许 version/files）")
    if obj.get("version") != 1:
        raise ProgressFormatError(f"version 必须是 1，收到：{obj.get('version')!r}")
    files = obj.get("files")
    if not isinstance(files, dict):
        raise ProgressFormatError("files 必须是对象")
    out: dict[str, Entry] = {}
    for key, raw in files.items():
        _validate_key(key)
        out[key] = _validate_entry(key, raw)
    return out


def load(novel_dir: Path) -> dict[str, Entry]:
    """文件不存在返回 `{}`；JSON 非法或不合 schema 抛 `ProgressFormatError`。"""
    src = path(novel_dir)
    if not src.exists():
        return {}
    try:
        obj = json.loads(src.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ProgressFormatError(f"{PROGRESS_REL} 不是合法 JSON：{e}") from e
    return validate(obj)


def statuses(novel_dir: Path) -> dict[str, str]:
    """`declared_status()` 的直接替代：`{路径: 成熟度}`。"""
    return {k: e.status for k, e in load(novel_dir).items()}


def normalize_key(novel_dir: Path, p) -> str:
    """把绝对/相对路径归一成相对小说目录的 POSIX key。"""
    p = Path(p)
    try:
        rel = p.relative_to(Path(novel_dir))
    except ValueError:
        rel = p
    return rel.as_posix()


def status_of(sts: dict[str, str], p, novel_dir: Path) -> Optional[str]:
    """精确匹配（schema 已禁止裸名，不再需要后缀模糊匹配）。"""
    return sts.get(normalize_key(novel_dir, p))


def category_status(sts: dict[str, str], keyword: str) -> Optional[str]:
    """TODO004 用：`keyword` 以 `/` 结尾走精确匹配；否则取排序最靠前、
    以 `"/" + keyword` 结尾的 key（比如 keyword="规划_卷01.md" 匹配
    `03_规划/01_第01部/01_卷01/规划_卷01.md`）。"""
    if keyword.endswith("/"):
        return sts.get(keyword)
    for k in sorted(sts):
        if k.endswith("/" + keyword):
            return sts[k]
    return None


def is_at_least(status: Optional[str], need: str = "定稿") -> bool:
    if status is None or status not in MATURITY:
        return False
    return MATURITY.index(status) <= MATURITY.index(need)


def save(novel_dir: Path, entries: dict[str, Entry]) -> None:
    """校验后原子写：key 排序、一行一条、`ensure_ascii=False`。"""
    files = {}
    for key, e in sorted(entries.items()):
        _validate_key(key)
        raw = {"status": e.status}
        if e.date is not None:
            raw["date"] = e.date
        files[key] = raw
    obj = {"version": 1, "files": files}
    # 写之前再校验一遍，保证落盘的一定是合法 schema（而不是信任调用方传对了 Entry）
    validate(obj)
    text = json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=False) + "\n"
    dst = path(novel_dir)
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(".json.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(dst)


def remove(novel_dir: Path, rel_key: str) -> bool:
    """删掉某个路径的声明；原子写。返回该 key 是否原本存在。"""
    entries = load(novel_dir)
    existed = rel_key in entries
    if existed:
        entries = {k: v for k, v in entries.items() if k != rel_key}
        save(novel_dir, entries)
    return existed


def empty_document() -> str:
    """新建小说的空骨架文本。"""
    return json.dumps({"version": 1, "files": {}}, ensure_ascii=False, indent=2) + "\n"
