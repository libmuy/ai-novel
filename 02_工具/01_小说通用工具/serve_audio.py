#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
章节浏览 / 音频服务 (serve_audio.py)

某本小说的本地 HTTP 索引，层次：

    /                首页——选「正文」或「工作区」
    /text            正文：章节列表
    /text/<部>/<卷>/<章>          该章——选「读」或「听」
    /text/<部>/<卷>/<章>/read     读：渲染 10_正文/…/章{C}.md（?raw=1 出纯文本）
    /text/<部>/<卷>/<章>/listen   听：内嵌播放器
    /work            工作区：章节列表
    /work/<部>/<卷>/<章>          该章——选「读」或「听」
    /work/<部>/<卷>/<章>/read     读：该章 05_工作区/…/章XXXX/ 文件浏览器
                                  （?f=<相对路径> 看单个文件）
    /work/<部>/<卷>/<章>/listen   听：同一份音频

    /feed.xml        标准播客 RSS——手机播客 App（Pocket Casts / Apple Podcasts…）订阅
    /audio/<部>/<卷>/<章>[/<场>].mp3   音频（支持 Range 断点续传）
    /health          ok

音频来自 tts_chapter.py 的产出 `05_工作区/**/03_音频/*.mp3`。
**纯 Python 标准库，无第三方依赖。** 按需手动跑，Ctrl-C 停。

用法
----
    python3 02_工具/01_小说通用工具/serve_audio.py <小说目录> \
        [--host 0.0.0.0] [--port 8765] [--base-url URL] [--title 标题]

