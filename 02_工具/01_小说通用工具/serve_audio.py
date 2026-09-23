#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
本地审查台 (serve_audio.py)

某本小说的本地 HTTP 服务：单页应用（`serve_ui/`）+ JSON API，取代旧版多页只读界面
（设计稿见设计项目「审查界面 v3」）。

    /                        单页壳（serve_ui/index.html）
    /app.js /nocturne.css    静态资源
    /api/book                书结构：部→卷→章，标题/状态标签
    /api/level               某一级（工作区/规划/正文 × 全书/部/卷/章）的文件分组 + 下级列表
    /api/file        GET     读单个文件（文本截断到 512 KiB；.mp3 返回播放地址）
    /api/file        PUT     保存（改名即另存为，已存在需 overwrite=true）
    /api/file        DELETE  移到 `<小说>/.trash/<时间戳>/…`
    /api/config              LLM 端点/模型（只读展示）、音色候选、只读模式标志
    /api/backfill-targets    某一级可回填的目标文件清单（服务端算，前端不能自拟路径）
    /api/backfill    POST    把已打开文件的内容复制到某个回填目标，可选顺带起一个冷读任务
    /api/jobs        POST    起一个后台任务（细纲/正文提示词、冷读、配音；大纲类任务先占位 501）
    /api/jobs/<id>   GET     任务状态（running/ok/warn/fail）+ 日志尾部

    /feed.xml                标准播客 RSS——手机播客 App（Pocket Casts / Apple Podcasts…）订阅
    /audio/<部>/<卷>/<章>[/<场>].mp3   音频（支持 Range 断点续传）
    /health                  ok

音频来自 tts_chapter.py 的产出 `05_工作区/**/03_音频/*.mp3`；提示词/冷读/配音任务由本
服务在后台子进程里调用 build_prompt.py / review_manuscript.py / tts_chapter.py。

用法
----
    python3 02_工具/01_小说通用工具/serve_audio.py <小说目录> \
        [--host 0.0.0.0] [--port 8765] [--base-url URL] [--title 标题] [--read-only]

