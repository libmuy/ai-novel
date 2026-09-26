#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""serve_audio.py 的离线单元测试（含本地回环 HTTP 请求）。"""
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from unittest import mock
from xml.etree import ElementTree as ET

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "01_小说通用工具"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import progress_store  # noqa: E402
import serve_audio as S  # noqa: E402

# 假 CBR mp3：MPEG2 Layer3 48kbps（b2 高 4 位=6）+ 填充 ≈ 13s
_MP3 = b"\xff\xf3\x60\xc4" + b"\x00" * 80000
_MANUSCRIPT = "第一段。\n\n第二段。\n\n※\n\n第二场。\n"


def _make_tree(tmp: Path, with_audio=True, with_ch2=True, with_ch2_manuscript=False, with_progress=False):
    novel = tmp / "00_苍玄"
    (novel / "10_正文" / "01_第01部" / "01_卷01").mkdir(parents=True)
    (novel / "10_正文" / "01_第01部" / "01_卷01" / "正文_卷01_章0001.md").write_text(_MANUSCRIPT, encoding="utf-8")
    ws1 = novel / "05_工作区" / "03_第01部" / "03_卷01" / "0001"
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
        ws2 = novel / "05_工作区" / "03_第01部" / "03_卷01" / "0002"
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

    if with_ch2 and with_ch2_manuscript:
        (novel / "10_正文" / "01_第01部" / "01_卷01" / "正文_卷01_章0002.md").write_text(
            _MANUSCRIPT, encoding="utf-8")
        (v1 / "规划_卷01_章0002.md").write_text("## 第二章细纲\n细纲内容", encoding="utf-8")

    if with_progress:
        prog = {"version": 1, "files": {
            "10_正文/01_第01部/01_卷01/正文_卷01_章0001.md": {"status": "定稿"},
        }}
        if with_ch2 and with_ch2_manuscript:
            prog["files"]["10_正文/01_第01部/01_卷01/正文_卷01_章0002.md"] = {"status": "定稿"}
        (novel / "00_进度.json").write_text(json.dumps(prog, ensure_ascii=False), encoding="utf-8")

    return novel


class TestParseRef(unittest.TestCase):
    def test_strips_extension(self):
        self.assertEqual(S._parse_ref("1/1/1.mp3"), (1, 1, 1, None))

    def test_scene(self):
        self.assertEqual(S._parse_ref("2/3/4/5.mp3"), (2, 3, 4, 5))

    def test_too_few(self):
        self.assertIsNone(S._parse_ref("1/1"))


class TestScan(unittest.TestCase):
    def test_merges_manuscript_ws_audio_outline(self):
        with tempfile.TemporaryDirectory() as td:
            entries = S.scan(_make_tree(Path(td)))
            self.assertEqual([e.key for e in entries], [(1, 1, 1), (1, 1, 2)])
            e1 = entries[0]
            self.assertTrue(e1.manuscript.name == "正文_卷01_章0001.md")
            self.assertTrue(e1.ws_dir.name == "0001")
            self.assertTrue(e1.outline.name == "规划_卷01_章0001.md")
            self.assertTrue(e1.has_audio)
            self.assertEqual(e1.duration_s(), 794)
            self.assertEqual(e1.audio_units(), [(None, e1.audio_dir / "章0001.mp3")])
            self.assertEqual(entries[1].manuscript, None)   # ch2 只有工作区
            self.assertIsNone(entries[1].outline)
            self.assertFalse(entries[1].has_audio)

    def test_latest_key(self):
        with tempfile.TemporaryDirectory() as td:
            entries = S.scan(_make_tree(Path(td), with_ch2_manuscript=True))
            self.assertEqual(S.latest_key(entries, "manuscript"), (1, 1, 2))
            self.assertEqual(S.latest_key(entries, "outline"), (1, 1, 2))

    def test_latest_key_empty(self):
        self.assertIsNone(S.latest_key([], "manuscript"))