安全：默认监听 0.0.0.0、无鉴权，仅适合可信局域网。章节定位只由解析出的整数
部/卷/章/场号拼装；工作区文件浏览器对 ?f= 做 realpath 越界校验，均无目录穿越。
"""
import argparse
import email.utils
import html
import io
import json
import re
import socket
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

_MP3_RE = re.compile(r"^章(\d+)(?:_场(\d+))?\.mp3$")
_WS_CH_RE = re.compile(r"章(\d+)")
_PUBDATE_ANCHOR = 1577836800  # 2020-01-01Z；feed 条目 pubDate 按章号合成，保证阅读顺序
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

    def pubdate_ts(self, scene: int | None = 0) -> int:
        return _PUBDATE_ANCHOR + (self.part * 1_000_000 + self.vol * 10_000
                                  + self.ch * 10 + (scene or 0)) * 3600


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
        m = re.match(r"^章(\d+)\.md$", md.name)
        mp = re.search(r"第0*(\d+)部", str(md))
        mv = re.search(r"卷0*(\d+)", str(md))
        if m and mp and mv:
            get(int(mp.group(1)), int(mv.group(1)), int(m.group(1))).manuscript = md

    # 工作区章目录
    for d in novel_dir.glob("05_工作区/*/*/*"):
        if not d.is_dir():
            continue
        m = _WS_CH_RE.search(d.name)
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


# ================================================================ HTML

CSS = """
:root{color-scheme:light dark;--fg:#1a1a1a;--bg:#fafafa;--card:#fff;--mut:#666;--line:#e3e3e3;--accent:#3355cc}
@media(prefers-color-scheme:dark){:root{--fg:#e8e8e8;--bg:#161616;--card:#1f1f1f;--mut:#9a9a9a;--line:#333;--accent:#8aa0ee}}
*{box-sizing:border-box}body{margin:0;font:15px/1.65 system-ui,-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;color:var(--fg);background:var(--bg)}
.wrap{max-width:820px;margin:0 auto;padding:20px 16px 64px}
a{color:var(--accent)}
.crumb{font-size:13px;color:var(--mut);margin-bottom:16px}.crumb a{text-decoration:none}
h1{font-size:21px;margin:0 0 16px}
h2{font-size:14px;color:var(--mut);border-bottom:1px solid var(--line);padding-bottom:6px;margin:26px 0 10px}
.feedbox{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin:18px 0;font-size:13px}
.feedbox code{background:rgba(128,128,128,.15);padding:2px 6px;border-radius:5px;word-break:break-all}
button{font:inherit;padding:4px 10px;border:1px solid var(--line);border-radius:6px;background:var(--card);color:var(--fg);cursor:pointer}
.choices{display:flex;gap:14px;flex-wrap:wrap;margin:8px 0 4px}
.choice{flex:1 1 200px;display:block;text-decoration:none;background:var(--card);border:1px solid var(--line);border-radius:12px;padding:22px 18px;color:var(--fg)}
.choice b{font-size:17px}.choice span{display:block;color:var(--mut);font-size:13px;margin-top:4px}
.choice.disabled{opacity:.45;pointer-events:none}
.row{display:flex;justify-content:space-between;align-items:center;gap:12px;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 14px;margin-bottom:8px}
.row .nm{font-weight:600}.row .meta{color:var(--mut);font-size:12px}
.row .acts a{margin-left:14px;text-decoration:none;font-size:13px}
.row .acts .off{color:var(--mut);pointer-events:none}
audio{width:100%;margin:12px 0}
.prose{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:20px 22px;font-size:16px;line-height:1.9}
.prose p{margin:0 0 1em;text-indent:2em}
.prose hr{border:0;text-align:center;margin:1.4em 0}.prose hr::before{content:"※";color:var(--mut)}
pre.file{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;overflow:auto;font:13px/1.6 ui-monospace,SFMono-Regular,Menlo,monospace;white-space:pre-wrap;word-break:break-word}
ul.tree{list-style:none;padding:0;margin:0}
ul.tree li{border:1px solid var(--line);border-radius:8px;padding:8px 12px;margin-bottom:6px;background:var(--card)}
ul.tree a{text-decoration:none}ul.tree .sz{color:var(--mut);font-size:12px;float:right}
.empty{color:var(--mut);padding:36px 0;text-align:center}
"""

JS = "function cp(u){navigator.clipboard.writeText(u).then(function(){var b=document.getElementById('cp');b.textContent='已复制';setTimeout(function(){b.textContent='复制';},1500);});}"


def _doc(title: str, body: str) -> bytes:
    return (f"<!doctype html><html lang=zh-CN><head><meta charset=utf-8>"
            f"<meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>{html.escape(title)}</title><style>{CSS}</style></head>"
            f"<body><div class=wrap>{body}<script>{JS}</script></div></body></html>").encode("utf-8")


def _crumb(*parts) -> str:
    bits = []
    for p in parts[:-1]:
        href, text = p
        bits.append(f"<a href='{html.escape(href)}'>{html.escape(text)}</a>")
    bits.append(html.escape(parts[-1][1]) if isinstance(parts[-1], tuple) else html.escape(parts[-1]))
    return f"<div class=crumb>{' / '.join(bits)}</div>"


def page_home(entries, title, base) -> bytes:
    feed = f"{base}/feed.xml"
    n_text = sum(1 for e in entries if e.manuscript)
    n_work = sum(1 for e in entries if e.ws_dir)
    n_audio = sum(1 for e in entries if e.has_audio)
    body = (
        f"<h1>{html.escape(title)}</h1>"
        "<div class=choices>"
        f"<a class=choice href='/text'><b>正文</b><span>{n_text} 章 · 读定稿正文 / 听配音</span></a>"
        f"<a class=choice href='/work'><b>工作区</b><span>{n_work} 章 · 提示词 / 模型输出 / 状态 / 校验记录</span></a>"
        "</div>"
        f"<div class=feedbox>📻 播客订阅（{n_audio} 章有配音）——手机播客 App 里粘："
        f"<code>{html.escape(feed)}</code> <button id=cp onclick=\"cp('{html.escape(feed)}')\">复制</button></div>"
    )
    return _doc(f"{title} · 章节索引", body)


def _chapter_rows(entries, branch: str) -> str:
    keep = [e for e in entries if (e.manuscript if branch == "text" else e.ws_dir)]
    if not keep:
        return "<div class=empty>（没有内容）</div>"
    out, cur = [], None
    for e in keep:
        if (e.part, e.vol) != cur:
            cur = (e.part, e.vol)
            out.append(f"<h2>第 {e.part} 部 · 卷 {e.vol:02d}</h2>")
        base3 = f"/{branch}/{e.path3()}"
        meta = []
        if branch == "text":
            meta.append(f"{_wordcount(e.manuscript)} 字" if e.manuscript else "")
        if e.has_audio:
            meta.append(_fmt_hms(e.duration_s()))
        meta_s = " · ".join(x for x in meta if x)
        listen = (f"<a href='{base3}/listen'>听</a>" if e.has_audio
                  else "<a class=off>听</a>")
        out.append(
            f"<div class=row><div><span class=nm>{html.escape(e.label)}</span>"
            f"{f' <span class=meta>{meta_s}</span>' if meta_s else ''}</div>"
            f"<div class=acts><a href='{base3}/read'>读</a>{listen}</div></div>"
        )
    return "".join(out)


def _wordcount(md: Path) -> int:
    try:
        return len(re.sub(r"\s", "", md.read_text(encoding="utf-8")))
    except OSError:
        return 0


def page_list(entries, title, branch) -> bytes:
    zh = "正文" if branch == "text" else "工作区"
    body = _crumb(("/", "首页"), zh) + f"<h1>{zh}</h1>" + _chapter_rows(entries, branch)
    return _doc(f"{title} · {zh}", body)


def page_chapter(e: Entry, title, branch) -> bytes:
    zh = "正文" if branch == "text" else "工作区"
    base3 = f"/{branch}/{e.path3()}"
    read_hint = ("渲染定稿正文" if branch == "text"
                 else "该章工作区文件（提示词/模型输出/状态/校验记录）")
    listen_cls = "" if e.has_audio else " disabled"
    listen_hint = _fmt_hms(e.duration_s()) if e.has_audio else "未配音"
    body = (
        _crumb(("/", "首页"), (f"/{branch}", zh), e.label)
        + f"<h1>{html.escape(e.title)}</h1><div class=choices>"
        f"<a class=choice href='{base3}/read'><b>读</b><span>{read_hint}</span></a>"
        f"<a class='choice{listen_cls}' href='{base3}/listen'><b>听</b><span>{listen_hint}</span></a>"
        "</div>"
    )
    return _doc(f"{title} · {e.title}", body)


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


def page_manuscript(e: Entry, title, raw: bool):
    if not e.manuscript or not e.manuscript.exists():
        return None
    data = e.manuscript.read_text(encoding="utf-8")
    if raw:
        return ("text/plain; charset=utf-8", data.encode("utf-8"))
    body = (_crumb(("/", "首页"), ("/text", "正文"),
                   (f"/text/{e.path3()}", e.label), "读")
            + f"<h1>{html.escape(e.title)}</h1>"
            f"<div class=crumb><a href='/text/{e.path3()}/read?raw=1'>纯文本</a>"
            f" · <a href='/text/{e.path3()}/listen'>听</a></div>"
            f"<div class=prose>{render_prose(data)}</div>")
    return ("text/html; charset=utf-8", _doc(f"{title} · {e.title}", body))


def page_listen(e: Entry, title, branch) -> bytes:
    zh = "正文" if branch == "text" else "工作区"
    units = e.audio_units()
    base3 = f"/{branch}/{e.path3()}"
    if not units:
        inner = "<div class=empty>这章还没配音。<br><code>tts_chapter.py --chapter-dir …</code></div>"
    else:
        m = e.manifest
        info = " · ".join(x for x in [m.get("voice", ""), m.get("generated_at", ""),
                                      _fmt_size(e.total_audio_bytes())] if x)
        blocks = []
        for scene, path in units:
            aurl = f"/audio/{e.path3()}" + (f"/{scene}.mp3" if scene else ".mp3")
            lbl = f"场 {scene}" if scene else "整章"
            blocks.append(f"<div class=meta>{lbl}</div>"
                          f"<audio controls preload=none src='{aurl}'></audio>"
                          f"<div><a href='{aurl}' download>下载</a></div>")
        inner = (f"<div class=crumb>{html.escape(info)}</div>" + "".join(blocks))
    body = (_crumb(("/", "首页"), (f"/{branch}", zh), (base3, e.label), "听")
            + f"<h1>{html.escape(e.title)}</h1>"
            f"<div class=crumb><a href='{base3}/read'>← 读这一章</a></div>" + inner)
    return _doc(f"{title} · {e.title} · 听", body)


def page_work_read(e: Entry, title, rel: str | None):
    if not e.ws_dir or not e.ws_dir.is_dir():
        return None
    root = e.ws_dir.resolve()
    base3 = f"/work/{e.path3()}"
    crumb = _crumb(("/", "首页"), ("/work", "工作区"), (base3, e.label), "读")

    if rel:
        target = (e.ws_dir / rel).resolve()
        if not target.is_file() or root not in target.parents:
            return ("text/plain; charset=utf-8", b"not found\n", 404)
        suffix = target.suffix.lower()
        if suffix == ".mp3":
            aurl = "/audio/" + e.path3() + (".mp3")
            body = crumb + f"<h1>{html.escape(rel)}</h1><audio controls src='{aurl}'></audio>"
            return ("text/html; charset=utf-8", _doc(title, body))
        if suffix not in _TEXT_EXT:
            body = crumb + f"<h1>{html.escape(rel)}</h1><p class=meta>（{_fmt_size(_safe_size(target))}，不支持预览）</p>"
            return ("text/html; charset=utf-8", _doc(title, body))
        raw = target.read_bytes()[:_RENDER_CAP]
        note = "<p class=meta>（文件较大，只显示前 512 KiB）</p>" if _safe_size(target) > _RENDER_CAP else ""
        body = (crumb + f"<h1>{html.escape(rel)}</h1>"
                f"<div class=crumb><a href='{base3}/read'>← 文件列表</a></div>{note}"
                f"<pre class=file>{html.escape(raw.decode('utf-8', 'replace'))}</pre>")
        return ("text/html; charset=utf-8", _doc(f"{title} · {rel}", body))

    files = sorted(p for p in e.ws_dir.rglob("*") if p.is_file())
    if not files:
        lst = "<div class=empty>（空目录）</div>"
    else:
        rows = []
        for p in files:
            r = p.relative_to(e.ws_dir).as_posix()
            q = urllib.parse.quote(r)
            rows.append(f"<li><a href='{base3}/read?f={q}'>{html.escape(r)}</a>"
                        f"<span class=sz>{_fmt_size(_safe_size(p))}</span></li>")
        lst = f"<ul class=tree>{''.join(rows)}</ul>"
    listen = (f"<div class=crumb><a href='{base3}/listen'>听这一章 →</a></div>"
              if e.has_audio else "")
    body = crumb + f"<h1>{html.escape(e.title)} · 工作区文件</h1>{listen}{lst}"
    return ("text/html; charset=utf-8", _doc(f"{title} · {e.title} · 工作区", body))


def render_feed(entries, title, base) -> bytes:
    items = []
    for e in entries:
        for scene, path in e.audio_units():
            slug = f"/{e.path3()}" + (f"/{scene}" if scene else "")
            url = f"{base}/audio{slug}.mp3"
            size = _safe_size(path)
            dur = (e.duration_s() if scene is None
                   else _mp3_duration_seconds(path, size))
            it_title = e.title + (f" · 场 {scene}" if scene else "")
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

class Handler(BaseHTTPRequestHandler):
    server_version = "serve_audio/2.0"
    protocol_version = "HTTP/1.1"
    novel_dir: Path = None
    title: str = ""
    base_override: str | None = None

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

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(u.path)
        qs = urllib.parse.parse_qs(u.query)
        seg = [s for s in path.split("/") if s]
        try:
            if path == "/health":
                return self._send(b"ok", "text/plain; charset=utf-8")
            entries = scan(self.novel_dir)
            base = self._base(u)

            if not seg:
                return self._send(page_home(entries, self.title, base), "text/html; charset=utf-8")
            if seg == ["feed.xml"]:
                return self._send(render_feed(entries, self.title, base),
                                  "application/rss+xml; charset=utf-8")
            if seg[0] == "audio":
                return self._serve_audio(seg[1:], entries)
            if seg[0] in ("text", "work"):
                return self._route_branch(seg, qs, entries)
            self._send(b"not found\n", "text/plain; charset=utf-8", 404)
        except BrokenPipeError:
            pass
        except Exception as ex:  # noqa: BLE001
            self._send(f"error: {ex}\n".encode(), "text/plain; charset=utf-8", 500)

    def _base(self, u) -> str:
        if self.base_override:
            return self.base_override.rstrip("/")
        host = self.headers.get("Host") or f"{self.server.server_address[0]}:{self.server.server_address[1]}"
        return f"http://{host}"

    def _route_branch(self, seg, qs, entries):
        branch = seg[0]
        rest = seg[1:]
        if not rest:
            return self._send(page_list(entries, self.title, branch), "text/html; charset=utf-8")
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
            return self._send(page_chapter(e, self.title, branch), "text/html; charset=utf-8")
        if verb == "listen":
            return self._send(page_listen(e, self.title, branch), "text/html; charset=utf-8")
        if verb == "read" and branch == "text":
            r = page_manuscript(e, self.title, raw=qs.get("raw", ["0"])[0] == "1")
            if r is None:
                return self._send(b"not found\n", "text/plain; charset=utf-8", 404)
            return self._send(r[1], r[0])
        if verb == "read" and branch == "work":
            r = page_work_read(e, self.title, qs.get("f", [None])[0])
            if r is None:
                return self._send(b"not found\n", "text/plain; charset=utf-8", 404)
            return self._send(r[1], r[0], r[2] if len(r) > 2 else 200)
        self._send(b"not found\n", "text/plain; charset=utf-8", 404)

    def _serve_audio(self, rest, entries):
        ref = _parse_ref("/".join(rest))
        if not ref:
            return self._send(b"not found\n", "text/plain; charset=utf-8", 404)
        part, vol, ch, scene = ref
        e = find(entries, part, vol, ch)
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


def make_handler(novel_dir: Path, title: str, base_override: str | None):
    return type("BoundHandler", (Handler,),
                {"novel_dir": novel_dir, "title": title, "base_override": base_override})


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
    ap = argparse.ArgumentParser(description="章节浏览 / 音频本地服务（正文·工作区·读·听 + 播客 RSS）")
    ap.add_argument("novel_dir")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--base-url")
    ap.add_argument("--title")
    args = ap.parse_args(argv)

    novel_dir = Path(args.novel_dir).resolve()
    if not ((novel_dir / "10_正文").is_dir() and (novel_dir / "05_工作区").is_dir()):
        _die(f"{novel_dir} 不像小说目录（需含 10_正文/ 与 05_工作区/）")
    title = args.title or novel_dir.name.split("_", 1)[-1]

    entries = scan(novel_dir)
    httpd = ThreadingHTTPServer((args.host, args.port), make_handler(novel_dir, title, args.base_url))
    disp = _lan_ip() if args.host in ("0.0.0.0", "::") else args.host
    feed = args.base_url.rstrip("/") + "/feed.xml" if args.base_url else f"http://{disp}:{args.port}/feed.xml"
    n_audio = sum(1 for e in entries if e.has_audio)
    print(f"章节服务 · {title} · {len(entries)} 章（{n_audio} 有配音）", flush=True)
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
