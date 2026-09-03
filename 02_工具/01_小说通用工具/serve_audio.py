#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
章节音频服务 (serve_audio.py)

本地 HTTP 服务，把某本小说 `05_工作区/**/03_音频/*.mp3`（tts_chapter.py 的产出）
挑出来，提供：

  GET /            浏览器播放页（按卷/章列表 + 内嵌 <audio> 播放器 + 看正文链接）
  GET /feed.xml    标准播客 RSS——手机里用任意播客 App（Pocket Casts / Apple Podcasts…）
                   订阅，自动下载、记播放进度
  GET /audio/<部>/<卷>/<章>[/<场>].mp3   音频文件（支持 Range 断点续传）
  GET /text/<部>/<卷>/<章>               对应章节正文（text/plain，看稿对照用）
  GET /health      ok

**纯 Python 标准库，无第三方依赖。** 按需手动跑，Ctrl-C 停。

用法
----
    python3 02_工具/01_小说通用工具/serve_audio.py <小说目录> \
        [--host 0.0.0.0] [--port 8765] [--base-url URL] [--title 标题]

      <小说目录>     含 10_正文/ 与 05_工作区/ 的那一层（如 01_小说数据/00_苍玄）
      --host         默认 0.0.0.0（同一局域网可访问；注意无鉴权）
      --port         默认 8765
      --base-url     RSS enclosure / 链接用的对外地址；缺省按请求 Host 头推断
      --title        播客/页面标题；缺省取小说目录名

