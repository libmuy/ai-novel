#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""serve_audio.py 的离线单元测试（含本地回环 HTTP 请求）。"""
import os
import sys
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path
from xml.etree import ElementTree as ET

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "01_小说通用工具"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import serve_audio as S  # noqa: E402

# 假 CBR mp3：MPEG2 Layer3 48kbps（b2 高 4 位=6）+ 填充 ≈ 13s
_MP3 = b"\xff\xf3\x60\xc4" + b"\x00" * 80000
_MANUSCRIPT = "第一段。\n\n第二段。\n\n※\n\n第二场。\n"


def _make_tree(tmp: Path, with_audio=True, with_ch2=True):
    novel = tmp / "00_苍玄"
    (novel / "10_正文" / "01_第01部" / "01_卷01").mkdir(parents=True)
    (novel / "10_正文" / "01_第01部" / "01_卷01" / "章0001.md").write_text(_MANUSCRIPT, encoding="utf-8")
    ws1 = novel / "05_工作区" / "03_第01部" / "03_卷01" / "03_章0001"
    (ws1 / "00_提示词").mkdir(parents=True)
    (ws1 / "00_提示词" / "01_正文生成.md").write_text("# 提示词\n内容", encoding="utf-8")
    (ws1 / "02_状态").mkdir()
    (ws1 / "02_状态" / "01_状态履历.md").write_text("| a | b |", encoding="utf-8")
    if with_audio:
        aud = ws1 / "03_音频"
        aud.mkdir()
        (aud / "章0001.mp3").write_bytes(_MP3)
        (aud / "章0001.json").write_text(
            '{"voice":"zh-CN-YunxiNeural","generated_at":"2026-09-03T20:00:00","duration_seconds":794}',
            encoding="utf-8")
    if with_ch2:
        ws2 = novel / "05_工作区" / "03_第01部" / "03_卷01" / "04_章0002"
        (ws2 / "00_提示词").mkdir(parents=True)
    # 规划目录
    plan = novel / "03_规划"
    plan.mkdir(parents=True)
    (plan / "00_伏笔总纲.md").write_text("# 伏笔总纲\n伏笔内容", encoding="utf-8")
    (plan / "规划.md").write_text("# 规划\n总体规划", encoding="utf-8")
    v1 = plan / "01_第01部" / "01_卷01"
    v1.mkdir(parents=True)
    (v1 / "规划_卷01.md").write_text("# 卷一规划\n卷规划内容", encoding="utf-8")
    (v1 / "规划_卷01_章0001.md").write_text("## 第一章细纲\n细纲内容", encoding="utf-8")
    (v1 / "01_事件").mkdir()
    (v1 / "01_事件" / "BT-V1-001_战斗结算.md").write_text("战斗结算", encoding="utf-8")
    return novel


class TestParseRef(unittest.TestCase):
    def test_strips_extension(self):
        self.assertEqual(S._parse_ref("1/1/1.mp3"), (1, 1, 1, None))

    def test_scene(self):
        self.assertEqual(S._parse_ref("2/3/4/5.mp3"), (2, 3, 4, 5))

    def test_too_few(self):
        self.assertIsNone(S._parse_ref("1/1"))


class TestScan(unittest.TestCase):
    def test_merges_manuscript_ws_audio(self):
        with tempfile.TemporaryDirectory() as td:
            entries = S.scan(_make_tree(Path(td)))
            self.assertEqual([e.key for e in entries], [(1, 1, 1), (1, 1, 2)])
            e1 = entries[0]
            self.assertTrue(e1.manuscript.name == "章0001.md")
            self.assertTrue(e1.ws_dir.name == "03_章0001")
            self.assertTrue(e1.has_audio)
            self.assertEqual(e1.duration_s(), 794)
            self.assertEqual(e1.audio_units(), [(None, e1.audio_dir / "章0001.mp3")])
            self.assertEqual(entries[1].manuscript, None)   # ch2 只有工作区
            self.assertFalse(entries[1].has_audio)


