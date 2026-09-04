"""
小说元信息推导 (novel_meta.py)

`02_工具/` 下的工具按 `AGENTS.md` §六 是**跨小说通用**的，因此规则代码里不得出现
任何一本具体小说的专名（主角名、世界名…）。本模块把这类元信息从该小说的定稿数据
里**推导**出来，供各 Rule 共享，结果按 AuditContext 缓存（一次审查只解析一遍）。

推导来源：
- 主角姓名 → `01_设定/00_主角档案.md`【基础档案】表的「姓名」行（`00_主角人设卡模板` 字段）
- 世界名   → `02_数据库/02_地理区域/02_地理区域_<世界名>.md`（世界层文件名不含下划线分段）
"""
import re
from typing import List, Optional

PROTAGONIST_CARD_REL = "01_设定/00_主角档案.md"
GEO_DIR_REL = "02_数据库/02_地理区域"
GEO_FILE_PREFIX = "02_地理区域_"

# 主角卡【基础档案】表：| 姓名 | (必) | 苏砚 |
_NAME_ROW_RE = re.compile(r"^\|\s*姓名\s*\|[^|]*\|\s*([^|]+?)\s*\|")
# 兜底：卡片标题 `# 苍玄 · 主角人设卡 · 苏砚`
_TITLE_RE = re.compile(r"^#\s+.*·\s*([^·\s]+)\s*$")


def _card_text(context, rel: str) -> str:
    fi = context.file_map.get(rel)
    if fi:
        return fi.content
    p = context.novel_dir / rel
    return p.read_text(encoding="utf-8", errors="ignore") if p.exists() else ""


def protagonist_name(context) -> Optional[str]:
    """主角姓名；主角档案缺失或没有「姓名」行时返回 None（调用方须容忍）。"""
    cached = getattr(context, "_protagonist_name", "__unset__")
    if cached != "__unset__":
        return cached

    name = None
    text = _card_text(context, PROTAGONIST_CARD_REL)
    for line in text.splitlines():
        m = _NAME_ROW_RE.match(line.strip())
        if m:
            candidate = m.group(1).strip()
            # 模板占位（"本名"）与空格式行不算
            if candidate and candidate not in ("本名", "-", "—"):
                name = candidate
            break
    if name is None:
        for line in text.splitlines():
            m = _TITLE_RE.match(line.strip())
            if m:
                name = m.group(1).strip()
                break

    context._protagonist_name = name
    return name


def world_files(context) -> List[str]:
    """世界层地理文件的**文件名**列表（`02_地理区域_<世界名>.md`，按名排序）。

    地理卡文件名是父子拼接的（世界 → 世界_区域 → 世界_区域_地名），
    因此「前缀去掉后不再含下划线」的那一层就是世界层。
    """
    cached = getattr(context, "_world_files", None)
    if cached is not None:
        return cached

    out = []
    for fi in context.files:
        rel = fi.relative_path
        if not rel.startswith(GEO_DIR_REL + "/"):
            continue
        fn = rel.rsplit("/", 1)[-1]
        if not (fn.startswith(GEO_FILE_PREFIX) and fn.endswith(".md")):
            continue
        remainder = fn[len(GEO_FILE_PREFIX):-3]
        if remainder and "_" not in remainder:
            out.append(fn)
    out.sort()
    context._world_files = out
    return out


def world_names(context) -> List[str]:
    return [fn[len(GEO_FILE_PREFIX):-3] for fn in world_files(context)]