class TestScanTree(unittest.TestCase):
    def test_titles_and_states(self):
        with tempfile.TemporaryDirectory() as td:
            novel = _make_tree(Path(td))
            tree, entries = S._scan_tree(novel)
            self.assertEqual([p["n"] for p in tree], [1])
            vol = tree[0]["vols"][0]
            self.assertEqual(vol["n"], 1)
            self.assertEqual(vol["title"], "卷一规划")  # 优先取「规划_卷NN.md」本体，不是伏笔册/事件文件
            ch1, ch2 = vol["chapters"]
            self.assertEqual(ch1["n"], 1)
            self.assertEqual(ch1["state"], "有音频")
            self.assertTrue(ch1["has_manuscript"])
            self.assertEqual(ch2["state"], "待细纲")
            self.assertFalse(ch2["has_manuscript"])

    def test_chapter_state_without_audio(self):
        with tempfile.TemporaryDirectory() as td:
            novel = _make_tree(Path(td), with_audio=False)
            tree, _entries = S._scan_tree(novel)
            ch1 = tree[0]["vols"][0]["chapters"][0]
            self.assertEqual(ch1["state"], "有正文")


class TestMp3Duration(unittest.TestCase):
    def test_cbr_estimate(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "a.mp3"
            p.write_bytes(_MP3)
            self.assertEqual(S._mp3_duration_seconds(p, p.stat().st_size), 13)


class TestFeed(unittest.TestCase):
    def test_feed_valid(self):
        with tempfile.TemporaryDirectory() as td:
            entries = S.scan(_make_tree(Path(td)))
            root = ET.fromstring(S.render_feed(entries, "苍玄", "http://p:8765"))
            encs = root.findall(".//item/enclosure")
            self.assertEqual(len(encs), 1)
            self.assertEqual(encs[0].get("url"), "http://p:8765/audio/1/1/1.mp3")
            self.assertEqual(int(encs[0].get("length")), len(_MP3))


class _HttpTestBase(unittest.TestCase):
    read_only = False

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.novel = _make_tree(Path(self.td.name))
        self.httpd = S.ThreadingHTTPServer(
            ("127.0.0.1", 0),
            S.make_handler(self.novel, "苍玄", None, self.read_only))
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.td.cleanup()

    def _req(self, method, path, body=None, csrf=True, headers=None):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        h = dict(headers or {})
        if data is not None:
            h["Content-Type"] = "application/json"
        if csrf and method != "GET":
            h["X-Review-UI"] = "1"
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data, method=method, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8") or "{}")

    def _get_raw(self, path):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=5) as r:
            return r.status, r.read(), dict(r.headers)


class TestApiBook(_HttpTestBase):
    def test_structure(self):
        st, body = self._req("GET", "/api/book")
        self.assertEqual(st, 200)
        self.assertEqual(body["meta"], {"parts": 1, "vols": 1, "chapters": 2})
        self.assertTrue(body["feed_url"].endswith("/feed.xml"))
        v = body["parts"][0]["vols"][0]
        self.assertEqual(len(v["chapters"]), 2)
        self.assertEqual(v["chapters"][0]["state"], "有音频")
        self.assertEqual(v["chapters"][0]["tag"], "neutral")