class TestScanPlanning(unittest.TestCase):
    def test_finds_root_and_vols(self):
        with tempfile.TemporaryDirectory() as td:
            root, vols = S.scan_planning(_make_tree(Path(td)))
            self.assertEqual(len(root.files), 2)
            names = [f.name for f in root.files]
            self.assertIn("00_伏笔总纲.md", names)
            self.assertIn("规划.md", names)
            self.assertEqual(len(vols), 1)
            self.assertEqual(vols[0].key, (1, 1))
            self.assertEqual(len(vols[0].files), 3)  # 卷规划 + 章细纲 + 事件文件

    def test_empty_when_no_plan_dir(self):
        with tempfile.TemporaryDirectory() as td:
            novel = Path(td) / "novel"
            (novel / "10_正文").mkdir(parents=True)
            (novel / "05_工作区").mkdir(parents=True)
            root, vols = S.scan_planning(novel)
            self.assertEqual(len(root.files), 0)
            self.assertEqual(len(vols), 0)


class TestRenderProse(unittest.TestCase):
    def test_scene_break_and_paragraphs(self):
        h = S.render_prose(_MANUSCRIPT)
        self.assertEqual(h.count("<p>"), 3)
        self.assertIn("<hr>", h)


class TestRenderMarkdown(unittest.TestCase):
    def test_headings_para_inline(self):
        h = S.render_markdown("# 标题\n\n一段 **粗** 和 `code` 文字。")
        self.assertIn("<h1>标题</h1>", h)
        self.assertIn("<strong>粗</strong>", h)
        self.assertIn("<code>code</code>", h)

    def test_gfm_table(self):
        h = S.render_markdown("| 字段 | 值 |\n|---|---|\n| 境界 | 凡人 |\n| 内力 | 0 |")
        self.assertIn("<table>", h)
        self.assertIn("<th>字段</th>", h)
        self.assertEqual(h.count("<tr>"), 3)   # 1 表头 + 2 行
        self.assertIn("<td>凡人</td>", h)

    def test_lists_and_checkboxes(self):
        h = S.render_markdown("- a\n- b\n\n1. 一\n2. 二\n\n- [ ] 待办\n- [x] 完成")
        self.assertIn("<ul><li>a</li><li>b</li></ul>", h)
        self.assertIn("<ol><li>一</li><li>二</li></ol>", h)
        self.assertIn("☐ 待办", h)
        self.assertIn("☑ 完成", h)

    def test_fence_and_quote_and_hr(self):
        h = S.render_markdown("> 引用\n\n```\nx | y\n```\n\n---\n\n末尾")
        self.assertIn("<blockquote>引用</blockquote>", h)
        self.assertIn("<pre class=code><code>x | y</code></pre>", h)
        self.assertIn("<hr>", h)

    def test_escapes_html(self):
        h = S.render_markdown("<script>alert(1)</script> 与 a<b")
        self.assertNotIn("<script>", h)
        self.assertIn("&lt;script&gt;", h)

    def test_underscores_in_filenames_not_italic(self):
        h = S.render_markdown("见 `00_提示词/01_正文生成.md` 与 05_工作区 目录")
        self.assertNotIn("<em>", h)


class TestPages(unittest.TestCase):
    def test_home_and_lists(self):
        with tempfile.TemporaryDirectory() as td:
            entries = S.scan(_make_tree(Path(td)))
            plan_root, plan_vols = S.scan_planning(Path(td) / "00_苍玄")
            home = S.page_home(entries, "苍玄", "http://p:8765", plan_root, plan_vols).decode()
            self.assertIn("href='/text'", home)
            self.assertIn("href='/work'", home)
            self.assertIn("href='/plan'", home)
            self.assertIn("http://p:8765/feed.xml", home)

            tl = S.page_list(entries, "苍玄", "text").decode()
            self.assertIn("/text/1/1/1/read", tl)
            self.assertIn("/text/1/1/1/listen", tl)
            self.assertNotIn("/text/1/1/2/", tl)  # ch2 无正文 → 不在正文列表

            wl = S.page_list(entries, "苍玄", "work").decode()
            self.assertIn("/work/1/1/1/read", wl)
            self.assertIn("/work/1/1/2/read", wl)   # ch2 有工作区
            self.assertIn("class=off>听", wl)       # ch2 无音频 → 听禁用

    def test_feed_valid(self):
        with tempfile.TemporaryDirectory() as td:
            entries = S.scan(_make_tree(Path(td)))
            root = ET.fromstring(S.render_feed(entries, "苍玄", "http://p:8765"))
            encs = root.findall(".//item/enclosure")
            self.assertEqual(len(encs), 1)
            self.assertEqual(encs[0].get("url"), "http://p:8765/audio/1/1/1.mp3")
            self.assertEqual(int(encs[0].get("length")), len(_MP3))


