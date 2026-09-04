# -*- coding: utf-8 -*-
"""
章级路径解析与预建 (layout.py)

把「第 P 部 · 第 V 卷 · 第 C 章」解析成本仓的全部 canonical path，并按
`00_云端提示词生成器.md`「输出文件与预建」预建三类空文件（只创建、不覆盖）。

部/卷目录名可能带名称（`01_第01部` 或 `01_枯港遗玉`），所以一律用 glob 解析、
不拼字符串——与 `review_manuscript.py` 的解析口径保持一致。

工作区目录编号：每级 `05_工作区/…` 下固定 `00_提示词` `01_模型输出` `02_状态`，
章目录接着往下连续编号（`03_章0001` `04_章0002` …），见 audit `workspace` 规则的
「00-based 连续、不跳号」。新建章目录时按已有章目录数续号。
"""
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

WS = "05_工作区"
STD_SUBDIRS = ("00_提示词", "01_模型输出", "02_状态")
# 每级工作区目录固定占用 00/01/02 三个号，子层级从 03 起
FIRST_CHILD_INDEX = len(STD_SUBDIRS)

_CHAPTER_DIR_RE = re.compile(r"^(\d{2})_章(\d{4})$")


class LayoutError(Exception):
    pass


@dataclass
class ChapterLayout:
    novel_dir: Path
    part: int
    volume: int
    chapter: int

    # 工作区
    chapter_dir: Path          # 05_工作区/03_第01部/03_卷01/04_章0002
    prompt_dir: Path           # …/00_提示词
    output_dir: Path           # …/01_模型输出
    state_dir: Path            # …/02_状态
    opener_state: Path         # …/02_状态/00_开篇状态.md

    # canonical data paths
    outline: Path              # 03_规划/…/规划_卷VV_章CCCC.md
    manuscript: Path           # 10_正文/…/章CCCC.md
    volume_plan: Path          # 03_规划/…/规划_卷VV.md
    volume_foreshadow: Path    # 03_规划/…/00_伏笔册_卷VV.md

    @property
    def chapter_id(self) -> str:
        return f"章{self.chapter:04d}"

    @property
    def volume_id(self) -> str:
        return f"卷{self.volume:02d}"


def _glob_one(novel_dir: Path, pattern: str, fallback: Path) -> Path:
    hits = sorted(novel_dir.glob(pattern))
    return hits[0] if hits else fallback


def _ws_child(parent: Path, pattern: str, default_name: str) -> Path:
    """在工作区某级目录下找匹配 pattern 的子目录；没有就按续号规则给出新路径。"""
    if parent.exists():
        hits = sorted(d for d in parent.iterdir() if d.is_dir() and re.search(pattern, d.name))
        if hits:
            return hits[0]
    return parent / default_name


def _next_chapter_dirname(volume_ws: Path, chapter: int) -> str:
    """章目录名：`NN_章CCCC`，NN 接在 00/01/02 之后按已有章目录数续号。"""
    existing = []
    if volume_ws.exists():
        for d in volume_ws.iterdir():
            m = _CHAPTER_DIR_RE.match(d.name) if d.is_dir() else None
            if m:
                existing.append((int(m.group(2)), d.name))
    for ch_num, name in existing:
        if ch_num == chapter:
            return name
    idx = FIRST_CHILD_INDEX + len(existing)
    return f"{idx:02d}_章{chapter:04d}"