class TestApiLevel(_HttpTestBase):
    def test_root_children_are_parts(self):
        st, body = self._req("GET", "/api/level?sec=work")
        self.assertEqual(st, 200)
        self.assertEqual(body["level"], "root")
        self.assertEqual([c["n"] for c in body["children"]], [1])

    def test_vol_children_are_chapters_and_meta(self):
        st, body = self._req("GET", "/api/level?sec=work&part=1&vol=1")
        self.assertEqual(st, 200)
        self.assertEqual(body["level"], "vol")
        self.assertEqual([c["n"] for c in body["children"]], [1, 2])
        self.assertEqual(body["meta"], "共 2 章")

    def test_text_section_only_lists_chapters_with_manuscript(self):
        st, body = self._req("GET", "/api/level?sec=text&part=1&vol=1")
        self.assertEqual(st, 200)
        self.assertEqual([c["n"] for c in body["children"]], [1])  # ch2 无正文，text 分区不显示

    def test_work_chapter_file_groups(self):
        st, body = self._req("GET", "/api/level?sec=work&part=1&vol=1&ch=1")
        self.assertEqual(st, 200)
        self.assertEqual(body["level"], "ch")
        names = {g["name"] for g in body["file_groups"]}
        self.assertIn("00_提示词", names)
        self.assertIn("02_状态", names)
        self.assertIn("03_音频", names)
        chapter = body["chapter"]
        self.assertEqual((chapter["n"], chapter["prev"], chapter["next"]), (1, None, 2))
        # ch1 是本书目前唯一有正文的章，正文/细纲各自都是「全书最新」
        self.assertTrue(chapter["withdraw"]["manuscript"]["latest"])
        self.assertTrue(chapter["withdraw"]["outline"]["latest"])

    def test_plan_vol_excludes_chapter_outline(self):
        st, body = self._req("GET", "/api/level?sec=plan&part=1&vol=1")
        self.assertEqual(st, 200)
        files = body["file_groups"][0]["files"]
        names = [f["name"] for f in files]
        self.assertIn("规划_卷01.md", names)
        self.assertNotIn("规划_卷01_章0001.md", names)  # 章细纲不在「本卷规划」，属于章级

    def test_plan_chapter_group(self):
        st, body = self._req("GET", "/api/level?sec=plan&part=1&vol=1&ch=1")
        self.assertEqual(st, 200)
        self.assertEqual(body["file_groups"][0]["files"][0]["name"], "规划_卷01_章0001.md")

    def test_text_root_has_no_file_groups(self):
        st, body = self._req("GET", "/api/level?sec=text")
        self.assertEqual(body["file_groups"], [])

    def test_unknown_level_404(self):
        st, body = self._req("GET", "/api/level?sec=work&part=9&vol=9")
        self.assertEqual(st, 404)


class TestApiFile(_HttpTestBase):
    def setUp(self):
        super().setUp()
        self.path = "05_工作区/03_第01部/03_卷01/0001/00_提示词/01_正文生成.md"

    def test_get(self):
        st, body = self._req("GET", "/api/file?path=" + urllib.parse.quote(self.path))
        self.assertEqual(st, 200)
        self.assertEqual(body["kind"], "prose")
        self.assertIn("内容", body["text"])

    def test_get_missing_404(self):
        st, body = self._req("GET", "/api/file?path=" + urllib.parse.quote("05_工作区/没有这个文件.md"))
        self.assertEqual(st, 404)

    def test_put_overwrite_same_path(self):
        st, body = self._req("PUT", "/api/file", {"path": self.path, "text": "新内容"})
        self.assertEqual(st, 200)
        self.assertEqual((self.novel / self.path).read_text(encoding="utf-8"), "新内容")

    def test_put_save_as_new_file(self):
        new_path = "05_工作区/03_第01部/03_卷01/0001/00_提示词/02_另存.md"
        st, body = self._req("PUT", "/api/file", {"path": new_path, "text": "另存内容", "orig": self.path})
        self.assertEqual(st, 200)
        self.assertTrue((self.novel / new_path).is_file())
        self.assertTrue((self.novel / self.path).is_file())  # 原文件保留

    def test_put_save_as_conflict_needs_overwrite(self):
        existing = "05_工作区/03_第01部/03_卷01/0001/02_状态/01_状态履历.md"
        st, body = self._req("PUT", "/api/file", {"path": existing, "text": "x", "orig": self.path})
        self.assertEqual(st, 409)
        st, body = self._req("PUT", "/api/file", {"path": existing, "text": "x", "orig": self.path, "overwrite": True})
        self.assertEqual(st, 200)

    def test_delete_moves_to_trash(self):
        st, body = self._req("DELETE", "/api/file?path=" + urllib.parse.quote(self.path))
        self.assertEqual(st, 200)
        self.assertFalse((self.novel / self.path).exists())
        trashed = self.novel / body["trashed_to"]
        self.assertTrue(trashed.is_file())
        st, body = self._req("GET", "/api/file?path=" + urllib.parse.quote(self.path))
        self.assertEqual(st, 404)

    def test_get_mp3_returns_audio(self):
        mp3 = "05_工作区/03_第01部/03_卷01/0001/03_音频/章0001.mp3"
        st, body = self._req("GET", "/api/file?path=" + urllib.parse.quote(mp3))
        self.assertEqual(st, 200)
        self.assertEqual(body["kind"], "audio")
        self.assertEqual(body["audio_url"], "/audio/1/1/1.mp3")

    def test_delete_mp3_moves_to_trash(self):
        mp3 = "05_工作区/03_第01部/03_卷01/0001/03_音频/章0001.mp3"
        st, body = self._req("DELETE", "/api/file?path=" + urllib.parse.quote(mp3))
        self.assertEqual(st, 200)
        self.assertFalse((self.novel / mp3).exists())
        self.assertTrue((self.novel / body["trashed_to"]).is_file())

    def test_put_mp3_still_rejected(self):
        st, body = self._req("PUT", "/api/file",
                             {"path": "05_工作区/03_第01部/03_卷01/0001/03_音频/新.mp3", "text": "x"})
        self.assertEqual(st, 400)
        self.assertIn("扩展名", body["error"])


