#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
本地审查台 (serve_audio.py)

某本小说的本地 HTTP 服务：单页应用（`serve_ui/`）+ JSON API，取代旧版多页只读界面
（设计稿见设计项目「审查界面 v3」）。实现细节在 `serve_api/`（拆分先例同
`prompt_build/`/`audit/`：本文件只留 CLI 入口，逻辑见该包 `__init__.py` 的模块地图）。

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
import socket
import sys
from http.server import ThreadingHTTPServer
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SYS_DIR = _HERE.parent / "00_系统级"
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_SYS_DIR))

from serve_api import feed, http, jobs, model  # noqa: E402

# ---- 兼容旧调用面：test_serve_audio.py 等按 `serve_audio.X` 引用这几个名字 ----
scan = model.scan
_scan_tree = model._scan_tree
_parse_ref = model._parse_ref
_mp3_duration_seconds = model._mp3_duration_seconds
render_feed = feed.render_feed
make_handler = http.make_handler
_JOB_BUILDERS = jobs._JOB_BUILDERS


def _die(msg, code=1):
    print(msg, file=sys.stderr)
    sys.exit(code)


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

    entries = model.scan(novel_dir)
    httpd = ThreadingHTTPServer((args.host, args.port),
                                http.make_handler(novel_dir, title, args.base_url, args.read_only))
    disp = _lan_ip() if args.host in ("0.0.0.0", "::") else args.host
    feed_url = args.base_url.rstrip("/") + "/feed.xml" if args.base_url else f"http://{disp}:{args.port}/feed.xml"
    n_audio = sum(1 for e in entries if e.has_audio)
    mode = "只读" if args.read_only else "可写"
    print(f"审查台 · {title} · {len(entries)} 章（{n_audio} 有配音）· {mode}", flush=True)
    print(f"  首页   : http://{disp}:{args.port}/", flush=True)
    print(f"  播客RSS: {feed_url}", flush=True)
    print("  Ctrl-C 停", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n停。")
        httpd.shutdown()


if __name__ == "__main__":
    main()
