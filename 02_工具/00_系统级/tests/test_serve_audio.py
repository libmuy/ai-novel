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


class TestRenderProse(unittest.TestCase):
    def test_scene_break_and_paragraphs(self):
        h = S.render_prose(_MANUSCRIPT)
        self.assertEqual(h.count("<p>"), 3)
        self.assertIn("<hr>", h)


class TestPages(unittest.TestCase):
    def test_home_and_lists(self):
        with tempfile.TemporaryDirectory() as td:
            entries = S.scan(_make_tree(Path(td)))
            home = S.page_home(entries, "苍玄", "http://p:8765").decode()
            self.assertIn("href='/text'", home)
            self.assertIn("href='/work'", home)
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
        self.httpd = S.ThreadingHTTPServer(("127.0.0.1", 0), S.make_handler(novel, "苍玄", None))
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
        for p in ("/", "/text", "/work", "/text/1/1/1", "/work/1/1/1", "/text/1/1/1/listen"):
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
        with self._get("/work/1/1/1/read?f=00_%E6%8F%90%E7%A4%BA%E8%AF%8D/01_%E6%AD%A3%E6%96%87%E7%94%9F%E6%88%90.md") as r:
            self.assertIn("pre class=file", r.read().decode())

    def test_traversal_blocked(self):
        try:
            self._get("/work/1/1/1/read?f=../../../../../../etc/passwd")
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
