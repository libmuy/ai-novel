#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
章节浏览 / 审查台本地服务 (serve_audio.py)

一个或多本小说的本地 HTTP 索引。给一个数据根目录（如 `01_小说数据/`）会自动发现
其下全部小说；给一本小说目录只服务那一本。层次（`<slug>` 是书名，多本时才需要）：

    /                    首页——多本时是书架，单本时直接是该书总览
    /n/<slug>/           某本书的总览——选「正文」「工作区」「规划」「指令」
    /n/<slug>/text       正文：章节列表
    /n/<slug>/text/<部>/<卷>/<章>          该章——选「读」「听」「指令」
    /n/<slug>/text/<部>/<卷>/<章>/read     读：渲染 10_正文/…/正文_卷VV_章{C}.md（?raw=1 出纯文本）
    /n/<slug>/text/<部>/<卷>/<章>/listen   听：内嵌播放器
    /n/<slug>/work       工作区：章节列表
    /n/<slug>/work/<部>/<卷>/<章>          该章——选「读」「听」「指令」
    /n/<slug>/work/<部>/<卷>/<章>/read     读：该章 05_工作区/…/CCCC/ 文件浏览器（左：分组文件列表，右：预览）
                                          （?f=<相对路径> 看单个文件，.md 渲染成 HTML；
                                           &raw=1 出纯文本；页面有「复制原文」按钮）
    /n/<slug>/work/<部>/<卷>/<章>/listen   听：同一份音频
    /n/<slug>/plan        规划：顶层文档卡片 + 各卷规划卡片
    /n/<slug>/plan/root?f=<文件名>         读：渲染 03_规划/顶层文件
    /n/<slug>/plan/<部>/<卷>               卷规划文件卡片
    /n/<slug>/plan/<部>/<卷>/read?f=<路径> 读：渲染卷内文件
    /n/<slug>/cmd?kind=&part=&vol=&ch=    指令：选任务+章节，渲染已有的提示词存档
                                          或该跑的本机命令（只读，不执行任何命令）

    /n/<slug>/audio/<部>/<卷>/<章>[/<场>].mp3   音频（支持 Range 断点续传）
    /static/<name>       静态资源（nocturne.css / app.css / icons.svg）
    /health               ok

无 `/n/<slug>` 前缀的旧路由（`/text/…` `/work/…` `/plan/…` `/audio/…`）继续服务
「默认本」（发现到的第一本，按书名排序），兼容单本模式时代的书签与直链。

音频来自 tts_chapter.py 的产出 `05_工作区/**/03_音频/*.mp3`。
**纯 Python 标准库，无第三方依赖。** 按需手动跑，Ctrl-C 停。

用法
----
    python3 02_工具/01_小说通用工具/serve_audio.py [<小说目录或数据根>...] \
        [--host 0.0.0.0] [--port 8765] [--title 标题]

    不给目录参数则从当前目录向上找 `01_小说数据/` 并扫描其下全部小说。
    `--title` 仅在只解析到一本小说时生效，用于覆盖侧边栏书名。

