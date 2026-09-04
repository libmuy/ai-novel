# -*- coding: utf-8 -*-
"""
源文件取材 (extract.py)

只做**确定性提取**：按标题取区块、按 `@引用` 解析卡片路径、按表格取行。
本模块从不改写、不概括、不生成——拼装出的每一段都能追到某个源文件的原文。

（历史上这一步是人工做的：每章手抄一遍模板与卡片，顺手做「有损压缩」。
代价是每章 141 KB 手工劳动，且压缩口径逐章漂移。见
`00_通用模板/04_提示词/00_云端提示词生成器.md`「与正文阶段任务的关系」§1：
规则件应当**全文内联，以文件名为区块标题**。）
"""
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

# ── @引用 → 卡片目录与文件名前缀 ────────────────────────────────
CARD_ROUTES = {
    "人物": ("02_数据库/07_人物", "07_人物_"),
    "势力": ("02_数据库/03_势力组织", "03_势力组织_"),
    "地名": ("02_数据库/02_地理区域", "02_地理区域_"),
    "区域": ("02_数据库/02_地理区域", "02_地理区域_"),
    "书籍": ("02_数据库/06_书籍", "06_书籍_"),
}
# 这些前缀是状态对象，没有独立卡片，值来自 00_开篇状态.md
STATE_ONLY_TYPES = {"物品", "财务", "关系", "世界"}

_REF_RE = re.compile(r"@(?P<type>主角|人物|势力|地名|区域|书籍|物品|财务|关系|伏笔|道义|资源|类型|世界)"
                     r"(?:\.(?:\[(?P<b>[^\]]+)\]|(?P<r>[A-Za-z0-9\-]+)))?")


@dataclass(frozen=True)
class Ref:
    ref_type: str
    name: str          # @主角 无名称时为空串

    def render(self) -> str:
        if self.ref_type == "主角":
            return "@主角"
        if self.ref_type in ("伏笔", "道义"):
            return f"@{self.ref_type}.{self.name}"
        return f"@{self.ref_type}.[{self.name}]"


@dataclass
class CastEntry:
    ref: Ref
    mode: str          # 出场方式：登场 / 提及 / 状态变动 …
    note: str


# ── Markdown 区块 ──────────────────────────────────────────────

_FENCE_RE = re.compile(r"^\s*(```|~~~)")


def iter_headings(text: str):
    """逐行产出 (行号, 级别, 标题)，**跳过围栏代码块内的 `#` 行**。

    `01_系统指令.md` 把每个任务的指令包在 ``` 围栏里，围栏内还有 `## 执行要求`。
    不跟踪围栏的话，取「任务2」这一节会在围栏内的 `##` 处提前截断——
    正文提示词的【任务】段就只剩一行标题。
    """
    in_fence = False
    for i, line in enumerate(text.splitlines()):
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = re.match(r"^(#{1,6})\s+(.*?)\s*$", line)
        if m:
            yield i, len(m.group(1)), m.group(2).strip()


def read_section(text: str, heading: str, include_heading: bool = True) -> str:
    """取标题恰为 `heading` 的那一节，到下一个同级或更高级标题为止（围栏内的 # 不算标题）。"""
    lines = text.splitlines()
    want = heading.strip()
    start = level = None
    for i, lv, title in iter_headings(text):
        if start is None and title == want:
            start, level = i, lv
            continue
        if start is not None and lv <= level:
            return "\n".join(lines[start if include_heading else start + 1:i]).rstrip() + "\n"
    if start is None:
        return ""
    return "\n".join(lines[start if include_heading else start + 1:]).rstrip() + "\n"


def section_titles(text: str, max_level: int = 3) -> list[str]:
    return [t for _, lv, t in iter_headings(text) if lv <= max_level]


def fenced_block(text: str) -> str:
    """取第一个围栏代码块的内容。

    `01_系统指令.md` 每个任务的**给云端的指令**都包在围栏里，围栏之后是
    「拼装为云端提示词时……」——那是给本地 Agent 的操作说明（含仓库路径与
    脚本名），不该发给云端。只取围栏内的部分，噪音与路径泄漏一起解决。
    """
    out, in_fence = [], False
    for line in text.splitlines():
        if _FENCE_RE.match(line):
            if in_fence:
                break
            in_fence = True
            continue
        if in_fence:
            out.append(line)
    return "\n".join(out).strip() + "\n" if out else ""


def table_rows(text: str) -> list[list[str]]:
    """所有 Markdown 表格数据行（已剔除表头分隔行；单元格去空白）。"""
    rows = []
    for line in text.splitlines():
        s = line.strip()
        if not s.startswith("|"):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if not cells or all(set(c) <= set(":- ") for c in cells):
            continue
        rows.append(cells)
    return rows


# ── @引用 ──────────────────────────────────────────────────────

