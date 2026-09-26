# -*- coding: utf-8 -*-
"""数据模型 + 目录扫描：Entry、scan/find、部/卷容器、部/卷/章标题与状态推导。"""
import json
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

_MP3_RE = re.compile(r"^章(\d+)(?:_场(\d+))?\.mp3$")
_WS_CH_RE = re.compile(r"^(\d{4})$")
_PART_DIR_RE = re.compile(r"第0*(\d+)部")
_VOL_DIR_RE = re.compile(r"卷0*(\d+)\b")
_OUTLINE_MD_RE = re.compile(r"^规划_卷\d+_章(\d+)\.md$")
_PUBDATE_ANCHOR = 1577836800  # 2020-01-01Z；feed 条目 pubDate 按章号合成，保证阅读顺序


# ================================================================ 数据模型：章

class Entry:
    """一章的合并视图：正文 + 工作区目录 + 章细纲 + 音频。"""

    def __init__(self, part, vol, ch, novel_dir):
        self.part, self.vol, self.ch = part, vol, ch
        self.novel_dir = novel_dir
        self.manuscript: Path | None = None
        self.ws_dir: Path | None = None
        self.outline: Path | None = None

    @property
    def key(self):
        return (self.part, self.vol, self.ch)

    def path3(self):
        return f"{self.part}/{self.vol}/{self.ch}"

    @property
    def audio_dir(self) -> Path | None:
        return self.ws_dir / "03_音频" if self.ws_dir else None

    def audio_units(self) -> list[tuple[int | None, Path]]:
        """[(场号|None, mp3路径), …]；有整章文件就只返它，否则返各场。"""
        d = self.audio_dir
        if not d or not d.is_dir():
            return []
        merged = d / f"章{self.ch:04d}.mp3"
        if merged.exists():
            return [(None, merged)]
        out = []
        for f in sorted(d.glob(f"章{self.ch:04d}_场*.mp3")):
            m = _MP3_RE.match(f.name)
            if m and m.group(2):
                out.append((int(m.group(2)), f))
        return out

    @property
    def has_audio(self) -> bool:
        return bool(self.audio_units())

    @property
    def manifest(self) -> dict:
        d = self.audio_dir
        if not d:
            return {}
        try:
            return json.loads((d / f"章{self.ch:04d}.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def unit_by_scene(self, scene: int | None) -> Path | None:
        for s, p in self.audio_units():
            if s == scene:
                return p
        return None

    def total_audio_bytes(self) -> int:
        return sum(_safe_size(p) for _s, p in self.audio_units())

    def duration_s(self) -> int:
        d = self.manifest.get("duration_seconds")
        if isinstance(d, (int, float)) and d > 0:
            return int(d)
        return sum(_mp3_duration_seconds(p, _safe_size(p)) for _s, p in self.audio_units())

    def pubdate_ts(self, scene: int | None = 0) -> int:
        return _PUBDATE_ANCHOR + (self.part * 1_000_000 + self.vol * 10_000
                                  + self.ch * 10 + (scene or 0)) * 3600

    def has_outline_output(self) -> bool:
        if not self.ws_dir:
            return False
        d = self.ws_dir / "01_模型输出"
        if not d.is_dir():
            return False
        return any(f.is_file() and not f.name.startswith(".") for f in d.iterdir())


def _safe_size(p: Path) -> int:
    try:
        return p.stat().st_size
    except OSError:
        return 0


def scan(novel_dir: Path) -> list[Entry]:
    entries: dict[tuple, Entry] = {}

    def get(part, vol, ch) -> Entry:
        k = (part, vol, ch)
        if k not in entries:
            entries[k] = Entry(part, vol, ch, novel_dir)
        return entries[k]

    for md in novel_dir.glob("10_正文/*/*/*.md"):
        m = re.match(r"^正文_卷\d+_章(\d+)\.md$", md.name)
        mp = re.search(r"第0*(\d+)部", str(md))
        mv = re.search(r"卷0*(\d+)", str(md))
        if m and mp and mv:
            get(int(mp.group(1)), int(mv.group(1)), int(m.group(1))).manuscript = md

    for d in novel_dir.glob("05_工作区/*/*/*"):
        if not d.is_dir():
            continue
        m = _WS_CH_RE.match(d.name)
        mp = re.search(r"第0*(\d+)部", str(d.parent.parent))
        mv = re.search(r"卷0*(\d+)", str(d.parent))
        if m and mp and mv:
            get(int(mp.group(1)), int(mv.group(1)), int(m.group(1))).ws_dir = d

    for md in novel_dir.glob("03_规划/*/*/规划_卷*_章*.md"):
        mc = _OUTLINE_MD_RE.match(md.name)
        mp = re.search(r"第0*(\d+)部", str(md.parent.parent))
        mv = re.search(r"卷0*(\d+)", str(md.parent))
        if mc and mp and mv:
            get(int(mp.group(1)), int(mv.group(1)), int(mc.group(1))).outline = md

    return [entries[k] for k in sorted(entries)]


def find(entries: list[Entry], part, vol, ch) -> Entry | None:
    for e in entries:
        if e.key == (part, vol, ch):
            return e
    return None


# ================================================================ 数据模型：部/卷容器

@dataclass
class DirMeta:
    n: int
    plan_dir: Path | None = None
    ws_dir: Path | None = None
    title: str = ""


def _glob_child_dir(parent: Path, pattern: str) -> Path | None:
    if not parent or not parent.is_dir():
        return None
    rx = re.compile(pattern)
    hits = sorted(d for d in parent.iterdir() if d.is_dir() and rx.search(d.name))
    return hits[0] if hits else None


def _ws_dir_for(novel_dir: Path, part: int, vol: int | None = None, ch: int | None = None) -> Path | None:
    d = _glob_child_dir(novel_dir / "05_工作区", rf"第0*{part}部")
    if vol is None or d is None:
        return d
    d = _glob_child_dir(d, rf"卷0*{vol}\b")
    if ch is None or d is None:
        return d
    cd = d / f"{ch:04d}"
    return cd if cd.is_dir() else None


def _plan_dir_for(novel_dir: Path, part: int, vol: int | None = None) -> Path | None:
    d = _glob_child_dir(novel_dir / "03_规划", rf"第0*{part}部")
    if vol is None or d is None:
        return d
    return _glob_child_dir(d, rf"卷0*{vol}\b")


def _containers(novel_dir: Path) -> tuple[dict[int, DirMeta], dict[tuple, DirMeta]]:
    """扫 03_规划 与 05_工作区 得到部/卷两级目录节点（标题、规划目录、工作区目录）。"""
    parts: dict[int, DirMeta] = {}
    vols: dict[tuple, DirMeta] = {}

    plan_root = novel_dir / "03_规划"
    if plan_root.is_dir():
        for pd in sorted(plan_root.iterdir()):
            m = _PART_DIR_RE.search(pd.name) if pd.is_dir() else None
            if not m:
                continue
            pn = int(m.group(1))
            parts.setdefault(pn, DirMeta(pn)).plan_dir = pd
            for vd in sorted(pd.iterdir()):
                mv = _VOL_DIR_RE.search(vd.name) if vd.is_dir() else None
                if not mv:
                    continue
                vn = int(mv.group(1))
                vols.setdefault((pn, vn), DirMeta(vn)).plan_dir = vd

    ws_root = novel_dir / "05_工作区"
    if ws_root.is_dir():
        for pd in sorted(ws_root.iterdir()):
            m = _PART_DIR_RE.search(pd.name) if pd.is_dir() else None
            if not m:
                continue
            pn = int(m.group(1))
            parts.setdefault(pn, DirMeta(pn)).ws_dir = pd
            for vd in sorted(pd.iterdir()):
                mv = _VOL_DIR_RE.search(vd.name) if vd.is_dir() else None
                if not mv:
                    continue
                vn = int(mv.group(1))
                vols.setdefault((pn, vn), DirMeta(vn)).ws_dir = vd

    for meta in parts.values():
        if meta.plan_dir:
            meta.title = _dir_first_heading(meta.plan_dir)
    for meta in vols.values():
        if meta.plan_dir:
            meta.title = _dir_first_heading(meta.plan_dir, exclude=_OUTLINE_MD_RE)

    return parts, vols


def _first_heading(path: Path | None) -> str:
    if not path:
        return ""
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")[:4096]
    except OSError:
        return ""
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#"):
            return line.lstrip("#").strip()
    return ""


def _dir_first_heading(d: Path, exclude=None) -> str:
    """目录里第一个有标题的 md：优先「规划_卷NN.md」/「规划_第NN部.md」这类卷/部纲本体，
    伏笔册、事件记录等旁支文件靠后（按文件名找，不依赖固定后缀，跨小说通用）。"""
    if not d or not d.is_dir():
        return ""
    files = [p for p in d.iterdir() if p.is_file() and p.suffix == ".md" and not p.name.startswith(".")]
    if exclude:
        files = [f for f in files if not exclude.match(f.name)]
    def _rank(f: Path):
        is_main = f.name.startswith("规划_") and "章" not in f.name
        return (0 if is_main else 1, f.name)
    for f in sorted(files, key=_rank):
        h = _first_heading(f)
        if h:
            return h
    return ""


def _chapter_state(e: Entry) -> str:
    if e.has_audio:
        return "有音频"
    if e.manuscript:
        return "有正文"
    if e.outline or e.has_outline_output():
        return "细纲已出"
    return "待细纲"


def _chapter_title(e: Entry) -> str:
    # 只认正文自己的标题：本项目正文从不带 # 标题，细纲的 # 标题是「单章细纲 · 第N卷第M章」
    # 这类自描述文档名，不是章名——借它当章标题，会让「正文」区看着像在显示「规划」内容。
    return _first_heading(e.manuscript)


# ================================================================ 树：部→卷→章（工作区/规划/正文共用）

def _scan_tree(novel_dir: Path):
    entries = scan(novel_dir)
    by_vol: dict[tuple, dict[int, Entry]] = defaultdict(dict)
    for e in entries:
        by_vol[(e.part, e.vol)][e.ch] = e
    parts_meta, vols_meta = _containers(novel_dir)

    part_nums = sorted(set(parts_meta) | {k[0] for k in vols_meta} | {k[0] for k in by_vol})
    tree = []
    for pn in part_nums:
        pmeta = parts_meta.get(pn)
        vol_nums = sorted({vn for (ppn, vn) in list(vols_meta) + list(by_vol) if ppn == pn})
        vols_out = []
        for vn in vol_nums:
            vmeta = vols_meta.get((pn, vn))
            chs = by_vol.get((pn, vn), {})
            ch_list = []
            for cn in sorted(chs):
                e = chs[cn]
                ch_list.append({"n": cn, "title": _chapter_title(e), "state": _chapter_state(e),
                                 "has_manuscript": bool(e.manuscript)})
            vols_out.append({"n": vn, "title": vmeta.title if vmeta else "", "chapters": ch_list})
        tree.append({"n": pn, "title": pmeta.title if pmeta else "", "vols": vols_out})
    return tree, entries


def _find_part(tree, pn):
    return next((p for p in tree if p["n"] == pn), None)


def _find_vol(part_node, vn):
    return next((v for v in part_node["vols"] if v["n"] == vn), None) if part_node else None


# ================================================================ mp3 时长 / 格式化

def _mp3_duration_seconds(path: Path, size: int) -> int:
    try:
        with open(path, "rb") as f:
            head = f.read(4096)
    except OSError:
        return 0
    i = head.find(b"\xff")
    while 0 <= i < len(head) - 4:
        b1, b2 = head[i + 1], head[i + 2]
        if head[i] == 0xFF and (b1 & 0xE0) == 0xE0:
            ver, layer = (b1 >> 3) & 0x03, (b1 >> 1) & 0x03
            bri = (b2 >> 4) & 0x0F
            if layer == 1 and bri not in (0, 15) and ver != 1:
                v1 = [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320]
                v2 = [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160]
                kbps = (v1 if ver == 3 else v2)[bri]
                if kbps:
                    return max(1, round(size * 8 / (kbps * 1000)))
        i = head.find(b"\xff", i + 1)
    return max(1, round(size * 8 / 48000)) if size else 0


def _parse_ref(rest: str):
    """'1/1/1.mp3' / '1/1/2/3' → (part, vol, ch, scene|None)。先去扩展名。"""
    rest = re.sub(r"\.[A-Za-z0-9]+$", "", rest)
    nums = re.findall(r"\d+", rest)
    if len(nums) < 3:
        return None
    part, vol, ch = int(nums[0]), int(nums[1]), int(nums[2])
    return (part, vol, ch, int(nums[3]) if len(nums) >= 4 else None)


def _fmt_hms(sec: int) -> str:
    h, rem = divmod(int(sec), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _fmt_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KiB"
    return f"{n / 1024 / 1024:.1f} MiB"


def _audio_url_for(p: Path) -> str | None:
    """任意 03_音频/章CCCC[_场N].mp3 → 现有 /audio/<部>/<卷>/<章>[/<场>].mp3 地址。"""
    m = _MP3_RE.match(p.name)
    if not m:
        return None
    ch, scene = int(m.group(1)), (int(m.group(2)) if m.group(2) else None)
    s = str(p)
    mp = re.search(r"第0*(\d+)部", s)
    mv = re.search(r"卷0*(\d+)", s)
    if not (mp and mv):
        return None
    slug = f"/{int(mp.group(1))}/{int(mv.group(1))}/{ch}" + (f"/{scene}" if scene else "")
    return f"/audio{slug}.mp3"