class TestSecurity(_HttpTestBase):
    def test_path_traversal_rejected(self):
        st, body = self._req("PUT", "/api/file", {"path": "05_工作区/../../../etc/passwd", "text": "x"})
        self.assertEqual(st, 400)

    def test_outside_allowed_roots_rejected(self):
        st, body = self._req("PUT", "/api/file", {"path": "01_设定/x.md", "text": "x"})
        self.assertEqual(st, 400)

    def test_bad_extension_rejected(self):
        st, body = self._req("PUT", "/api/file", {"path": "05_工作区/x.exe", "text": "x"})
        self.assertEqual(st, 400)

    def test_missing_csrf_header_rejected(self):
        st, body = self._req("PUT", "/api/file", {"path": "05_工作区/x.md", "text": "x"}, csrf=False)
        self.assertEqual(st, 403)

    def test_mismatched_origin_rejected(self):
        st, body = self._req("PUT", "/api/file", {"path": "05_工作区/x.md", "text": "x"},
                             headers={"Origin": "http://evil.example:1234"})
        self.assertEqual(st, 403)


class TestReadOnly(_HttpTestBase):
    read_only = True

    def test_put_rejected(self):
        st, body = self._req("PUT", "/api/file", {"path": "05_工作区/x.md", "text": "x"})
        self.assertEqual(st, 403)

    def test_get_still_allowed(self):
        st, body = self._req("GET", "/api/book")
        self.assertEqual(st, 200)

    def test_config_reports_read_only(self):
        st, body = self._req("GET", "/api/config")
        self.assertEqual(st, 200)
        self.assertTrue(body["read_only"])