def parse_refs(text: str) -> list[Ref]:
    seen, out = set(), []
    for m in _REF_RE.finditer(text):
        t = m.group("type")
        name = m.group("b") or m.group("r") or ""
        if t != "主角" and not name:
            continue
        ref = Ref(t, name)
        if ref not in seen:
            seen.add(ref)
            out.append(ref)
    return out


def parse_cast(outline_text: str) -> list[CastEntry]:
    """单章细纲「## 出场对象」表 → 出场对象清单。

    这张表是 `07_单章细纲模板` 规定的必填区块，也是
    `build_state_snapshot.py --write-chapter-openers` 的取数依据——
    因此正文提示词该内联哪些卡片，完全由它决定，不需要人再判断一次。
    """
    sec = read_section(outline_text, "出场对象")
    if not sec:
        return []
    out: list[CastEntry] = []
    for cells in table_rows(sec):
        refs = parse_refs(cells[0])
        if not refs:
            continue
        out.append(CastEntry(
            ref=refs[0],
            mode=cells[1] if len(cells) > 1 else "",
            note=cells[2] if len(cells) > 2 else "",
        ))
    return out


def card_path(novel_dir: Path, ref: Ref) -> Optional[Path]:
    """`@类型.[名称]` → 数据库卡片路径；不是卡片类对象或找不到则 None。"""
    if ref.ref_type == "主角":
        for cand in ("01_设定/00_主角档案_当前阶段.md", "01_设定/00_主角档案.md"):
            p = novel_dir / cand
            if p.exists():
                return p
        return None
    route = CARD_ROUTES.get(ref.ref_type)
    if not route:
        return None
    d, prefix = route
    base = novel_dir / d
    if not base.is_dir():
        return None
    exact = base / f"{prefix}{ref.name}.md"
    if exact.exists():
        return exact
    # 地理卡文件名是父子拼接的（…_苍玄界_灰壤凡域_枯港矿城.md），按叶子名匹配
    hits = [p for p in sorted(base.glob(f"{prefix}*.md"))
            if p.stem.rsplit("_", 1)[-1] == ref.name]
    return hits[0] if len(hits) == 1 else None


# ── 具体字段 ───────────────────────────────────────────────────

def wr_rules(concept_text: str, states: Iterable[str] = ("硬",)) -> list[str]:
    """`00_小说概念.md`【世界基本法则】里指定状态的 WR 规则行（原文照抄）。"""
    block = read_section(concept_text, "【世界基本法则】")
    if not block:
        return []
    want = set(states)
    out = []
    for cells in table_rows(block):
        if not cells or not cells[0].startswith("WR-"):
            continue
        if len(cells) >= 3 and cells[2].strip() in want:
            out.append("| " + " | ".join(cells) + " |")
    return out


def dy_block(core_dy_text: str, dy_id: str) -> str:
    """`05_核心道义.md` 里某条 DY 的完整小节（按标题含该 ID 匹配）。"""
    lines = core_dy_text.splitlines()
    for i, line in enumerate(lines):
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m and dy_id in m.group(2):
            return read_section(core_dy_text, m.group(2).strip())
    return ""


def ledger_rows(text: str, ids: Iterable[str]) -> list[str]:
    """伏笔总纲 / 伏笔册里指定 ID 的登记行（原文照抄）。"""
    want = set(ids)
    out = []
    for cells in table_rows(text):
        if cells and cells[0].strip() in want:
            out.append("| " + " | ".join(cells) + " |")
    return out


def field_value(text: str, field: str) -> str:
    """两列或三列表格里 `| <field> | <值> |` 的值（取最后一个非空单元格）。"""
    for cells in table_rows(text):
        if cells and cells[0].strip() == field:
            for c in reversed(cells[1:]):
                if c:
                    return c
    return ""


def scene_blocks(outline_text: str) -> list[tuple[str, str]]:
    """【场景列表】下各 `### 第N场景 · 标题` → [(标题, 正文)]。"""
    sec = read_section(outline_text, "【场景列表】")
    if not sec:
        return []
    out, cur, buf = [], None, []
    in_fence = False
    for line in sec.splitlines():
        if _FENCE_RE.match(line):
            in_fence = not in_fence
        m = None if in_fence else re.match(r"^#{3,4}\s+(第\s*\d+\s*场景.*?)\s*$", line)
        if m:
            if cur:
                out.append((cur, "\n".join(buf).strip()))
            cur, buf = m.group(1), []
        elif cur:
            buf.append(line)
    if cur:
        out.append((cur, "\n".join(buf).strip()))
    return out


def tail_text(manuscript_text: str, chars: int = 500) -> str:
    """上一章正文结尾 N 字（剥掉 Markdown 标题行与分隔线）。"""
    body = [ln for ln in manuscript_text.splitlines()
            if not ln.strip().startswith("#") and set(ln.strip()) != {"-"}]
    joined = "\n".join(body).strip()
    return joined[-chars:] if len(joined) > chars else joined