安全：默认监听 0.0.0.0、无鉴权，仅适合可信局域网。文件路径只由解析出的
部/卷/章/场号（整数）拼装，不吃请求里的原始路径，无目录穿越。
"""
import argparse
import email.utils
import html
import io
import json
import os
import re
import socket
import sys
import urllib.parse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

_CH_RE = re.compile(r"^章(\d+)(?:_场(\d+))?\.mp3$")
# 合成 pubDate 的锚点：让 feed 里的条目严格按阅读顺序排（重生成不打乱顺序）
_PUBDATE_ANCHOR = 1577836800  # 2020-01-01T00:00:00Z


def _die(msg, code=1):
    print(msg, file=sys.stderr)
    sys.exit(code)


# ---------------------------------------------------------------- 扫描

class Chapter:
    __slots__ = ("part", "vol", "ch", "scene", "mp3", "manifest", "novel_dir")

    def __init__(self, part, vol, ch, scene, mp3: Path, novel_dir: Path):
        self.part, self.vol, self.ch, self.scene = part, vol, ch, scene
        self.mp3 = mp3
        self.novel_dir = novel_dir
        self.manifest = self._load_manifest()

    def _load_manifest(self) -> dict:
        j = self.mp3.with_name(f"章{self.ch:04d}.json")
        try:
            return json.loads(j.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    @property
    def key(self):
        return (self.part, self.vol, self.ch, self.scene or 0)

    @property
    def slug(self):
        s = f"/{self.part}/{self.vol}/{self.ch}"
        return f"{s}/{self.scene}" if self.scene else s

    @property
    def label(self):
        base = f"第 {self.ch} 章"
        return f"{base}·场 {self.scene}" if self.scene else base

    @property
    def title(self):
        return f"卷{self.vol:02d} {self.label}"

    @property
    def size(self):
        try:
            return self.mp3.stat().st_size
        except OSError:
            return 0

    @property
    def mtime(self):
        try:
            return self.mp3.stat().st_mtime
        except OSError:
            return 0

    @property
    def duration_s(self) -> int:
        d = self.manifest.get("duration_seconds")
        if isinstance(d, (int, float)) and d > 0:
            return int(d)
        return _mp3_duration_seconds(self.mp3, self.size)

    @property
    def pubdate_ts(self) -> int:
        return _PUBDATE_ANCHOR + (self.part * 1_000_000 + self.vol * 10_000
                                  + self.ch * 10 + (self.scene or 0)) * 3600

    def manuscript_path(self) -> Path | None:
        hits = sorted(self.novel_dir.glob(
            f"10_正文/*第{self.part:02d}部*/*卷{self.vol:02d}*/章{self.ch:04d}.md"))
        return hits[0] if hits else None


def scan(novel_dir: Path) -> list[Chapter]:
    out: list[Chapter] = []
    for mp3 in novel_dir.glob("05_工作区/**/03_音频/*.mp3"):
        m = _CH_RE.match(mp3.name)
        if not m:
            continue
        parts = str(mp3)
        mp = re.search(r"第0*(\d+)部", parts)
        mv = re.search(r"卷0*(\d+)", parts)
        if not (mp and mv):
            continue
        ch = int(m.group(1))
        scene = int(m.group(2)) if m.group(2) else None
        out.append(Chapter(int(mp.group(1)), int(mv.group(1)), ch, scene, mp3, novel_dir))
    out.sort(key=lambda c: c.key)
    return out


# ---------------------------------------------------------------- MP3 时长（CBR 估算）

def _mp3_duration_seconds(path: Path, size: int) -> int:
    """从首帧读比特率按 CBR 估算。失败退回 48kbps 假设（edge-tts 默认）。"""
    try:
        with open(path, "rb") as f:
            head = f.read(4096)
        i = head.find(b"\xff")
        while 0 <= i < len(head) - 4:
            b1, b2 = head[i + 1], head[i + 2]
            if head[i] == 0xFF and (b1 & 0xE0) == 0xE0:
                ver = (b1 >> 3) & 0x03      # 3=MPEG1, 2=MPEG2, 0=MPEG2.5
                layer = (b1 >> 1) & 0x03    # 1=Layer3
                bri = (b2 >> 4) & 0x0F      # 比特率索引在第 3 个字节高 4 位
                if layer == 1 and bri not in (0, 15) and ver != 1:
                    v1 = [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320]
                    v2 = [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160]
                    kbps = (v1 if ver == 3 else v2)[bri]
                    if kbps:
                        return max(1, round(size * 8 / (kbps * 1000)))
            i = head.find(b"\xff", i + 1)
    except OSError:
        pass
    return max(1, round(size * 8 / 48000))


def _parse_ref(rest: str):
    """'/1/1/1.mp3' 或 '1/1/2/3' → (part, vol, ch, scene|None)。扩展名先去掉，
    免得 '.mp3' 里的 '3' 混进数字序列。"""
    rest = re.sub(r"\.[A-Za-z0-9]+$", "", rest)
    nums = re.findall(r"\d+", rest)
    if len(nums) < 3:
        return None
    part, vol, ch = int(nums[0]), int(nums[1]), int(nums[2])
    scene = int(nums[3]) if len(nums) >= 4 else None
    return (part, vol, ch, scene)


def _fmt_hms(sec: int) -> str:
    h, rem = divmod(int(sec), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


# ---------------------------------------------------------------- 页面 / feed

def _base_url(handler, override: str | None) -> str:
    if override:
        return override.rstrip("/")
    host = handler.headers.get("Host") or f"{handler.server.server_address[0]}:{handler.server.server_address[1]}"
    return f"http://{host}"


PAGE_CSS = """
:root{color-scheme:light dark;--fg:#1a1a1a;--bg:#fafafa;--card:#fff;--mut:#666;--line:#e3e3e3;--accent:#3355cc}
@media(prefers-color-scheme:dark){:root{--fg:#e8e8e8;--bg:#161616;--card:#1f1f1f;--mut:#9a9a9a;--line:#333;--accent:#8aa0ee}}
*{box-sizing:border-box}body{margin:0;font:15px/1.6 system-ui,-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;color:var(--fg);background:var(--bg)}
.wrap{max-width:820px;margin:0 auto;padding:24px 16px 64px}
h1{font-size:22px;margin:0 0 4px}.sub{color:var(--mut);font-size:13px;margin-bottom:20px}
.feedbox{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin-bottom:24px;font-size:13px}
.feedbox code{background:rgba(128,128,128,.15);padding:2px 6px;border-radius:5px;word-break:break-all}
button{font:inherit;padding:4px 10px;border:1px solid var(--line);border-radius:6px;background:var(--card);color:var(--fg);cursor:pointer}
h2{font-size:15px;color:var(--mut);border-bottom:1px solid var(--line);padding-bottom:6px;margin:28px 0 12px}
.item{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin-bottom:12px}
.item .top{display:flex;justify-content:space-between;align-items:baseline;gap:12px;flex-wrap:wrap}
.item .name{font-weight:600}.item .meta{color:var(--mut);font-size:12px}
.item audio{width:100%;margin-top:10px}
.item a{color:var(--accent);text-decoration:none;font-size:12px;margin-right:12px}
.empty{color:var(--mut);padding:40px 0;text-align:center}
"""

PAGE_JS = """
function copyFeed(u){navigator.clipboard.writeText(u).then(function(){var b=document.getElementById('cp');b.textContent='已复制';setTimeout(function(){b.textContent='复制';},1500);});}
"""


def render_index(chapters: list[Chapter], title: str, base: str) -> bytes:
    feed = f"{base}/feed.xml"
    buf = io.StringIO()
    buf.write(f"<!doctype html><html lang=zh-CN><head><meta charset=utf-8>"
              f"<meta name=viewport content='width=device-width,initial-scale=1'>"
              f"<title>{html.escape(title)} · 章节音频</title><style>{PAGE_CSS}</style></head><body><div class=wrap>")
    buf.write(f"<h1>{html.escape(title)} · 章节音频</h1>")
    buf.write(f"<div class=sub>{len(chapters)} 段音频 · tts_chapter.py 产出</div>")
    buf.write("<div class=feedbox>📻 播客订阅（手机播客 App 里粘这个地址）："
              f"<code>{html.escape(feed)}</code> "
              f"<button id=cp onclick=\"copyFeed('{html.escape(feed)}')\">复制</button></div>")
    if not chapters:
        buf.write("<div class=empty>还没有音频。先跑 <code>tts_chapter.py --chapter-dir …</code></div>")
    cur_vol = None
    for c in chapters:
        if (c.part, c.vol) != cur_vol:
            cur_vol = (c.part, c.vol)
            buf.write(f"<h2>第 {c.part} 部 · 卷 {c.vol:02d}</h2>")
        voice = html.escape(str(c.manifest.get("voice", "")))
        gen = html.escape(str(c.manifest.get("generated_at", "")))
        meta = " · ".join(x for x in [_fmt_hms(c.duration_s), voice, gen] if x)
        buf.write("<div class=item><div class=top>"
                  f"<span class=name>{html.escape(c.label)}</span>"
                  f"<span class=meta>{meta}</span></div>")
        buf.write(f"<audio controls preload=none src='/audio{c.slug}.mp3'></audio><div>")
        buf.write(f"<a href='/audio{c.slug}.mp3' download>下载 mp3</a>")
        if c.scene is None:
            buf.write(f"<a href='/text/{c.part}/{c.vol}/{c.ch}' target=_blank>看正文</a>")
        buf.write("</div></div>")
    buf.write(f"<script>{PAGE_JS}</script></div></body></html>")
    return buf.getvalue().encode("utf-8")


def render_feed(chapters: list[Chapter], title: str, base: str) -> bytes:
    now = email.utils.formatdate(usegmt=True)
    items = []
    for c in chapters:
        url = f"{base}/audio{c.slug}.mp3"
        guid = f"{base}{c.slug}"
        items.append(
            "<item>"
            f"<title>{xml_escape(c.title)}</title>"
            f"<guid isPermaLink=\"false\">{xml_escape(guid)}</guid>"
            f"<pubDate>{email.utils.formatdate(c.pubdate_ts, usegmt=True)}</pubDate>"
            f"<enclosure url=\"{xml_escape(url)}\" length=\"{c.size}\" type=\"audio/mpeg\"/>"
            f"<itunes:duration>{_fmt_hms(c.duration_s)}</itunes:duration>"
            f"<itunes:explicit>false</itunes:explicit>"
            "</item>"
        )
    xml = (
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
        "<rss version=\"2.0\" xmlns:itunes=\"http://www.itunes.com/dtds/podcast-1.0.dtd\">"
        "<channel>"
        f"<title>{xml_escape(title)}</title>"
        f"<link>{xml_escape(base)}/</link>"
        f"<language>zh-cn</language>"
        f"<description>{xml_escape(title)} 章节 TTS 配音（听稿校对用）</description>"
        f"<itunes:author>{xml_escape(title)}</itunes:author>"
        f"<lastBuildDate>{now}</lastBuildDate>"
        f"{''.join(items)}"
        "</channel></rss>"
    )
    return xml.encode("utf-8")


# ---------------------------------------------------------------- HTTP handler

class Handler(BaseHTTPRequestHandler):
    server_version = "serve_audio/1.0"
    protocol_version = "HTTP/1.1"

    # 注入项（由 make_handler 设置）
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
        path = urllib.parse.urlparse(self.path).path
        try:
            if path == "/health":
                return self._send(b"ok", "text/plain; charset=utf-8")
            chapters = scan(self.novel_dir)
            base = _base_url(self, self.base_override)
            if path == "/":
                return self._send(render_index(chapters, self.title, base), "text/html; charset=utf-8")
            if path == "/feed.xml":
                return self._send(render_feed(chapters, self.title, base),
                                  "application/rss+xml; charset=utf-8")
            if path.startswith("/audio/"):
                return self._serve_audio(path, chapters)
            if path.startswith("/text/"):
                return self._serve_text(path, chapters)
            self._send(b"not found\n", "text/plain; charset=utf-8", 404)
        except BrokenPipeError:
            pass
        except Exception as e:  # noqa: BLE001
            self._send(f"error: {e}\n".encode(), "text/plain; charset=utf-8", 500)

    def _match(self, path, chapters, prefix):
        ref = _parse_ref(path[len(prefix):])
        if ref is None:
            return None
        for c in chapters:
            if (c.part, c.vol, c.ch, c.scene) == ref:
                return c
        return None

    def _serve_audio(self, path, chapters):
        c = self._match(path, chapters, "/audio/")
        if not c or not c.mp3.exists():
            return self._send(b"not found\n", "text/plain; charset=utf-8", 404)
        size = c.size
        rng = self.headers.get("Range")
        start, end = 0, size - 1
        partial = False
        if rng:
            m = re.match(r"bytes=(\d*)-(\d*)", rng.strip())
            if m:
                if m.group(1):
                    start = int(m.group(1))
                    end = int(m.group(2)) if m.group(2) else size - 1
                elif m.group(2):  # suffix range
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
        with open(c.mp3, "rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(65536, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def _serve_text(self, path, chapters):
        c = self._match(path, chapters, "/text/")
        mp = c.manuscript_path() if c else None
        if not mp or not mp.exists():
            return self._send(b"not found\n", "text/plain; charset=utf-8", 404)
        self._send(mp.read_bytes(), "text/plain; charset=utf-8")


def make_handler(novel_dir: Path, title: str, base_override: str | None):
    return type("BoundHandler", (Handler,), {
        "novel_dir": novel_dir, "title": title, "base_override": base_override,
    })


# ---------------------------------------------------------------- main

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
    ap = argparse.ArgumentParser(description="章节音频本地 HTTP 服务（播放页 + 播客 RSS）")
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

    chapters = scan(novel_dir)
    httpd = ThreadingHTTPServer((args.host, args.port), make_handler(novel_dir, title, args.base_url))
    disp = _lan_ip() if args.host in ("0.0.0.0", "::") else args.host
    feed = args.base_url.rstrip("/") + "/feed.xml" if args.base_url else f"http://{disp}:{args.port}/feed.xml"
    print(f"章节音频服务 · {title} · {len(chapters)} 段", flush=True)
    print(f"  播放页 : http://{disp}:{args.port}/", flush=True)
    print(f"  播客RSS: {feed}", flush=True)
    print("  Ctrl-C 停", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n停。")
        httpd.shutdown()


if __name__ == "__main__":
    main()