class TestBackfill(_HttpTestBase):
    def test_targets_root_level_are_existing_plan_files(self):
        st, body = self._req("GET", "/api/backfill-targets")
        self.assertEqual(st, 200)
        ids = {t["id"] for t in body["targets"]}
        self.assertIn("03_规划/规划.md", ids)
        self.assertIn("03_规划/00_伏笔总纲.md", ids)

    def test_chapter_targets_are_canonical_outline_and_manuscript(self):
        st, body = self._req("GET", "/api/backfill-targets?part=1&vol=1&ch=1")
        self.assertEqual(st, 200)
        ids = {t["id"] for t in body["targets"]}
        self.assertEqual(ids, {"03_规划/01_第01部/01_卷01/规划_卷01_章0001.md",
                               "10_正文/01_第01部/01_卷01/正文_卷01_章0001.md"})

    def test_apply_rejects_unknown_target_id(self):
        src = "05_工作区/03_第01部/03_卷01/0001/00_提示词/01_正文生成.md"
        st, body = self._req("POST", "/api/backfill",
                             {"src": src, "target_id": "10_正文/别的.md", "part": 1, "vol": 1, "ch": 1})
        self.assertEqual(st, 400)

    def test_apply_copies_content_and_reports_mode(self):
        src = "05_工作区/03_第01部/03_卷01/0001/00_提示词/01_正文生成.md"
        target = "10_正文/01_第01部/01_卷01/正文_卷01_章0001.md"
        st, body = self._req("POST", "/api/backfill", {"src": src, "target_id": target, "part": 1, "vol": 1, "ch": 1})
        self.assertEqual(st, 200)
        self.assertEqual(body["mode"], "manuscript")
        self.assertEqual((self.novel / target).read_text(encoding="utf-8"),
                         (self.novel / src).read_text(encoding="utf-8"))

    def test_apply_from_mp3_rejected(self):
        src = "05_工作区/03_第01部/03_卷01/0001/03_音频/章0001.mp3"
        target = "10_正文/01_第01部/01_卷01/正文_卷01_章0001.md"
        st, body = self._req("POST", "/api/backfill", {"src": src, "target_id": target, "part": 1, "vol": 1, "ch": 1})
        self.assertEqual(st, 400)
        self.assertIn("扩展名", body["error"])


class TestJobs(_HttpTestBase):
    def test_unimplemented_kind_501(self):
        st, body = self._req("POST", "/api/jobs", {"kind": "check", "part": 1, "vol": 1, "ch": 1})
        self.assertEqual(st, 501)
        self.assertIn("未实现", body["error"])

    def test_unknown_kind_400(self):
        st, body = self._req("POST", "/api/jobs", {"kind": "not_a_kind", "part": 1, "vol": 1, "ch": 1})
        self.assertEqual(st, 400)

    def test_get_missing_job_404(self):
        st, body = self._req("GET", "/api/jobs/does-not-exist")
        self.assertEqual(st, 404)

    def test_lifecycle_with_fake_runner(self):
        def _fake(novel_dir, part, vol, ch, body):
            return [sys.executable, "-c", "print('job-ran-ok')"], []
        with mock.patch.dict(S._JOB_BUILDERS, {"outline_ch": _fake}):
            st, body = self._req("POST", "/api/jobs", {"kind": "outline_ch", "part": 1, "vol": 1, "ch": 1})
            self.assertEqual(st, 200)
            job_id = body["job_id"]
            job = self._poll_job(job_id)
            self.assertEqual(job["status"], "ok")
            self.assertIn("job-ran-ok", job["log"])

    def test_dedupe_rejects_concurrent_same_job(self):
        def _slow(novel_dir, part, vol, ch, body):
            return [sys.executable, "-c", "import time; time.sleep(0.6)"], []
        with mock.patch.dict(S._JOB_BUILDERS, {"outline_ch": _slow}):
            st1, body1 = self._req("POST", "/api/jobs", {"kind": "outline_ch", "part": 1, "vol": 1, "ch": 1})
            self.assertEqual(st1, 200)
            st2, body2 = self._req("POST", "/api/jobs", {"kind": "outline_ch", "part": 1, "vol": 1, "ch": 1})
            self.assertEqual(st2, 409)
            self._poll_job(body1["job_id"])  # 等它跑完，不留后台线程

    def _poll_job(self, job_id, timeout=5):
        deadline = time.time() + timeout
        while time.time() < deadline:
            st, body = self._req("GET", f"/api/jobs/{job_id}")
            self.assertEqual(st, 200)
            if body["status"] != "running":
                return body
            time.sleep(0.05)
        self.fail("job 一直没跑完")