def resolve(novel_dir: Path, part: int, volume: int, chapter: int) -> ChapterLayout:
    novel_dir = novel_dir.resolve()
    if not novel_dir.is_dir():
        raise LayoutError(f"小说目录不存在：{novel_dir}")

    p2, v2, c4 = f"{part:02d}", f"{volume:02d}", f"{chapter:04d}"

    # ---- 工作区（可能尚未建，按规则给出应有路径）----
    ws = novel_dir / WS
    part_ws = _ws_child(ws, rf"第0*{part}部", f"{FIRST_CHILD_INDEX:02d}_第{p2}部")
    vol_ws = _ws_child(part_ws, rf"卷0*{volume}\b", f"{FIRST_CHILD_INDEX:02d}_卷{v2}")
    chapter_dir = vol_ws / _next_chapter_dirname(vol_ws, chapter)

    # ---- 规划层 / 正文层（已存在的用 glob，缺的按规范拼）----
    plan_root = novel_dir / "03_规划"
    plan_vol_dir = _glob_one(
        novel_dir, f"03_规划/*第{p2}部*/*卷{v2}*",
        plan_root / f"01_第{p2}部" / f"01_卷{v2}")
    text_vol_dir = _glob_one(
        novel_dir, f"10_正文/*第{p2}部*/*卷{v2}*",
        novel_dir / "10_正文" / f"01_第{p2}部" / f"01_卷{v2}")

    return ChapterLayout(
        novel_dir=novel_dir, part=part, volume=volume, chapter=chapter,
        chapter_dir=chapter_dir,
        prompt_dir=chapter_dir / STD_SUBDIRS[0],
        output_dir=chapter_dir / STD_SUBDIRS[1],
        state_dir=chapter_dir / STD_SUBDIRS[2],
        opener_state=chapter_dir / STD_SUBDIRS[2] / "00_开篇状态.md",
        outline=plan_vol_dir / f"规划_卷{v2}_章{c4}.md",
        manuscript=text_vol_dir / f"章{c4}.md",
        volume_plan=_glob_one(novel_dir, f"03_规划/*第{p2}部*/*卷{v2}*/规划_卷{v2}.md",
                              plan_vol_dir / f"规划_卷{v2}.md"),
        volume_foreshadow=_glob_one(novel_dir, f"03_规划/*第{p2}部*/*卷{v2}*/00_伏笔册_*.md",
                                    plan_vol_dir / f"00_伏笔册_卷{v2}.md"),
    )


def prebuild(layout: ChapterLayout, archive_name: str, target: Path,
             dry_run: bool = False) -> list[str]:
    """预建三类文件（只创建、不覆盖），返回实际新建的相对路径清单。

    ① 提示词存档 `00_提示词/<archive_name>`
    ② 云端产出回填 `01_模型输出/<archive_name>`（与存档同名，WS006 配对）
    ③ 目标数据文件 `target`（正文 `10_正文/…`，细纲 `03_规划/…`）
    """
    created: list[str] = []
    placeholder = f"> 待云端产出回填（{archive_name}）。\n"

    for d in (layout.prompt_dir, layout.output_dir, layout.state_dir):
        if not d.exists() and not dry_run:
            d.mkdir(parents=True, exist_ok=True)

    for path, body in ((layout.output_dir / archive_name, placeholder),
                       (target, placeholder)):
        if path.exists():
            continue
        created.append(_rel(layout.novel_dir, path))
        if dry_run:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return created


def _rel(novel_dir: Path, p: Path) -> str:
    try:
        return p.relative_to(novel_dir).as_posix()
    except ValueError:
        return str(p)


def rel(novel_dir: Path, p: Path) -> str:
    return _rel(novel_dir, p)


def parse_chapter_dir(chapter_dir: Path) -> tuple[int, int, int]:
    """从章工作区目录反解 (部, 卷, 章)。"""
    s = str(chapter_dir)
    m_part = re.search(r"第0*(\d+)部", s)
    m_vol = re.search(r"卷0*(\d+)", s)
    m_ch = re.search(r"章0*(\d+)", Path(chapter_dir).name)
    if not (m_part and m_vol and m_ch):
        raise LayoutError(f"无法从 {chapter_dir} 解析 部/卷/章 号")
    return int(m_part.group(1)), int(m_vol.group(1)), int(m_ch.group(1))


def find_novel_dir(chapter_dir: Path) -> Optional[Path]:
    """05_工作区/<部>/<卷>/<章> 向上 4 层是小说目录。"""
    p = Path(chapter_dir).resolve()
    return p.parents[3] if len(p.parents) >= 4 else None