安全：默认监听 0.0.0.0、无鉴权，仅适合可信局域网。写入一律做 realpath 校验，只允许
落在 `05_工作区/03_规划/10_正文` 三棵树之内，扩展名限文本类；状态树
`05_工作区/02_状态/01_最新状态` 只读（改状态须走 merge_chapter_state.py，不经本服务）。
所有非 GET 请求必须带 `X-Review-UI: 1` 请求头（简单 CSRF 防护），带 Origin 时须与 Host
一致。`--read-only` 关闭全部写入与任务接口（返回 403），仅保留浏览/播放。
"""
import argparse
import email.utils
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
import urllib.parse
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

_HERE = Path(__file__).resolve().parent
_SYS_DIR = _HERE.parent / "00_系统级"
_REPO_ROOT = _HERE.parents[1]
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_SYS_DIR))

from prompt_build import layout as L  # noqa: E402
from progress_report import han_count  # noqa: E402

_MP3_RE = re.compile(r"^章(\d+)(?:_场(\d+))?\.mp3$")
_WS_CH_RE = re.compile(r"^(\d{4})$")
_PART_DIR_RE = re.compile(r"第0*(\d+)部")
_VOL_DIR_RE = re.compile(r"卷0*(\d+)\b")
_OUTLINE_MD_RE = re.compile(r"^规划_卷\d+_章(\d+)\.md$")
_PUBDATE_ANCHOR = 1577836800  # 2020-01-01Z；feed 条目 pubDate 按章号合成，保证阅读顺序
_TEXT_EXT = {".md", ".txt", ".json", ".jsonl", ".toml", ".csv"}
_RENDER_CAP = 512 * 1024
_ALLOWED_ROOTS = ("05_工作区", "03_规划", "10_正文")
_WS_SUBDIRS = (("00_提示词", "ph-arrow-square-in"), ("01_模型输出", "ph-arrow-square-out"),
               ("02_状态", "ph-shield-check"), ("03_音频", "ph-waveform"))
_SEC_LABEL = {"work": "工作区", "plan": "规划", "text": "正文"}
_STATE_TAG = {"有音频": "neutral", "有正文": "neutral", "细纲已出": "outline", "待细纲": "accent"}
_VOICES = [
    {"id": "", "label": "配置默认（见 tts.config.toml）"},
    {"id": "zh-CN-YunxiNeural", "label": "男声 · 云希"},
    {"id": "zh-CN-XiaoxiaoNeural", "label": "女声 · 晓晓"},
]
# 大纲/校验类任务本轮只占位接口，runner 下一轮再补（见计划 §指令 → 任务）
_UNIMPLEMENTED_JOBS = {
    "outline_book": "全书大纲提示词",
    "outline_part": "部大纲提示词",
    "outline_vol": "卷大纲提示词",
    "check": "一致性校验提示词",
}


def _die(msg, code=1):
    print(msg, file=sys.stderr)
    sys.exit(code)


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
    return _first_heading(e.manuscript) or _first_heading(e.outline)


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


# ================================================================ mp3 时长

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


# ================================================================ 文件条目 / 分组

def _rel(novel_dir: Path, p: Path) -> str:
    return p.resolve().relative_to(novel_dir.resolve()).as_posix()


def _file_kind(p: Path) -> str:
    if p.suffix.lower() == ".mp3":
        return "audio"
    if p.suffix.lower() == ".md" and "正文" in p.name:
        return "prose"
    return "code"


def _file_desc(p: Path) -> str:
    parent = p.parent.name
    if parent == "00_提示词":
        return "输入 · 提示词"
    if parent == "01_模型输出":
        return "输出 · 模型产出"
    if parent == "02_状态":
        return "状态"
    if p.suffix.lower() == ".mp3":
        return _fmt_hms(_mp3_duration_seconds(p, _safe_size(p)))
    if _file_kind(p) == "prose":
        try:
            return f"{han_count(p.read_text(encoding='utf-8', errors='ignore'))} 字"
        except OSError:
            return ""
    return ""


def _file_icon(p: Path) -> str:
    if p.suffix.lower() == ".mp3":
        return "ph-headphones"
    if p.suffix.lower() == ".json":
        return "ph-brackets-curly"
    n = p.name
    if "冷读" in n:
        return "ph-eyeglasses"
    if "校验" in n or "对照" in n:
        return "ph-check-square"
    if "伏笔" in n:
        return "ph-git-branch"
    if "细纲" in n or "大纲" in n:
        return "ph-list-dashes"
    return "ph-file-text"


def _file_entry(novel_dir: Path, p: Path) -> dict:
    return {"path": _rel(novel_dir, p), "name": p.name, "size": _fmt_size(_safe_size(p)),
            "desc": _file_desc(p), "icon": _file_icon(p), "kind": _file_kind(p)}


def _list_files(novel_dir: Path, d: Path | None) -> list[dict]:
    if not d or not d.is_dir():
        return []
    out = []
    for f in sorted(d.iterdir()):
        if not f.is_file() or f.name.startswith("."):
            continue
        if f.suffix.lower() not in _TEXT_EXT and f.suffix.lower() != ".mp3":
            continue
        out.append(_file_entry(novel_dir, f))
    return out


def _validate_rel_path(novel_dir: Path, rel_path: str) -> Path:
    """校验一个「可能尚不存在」的相对路径可以安全写入；返回绝对路径。"""
    if not rel_path or rel_path.startswith("/") or rel_path.startswith("~"):
        raise ValueError("非法路径")
    parts = Path(rel_path).parts
    if not parts or ".." in parts:
        raise ValueError("非法路径")
    if parts[0] not in _ALLOWED_ROOTS:
        raise ValueError("路径必须在 05_工作区/03_规划/10_正文 之内")
    if Path(rel_path).suffix.lower() not in _TEXT_EXT:
        raise ValueError("不支持写入该扩展名")
    nd = novel_dir.resolve()
    target = (nd / rel_path).resolve()
    if nd != target and nd not in target.parents:
        raise ValueError("越界路径")
    # 目标（或其最近存在的祖先）必须仍在小说目录内 —— 防符号链接逃逸
    check = target if target.exists() else target.parent
    while not check.exists():
        check = check.parent
    check = check.resolve()
    if check != nd and nd not in check.parents:
        raise ValueError("越界路径")
    return target


def _safe_resolve(novel_dir: Path, rel_path: str) -> Path:
    """校验一个必须已存在的相对路径；返回绝对路径。"""
    target = _validate_rel_path(novel_dir, rel_path)
    if not target.is_file():
        raise FileNotFoundError(rel_path)
    return target


# ================================================================ 各级文件分组

def _ws_groups(novel_dir: Path, d: Path | None) -> list[dict]:
    if not d:
        return []
    groups = []
    for name, icon in _WS_SUBDIRS:
        files = _list_files(novel_dir, d / name)
        if files:
            groups.append({"name": name, "icon": icon, "files": files})
    return groups


def _level_file_groups(novel_dir: Path, sec: str, level: str, part, vol, ch, entries) -> list[dict]:
    if sec == "work":
        if level == "root":
            d = novel_dir / "05_工作区"
        elif level == "part":
            d = _ws_dir_for(novel_dir, part)
        elif level == "vol":
            d = _ws_dir_for(novel_dir, part, vol)
        else:
            d = _ws_dir_for(novel_dir, part, vol, ch)
        return _ws_groups(novel_dir, d)

    if sec == "plan":
        if level == "root":
            files = _list_files(novel_dir, novel_dir / "03_规划")
            return [{"name": "顶层规划", "icon": "ph-files", "files": files}] if files else []
        if level == "part":
            files = _list_files(novel_dir, _plan_dir_for(novel_dir, part))
            return [{"name": "本部规划", "icon": "ph-files", "files": files}] if files else []
        if level == "vol":
            pd = _plan_dir_for(novel_dir, part, vol)
            files = [f for f in _list_files(novel_dir, pd)
                     if not _OUTLINE_MD_RE.match(Path(f["path"]).name)]
            return [{"name": "本卷规划", "icon": "ph-files", "files": files}] if files else []
        # ch
        e = find(entries, part, vol, ch)
        files = [_file_entry(novel_dir, e.outline)] if e and e.outline else []
        return [{"name": "本章规划", "icon": "ph-list-dashes", "files": files}] if files else []

    if sec == "text":
        if level != "ch":
            return []
        e = find(entries, part, vol, ch)
        files = []
        if e and e.manuscript:
            files.append(_file_entry(novel_dir, e.manuscript))
        if e:
            for _scene, p in e.audio_units():
                files.append(_file_entry(novel_dir, p))
        return [{"name": "本章", "icon": "ph-files", "files": files}] if files else []

    return []


# ================================================================ /api/book, /api/level

def _book_payload(novel_dir: Path, title: str, base: str, tree) -> dict:
    parts_out, n_vols, n_ch = [], 0, 0
    for p in tree:
        vols_out = []
        for v in p["vols"]:
            vols_out.append({
                "n": v["n"], "label": f"卷 {v['n']:02d}", "title": v["title"],
                "chapters": [{"n": c["n"], "title": c["title"], "state": c["state"],
                              "tag": _STATE_TAG[c["state"]], "has_manuscript": c["has_manuscript"]}
                             for c in v["chapters"]],
            })
            n_vols += 1
            n_ch += len(v["chapters"])
        parts_out.append({"n": p["n"], "label": f"第 {p['n']} 部", "title": p["title"], "vols": vols_out})
    return {"title": title, "parts": parts_out,
            "meta": {"parts": len(parts_out), "vols": n_vols, "chapters": n_ch},
            "feed_url": f"{base}/feed.xml"}


def _level_payload(novel_dir: Path, sec: str, part, vol, ch, title: str, tree, entries) -> tuple[int, dict]:
    sec_label = _SEC_LABEL.get(sec)
    if sec_label is None:
        return 400, {"error": f"未知分区：{sec}"}

    if ch is not None:
        if part is None or vol is None:
            return 400, {"error": "章级需要 part/vol"}
        p = _find_part(tree, part)
        v = _find_vol(p, vol)
        if not v:
            return 404, {"error": "not found"}
        visible = [c for c in v["chapters"] if sec != "text" or c["has_manuscript"]]
        nums = [c["n"] for c in visible]
        idx = nums.index(ch) if ch in nums else -1
        prev_n = nums[idx - 1] if idx > 0 else None
        next_n = nums[idx + 1] if 0 <= idx < len(nums) - 1 else None
        cnode = next((c for c in v["chapters"] if c["n"] == ch), None)
        if cnode is None:
            return 404, {"error": "not found"}
        title_bits = f"第 {ch} 章" + (f" · {cnode['title']}" if cnode["title"] else "")
        return 200, {
            "level": "ch", "kicker": f"{sec_label} · 章", "title": title_bits,
            "meta": f"第 {part} 部 卷 {vol:02d} · {cnode['state']}",
            "children": None,
            "file_groups": _level_file_groups(novel_dir, sec, "ch", part, vol, ch, entries),
            "chapter": {"n": ch, "prev": prev_n, "next": next_n},
        }

    if vol is not None:
        if part is None:
            return 400, {"error": "卷级需要 part"}
        p = _find_part(tree, part)
        v = _find_vol(p, vol)
        if not v:
            return 404, {"error": "not found"}
        visible = [c for c in v["chapters"] if sec != "text" or c["has_manuscript"]]
        children = [{"code": f"{c['n']:04d}", "n": c["n"], "title": c["title"],
                     "state": c["state"], "tag": _STATE_TAG[c["state"]]} for c in visible]
        meta = f"共 {len(visible)} 章" if visible else "尚无章节，卷大纲为草稿"
        title_bits = f"卷 {vol:02d}" + (f" · {v['title']}" if v["title"] else "")
        return 200, {
            "level": "vol", "kicker": f"{sec_label} · 卷", "title": title_bits, "meta": meta,
            "children": children,
            "file_groups": _level_file_groups(novel_dir, sec, "vol", part, vol, None, entries),
        }

    if part is not None:
        p = _find_part(tree, part)
        if not p:
            return 404, {"error": "not found"}
        children = [{"code": f"{v['n']:02d}", "n": v["n"], "title": v["title"],
                     "state": f"{len(v['chapters'])} 章", "tag": "neutral"} for v in p["vols"]]
        title_bits = f"第 {part} 部" + (f" · {p['title']}" if p["title"] else "")
        return 200, {
            "level": "part", "kicker": f"{sec_label} · 部", "title": title_bits,
            "meta": f"{len(p['vols'])} 卷 · {sum(len(v['chapters']) for v in p['vols'])} 章",
            "children": children,
            "file_groups": _level_file_groups(novel_dir, sec, "part", part, None, None, entries),
        }

    children = [{"code": f"{p['n']:02d}", "n": p["n"], "title": p["title"],
                 "state": f"{len(p['vols'])} 卷", "tag": "neutral"} for p in tree]
    return 200, {
        "level": "root", "kicker": f"{sec_label} · 全书", "title": title,
        "meta": (f"{len(tree)} 部 · {sum(len(p['vols']) for p in tree)} 卷 · "
                 f"{sum(len(v['chapters']) for p in tree for v in p['vols'])} 章"),
        "children": children,
        "file_groups": _level_file_groups(novel_dir, sec, "root", None, None, None, entries),
    }


# ================================================================ 回填

def _backfill_targets(novel_dir: Path, part, vol, ch, tree) -> list[dict]:
    if ch is not None:
        lay = L.resolve(novel_dir, part, vol, ch)
        return [
            {"id": L.rel(novel_dir, lay.outline), "label": f"章细纲 → {L.rel(novel_dir, lay.outline)}"},
            {"id": L.rel(novel_dir, lay.manuscript), "label": f"正文 → {L.rel(novel_dir, lay.manuscript)}"},
        ]
    if vol is not None:
        pd = _plan_dir_for(novel_dir, part, vol)
        files = [f for f in _list_files(novel_dir, pd) if not _OUTLINE_MD_RE.match(Path(f["path"]).name)]
    elif part is not None:
        files = _list_files(novel_dir, _plan_dir_for(novel_dir, part))
    else:
        files = _list_files(novel_dir, novel_dir / "03_规划")
    return [{"id": f["path"], "label": f"{f['name']} → {f['path']}"} for f in files]


def _do_backfill(novel_dir: Path, src_rel: str, target_id: str, part, vol, ch, tree) -> dict:
    src = _safe_resolve(novel_dir, src_rel)
    targets = _backfill_targets(novel_dir, part, vol, ch, tree)
    if target_id not in {t["id"] for t in targets}:
        raise ValueError("非法回填目标")
    target = _validate_rel_path(novel_dir, target_id)
    text = src.read_text(encoding="utf-8", errors="ignore")
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(target)
    result = {"ok": True, "target": L.rel(novel_dir, target)}
    if ch is not None:
        lay = L.resolve(novel_dir, part, vol, ch)
        if target == lay.outline:
            result["mode"] = "outline"
        elif target == lay.manuscript:
            result["mode"] = "manuscript"
    return result


# ================================================================ 后台任务

@dataclass
class Job:
    id: str
    kind: str
    cmd: list
    cwd: str
    tmp_files: list = field(default_factory=list)
    status: str = "running"
    returncode: int | None = None
    log: str = ""
    started_at: float = field(default_factory=time.time)


_JOBS: dict[str, Job] = {}
_JOBS_LOCK = threading.Lock()
_RUNNING_KEYS: set[str] = set()
_RUNNING_LOCK = threading.Lock()


def _toml_val(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, list):
        return "[" + ", ".join(_toml_val(x) for x in v) + "]"
    return json.dumps(v, ensure_ascii=False)


def _toml_dump(d: dict) -> str:
    lines = []
    for name, table in d.items():
        if not isinstance(table, dict):
            continue
        lines.append(f"[{name}]")
        for k, v in table.items():
            lines.append(f"{k} = {_toml_val(v)}")
        lines.append("")
    return "\n".join(lines)


def _write_temp_review_config(engine: str, body: dict) -> Path:
    """按引擎选择临时覆盖 review.config.toml 的 [critics] 段，其余（超时/遍数）沿用原配置。"""
    base = tomllib.loads((_SYS_DIR / "review.config.toml").read_text(encoding="utf-8"))
    critics = dict(base.get("critics", {}))
    if engine == "opencode":
        critics["opencode"] = True
        critics["local_qwen"] = False
        models = body.get("oc_models")
        if models:
            critics["opencode_models"] = list(models)
    else:
        critics["opencode"] = False
        critics["local_qwen"] = True
    base["critics"] = critics
    fd, path = tempfile.mkstemp(suffix=".toml", prefix="review_ui_", dir=tempfile.gettempdir())
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(_toml_dump(base))
    return Path(path)


def _cmd_outline_ch(novel_dir, part, vol, ch, body):
    lay = L.resolve(novel_dir, part, vol, ch)
    cmd = [sys.executable, str(_HERE / "build_prompt.py"), "--chapter-dir", str(lay.chapter_dir), "--task", "细纲"]
    if body.get("force"):
        cmd.append("--force")
    return cmd, []


def _cmd_draft(novel_dir, part, vol, ch, body):
    lay = L.resolve(novel_dir, part, vol, ch)
    cmd = [sys.executable, str(_HERE / "build_prompt.py"), "--chapter-dir", str(lay.chapter_dir), "--task", "正文"]
    if body.get("force"):
        cmd.append("--force")
    return cmd, []


def _cmd_cold(novel_dir, part, vol, ch, body):
    lay = L.resolve(novel_dir, part, vol, ch)
    mode = "outline" if body.get("mode") == "outline" else "manuscript"
    cfg_path = _write_temp_review_config(body.get("engine", "local"), body)
    cmd = [sys.executable, str(_HERE / "review_manuscript.py"), "--chapter-dir", str(lay.chapter_dir),
           "--mode", mode, "--config", str(cfg_path)]
    return cmd, [cfg_path]


def _cmd_audio(novel_dir, part, vol, ch, body):
    lay = L.resolve(novel_dir, part, vol, ch)
    cmd = [sys.executable, str(_HERE / "tts_chapter.py"), "--chapter-dir", str(lay.chapter_dir),
           "--format", "json", "--force"]
    if body.get("per_scene"):
        cmd.append("--per-scene")
    voice = body.get("voice")
    if voice:
        cmd += ["--voice", voice]
    return cmd, []


_JOB_BUILDERS = {"outline_ch": _cmd_outline_ch, "draft": _cmd_draft, "cold": _cmd_cold, "audio": _cmd_audio}


def _run_job(job: Job):
    try:
        p = subprocess.run(job.cmd, cwd=job.cwd, capture_output=True, text=True, timeout=1800)
        job.log = ((p.stdout or "") + (("\n" + p.stderr) if p.stderr else ""))[-20000:]
        job.returncode = p.returncode
        # build_prompt.py: 0=已生成 2=前置门禁未过 3=生成了但内部标识自检未过（已写文件，需人工复核）
        job.status = "ok" if p.returncode == 0 else ("warn" if p.returncode == 3 else "fail")
    except subprocess.TimeoutExpired as e:
        job.log = f"超时（1800s）：{e}"
        job.status = "fail"
    except Exception as e:  # noqa: BLE001
        job.log = str(e)
        job.status = "fail"
    finally:
        for f in job.tmp_files:
            try:
                Path(f).unlink(missing_ok=True)
            except OSError:
                pass
        with _RUNNING_LOCK:
            _RUNNING_KEYS.discard(f"{job.kind}:{job.id_key}")


def _create_job(novel_dir: Path, body: dict) -> tuple[int, dict]:
    kind = body.get("kind")
    if kind in _UNIMPLEMENTED_JOBS:
        return 501, {"error": f"未实现：{_UNIMPLEMENTED_JOBS[kind]}", "kind": kind}
    builder = _JOB_BUILDERS.get(kind)
    if not builder:
        return 400, {"error": f"未知任务类型：{kind}"}
    part, vol, ch = body.get("part"), body.get("vol"), body.get("ch")
    if part is None or vol is None or ch is None:
        return 400, {"error": "缺少 part/vol/ch"}
    id_key = f"{part}/{vol}/{ch}"
    dedupe = f"{kind}:{id_key}"
    with _RUNNING_LOCK:
        if dedupe in _RUNNING_KEYS:
            return 409, {"error": "同一任务正在进行，请等它结束"}
        _RUNNING_KEYS.add(dedupe)
    try:
        cmd, tmp_files = builder(novel_dir, part, vol, ch, body)
    except Exception as e:  # noqa: BLE001
        with _RUNNING_LOCK:
            _RUNNING_KEYS.discard(dedupe)
        return 400, {"error": str(e)}
    job = Job(id=uuid.uuid4().hex[:12], kind=kind, cmd=cmd, cwd=str(_REPO_ROOT), tmp_files=tmp_files)
    job.id_key = id_key
    with _JOBS_LOCK:
        _JOBS[job.id] = job
    threading.Thread(target=_run_job, args=(job,), daemon=True).start()
    return 200, {"job_id": job.id}


def _job_payload(job: Job) -> dict:
    return {"id": job.id, "kind": job.kind, "status": job.status, "returncode": job.returncode, "log": job.log}


# ================================================================ RSS feed（不变）

def render_feed(entries, title, base) -> bytes:
    items = []
    for e in entries:
        for scene, path in e.audio_units():
            slug = f"/{e.path3()}" + (f"/{scene}" if scene else "")
            url = f"{base}/audio{slug}.mp3"
            size = _safe_size(path)
            dur = (e.duration_s() if scene is None
                   else _mp3_duration_seconds(path, size))
            it_title = f"卷{e.vol:02d} 第 {e.ch} 章" + (f" · 场 {scene}" if scene else "")
            items.append(
                "<item>"
                f"<title>{xml_escape(it_title)}</title>"
                f"<guid isPermaLink=\"false\">{xml_escape(base + slug)}</guid>"
                f"<pubDate>{email.utils.formatdate(e.pubdate_ts(scene), usegmt=True)}</pubDate>"
                f"<enclosure url=\"{xml_escape(url)}\" length=\"{size}\" type=\"audio/mpeg\"/>"
                f"<itunes:duration>{_fmt_hms(dur)}</itunes:duration>"
                "<itunes:explicit>false</itunes:explicit>"
                "</item>"
            )
    xml = (
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
        "<rss version=\"2.0\" xmlns:itunes=\"http://www.itunes.com/dtds/podcast-1.0.dtd\"><channel>"
        f"<title>{xml_escape(title)}</title><link>{xml_escape(base)}/</link>"
        f"<language>zh-cn</language>"
        f"<description>{xml_escape(title)} 章节 TTS 配音（听稿校对用）</description>"
        f"<itunes:author>{xml_escape(title)}</itunes:author>"
        f"<lastBuildDate>{email.utils.formatdate(usegmt=True)}</lastBuildDate>"
        f"{''.join(items)}</channel></rss>"
    )
    return xml.encode("utf-8")


# ================================================================ HTTP

_UI_DIR = _HERE / "serve_ui"
_STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "application/javascript; charset=utf-8"),
    "/nocturne.css": ("nocturne.css", "text/css; charset=utf-8"),
}


class Handler(BaseHTTPRequestHandler):
    server_version = "serve_audio/3.0"
    protocol_version = "HTTP/1.1"
    novel_dir: Path = None
    title: str = ""
    base_override: str | None = None
    read_only: bool = False

    def log_message(self, fmt, *a):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % a))

    def _send(self, body: bytes, ctype: str, code=200, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8", code)

    def _err(self, code, msg):
        self._json({"error": msg}, code)

    def do_HEAD(self):
        self.do_GET()

    def _base(self, u) -> str:
        if self.base_override:
            return self.base_override.rstrip("/")
        host = self.headers.get("Host") or f"{self.server.server_address[0]}:{self.server.server_address[1]}"
        return f"http://{host}"

    # ---------------------------------------------------------- GET

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(u.path)
        qs = urllib.parse.parse_qs(u.query)
        try:
            if path == "/health":
                return self._send(b"ok", "text/plain; charset=utf-8")
            if path in _STATIC_FILES:
                name, ctype = _STATIC_FILES[path]
                f = _UI_DIR / name
                if not f.is_file():
                    return self._err(404, "not found")
                self._send(f.read_bytes(), ctype)
                return
            if path == "/feed.xml":
                entries = scan(self.novel_dir)
                return self._send(render_feed(entries, self.title, self._base(u)),
                                  "application/rss+xml; charset=utf-8")
            if path.startswith("/audio/"):
                return self._serve_audio(path[len("/audio/"):].split("/"), scan(self.novel_dir))
            if path.startswith("/api/"):
                return self._route_api_get(path, qs)
            self._err(404, "not found")
        except BrokenPipeError:
            pass
        except Exception as ex:  # noqa: BLE001
            self._err(500, str(ex))

    def _route_api_get(self, path, qs):
        novel_dir = self.novel_dir
        if path == "/api/config":
            return self._json(self._config_payload())
        if path == "/api/book":
            tree, _entries = _scan_tree(novel_dir)
            return self._json(_book_payload(novel_dir, self.title, self._base(urllib.parse.urlparse(self.path)), tree))
        if path == "/api/level":
            sec = qs.get("sec", ["work"])[0]
            part, vol, ch = _qint(qs, "part"), _qint(qs, "vol"), _qint(qs, "ch")
            tree, entries = _scan_tree(novel_dir)
            code, data = _level_payload(novel_dir, sec, part, vol, ch, self.title, tree, entries)
            return self._json(data, code)
        if path == "/api/file":
            rel = qs.get("path", [None])[0]
            return self._get_file(rel)
        if path == "/api/backfill-targets":
            part, vol, ch = _qint(qs, "part"), _qint(qs, "vol"), _qint(qs, "ch")
            tree, _entries = _scan_tree(novel_dir)
            try:
                targets = _backfill_targets(novel_dir, part, vol, ch, tree)
            except L.LayoutError as e:
                return self._err(400, str(e))
            return self._json({"targets": targets})
        if path.startswith("/api/jobs/"):
            job_id = path[len("/api/jobs/"):]
            with _JOBS_LOCK:
                job = _JOBS.get(job_id)
            if not job:
                return self._err(404, "任务不存在")
            return self._json(_job_payload(job))
        self._err(404, "not found")

    def _config_payload(self) -> dict:
        llm = {"base_url": "", "model": ""}
        try:
            cfg = tomllib.loads((_SYS_DIR / "llm.config.toml").read_text(encoding="utf-8"))
            llm = {"base_url": cfg.get("base_url", ""), "model": cfg.get("model", "")}
        except OSError:
            pass
        return {"read_only": self.read_only, "voices": _VOICES, "llm": llm}

    def _get_file(self, rel_path):
        if not rel_path:
            return self._err(400, "缺少 path")
        try:
            p = _safe_resolve(self.novel_dir, rel_path)
        except FileNotFoundError:
            return self._err(404, "not found")
        except ValueError as e:
            return self._err(400, str(e))
        if p.suffix.lower() == ".mp3":
            return self._json({"path": rel_path, "name": p.name, "kind": "audio",
                                "size": _fmt_size(_safe_size(p)), "audio_url": _audio_url_for(p) or ""})
        if p.suffix.lower() not in _TEXT_EXT:
            return self._json({"path": rel_path, "name": p.name, "kind": "binary",
                                "size": _fmt_size(_safe_size(p))})
        raw = p.read_bytes()
        truncated = len(raw) > _RENDER_CAP
        text = raw[:_RENDER_CAP].decode("utf-8", "replace")
        return self._json({"path": rel_path, "name": p.name, "kind": _file_kind(p),
                            "text": text, "truncated": truncated, "size": _fmt_size(len(raw))})

    def _serve_audio(self, rest, entries):
        ref = _parse_ref("/".join(rest))
        if not ref:
            return self._err(404, "not found")
        part, vol, ch, scene = ref
        e = find(entries, part, vol, ch)
        path = e.unit_by_scene(scene) if e else None
        if not path or not path.exists():
            return self._err(404, "not found")
        self._stream(path)

    def _stream(self, path: Path):
        size = _safe_size(path)
        rng = self.headers.get("Range")
        start, end, partial = 0, size - 1, False
        if rng:
            m = re.match(r"bytes=(\d*)-(\d*)", rng.strip())
            if m and (m.group(1) or m.group(2)):
                if m.group(1):
                    start = int(m.group(1))
                    end = int(m.group(2)) if m.group(2) else size - 1
                else:
                    start = max(0, size - int(m.group(2)))
                end = min(end, size - 1)
                if start > end or start >= size:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                partial = True
        length = end - start + 1
        self.send_response(206 if partial else 200)
        self.send_header("Content-Type", "audio/mpeg")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if partial:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if self.command == "HEAD":
            return
        with open(path, "rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(65536, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    # ---------------------------------------------------------- 写 / 任务

    def _check_write_allowed(self) -> bool:
        if self.read_only:
            self._err(403, "只读模式：服务端已禁用写入")
            return False
        if self.headers.get("X-Review-UI") != "1":
            self._err(403, "缺少 X-Review-UI 请求头")
            return False
        origin = self.headers.get("Origin")
        if origin:
            host = self.headers.get("Host", "")
            origin_host = urllib.parse.urlparse(origin).netloc
            if origin_host and origin_host != host:
                self._err(403, "Origin 与 Host 不匹配")
                return False
        return True

    def _read_json_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b""
        return json.loads(raw) if raw else {}

    def do_PUT(self):
        path = urllib.parse.unquote(urllib.parse.urlparse(self.path).path)
        if not path.startswith("/api/"):
            return self._err(404, "not found")
        if not self._check_write_allowed():
            return
        try:
            body = self._read_json_body()
        except json.JSONDecodeError:
            return self._err(400, "请求体不是合法 JSON")
        try:
            if path == "/api/file":
                return self._put_file(body)
            self._err(404, "not found")
        except (ValueError, L.LayoutError) as e:
            self._err(400, str(e))
        except Exception as e:  # noqa: BLE001
            self._err(500, str(e))

    def _put_file(self, body):
        rel_path = body.get("path", "")
        text = body.get("text", "")
        orig = body.get("orig") or rel_path
        overwrite = bool(body.get("overwrite"))
        target = _validate_rel_path(self.novel_dir, rel_path)
        if rel_path != orig and target.exists() and not overwrite:
            return self._json({"error": f"{rel_path} 已存在，未覆盖"}, 409)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(target)
        self._json({"ok": True, "path": _rel(self.novel_dir, target), "size": _fmt_size(_safe_size(target))})

    def do_DELETE(self):
        u = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(u.path)
        qs = urllib.parse.parse_qs(u.query)
        if not path.startswith("/api/"):
            return self._err(404, "not found")
        if not self._check_write_allowed():
            return
        try:
            if path == "/api/file":
                return self._delete_file(qs.get("path", [None])[0])
            self._err(404, "not found")
        except FileNotFoundError:
            self._err(404, "not found")
        except ValueError as e:
            self._err(400, str(e))
        except Exception as e:  # noqa: BLE001
            self._err(500, str(e))

    def _delete_file(self, rel_path):
        if not rel_path:
            return self._err(400, "缺少 path")
        target = _safe_resolve(self.novel_dir, rel_path)
        ts = time.strftime("%Y%m%d_%H%M%S")
        dest = self.novel_dir / ".trash" / ts / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        target.rename(dest)
        self._json({"ok": True, "trashed_to": _rel(self.novel_dir, dest)})

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(u.path)
        if not path.startswith("/api/"):
            return self._err(404, "not found")
        if not self._check_write_allowed():
            return
        try:
            body = self._read_json_body()
        except json.JSONDecodeError:
            return self._err(400, "请求体不是合法 JSON")
        try:
            if path == "/api/backfill":
                return self._post_backfill(body)
            if path == "/api/jobs":
                code, data = _create_job(self.novel_dir, body)
                return self._json(data, code)
            self._err(404, "not found")
        except (ValueError, L.LayoutError) as e:
            self._err(400, str(e))
        except FileNotFoundError:
            self._err(404, "not found")
        except Exception as e:  # noqa: BLE001
            self._err(500, str(e))

    def _post_backfill(self, body):
        part, vol, ch = body.get("part"), body.get("vol"), body.get("ch")
        tree, _entries = _scan_tree(self.novel_dir)
        result = _do_backfill(self.novel_dir, body.get("src", ""), body.get("target_id", ""),
                              part, vol, ch, tree)
        if body.get("cold") and result.get("mode") in ("outline", "manuscript"):
            job_body = {"kind": "cold", "part": part, "vol": vol, "ch": ch,
                        "mode": result["mode"], "engine": body.get("engine", "local"),
                        "oc_models": body.get("oc_models")}
            code, job_data = _create_job(self.novel_dir, job_body)
            if code == 200:
                result["job_id"] = job_data["job_id"]
            else:
                result["job_error"] = job_data.get("error")
        self._json(result)


def _qint(qs, key):
    v = qs.get(key, [None])[0]
    if v in (None, "", "null"):
        return None
    try:
        return int(v)
    except ValueError:
        return None


def make_handler(novel_dir: Path, title: str, base_override: str | None, read_only: bool = False):
    return type("BoundHandler", (Handler,),
                {"novel_dir": novel_dir, "title": title, "base_override": base_override,
                 "read_only": read_only})


# ================================================================ main

def _lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


def main(argv=None):
    ap = argparse.ArgumentParser(description="本地审查台：浏览/编辑/指令（提示词·冷读·配音）+ 播客 RSS")
    ap.add_argument("novel_dir")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--base-url")
    ap.add_argument("--title")
    ap.add_argument("--read-only", action="store_true", help="关闭全部写入与任务接口，仅浏览/播放")
    args = ap.parse_args(argv)

    novel_dir = Path(args.novel_dir).resolve()
    if not ((novel_dir / "10_正文").is_dir() and (novel_dir / "05_工作区").is_dir()):
        _die(f"{novel_dir} 不像小说目录（需含 10_正文/ 与 05_工作区/）")
    title = args.title or novel_dir.name.split("_", 1)[-1]

    entries = scan(novel_dir)
    httpd = ThreadingHTTPServer((args.host, args.port),
                                make_handler(novel_dir, title, args.base_url, args.read_only))
    disp = _lan_ip() if args.host in ("0.0.0.0", "::") else args.host
    feed = args.base_url.rstrip("/") + "/feed.xml" if args.base_url else f"http://{disp}:{args.port}/feed.xml"
    n_audio = sum(1 for e in entries if e.has_audio)
    mode = "只读" if args.read_only else "可写"
    print(f"审查台 · {title} · {len(entries)} 章（{n_audio} 有配音）· {mode}", flush=True)
    print(f"  首页   : http://{disp}:{args.port}/", flush=True)
    print(f"  播客RSS: {feed}", flush=True)
    print("  Ctrl-C 停", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n停。")
        httpd.shutdown()


if __name__ == "__main__":
    main()