class TestHttpMisc(_HttpTestBase):
    def test_static_and_health(self):
        st, body, headers = self._get_raw("/health")
        self.assertEqual(st, 200)
        st, body, headers = self._get_raw("/")
        self.assertEqual(st, 200)
        self.assertIn("text/html", headers["Content-Type"])
        st, body, headers = self._get_raw("/app.js")
        self.assertEqual(st, 200)
        self.assertIn("javascript", headers["Content-Type"])

    def test_audio_range_and_feed(self):
        st, body, headers = self._get_raw("/audio/1/1/1.mp3")
        # 无 Range 头默认整段返回；专测 Range 见下
        self.assertEqual(st, 200)
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/audio/1/1/1.mp3", headers={"Range": "bytes=10-59"})
        with urllib.request.urlopen(req, timeout=5) as r:
            self.assertEqual(r.status, 206)
            self.assertEqual(r.headers["Content-Range"], f"bytes 10-59/{len(_MP3)}")
            self.assertEqual(len(r.read()), 50)
        st, body, headers = self._get_raw("/feed.xml")
        self.assertIn("rss+xml", headers["Content-Type"])


def _make_withdraw_tree(tmp: Path) -> Path:
    """ch1（无音频）+ ch2（有音频，全书最新）各自正文+细纲齐全，附 00_进度.json 声明两章
    「定稿」——专供 TestWithdraw 用，不复用 `_make_tree`：那份共享 fixture 的默认形状
    （ch2 只建工作区、没有正文）被好几个别的测试断言死了，硬塞更多参数进去只会更难读。"""
    novel = tmp / "00_苍玄"
    text_dir = novel / "10_正文" / "01_第01部" / "01_卷01"
    text_dir.mkdir(parents=True)
    plan_dir = novel / "03_规划" / "01_第01部" / "01_卷01"
    plan_dir.mkdir(parents=True)
    prog_files = {}
    for ch in (1, 2):
        (text_dir / f"正文_卷01_章{ch:04d}.md").write_text(_MANUSCRIPT, encoding="utf-8")
        (plan_dir / f"规划_卷01_章{ch:04d}.md").write_text(f"## 第{ch}章细纲\n细纲内容", encoding="utf-8")
        prog_files[f"10_正文/01_第01部/01_卷01/正文_卷01_章{ch:04d}.md"] = {"status": "定稿"}
        prog_files[f"03_规划/01_第01部/01_卷01/规划_卷01_章{ch:04d}.md"] = {"status": "定稿"}
        ws = novel / "05_工作区" / "03_第01部" / "03_卷01" / f"{ch:04d}"
        (ws / "00_提示词").mkdir(parents=True)
        (ws / "00_提示词" / "01_正文生成.md").write_text("# 提示词\n内容", encoding="utf-8")
        (ws / "02_状态").mkdir()
        (ws / "02_状态" / "01_状态履历.md").write_text("| a | b |", encoding="utf-8")
        (ws / "02_状态" / "03_细纲落地核对.md").write_text("- [x] 已落地", encoding="utf-8")
        if ch == 2:  # 音频挂在「最新」这章，撤下时才能真正验到归档逻辑
            aud = ws / "03_音频"
            aud.mkdir()
            (aud / f"章{ch:04d}.mp3").write_bytes(_MP3)
            (aud / f"章{ch:04d}.json").write_text('{"voice":"x"}', encoding="utf-8")
    (novel / "00_进度.json").write_text(
        json.dumps({"version": 1, "files": prog_files}, ensure_ascii=False), encoding="utf-8")
    return novel


