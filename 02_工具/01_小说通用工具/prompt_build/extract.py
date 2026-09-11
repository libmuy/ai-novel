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

# `角色` 是 `人物` 的别名（持有者/师承 字段历来写 `@角色.[X]`）——parse_refs 里归一到 `人物`。
_REF_RE = re.compile(r"@(?P<type>主角|人物|角色|势力|地名|区域|书籍|物品|财务|关系|伏笔|道义|资源|类型|世界)"
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


def read_sections(text: str, headings: list[str], include_heading: bool = True) -> str:
    """按 `headings` 列出的顺序取多节、拼接（缺失的节跳过）。

    用于「对象卡只取本任务需要的区块」——每段仍是源文件某节的原文逐字，不概括。
    取哪几节由 `00_通用模板/04_提示词/任务输入清单.toml` 声明、`RULE009` 校验节名在
    **模板**里存在、`card_sections` 审计校验节名在**实际卡片**里存在。

    「缺失的节跳过」是**刻意的静默**：调用方若要区分「取到几节 / 少了哪几节」，
    先用 `sections_present` 拿到实际命中的清单，再决定要不要记 todo。
    """
    out = []
    for h in headings:
        sec = read_section(text, h, include_heading)
        if sec.strip():
            out.append(sec.rstrip())
    return ("\n\n".join(out) + "\n") if out else ""


def sections_present(text: str, headings: list[str]) -> list[str]:
    """`headings` 中在 `text` 里确实存在且非空的那些（保持传入顺序）。

    给拼装层用来把「区块被改名 / 漏填 → 静默消失」变成显式 todo：
    `set(headings) - set(sections_present(...))` 就是丢掉的区块。
    """
    return [h for h in headings if read_section(text, h).strip()]


def card_fields(text: str, fields: list[str]) -> str:
    """卡片里 `fields` 列出的字段行 → 一张小表（原文取值，不概括）。"""
    rows = [f"| {f} | {v} |" for f in fields if (v := field_value(text, f))]
    return ("| 字段 | 值 |\n|---|---|\n" + "\n".join(rows) + "\n") if rows else ""


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
        if t == "角色":                     # `@角色.X` 归一到 `人物`
            t = "人物"
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

def wr_rules(concept_text: str, states: Optional[Iterable[str]] = ("硬",)) -> list[str]:
    """`00_小说概念.md`【世界基本法则】里的 WR 规则行（原文照抄）。

    `states=None` → 不按状态过滤，返回全部 WR 行（用来判断「规则在、只是没一条命中状态」）。
    """
    block = read_section(concept_text, "【世界基本法则】")
    if not block:
        return []
    want = None if states is None else set(states)
    out = []
    for cells in table_rows(block):
        if not cells or not cells[0].startswith("WR-"):
            continue
        if want is None or (len(cells) >= 3 and cells[2].strip() in want):
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


_DY_HEADING_RE = re.compile(r"^#{1,6}\s*.*?(DY-\d+)", re.M)


def dy_ids_all(core_dy_text: str) -> list[str]:
    """`05_核心道义.md`【二、道义条目详情】下全部已登记 DY 号，按文中出现顺序、去重。

    用于节拍摘要没点名具体 DY 号时，给一份轻量索引兜底——否则细纲模板的
    「本章落地道义」必填字段完全没有已登记道义可选，只能编或打问号。
    """
    out: list[str] = []
    for m in _DY_HEADING_RE.finditer(core_dy_text):
        did = m.group(1)
        if did not in out:
            out.append(did)
    return out


def dy_summary(core_dy_text: str, dy_id: str) -> tuple[str, str]:
    """某条 DY 的 (道义类型, 道义表述) 一句话摘要，原文摘取、不改写。"""
    body = dy_block(core_dy_text, dy_id)
    type_m = re.search(r"\*\*道义类型\*\*[：:]\s*(.+)", body)
    desc_m = re.search(r"\*\*道义表述\*\*[：:]\s*\n+(.+?)\n", body)
    return (type_m.group(1).strip() if type_m else "",
            desc_m.group(1).strip() if desc_m else "")


def ledger_rows(text: str, ids: Iterable[str]) -> list[str]:
    """伏笔总纲 / 伏笔册里指定 ID 的登记行（原文照抄）。"""
    want = set(ids)
    out = []
    for cells in table_rows(text):
        if cells and cells[0].strip() in want:
            out.append("| " + " | ".join(cells) + " |")
    return out


def fh_index_all(vol_ledger_text: str) -> list[dict]:
    """本卷伏笔册【1. 本卷新埋伏笔】表的轻量索引，按表头动态取名、原文摘取、去重。

    用于节拍摘要没点名具体 FH 号时兜底——否则「伏笔的埋设/推进/回收只能用已登记
    编号」这条硬约束无从遵守，云端要么现编号、要么打问号（同 DY 兜底，见
    `assemble._dy_index_block`）。只取本卷（不取全书总纲）——总纲跨全部部/卷，
    把还没写到的未来伏笔摊给当前章节只会诱导提前剧透、且与「未知规则须保持
    未知」的原则冲突。
    """
    section = read_section(vol_ledger_text, "1. 本卷新埋伏笔")
    if not section:
        return []
    rows = table_rows(section)
    if not rows:
        return []
    header = rows[0]
    out: list[dict] = []
    seen: set[str] = set()
    for cells in rows[1:]:
        if not cells or not re.match(r"^FH-\d+$", cells[0]):
            continue
        if cells[0] in seen:
            continue
        seen.add(cells[0])
        out.append({(header[i] if i < len(header) else f"列{i}"): v
                    for i, v in enumerate(cells)})
    return out


def field_value(text: str, field: str) -> str:
    """`| <field> | … |` 那一行的**值列**。

    卡片有两种表宽：两列 `| 字段 | 值 |`、三列 `| 字段 | 必填 | 内容 |`
    （人物卡 / 修炼卡）。三列取第 3 列，**不回退到「必填」列**——否则「内容」留空时
    会把 `(必)` 之类的必填标记当成字段值返回。取到空就返回 ""，由上层记 todo。
    """
    for cells in table_rows(text):
        if cells and cells[0].strip() == field:
            if len(cells) >= 3:
                return cells[2]
            if len(cells) == 2:
                return cells[1]
            return ""
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