class TestMp3Duration(unittest.TestCase):
    def test_cbr_estimate(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "a.mp3"
            p.write_bytes(_MP3)
            self.assertEqual(S._mp3_duration_seconds(p, p.stat().st_size), 13)


class TestHttp(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        novel = _make_tree(Path(self.td.name))
        plan_root, plan_vols = S.scan_planning(novel)
        self.httpd = S.ThreadingHTTPServer(
            ("127.0.0.1", 0),
            S.make_handler(novel, "苍玄", None, plan_root, plan_vols))
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.td.cleanup()

    def _get(self, path, headers=None):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", headers=headers or {})
        return urllib.request.urlopen(req, timeout=5)

    def test_routes(self):
        for p in ("/", "/text", "/work", "/plan",
                  "/text/1/1/1", "/work/1/1/1", "/text/1/1/1/listen",
                  "/plan/1/1"):
            with self._get(p) as r:
                self.assertEqual(r.status, 200, p)
                self.assertIn("text/html", r.headers["Content-Type"], p)

    def test_manuscript_render_and_raw(self):
        with self._get("/text/1/1/1/read") as r:
            self.assertIn("class=prose", r.read().decode())
        with self._get("/text/1/1/1/read?raw=1") as r:
            self.assertEqual(r.headers["Content-Type"], "text/plain; charset=utf-8")
            self.assertIn("第一段。", r.read().decode())

    def test_work_file_browser(self):
        with self._get("/work/1/1/1/read") as r:
            body = r.read().decode()
            self.assertIn("00_提示词/01_正文生成.md", body)
        # .md → 渲染成 HTML（class=md），带「复制原文」按钮，不再是 <pre>
        f = "00_%E6%8F%90%E7%A4%BA%E8%AF%8D/01_%E6%AD%A3%E6%96%87%E7%94%9F%E6%88%90.md"
        with self._get(f"/work/1/1/1/read?f={f}") as r:
            body = r.read().decode()
            self.assertIn("class=md", body)
            self.assertIn("<h1>提示词</h1>", body)
            self.assertIn("cpfile(this)", body)
            self.assertNotIn("pre class=file", body)
        # &raw=1 → 纯文本原文
        with self._get(f"/work/1/1/1/read?f={f}&raw=1") as r:
            self.assertEqual(r.headers["Content-Type"], "text/plain; charset=utf-8")
            self.assertEqual(r.read().decode(), "# 提示词\n内容")

    def test_traversal_blocked(self):
        try:
            self._get("/work/1/1/1/read?f=../../../../../../etc/passwd")
            self.fail("expected 404")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 404)

    def test_plan_file_browser(self):
        with self._get("/plan") as r:
            body = r.read().decode()
            self.assertIn("伏笔总纲.md", body)
            self.assertIn("规划.md", body)
            self.assertIn("第 1 部 · 卷 01", body)
        with self._get("/plan/1/1") as r:
            body = r.read().decode()
            self.assertIn("规划_卷01.md", body)
            self.assertIn("规划_卷01_章0001.md", body)
        # .md → rendered as HTML with class=md
        f = "01_%E7%AC%AC01%E9%83%A8/01_%E5%8D%B701/%E8%A7%84%E5%88%92_%E5%8D%B701.md"
        with self._get(f"/plan/root?f={f}") as r:
            body = r.read().decode()
            self.assertIn("class=md", body)
            self.assertIn("<h1>卷一规划</h1>", body)
            self.assertIn("cpfile(this)", body)
        # raw mode
        with self._get(f"/plan/root?f={f}&raw=1") as r:
            self.assertEqual(r.headers["Content-Type"], "text/plain; charset=utf-8")
            self.assertIn("卷规划内容", r.read().decode())
        # traversal blocked
        try:
            self._get("/plan/root?f=../../../../../../etc/passwd")
            self.fail("expected 404")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 404)

    def test_audio_range_and_feed(self):
        with self._get("/audio/1/1/1.mp3", {"Range": "bytes=10-59"}) as r:
            self.assertEqual(r.status, 206)
            self.assertEqual(r.headers["Content-Range"], f"bytes 10-59/{len(_MP3)}")
            self.assertEqual(len(r.read()), 50)
        with self._get("/feed.xml") as r:
            self.assertIn("rss+xml", r.headers["Content-Type"])


if __name__ == "__main__":
    unittest.main()