class TestWithdraw(_HttpTestBase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.novel = _make_withdraw_tree(Path(self.td.name))
        self.httpd = S.ThreadingHTTPServer(
            ("127.0.0.1", 0),
            S.make_handler(self.novel, "苍玄", None, self.read_only))
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def test_non_latest_chapter_rejected(self):
        st, body = self._req("POST", "/api/withdraw", {"part": 1, "vol": 1, "ch": 1, "kind": "manuscript"})
        self.assertEqual(st, 409)
        self.assertIn("06_章节回溯修改.md", body["error"])
        self.assertTrue((self.novel / "10_正文/01_第01部/01_卷01/正文_卷01_章0001.md").exists())

    def test_outline_blocked_while_manuscript_exists(self):
        st, body = self._req("POST", "/api/withdraw", {"part": 1, "vol": 1, "ch": 2, "kind": "outline"})
        self.assertEqual(st, 409)
        self.assertIn("先撤正文", body["error"])

    def test_missing_csrf_header_rejected(self):
        st, body = self._req("POST", "/api/withdraw", {"part": 1, "vol": 1, "ch": 2, "kind": "manuscript"}, csrf=False)
        self.assertEqual(st, 403)

    def test_bad_progress_json_aborts_without_moving_file(self):
        (self.novel / "00_进度.json").write_text("{not json", encoding="utf-8")
        st, body = self._req("POST", "/api/withdraw", {"part": 1, "vol": 1, "ch": 2, "kind": "manuscript"})
        self.assertEqual(st, 400)
        self.assertTrue((self.novel / "10_正文/01_第01部/01_卷01/正文_卷01_章0002.md").exists())

    def test_withdraw_latest_manuscript_archives_and_clears_progress(self):
        canon = self.novel / "10_正文/01_第01部/01_卷01/正文_卷01_章0002.md"
        original = canon.read_text(encoding="utf-8")
        st, body = self._req("POST", "/api/withdraw", {"part": 1, "vol": 1, "ch": 2, "kind": "manuscript"})
        self.assertEqual(st, 200)
        self.assertTrue(body["progress_cleared"])
        self.assertFalse(canon.exists())
        archived = self.novel / body["archived_to"]
        self.assertTrue(archived.is_file())
        self.assertEqual(archived.read_text(encoding="utf-8"), original)
        self.assertTrue(archived.name.startswith("01_正文生成_旧稿_"))

        # 进度表登记已清，PROGRESS001 不会因为「声明了但文件不在」报错
        statuses = progress_store.statuses(self.novel)
        self.assertNotIn("10_正文/01_第01部/01_卷01/正文_卷01_章0002.md", statuses)

        # 音频一并归档，标签与 ch1 一样变回没有正文
        ws2_audio = self.novel / "05_工作区/03_第01部/03_卷01/0002/03_音频"
        self.assertEqual(list(ws2_audio.glob("章0002.mp3")), [])
        self.assertEqual(len(body["audio_archived"]), 2)  # .mp3 + .json

        st, body = self._req("GET", "/api/level?sec=text&part=1&vol=1")
        codes = {c["code"] for c in body["children"]}
        self.assertEqual(codes, {"0001"})  # ch2 从「有正文」列表里消失

        _st, raw, _headers = self._get_raw("/feed.xml")
        self.assertNotIn(b"/audio/1/1/2", raw)  # 撤下的这章音频不再出现在播客里

    def test_withdraw_now_latest_shifts_to_earlier_chapter(self):
        self._req("POST", "/api/withdraw", {"part": 1, "vol": 1, "ch": 2, "kind": "manuscript"})
        st, body = self._req("GET", "/api/level?sec=work&part=1&vol=1&ch=1")
        self.assertTrue(body["chapter"]["withdraw"]["manuscript"]["latest"])


class TestWithdrawReadOnly(_HttpTestBase):
    read_only = True

    def test_withdraw_rejected(self):
        st, body = self._req("POST", "/api/withdraw", {"part": 1, "vol": 1, "ch": 1, "kind": "manuscript"})
        self.assertEqual(st, 403)


if __name__ == "__main__":
    unittest.main()