安全：默认监听 0.0.0.0、无鉴权，仅适合可信局域网。章节定位只由解析出的整数
部/卷/章/场号拼装；工作区/规划文件浏览器对 ?f= 做 realpath 越界校验，均无目录
穿越。「指令」页只读——渲染已有存档或展示该跑的命令，不代你执行任何命令。
"""
import argparse
import html
import json
import re
import socket
import sys
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from build_prompt import TASKS  # noqa: E402  复用任务名/存档名，不另写一份

_MP3_RE = re.compile(r"^章(\d+)(?:_场(\d+))?\.mp3$")
_WS_CH_RE = re.compile(r"^(\d{4})$")
_TEXT_EXT = {".md", ".txt", ".json", ".jsonl", ".toml", ".csv"}
_RENDER_CAP = 512 * 1024


def _die(msg, code=1):
    print(msg, file=sys.stderr)
    sys.exit(code)


# ================================================================ 数据模型

class Entry:
    """一章的合并视图：正文 + 工作区目录 + 音频。"""

    def __init__(self, part, vol, ch, novel_dir):
        self.part, self.vol, self.ch = part, vol, ch
        self.novel_dir = novel_dir
        self.manuscript: Path | None = None
        self.ws_dir: Path | None = None

    # -- 标识 --
    @property
    def key(self):
        return (self.part, self.vol, self.ch)

    @property
    def label(self):
        return f"第 {self.ch} 章"

    @property
    def title(self):
        return f"卷{self.vol:02d} 第 {self.ch} 章"

    def path3(self):
        return f"{self.part}/{self.vol}/{self.ch}"

    # -- 音频 --
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

    # 正文
    for md in novel_dir.glob("10_正文/*/*/*.md"):
        m = re.match(r"^正文_卷\d+_章(\d+)\.md$", md.name)
        mp = re.search(r"第0*(\d+)部", str(md))
        mv = re.search(r"卷0*(\d+)", str(md))
        if m and mp and mv:
            get(int(mp.group(1)), int(mv.group(1)), int(m.group(1))).manuscript = md

    # 工作区章目录
    for d in novel_dir.glob("05_工作区/*/*/*"):
        if not d.is_dir():
            continue
        m = _WS_CH_RE.match(d.name)
        mp = re.search(r"第0*(\d+)部", str(d.parent.parent))
        mv = re.search(r"卷0*(\d+)", str(d.parent))
        if m and mp and mv:
            get(int(mp.group(1)), int(mv.group(1)), int(m.group(1))).ws_dir = d

    return [entries[k] for k in sorted(entries)]


def find(entries: list[Entry], part, vol, ch) -> Entry | None:
    for e in entries:
        if e.key == (part, vol, ch):
            return e
    return None


# ================================================================ 规划

class PlanVol:
    """一卷的规划文件聚合。"""

    def __init__(self, part: int, vol: int, novel_dir: Path):
        self.part, self.vol = part, vol
        self.novel_dir = novel_dir
        self.files: list[Path] = []

    @property
    def key(self):
        return (self.part, self.vol)

    @property
    def label(self):
        return f"第 {self.part} 部 · 卷 {self.vol:02d}"

    def path2(self):
        return f"{self.part}/{self.vol}"


class PlanRoot:
    """规划根目录下的顶层文件（03_规划/*.md）。"""

    def __init__(self, novel_dir: Path):
        self.novel_dir = novel_dir
        self.files: list[Path] = []


def scan_planning(novel_dir: Path) -> tuple[PlanRoot, list[PlanVol]]:
    plan_dir = novel_dir / "03_规划"
    if not plan_dir.is_dir():
        return PlanRoot(novel_dir), []

    root = PlanRoot(novel_dir)
    for f in sorted(plan_dir.iterdir()):
        if f.is_file() and f.suffix == ".md":
            root.files.append(f)

    vols: dict[tuple, PlanVol] = {}

    def get_vol(part, vol):
        k = (part, vol)
        if k not in vols:
            vols[k] = PlanVol(part, vol, novel_dir)
        return vols[k]

    for md in plan_dir.rglob("*.md"):
        rel = md.relative_to(plan_dir)
        parts = rel.parts
        if len(parts) < 2:
            continue
        mp = re.search(r"第0*(\d+)部", parts[0])
        mv = re.search(r"卷0*(\d+)", parts[1])
        if mp and mv:
            get_vol(int(mp.group(1)), int(mv.group(1))).files.append(md)

    return root, [vols[k] for k in sorted(vols)]


# ================================================================ 多本小说

class Novel:
    """一本小说：目录 + 面向 URL 的 slug + 展示用书名。scan 结果不缓存——
    章节数百量级下每请求重扫足够快，比「缓存但可能过期」更值得信赖。"""

    __slots__ = ("dir", "slug", "title")

    def __init__(self, novel_dir: Path, slug: str, title: str):
        self.dir = novel_dir
        self.slug = slug
        self.title = title

    @property
    def entries(self) -> list[Entry]:
        return scan(self.dir)

    @property
    def plan(self) -> tuple[PlanRoot, list[PlanVol]]:
        return scan_planning(self.dir)


def _is_novel_dir(p: Path) -> bool:
    return (p / "10_正文").is_dir() and (p / "05_工作区").is_dir()


def _novel_title(p: Path) -> str:
    name = p.name
    return name.split("_", 1)[-1] if "_" in name else name


def discover_novels(dirs: list[Path]) -> list[Novel]:
    """把 CLI 传入的目录列表解析成 Novel 列表：每个目录本身是一本小说，
    或是含多本小说的数据根（一层子目录扫描）；不给目录就从 CWD 向上找
    `01_小说数据/`。"""
    if not dirs:
        cwd = Path.cwd().resolve()
        root = None
        for cand in (cwd, *cwd.parents):
            if (cand / "01_小说数据").is_dir():
                root = cand / "01_小说数据"
                break
        if root is None:
            _die("未指定小说目录，且当前目录及其上级找不到 `01_小说数据/`；"
                 "请传 <小说目录> 或 <数据根目录>。")
        dirs = [root]

    found: list[Path] = []
    for d in dirs:
        d = d.resolve()
        if _is_novel_dir(d):
            found.append(d)
        elif d.is_dir():
            for child in sorted(d.iterdir()):
                if child.is_dir() and _is_novel_dir(child):
                    found.append(child)
        else:
            _die(f"{d} 不存在或不是目录")

    seen: set[Path] = set()
    uniq: list[Path] = []
    for d in found:
        if d not in seen:
            seen.add(d)
            uniq.append(d)
    if not uniq:
        _die("没有找到任何小说目录（需含 10_正文/ 与 05_工作区/）")

    slug_owner: dict[str, Path] = {}
    novels = []
    for d in uniq:
        slug = _novel_title(d)
        if slug in slug_owner and slug_owner[slug] != d:
            slug = d.name  # 撞名退回完整目录名
        slug_owner[slug] = d
        novels.append(Novel(d, slug, _novel_title(d)))
    return sorted(novels, key=lambda n: n.slug)


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


# ================================================================ 静态资源

_WEB_DIR = Path(__file__).resolve().parent / "web"
_STATIC_CTYPE = {
    "nocturne.css": "text/css; charset=utf-8",
    "app.css": "text/css; charset=utf-8",
    "icons.svg": "image/svg+xml; charset=utf-8",
}


def _load_static() -> dict[str, bytes]:
    out = {}
    for name in _STATIC_CTYPE:
        p = _WEB_DIR / name
        if not p.is_file():
            _die(f"缺静态资源 {p}（web/ 目录下应有 nocturne.css / app.css / icons.svg）")
        out[name] = p.read_bytes()
    return out


_STATIC = _load_static()

JS = ("function cpfile(b){var t=document.getElementById('src');navigator.clipboard.writeText(t.value).then("
      "function(){var o=b.textContent;b.textContent='已复制';setTimeout(function(){b.textContent=o;},1500);});}"
      "function chfilter(q){q=(q||'').trim();var rows=document.querySelectorAll('#chrows .chrow');"
      "rows.forEach(function(r){r.style.display=(!q||r.textContent.indexOf(q)>-1)?'':'none';});}")


def _icon(name: str, size: int | None = None) -> str:
    style = f' style="font-size:{size}px"' if size else ""
    return (f'<svg class=icon{style} aria-hidden="true">'
            f'<use href="/static/icons.svg#ph-{name}"></use></svg>')


# ================================================================ 页面骨架

class Ctx:
    """一次请求内的渲染上下文：当前书、全部书、URL 前缀、侧边栏激活项。"""

    def __init__(self, novel: "Novel", novels: list["Novel"], base: str, active: str):
        self.novel = novel
        self.novels = novels
        self.base = base   # "" 或 "/n/<quoted-slug>"
        self.active = active

    def url(self, *parts: str) -> str:
        tail = "/".join(p.strip("/") for p in parts if p != "")
        if not tail:
            return f"{self.base}/" if self.base else "/"
        return f"{self.base}/{tail}" if self.base else f"/{tail}"


def _doc(title: str, inner: str) -> bytes:
    return (
        "<!doctype html><html lang=zh-CN><head><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>{html.escape(title)}</title>"
        "<link rel=stylesheet href=/static/nocturne.css>"
        "<link rel=stylesheet href=/static/app.css>"
        "</head><body>" + inner + f"<script>{JS}</script></body></html>"
    ).encode("utf-8")


def _novel_summary(n: "Novel") -> str:
    entries = n.entries
    n_text = sum(1 for e in entries if e.manuscript)
    n_audio = sum(1 for e in entries if e.has_audio)
    return f"{n_text} 章 · {n_audio} 有配音"


def _side_switcher(ctx: Ctx) -> str:
    others = [n for n in ctx.novels if n is not ctx.novel]
    if not others:
        return ""
    rows = "".join(
        f"<a href='/n/{n.slug}/'>{html.escape(n.title)}</a>"
        for n in others
    )
    return (
        f'<details class=side-books><summary>{_icon("caret-up-down")}'
        f'切换书目（{len(ctx.novels)}）</summary>'
        f'<div class=list>{rows}<a class=all href="/">全部书目</a></div></details>'
    )


def _shell(ctx: Ctx | None, title: str, body: str) -> bytes:
    if ctx is None:
        aside = (f'<div class=side-kicker>{_icon("books")}书库</div>'
                  '<div class=side-title>全部小说</div>')
        rest = ""
    else:
        n = ctx.novel
        aside = (
            f"<a class=side-title-link href='{ctx.url()}'>"
            f'<div class=side-kicker>{_icon("eye")}审查台</div>'
            f"<div class=side-title>{html.escape(n.title)}</div></a>"
            f"<div class=side-sub>{_novel_summary(n)}</div>"
        )
        items = (("text", "正文", "book-open"), ("work", "工作区", "folder-open"),
                  ("plan", "规划", "compass"), ("cmd", "指令", "terminal-window"))
        nav_rows = []
        for key, label, icon in items:
            cls = " class=on" if ctx.active == key else ""
            nav_rows.append(f"<a{cls} href='{ctx.url(key)}'><span class=bar></span>"
                             f"{_icon(icon)}<span class=lbl>{label}</span></a>")
        rest = f'<nav class=side-nav>{"".join(nav_rows)}</nav>' + _side_switcher(ctx)
    inner = (f'<div class=shell><aside class=side>{aside}{rest}</aside>'
             f'<main class=main>{body}</main></div>')
    return _doc(title, inner)


def _crumb(*parts) -> str:
    bits = []
    for p in parts[:-1]:
        href, text = p
        bits.append(f"<a href='{html.escape(href)}'>{html.escape(text)}</a>")
    bits.append(html.escape(parts[-1][1]) if isinstance(parts[-1], tuple) else html.escape(parts[-1]))
    return f"<div class=crumb>{' / '.join(bits)}</div>"


def _header(eyebrow: str, title: str) -> str:
    return f"<div class=eyebrow>{html.escape(eyebrow)}</div><h1 class=page-title>{html.escape(title)}</h1>"


def _copybar(raw_text: str, label: str = "复制原文") -> str:
    return (f"<div class=copybar><button class='btn btn-secondary' onclick='cpfile(this)'>{label}</button>"
            f"<textarea id=src hidden>{html.escape(raw_text)}</textarea></div>")


# ================================================================ 首页 / 书架

def page_root(novels: list[Novel]) -> bytes:
    if len(novels) == 1:
        return page_novel_home(Ctx(novels[0], novels, "", "home"))
    cards = []
    for n in novels:
        entries = n.entries
        n_text = sum(1 for e in entries if e.manuscript)
        n_work = sum(1 for e in entries if e.ws_dir)
        n_audio = sum(1 for e in entries if e.has_audio)
        root_p, vols_p = n.plan
        n_plan = len(vols_p) + len(root_p.files)
        href = f"/n/{n.slug}/"
        cards.append(
            f"<a class=card href='{href}'>"
            "<div class=card-kicker>小说</div>"
            f'<div class=card-title-row>{_icon("book-open", 15)}<span>{html.escape(n.title)}</span></div>'
            f"<div class=card-body>{n_text} 章正文 · {n_work} 章工作区 · {n_audio} 有配音 · {n_plan} 规划项</div>"
            "</a>"
        )
    body = _header("书库", "全部小说") + f'<div class=shelf>{"".join(cards)}</div>'
    return _shell(None, "小说库", body)


def page_novel_home(ctx: Ctx) -> bytes:
    n = ctx.novel
    entries = n.entries
    root_p, vols_p = n.plan
    n_text = sum(1 for e in entries if e.manuscript)
    n_work = sum(1 for e in entries if e.ws_dir)
    n_audio = sum(1 for e in entries if e.has_audio)
    n_plan = len(vols_p) + len(root_p.files)
    body = (
        _header("首页", n.title)
        + "<div class=choices>"
        f"<a class=choice href='{ctx.url('text')}'><b>正文</b><span>{n_text} 章 · 读定稿正文 / 听配音</span></a>"
        f"<a class=choice href='{ctx.url('work')}'><b>工作区</b><span>{n_work} 章 · 提示词 / 模型输出 / 状态 / 校验记录</span></a>"
        f"<a class=choice href='{ctx.url('plan')}'><b>规划</b><span>{n_plan} 项 · 伏笔总纲 / 卷规划 / 章细纲</span></a>"
        f"<a class=choice href='{ctx.url('cmd')}'><b>指令</b><span>拼提示词 / 看该跑的命令</span></a>"
        "</div>"
    )
    if n_text:
        body += ('<div class=row-h2>最近章节</div>'
                 f'<div class=rows>{_chapter_rows(entries, ctx, "text", limit=5)}</div>')
    return _shell(ctx, f"{n.title} · 首页", body)


# ================================================================ 章节状态

def chapter_state(e: Entry, branch: str) -> tuple[str, str]:
    """章节状态标签（文案, tag 类名）——由文件系统事实推导，不解析 00_进度.md。"""
    has_outline = bool(e.ws_dir) and (e.ws_dir / "01_模型输出" / "00_单章细纲.md").exists()
    if branch == "text":
        if not e.manuscript:
            return ("待写正文", "tag-accent") if has_outline else ("待细纲", "tag-accent")
        return ("已配音", "tag-neutral") if e.has_audio else ("待配音", "tag-neutral")
    # branch == "work"
    if not e.ws_dir:
        return ("无工作区", "tag-outline")
    if (e.ws_dir / "02_状态" / "02_正文校验记录.md").exists():
        return ("已冷读", "tag-outline")
    if e.manuscript:
        return ("已配音", "tag-neutral") if e.has_audio else ("待配音", "tag-neutral")
    return ("待写正文", "tag-accent") if has_outline else ("待细纲", "tag-accent")


def _wordcount(md: Path) -> int:
    try:
        return len(re.sub(r"\s", "", md.read_text(encoding="utf-8")))
    except OSError:
        return 0


def _chapter_rows(entries, ctx: Ctx, branch: str, limit: int | None = None) -> str:
    keep = [e for e in entries if (e.manuscript if branch == "text" else e.ws_dir)]
    if limit:
        keep = keep[-limit:]
    if not keep:
        return "<div class=empty>（没有内容）</div>"
    out, cur = [], None
    for e in keep:
        if (e.part, e.vol) != cur:
            cur = (e.part, e.vol)
            out.append(f"<div class=row-h2>第 {e.part} 部 · 卷 {e.vol:02d}</div>")
        base3 = ctx.url(branch, str(e.part), str(e.vol), str(e.ch))
        state_txt, state_cls = chapter_state(e, branch)
        meta = []
        if branch == "text":
            meta.append(f"{_wordcount(e.manuscript)} 字" if e.manuscript else "")
        if e.has_audio:
            meta.append(_fmt_hms(e.duration_s()))
        meta_s = " · ".join(x for x in meta if x)
        listen = (f"<a class='btn btn-ghost' href='{base3}/listen'>{_icon('headphones')}听</a>"
                  if e.has_audio else "<span class=off>听</span>")
        cmd_href = f"{ctx.url('cmd')}?part={e.part}&vol={e.vol}&ch={e.ch}"
        out.append(
            "<div class=chrow>"
            f"<span class='no mono'>{e.ch:04d}</span>"
            f"<span class=ti>{html.escape(e.label)}</span>"
            + (f"<span class=me>{html.escape(meta_s)}</span>" if meta_s else "")
            + f"<span class='tag {state_cls}'>{state_txt}</span>"
            "<span class=acts>"
            f"<a class='btn btn-secondary' href='{base3}/read'>{_icon('book-open')}读</a>"
            f"{listen}"
            f"<a class='btn btn-ghost' href='{cmd_href}'>{_icon('terminal-window')}指令</a>"
            "</span></div>"
        )
    return "".join(out)


def page_list(entries, ctx: Ctx, branch: str) -> bytes:
    zh = "正文" if branch == "text" else "工作区"
    body = (
        _crumb((ctx.url(), ctx.novel.title), zh)
        + "<div class=list-head>"
        f"<div class=grow>{_header(zh, zh)}</div>"
        f"<label class=searchbar>{_icon('magnifying-glass')}"
        "<input class=input placeholder='搜章号、标题、状态…' oninput='chfilter(this.value)'></label>"
        "</div>"
        f"<div class=rows id=chrows>{_chapter_rows(entries, ctx, branch)}</div>"
    )
    return _shell(ctx, f"{ctx.novel.title} · {zh}", body)


def page_chapter(e: Entry, ctx: Ctx, branch: str) -> bytes:
    zh = "正文" if branch == "text" else "工作区"
    base3 = ctx.url(branch, str(e.part), str(e.vol), str(e.ch))
    read_hint = ("渲染定稿正文" if branch == "text"
                 else "该章工作区文件（提示词/模型输出/状态/校验记录）")
    listen_cls = "" if e.has_audio else " disabled"
    listen_hint = _fmt_hms(e.duration_s()) if e.has_audio else "未配音"
    cmd_href = f"{ctx.url('cmd')}?part={e.part}&vol={e.vol}&ch={e.ch}"
    body = (
        _crumb((ctx.url(), ctx.novel.title), (ctx.url(branch), zh), e.label)
        + _header(zh, e.title)
        + "<div class=choices>"
        f"<a class=choice href='{base3}/read'><b>读</b><span>{read_hint}</span></a>"
        f"<a class='choice{listen_cls}' href='{base3}/listen'><b>听</b><span>{listen_hint}</span></a>"
        f"<a class=choice href='{cmd_href}'><b>指令</b><span>拼提示词 / 看该跑的命令</span></a>"
        "</div>"
    )
    return _shell(ctx, f"{ctx.novel.title} · {e.title}", body)


# ================================================================ Markdown 子集渲染

_MD_CODE = re.compile(r"`([^`]+)`")
_MD_BOLD = re.compile(r"\*\*(?!\s)([^*\n]+?)\*\*")
_MD_ITAL = re.compile(r"(?<![\*\w])\*(?!\s)([^*\n]+?)\*(?!\*)")
_MD_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)")
_MD_HEAD = re.compile(r"(#{1,6})\s+(.*)")
_MD_HR = re.compile(r"(?:\*\s*){3,}|(?:-\s*){3,}|(?:_\s*){3,}")
_MD_LI = re.compile(r"\s*([-*+]|\d+[.)])\s+(.*)")
_MD_TSEP = re.compile(r"\s*\|?[\s:|-]*-[\s:|-]*\|?\s*")


def _md_inline(s: str) -> str:
    """行内标记 → HTML。s 必须已 html.escape。"""
    s = _MD_CODE.sub(lambda m: f"<code>{m.group(1)}</code>", s)
    s = _MD_BOLD.sub(lambda m: f"<strong>{m.group(1)}</strong>", s)
    s = _MD_ITAL.sub(lambda m: f"<em>{m.group(1)}</em>", s)
    s = _MD_LINK.sub(lambda m: f"<a href=\"{m.group(2)}\" rel=noopener target=_blank>{m.group(1)}</a>", s)
    return s


def _md_row(line: str) -> list[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def _md_li_html(item: str) -> str:
    box = {"[ ]": "☐ ", "[x]": "☑ ", "[X]": "☑ "}
    for k, v in box.items():
        if item.startswith(k + " "):
            return v + _md_inline(html.escape(item[len(k) + 1:]))
    return _md_inline(html.escape(item))


def render_markdown(text: str) -> str:
    """Markdown 子集 → HTML（标题/列表/表格/引用/围栏代码/分隔线/行内标记）。

    纯字符串处理，无第三方依赖；未覆盖的写法按普通段落原样（转义后）显示。
    """
    lines = text.lstrip("﻿").split("\n")
    out: list[str] = []
    para: list[str] = []
    n = len(lines)
    i = 0

    def flush():
        if para:
            out.append("<p>" + "<br>".join(_md_inline(html.escape(x)) for x in para) + "</p>")
            para.clear()

    while i < n:
        raw = lines[i]
        s = raw.strip()

        if s.startswith("```") or s.startswith("~~~"):
            flush()
            fence = s[:3]
            i += 1
            code = []
            while i < n and not lines[i].strip().startswith(fence):
                code.append(lines[i])
                i += 1
            i += 1
            out.append("<pre class=code><code>" + html.escape("\n".join(code)) + "</code></pre>")
            continue

        if not s:
            flush()
            i += 1
            continue

        m = _MD_HEAD.match(s)
        if m:
            flush()
            lvl = len(m.group(1))
            out.append(f"<h{lvl}>{_md_inline(html.escape(m.group(2).rstrip('#').strip()))}</h{lvl}>")
            i += 1
            continue

        if _MD_HR.fullmatch(s):
            flush()
            out.append("<hr>")
            i += 1
            continue

        if "|" in raw and i + 1 < n and "-" in lines[i + 1] and _MD_TSEP.fullmatch(lines[i + 1]):
            flush()
            header = _md_row(raw)
            i += 2
            rows = []
            while i < n and lines[i].strip() and "|" in lines[i] and not lines[i].lstrip().startswith("#"):
                rows.append(_md_row(lines[i]))
                i += 1
            th = "".join(f"<th>{_md_inline(html.escape(c))}</th>" for c in header)
            tb = "".join("<tr>" + "".join(f"<td>{_md_inline(html.escape(c))}</td>" for c in r)
                         + "</tr>" for r in rows)
            out.append(f"<table><thead><tr>{th}</tr></thead><tbody>{tb}</tbody></table>")
            continue

        if s.startswith(">"):
            flush()
            quote = []
            while i < n and lines[i].strip().startswith(">"):
                quote.append(lines[i].strip()[1:].lstrip())
                i += 1
            out.append("<blockquote>"
                       + "<br>".join(_md_inline(html.escape(x)) for x in quote) + "</blockquote>")
            continue

        m = _MD_LI.fullmatch(raw)
        if m:
            flush()
            tag = "ol" if m.group(1)[0].isdigit() else "ul"
            items = []
            while i < n and lines[i].strip():
                mm = _MD_LI.fullmatch(lines[i])
                if not mm:
                    break
                items.append(mm.group(2))
                i += 1
            out.append(f"<{tag}>" + "".join(f"<li>{_md_li_html(x)}</li>" for x in items) + f"</{tag}>")
            continue

        para.append(s)
        i += 1

    flush()
    return "".join(out)


def render_prose(text: str) -> str:
    text = text.lstrip("﻿")
    html_parts = []
    for block in re.split(r"\n\s*\n", text):
        block = block.strip()
        if not block:
            continue
        if block in ("※", "＊", "***", "* * *"):
            html_parts.append("<hr>")
        else:
            html_parts.append("<p>" + html.escape(block).replace("\n", "<br>") + "</p>")
    return "".join(html_parts)


def page_manuscript(e: Entry, ctx: Ctx, raw: bool):
    if not e.manuscript or not e.manuscript.exists():
        return None
    data = e.manuscript.read_text(encoding="utf-8")
    if raw:
        return ("text/plain; charset=utf-8", data.encode("utf-8"))
    base3 = ctx.url("text", str(e.part), str(e.vol), str(e.ch))
    body = (_crumb((ctx.url(), ctx.novel.title), (ctx.url("text"), "正文"), (base3, e.label), "读")
            + _header("正文", e.title)
            + f"<div class=crumb><a href='{base3}/read?raw=1'>纯文本</a>"
            f" · <a href='{base3}/listen'>听</a></div>"
            f"{_copybar(data)}"
            f"<div class=prose>{render_prose(data)}</div>")
    return ("text/html; charset=utf-8", _shell(ctx, f"{ctx.novel.title} · {e.title}", body))


def page_listen(e: Entry, ctx: Ctx, branch: str) -> bytes:
    zh = "正文" if branch == "text" else "工作区"
    units = e.audio_units()
    base3 = ctx.url(branch, str(e.part), str(e.vol), str(e.ch))
    if not units:
        inner = "<div class=empty>这章还没配音。<br><code>tts_chapter.py --chapter-dir …</code></div>"
    else:
        m = e.manifest
        info = " · ".join(x for x in [m.get("voice", ""), m.get("generated_at", ""),
                                      _fmt_size(e.total_audio_bytes())] if x)
        blocks = []
        for scene, path in units:
            aurl = ctx.url("audio", str(e.part), str(e.vol), str(e.ch)) + (f"/{scene}.mp3" if scene else ".mp3")
            lbl = f"场 {scene}" if scene else "整章"
            blocks.append(f"<div class=me>{lbl}</div>"
                          f"<audio controls preload=none src='{aurl}'></audio>"
                          f"<div><a href='{aurl}' download>下载</a></div>")
        inner = (f"<div class=crumb>{html.escape(info)}</div>" + "".join(blocks))
    body = (_crumb((ctx.url(), ctx.novel.title), (ctx.url(branch), zh), (base3, e.label), "听")
            + _header(zh, e.title)
            + f"<div class=crumb><a href='{base3}/read'>← 读这一章</a></div>" + inner)
    return _shell(ctx, f"{ctx.novel.title} · {e.title} · 听", body)


def page_work_read(e: Entry, ctx: Ctx, rel: str | None, raw: bool = False):
    if not e.ws_dir or not e.ws_dir.is_dir():
        return None
    root = e.ws_dir.resolve()
    base3 = ctx.url("work", str(e.part), str(e.vol), str(e.ch))
    crumb = _crumb((ctx.url(), ctx.novel.title), (ctx.url("work"), "工作区"), (base3, e.label), "读")

    files = sorted(p for p in e.ws_dir.rglob("*") if p.is_file())
    groups: dict[str, list[Path]] = {}
    for p in files:
        r = p.relative_to(e.ws_dir).as_posix()
        grp = r.split("/", 1)[0] if "/" in r else "（根目录）"
        groups.setdefault(grp, []).append(p)

    active_rel = rel
    if active_rel is None and files:
        active_rel = next(iter(groups.values()))[0].relative_to(e.ws_dir).as_posix()

    if active_rel:
        target = (e.ws_dir / active_rel).resolve()
        if not target.is_file() or root not in target.parents:
            return ("text/plain; charset=utf-8", b"not found\n", 404)
        suffix = target.suffix.lower()
        if suffix == ".mp3":
            aurl = ctx.url("audio", str(e.part), str(e.vol), str(e.ch)) + ".mp3"
            preview = (f"<div class=preview-head><span class=nm>{html.escape(active_rel)}</span></div>"
                       f"<div class=preview-body><audio controls src='{aurl}'></audio></div>")
        elif suffix not in _TEXT_EXT:
            preview = (f"<div class=preview-head><span class=nm>{html.escape(active_rel)}</span></div>"
                       f"<div class=preview-body><p class=text-muted>"
                       f"（{_fmt_size(_safe_size(target))}，不支持预览）</p></div>")
        else:
            text = target.read_bytes()[:_RENDER_CAP].decode("utf-8", "replace")
            if raw:
                return ("text/plain; charset=utf-8", text.encode("utf-8"))
            note = ("<p class=text-muted>（文件较大，只显示前 512 KiB）</p>"
                    if _safe_size(target) > _RENDER_CAP else "")
            rq = urllib.parse.quote(active_rel)
            rendered = (f"<div class=md>{render_markdown(text)}</div>" if suffix == ".md"
                        else f"<pre class=file>{html.escape(text)}</pre>")
            preview = (
                "<div class=preview-head>"
                f"<span class=nm>{html.escape(active_rel)}</span>"
                "<div class=acts>"
                f"<a class='btn btn-ghost' href='{base3}/read?f={rq}&raw=1'>{_icon('file-text')}纯文本</a>"
                f"{_copybar(text)}"
                "</div></div>"
                f"<div class=preview-body>{note}{rendered}</div>"
            )
    else:
        preview = "<div class=empty>（空目录）</div>"

    if not files:
        left = "<div class=empty>（空目录）</div>"
    else:
        blocks = []
        for grp, ps in groups.items():
            rows = []
            for p in ps:
                r = p.relative_to(e.ws_dir).as_posix()
                q = urllib.parse.quote(r)
                cls = " class=on" if r == active_rel else ""
                rows.append(
                    f"<a{cls} href='{base3}/read?f={q}' title='{html.escape(r)}'>"
                    f"<span class=nm>{html.escape(p.name)}</span>"
                    f"<span class=sz>{_fmt_size(_safe_size(p))}</span></a>"
                )
            blocks.append(f"<div><div class=ws-group-h>{html.escape(grp)}</div>"
                          f'<div class=ws-group-files>{"".join(rows)}</div></div>')
        left = "".join(blocks)

    listen = (f"<div class=crumb><a href='{base3}/listen'>听这一章 →</a></div>" if e.has_audio else "")
    body = (
        crumb + _header("工作区", f"{e.title} · 过程文件") + listen
        + "<div class=ws>"
        f"<div class=ws-files>{left}</div>"
        f"<div class=preview>{preview}</div>"
        "</div>"
    )
    return ("text/html; charset=utf-8", _shell(ctx, f"{ctx.novel.title} · {e.title} · 工作区", body))


# ================================================================ 规划页面

def page_plan_root(root: PlanRoot, vols: list[PlanVol], ctx: Ctx) -> bytes:
    body = _crumb((ctx.url(), ctx.novel.title), "规划") + _header("规划", "总纲与卷规划")
    cards = []
    for f in root.files:
        href = f"{ctx.url('plan', 'root')}?f={urllib.parse.quote(f.name)}"
        cards.append(
            f"<a class=card href='{href}'>"
            "<div class=card-kicker>顶层</div>"
            f'<div class=card-title-row>{_icon("file-text", 15)}<span>{html.escape(f.name)}</span></div>'
            "<div class=card-body>顶层规划文档</div>"
            f"<div class=card-meta>{_fmt_size(_safe_size(f))}</div></a>"
        )
    for v in vols:
        href = ctx.url("plan", str(v.part), str(v.vol))
        cards.append(
            f"<a class=card href='{href}'>"
            f"<div class=card-kicker>第 {v.part} 部 · 卷 {v.vol:02d}</div>"
            f'<div class=card-title-row>{_icon("map-trifold", 15)}<span>卷规划</span></div>'
            "<div class=card-body>浏览该卷全部规划文件</div>"
            f"<div class=card-meta>{len(v.files)} 个文件</div></a>"
        )
    if not root.files and not vols:
        body += "<div class=empty>（03_规划/ 目录为空）</div>"
    else:
        body += f'<div class=plan-grid>{"".join(cards)}</div>'
    return _shell(ctx, f"{ctx.novel.title} · 规划", body)


def page_plan_vol(vol: PlanVol, ctx: Ctx) -> bytes:
    base2 = ctx.url("plan", str(vol.part), str(vol.vol))
    crumb = _crumb((ctx.url(), ctx.novel.title), (ctx.url("plan"), "规划"), vol.label)
    if not vol.files:
        inner = "<div class=empty>（空目录）</div>"
    else:
        cards = []
        for f in vol.files:
            r = f.relative_to(vol.novel_dir / "03_规划").as_posix()
            q = urllib.parse.quote(r)
            cards.append(
                f"<a class=card href='{base2}/read?f={q}'>"
                f'<div class=card-title-row>{_icon("file-text", 15)}<span>{html.escape(f.name)}</span></div>'
                f"<div class=card-meta>{_fmt_size(_safe_size(f))}</div></a>"
            )
        inner = f'<div class=plan-grid>{"".join(cards)}</div>'
    body = crumb + _header("规划", f"{vol.label} · 规划") + inner
    return _shell(ctx, f"{ctx.novel.title} · {vol.label} · 规划", body)


def page_plan_file(plan_dir: Path, rel: str, ctx: Ctx, raw: bool = False):
    target = (plan_dir / rel).resolve()
    if not target.is_file() or plan_dir.resolve() not in target.parents:
        return ("text/plain; charset=utf-8", b"not found\n", 404)
    suffix = target.suffix.lower()
    crumb = _crumb((ctx.url(), ctx.novel.title), (ctx.url("plan"), "规划"), rel)
    if suffix not in _TEXT_EXT:
        body = (crumb + _header("规划", rel)
                + f"<p class=text-muted>（{_fmt_size(_safe_size(target))}，不支持预览）</p>")
        return ("text/html; charset=utf-8", _shell(ctx, ctx.novel.title, body))
    text = target.read_bytes()[:_RENDER_CAP].decode("utf-8", "replace")
    if raw:
        return ("text/plain; charset=utf-8", text.encode("utf-8"))
    note = "<p class=text-muted>（文件较大，只显示前 512 KiB）</p>" if _safe_size(target) > _RENDER_CAP else ""
    rq = urllib.parse.quote(rel)
    rendered = (f"<div class=md>{render_markdown(text)}</div>" if suffix == ".md"
                else f"<pre class=file>{html.escape(text)}</pre>")
    body = (crumb + _header("规划", rel)
            + f"<div class=crumb><a href='{ctx.url('plan')}'>← 规划列表</a>"
            f" · <a href='{ctx.url('plan', 'root')}?f={rq}&raw=1'>纯文本</a></div>{note}"
            f"{_copybar(text)}{rendered}")
    return ("text/html; charset=utf-8", _shell(ctx, f"{ctx.novel.title} · {rel}", body))


def page_plan_file_in_vol(vol: PlanVol, rel: str, ctx: Ctx, raw: bool = False):
    plan_dir = vol.novel_dir / "03_规划"
    target = (plan_dir / rel).resolve()
    if not target.is_file() or plan_dir.resolve() not in target.parents:
        return ("text/plain; charset=utf-8", b"not found\n", 404)
    suffix = target.suffix.lower()
    base2 = ctx.url("plan", str(vol.part), str(vol.vol))
    crumb = _crumb((ctx.url(), ctx.novel.title), (ctx.url("plan"), "规划"), (base2, vol.label), rel)
    if suffix not in _TEXT_EXT:
        body = (crumb + _header("规划", rel)
                + f"<p class=text-muted>（{_fmt_size(_safe_size(target))}，不支持预览）</p>")
        return ("text/html; charset=utf-8", _shell(ctx, ctx.novel.title, body))
    text = target.read_bytes()[:_RENDER_CAP].decode("utf-8", "replace")
    if raw:
        return ("text/plain; charset=utf-8", text.encode("utf-8"))
    note = "<p class=text-muted>（文件较大，只显示前 512 KiB）</p>" if _safe_size(target) > _RENDER_CAP else ""
    rq = urllib.parse.quote(rel)
    rendered = (f"<div class=md>{render_markdown(text)}</div>" if suffix == ".md"
                else f"<pre class=file>{html.escape(text)}</pre>")
    body = (crumb + _header("规划", rel)
            + f"<div class=crumb><a href='{base2}'>← 文件列表</a>"
            f" · <a href='{base2}/read?f={rq}&raw=1'>纯文本</a></div>{note}"
            f"{_copybar(text)}{rendered}")
    return ("text/html; charset=utf-8", _shell(ctx, f"{ctx.novel.title} · {rel}", body))


# ================================================================ 指令页（只读拼装器）

_CMD_KINDS = (
    ("细纲", "生成章细纲", "拼出云端提示词（卷规划 + 伏笔 + 前文摘要）", "list-dashes"),
    ("正文", "生成章正文", "以采纳的细纲为输入，输出正文草稿", "pen-nib"),
    ("冷读", "一致性冷读", "多模型对抗性冷读：人物知情 / 时间线 / 伏笔", "check-circle"),
    ("配音", "生成配音", "本机 tts_chapter.py，按场切分", "waveform"),
    ("审计", "一致性审计", "确定性一致性审查（引用/索引/状态/伏笔…）", "shield-check"),
)


def _recent_cmd_items(novel: Novel, ctx: Ctx, limit: int = 6) -> list[str]:
    items = []
    for e in novel.entries:
        if not e.ws_dir or not e.ws_dir.is_dir():
            continue
        for p in e.ws_dir.rglob("*"):
            if p.is_file():
                try:
                    items.append((p.stat().st_mtime, p, e))
                except OSError:
                    continue
    items.sort(key=lambda x: x[0], reverse=True)
    out = []
    for mt, p, e in items[:limit]:
        rel = p.relative_to(e.ws_dir).as_posix()
        href = f"{ctx.url('work', str(e.part), str(e.vol), str(e.ch))}/read?f={urllib.parse.quote(rel)}"
        when = time.strftime("%m-%d %H:%M", time.localtime(mt))
        out.append(
            f"<a class=item href='{href}'>{_icon('clock-counter-clockwise')}"
            f"<span class=what>第 {e.ch} 章 · {html.escape(rel)}</span>"
            f"<span class=when>{when}</span></a>"
        )
    return out


def page_cmd(novel: Novel, ctx: Ctx, kind: str | None, part: str, vol: str, ch: str | None) -> bytes:
    valid_kinds = {k[0] for k in _CMD_KINDS}
    kind = kind if kind in valid_kinds else "细纲"
    try:
        part_i, vol_i = int(part), int(vol)
    except (TypeError, ValueError):
        part_i, vol_i = 1, 1

    entries = novel.entries
    ch_i = None
    if ch:
        try:
            ch_i = int(ch)
        except ValueError:
            ch_i = None
    if ch_i is None:
        ch_i = entries[-1].ch if entries else 1

    e = find(entries, part_i, vol_i, ch_i)

    kind_rows = []
    for kid, label, desc, icon in _CMD_KINDS:
        cls = " class=on" if kid == kind else ""
        href = f"{ctx.url('cmd')}?kind={urllib.parse.quote(kid)}&part={part_i}&vol={vol_i}&ch={ch_i}"
        kind_rows.append(
            f"<a{cls} href='{href}'>{_icon(icon, 18)}"
            f"<span><span class=t>{html.escape(label)}</span>"
            f"<span class=d>{html.escape(desc)}</span></span></a>"
        )

    novel_path = str(novel.dir)
    chapter_dir = str(e.ws_dir) if (e and e.ws_dir) else None
    archive_rel = f"00_提示词/{TASKS[kind][1]}" if kind in TASKS else None

    if kind in TASKS:
        cmd_text = (f"python3 02_工具/01_小说通用工具/build_prompt.py --novel {novel_path} "
                    f"--task {kind} --part {part_i} --volume {vol_i} --chapter {ch_i}")
    elif kind == "冷读":
        cmd_text = (f"python3 02_工具/01_小说通用工具/review_manuscript.py "
                    f"--chapter-dir {chapter_dir or '<章工作区目录>'}")
    elif kind == "配音":
        cmd_text = (f"python3 02_工具/01_小说通用工具/tts_chapter.py "
                    f"--chapter-dir {chapter_dir or '<章工作区目录>'}")
    else:  # 审计
        cmd_text = f"python3 02_工具/01_小说通用工具/audit_consistency.py {novel_path}"

    archive_text = None
    if archive_rel and e and e.ws_dir:
        p = e.ws_dir / archive_rel
        if p.is_file():
            archive_text = p.read_text(encoding="utf-8", errors="replace")

    if archive_text is not None:
        out_head = (f"<span class=nm>{html.escape(archive_rel)}</span>"
                    "<span class='tag tag-accent'>已生成</span>")
        out_body = f"{_copybar(archive_text, '复制提示词')}<pre class=file>{html.escape(archive_text)}</pre>"
    else:
        note = "（该存档还没生成，跑下面的命令后回来看）" if archive_rel else "（本任务没有提示词存档，直接跑命令）"
        out_head = "<span class=nm>该跑的命令</span><span class='tag tag-outline'>未生成</span>"
        out_body = (f"<p class=text-muted>{note}</p>"
                    f"{_copybar(cmd_text, '复制命令')}<pre class=file>{html.escape(cmd_text)}</pre>")

    form = (
        "<div class=cmd-form>"
        f"<label>部<input class=input value='{part_i}' readonly></label>"
        f"<label>卷<input class=input value='{vol_i}' readonly></label>"
        f"<label>章<input class=input value='{ch_i}' readonly></label>"
        "</div>"
        "<p class=text-muted style='font-size:12px'>改部/卷/章：在地址栏加 "
        "<code class=mono>?part=&vol=&ch=</code>，或从「正文/工作区」章节行的「指令」按钮进来。</p>"
    )

    recent = _recent_cmd_items(novel, ctx)
    recent_html = f'<div class=cmd-recent>{"".join(recent)}</div>' if recent else "<div class=empty>（还没有任何产出）</div>"

    body = (
        _crumb((ctx.url(), novel.title), "指令")
        + _header("指令", "发出指令")
        + "<p class=text-muted style='max-width:60ch'>选任务与章节，界面拼出可直接贴给 Agent 的提示词，"
        "或给出该跑的本机命令；本页只读，不会代你执行。</p>"
        + "<div class=cmd-grid>"
        f'<div class=cmd-kinds>{"".join(kind_rows)}{form}</div>'
        "<div class=cmd-out>"
        f'<div class=cmd-out-head>{_icon("brackets-curly")}{out_head}</div>'
        f"<div class=preview-body>{out_body}</div>"
        "</div></div>"
        '<div class=row-h2>最近产出</div>' + recent_html
    )
    return _shell(ctx, f"{novel.title} · 指令", body)


# ================================================================ HTTP

class Handler(BaseHTTPRequestHandler):
    server_version = "serve_audio/3.0"
    protocol_version = "HTTP/1.1"
    novels: list[Novel] = []

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

    def do_HEAD(self):
        self.do_GET()

    @property
    def default_novel(self) -> Novel:
        return self.novels[0]

    def _find_novel(self, slug: str) -> Novel | None:
        for n in self.novels:
            if n.slug == slug:
                return n
        return None

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(u.path)
        qs = urllib.parse.parse_qs(u.query)
        seg = [s for s in path.split("/") if s]
        try:
            if path == "/health":
                return self._send(b"ok", "text/plain; charset=utf-8")
            if len(seg) == 2 and seg[0] == "static":
                return self._serve_static(seg[1])
            if not seg:
                return self._send(page_root(self.novels), "text/html; charset=utf-8")
            if seg[0] == "n" and len(seg) >= 2:
                novel = self._find_novel(seg[1])
                if not novel:
                    return self._send(b"not found\n", "text/plain; charset=utf-8", 404)
                base = f"/n/{novel.slug}"
                return self._route_novel(novel, seg[2:], qs, base)
            return self._route_novel(self.default_novel, seg, qs, "")
        except BrokenPipeError:
            pass
        except Exception as ex:  # noqa: BLE001
            self._send(f"error: {ex}\n".encode(), "text/plain; charset=utf-8", 500)

    def _serve_static(self, name: str):
        data = _STATIC.get(name)
        if data is None:
            return self._send(b"not found\n", "text/plain; charset=utf-8", 404)
        self._send(data, _STATIC_CTYPE[name], extra={"Cache-Control": "max-age=3600"})

    def _route_novel(self, novel: Novel, seg: list[str], qs: dict, base: str):
        active = seg[0] if seg and seg[0] in ("text", "work", "plan", "cmd") else "home"
        ctx = Ctx(novel, self.novels, base, active)

        if not seg:
            return self._send(page_novel_home(ctx), "text/html; charset=utf-8")
        if seg[0] == "audio":
            return self._serve_audio(novel, seg[1:])
        if seg[0] in ("text", "work"):
            return self._route_branch(ctx, seg, qs, novel.entries)
        if seg[0] == "plan":
            return self._route_plan(ctx, novel, seg, qs)
        if seg[0] == "cmd":
            kind = qs.get("kind", [None])[0]
            part = qs.get("part", ["1"])[0]
            vol = qs.get("vol", ["1"])[0]
            ch = qs.get("ch", [None])[0]
            return self._send(page_cmd(novel, ctx, kind, part, vol, ch), "text/html; charset=utf-8")
        self._send(b"not found\n", "text/plain; charset=utf-8", 404)

    def _route_branch(self, ctx: Ctx, seg, qs, entries):
        branch = seg[0]
        rest = seg[1:]
        if not rest:
            return self._send(page_list(entries, ctx, branch), "text/html; charset=utf-8")
        if len(rest) < 3:
            return self._send(b"not found\n", "text/plain; charset=utf-8", 404)
        try:
            part, vol, ch = int(rest[0]), int(rest[1]), int(rest[2])
        except ValueError:
            return self._send(b"not found\n", "text/plain; charset=utf-8", 404)
        e = find(entries, part, vol, ch)
        if not e:
            return self._send(b"not found\n", "text/plain; charset=utf-8", 404)
        verb = rest[3] if len(rest) >= 4 else None

        if verb is None:
            return self._send(page_chapter(e, ctx, branch), "text/html; charset=utf-8")
        if verb == "listen":
            return self._send(page_listen(e, ctx, branch), "text/html; charset=utf-8")
        if verb == "read" and branch == "text":
            r = page_manuscript(e, ctx, raw=qs.get("raw", ["0"])[0] == "1")
            if r is None:
                return self._send(b"not found\n", "text/plain; charset=utf-8", 404)
            return self._send(r[1], r[0])
        if verb == "read" and branch == "work":
            r = page_work_read(e, ctx, qs.get("f", [None])[0], raw=qs.get("raw", ["0"])[0] == "1")
            if r is None:
                return self._send(b"not found\n", "text/plain; charset=utf-8", 404)
            return self._send(r[1], r[0], r[2] if len(r) > 2 else 200)
        self._send(b"not found\n", "text/plain; charset=utf-8", 404)

    def _route_plan(self, ctx: Ctx, novel: Novel, seg, qs):
        rest = seg[1:]
        raw = qs.get("raw", ["0"])[0] == "1"
        plan_dir = novel.dir / "03_规划"
        plan_root, plan_vols = novel.plan

        if not rest:
            return self._send(page_plan_root(plan_root, plan_vols, ctx), "text/html; charset=utf-8")

        if rest[0] == "root":
            f = qs.get("f", [None])[0]
            if not f:
                return self._send(b"not found\n", "text/plain; charset=utf-8", 404)
            r = page_plan_file(plan_dir, f, ctx, raw=raw)
            if isinstance(r, tuple) and len(r) == 3:
                return self._send(r[1], r[0], r[2])
            return self._send(r[1], r[0])

        if len(rest) < 2:
            return self._send(b"not found\n", "text/plain; charset=utf-8", 404)
        try:
            part, vol = int(rest[0]), int(rest[1])
        except ValueError:
            return self._send(b"not found\n", "text/plain; charset=utf-8", 404)
        v = next((pv for pv in plan_vols if pv.key == (part, vol)), None)
        if not v:
            return self._send(b"not found\n", "text/plain; charset=utf-8", 404)

        verb = rest[2] if len(rest) >= 3 else None
        if verb is None:
            return self._send(page_plan_vol(v, ctx), "text/html; charset=utf-8")
        if verb == "read":
            f = qs.get("f", [None])[0]
            if not f:
                return self._send(b"not found\n", "text/plain; charset=utf-8", 404)
            r = page_plan_file_in_vol(v, f, ctx, raw=raw)
            if isinstance(r, tuple) and len(r) == 3:
                return self._send(r[1], r[0], r[2])
            return self._send(r[1], r[0])
        self._send(b"not found\n", "text/plain; charset=utf-8", 404)

    def _serve_audio(self, novel: Novel, rest):
        ref = _parse_ref("/".join(rest))
        if not ref:
            return self._send(b"not found\n", "text/plain; charset=utf-8", 404)
        part, vol, ch, scene = ref
        e = find(novel.entries, part, vol, ch)
        path = e.unit_by_scene(scene) if e else None
        if not path or not path.exists():
            return self._send(b"not found\n", "text/plain; charset=utf-8", 404)
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


def make_handler(novels: list[Novel]):
    return type("BoundHandler", (Handler,), {"novels": novels})


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
    ap = argparse.ArgumentParser(
        description="章节浏览 / 审查台本地服务（正文·工作区·规划·指令，支持多本小说）")
    ap.add_argument("novel_dir", nargs="*",
                     help="一本小说目录，或含多本小说的数据根目录（如 01_小说数据/）；"
                          "不给则从当前目录向上找 01_小说数据/。可传多个。")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--title", help="仅解析到一本小说时可覆盖侧边栏书名")
    args = ap.parse_args(argv)

    novels = discover_novels([Path(d) for d in args.novel_dir])
    if len(novels) == 1 and args.title:
        novels[0] = Novel(novels[0].dir, novels[0].slug, args.title)

    httpd = ThreadingHTTPServer((args.host, args.port), make_handler(novels))
    disp = _lan_ip() if args.host in ("0.0.0.0", "::") else args.host
    if len(novels) == 1:
        n = novels[0]
        entries = n.entries
        n_audio = sum(1 for e in entries if e.has_audio)
        print(f"章节服务 · {n.title} · {len(entries)} 章（{n_audio} 有配音）", flush=True)
        print(f"  首页   : http://{disp}:{args.port}/", flush=True)
    else:
        print(f"章节服务 · {len(novels)} 本小说", flush=True)
        for n in novels:
            entries = n.entries
            n_audio = sum(1 for e in entries if e.has_audio)
            print(f"  {n.title} : {len(entries)} 章（{n_audio} 有配音）"
                  f" http://{disp}:{args.port}/n/{n.slug}/", flush=True)
        print(f"  书架   : http://{disp}:{args.port}/", flush=True)
    print("  Ctrl-C 停", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n停。")
        httpd.shutdown()


if __name__ == "__main__":
    main()
