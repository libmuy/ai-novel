#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""serve_audio.py 的离线单元测试（含一次本地回环 HTTP 请求）。"""
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

# 假 CBR mp3：MPEG2 Layer3 48kbps 帧头（b2 高 4 位=6 → 48kbps）+ 填充 ≈ 13s
_MP3 = b"\xff\xf3\x60\xc4" + b"\x00" * 80000


def _make_tree(tmp: Path):
    novel = tmp / "00_苍玄"
    (novel / "10_正文" / "01_第01部" / "01_卷01").mkdir(parents=True)
    (novel / "10_正文" / "01_第01部" / "01_卷01" / "章0001.md").write_text(
        "正文第一句。\n\n※\n\n第二场。\n", encoding="utf-8")
    aud = novel / "05_工作区" / "03_第01部" / "03_卷01" / "03_章0001" / "03_音频"
    aud.mkdir(parents=True)
    (aud / "章0001.mp3").write_bytes(_MP3)
    (aud / "章0001.json").write_text(
        '{"voice":"zh-CN-YunxiNeural","generated_at":"2026-09-03T20:00:00",'
        '"duration_seconds":794}', encoding="utf-8")
    return novel


class TestParseRef(unittest.TestCase):
    def test_strips_extension(self):
        self.assertEqual(S._parse_ref("/1/1/1.mp3"), (1, 1, 1, None))

    def test_scene(self):
        self.assertEqual(S._parse_ref("/2/3/4/5.mp3"), (2, 3, 4, 5))

    def test_too_few(self):
        self.assertIsNone(S._parse_ref("/1/1"))


class TestScan(unittest.TestCase):
    def test_finds_chapter(self):
        with tempfile.TemporaryDirectory() as td:
            novel = _make_tree(Path(td))
            chs = S.scan(novel)
            self.assertEqual(len(chs), 1)
            c = chs[0]
            self.assertEqual((c.part, c.vol, c.ch, c.scene), (1, 1, 1, None))
            self.assertEqual(c.manifest["voice"], "zh-CN-YunxiNeural")
            self.assertEqual(c.duration_s, 794)  # 取自 manifest
            self.assertEqual(c.manuscript_path().name, "章0001.md")


class TestRender(unittest.TestCase):
    def test_feed_is_valid_rss_with_enclosure(self):
        with tempfile.TemporaryDirectory() as td:
            chs = S.scan(_make_tree(Path(td)))
            xml = S.render_feed(chs, "苍玄", "http://pi.local:8765")
            root = ET.fromstring(xml)
            enc = root.find(".//item/enclosure")
            self.assertEqual(enc.get("type"), "audio/mpeg")
            self.assertEqual(enc.get("url"), "http://pi.local:8765/audio/1/1/1.mp3")
            self.assertEqual(int(enc.get("length")), len(_MP3))

    def test_index_has_player_and_feed_link(self):
        with tempfile.TemporaryDirectory() as td:
            chs = S.scan(_make_tree(Path(td)))
            page = S.render_index(chs, "苍玄", "http://pi.local:8765").decode("utf-8")
            self.assertIn("/feed.xml", page)
            self.assertIn("<audio controls preload=none src='/audio/1/1/1.mp3'>", page)


class TestMp3Duration(unittest.TestCase):
    def test_cbr_estimate(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "a.mp3"
            p.write_bytes(_MP3)
            # 80004 字节 @ 48kbps ≈ 13s
            self.assertEqual(S._mp3_duration_seconds(p, p.stat().st_size), 13)


class TestHttpRoundtrip(unittest.TestCase):
    def test_range_and_feed_over_http(self):
        with tempfile.TemporaryDirectory() as td:
            novel = _make_tree(Path(td))
            httpd = S.ThreadingHTTPServer(
                ("127.0.0.1", 0), S.make_handler(novel, "苍玄", None))
            port = httpd.server_address[1]
            t = threading.Thread(target=httpd.serve_forever, daemon=True)
            t.start()
            try:
                req = urllib.request.Request(
                    f"http://127.0.0.1:{port}/audio/1/1/1.mp3", headers={"Range": "bytes=0-99"})
                with urllib.request.urlopen(req, timeout=5) as r:
                    self.assertEqual(r.status, 206)
                    self.assertEqual(r.headers["Content-Range"], f"bytes 0-99/{len(_MP3)}")
                    self.assertEqual(len(r.read()), 100)

                with urllib.request.urlopen(f"http://127.0.0.1:{port}/feed.xml", timeout=5) as r:
                    self.assertEqual(r.status, 200)
                    self.assertIn("rss+xml", r.headers["Content-Type"])

                with urllib.request.urlopen(f"http://127.0.0.1:{port}/text/1/1/1", timeout=5) as r:
                    self.assertIn("正文第一句", r.read().decode("utf-8"))
            finally:
                httpd.shutdown()


if __name__ == "__main__":
    unittest.main()
