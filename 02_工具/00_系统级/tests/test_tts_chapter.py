#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tts_chapter.py 的离线单元测试（不联网 / 不调 edge-tts）。"""
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "01_小说通用工具"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import tts_chapter as T  # noqa: E402

SAMPLE = (
    "﻿# 章0001\n\n"
    "第一段正文。\n\n\n\n第二段正文。\n\n"
    "※\n\n"
    "第二场开头。\n\n"
    "＊\n\n"
    "第三场。\n\n"
    "***\n\n"
    "第四场结尾。\n"
)


class TestCleanText(unittest.TestCase):
    def test_splits_scenes_and_strips_markup(self):
        scenes = T._clean_text(SAMPLE)
        self.assertEqual(len(scenes), 4)
        self.assertNotIn("#", scenes[0])
        self.assertNotIn("※", "\n".join(scenes))
        self.assertNotIn("\n\n\n", scenes[0])  # 连续空行折叠
        self.assertTrue(scenes[0].startswith("第一段正文"))
        self.assertEqual(scenes[3], "第四场结尾。")

    def test_frontmatter_and_comments_removed(self):
        raw = "---\ntitle: x\n---\n正文开始<!-- 内部注释 -->继续。\n"
        scenes = T._clean_text(raw)
        self.assertEqual(scenes, ["正文开始继续。"])

    def test_empty_after_clean(self):
        self.assertEqual(T._clean_text("# 只有标题\n\n※\n"), [])


class TestParagraphChunks(unittest.TestCase):
    def test_no_split_when_short(self):
        self.assertEqual(T._paragraph_chunks("abc", 100), ["abc"])

    def test_splits_on_paragraph_boundary(self):
        scene = "\n\n".join(["段" * 30 for _ in range(6)])
        chunks = T._paragraph_chunks(scene, 80)
        self.assertGreater(len(chunks), 1)
        self.assertEqual("\n\n".join(chunks).replace("\n\n", ""), scene.replace("\n\n", ""))


def _make_chapter(tmp: Path, body: str = SAMPLE):
    novel = tmp / "novel"
    ms = novel / "10_正文" / "01_第01部" / "01_卷01" / "章0001.md"
    ms.parent.mkdir(parents=True)
    ms.write_text(body, encoding="utf-8")
    chdir = novel / "05_工作区" / "03_第01部" / "03_卷01" / "03_章0001"
    for sub in ("00_提示词", "01_模型输出", "02_状态"):
        (chdir / sub).mkdir(parents=True)
    return novel, ms, chdir


class TestResolveTargets(unittest.TestCase):
    def test_chapter_dir_resolution(self):
        with tempfile.TemporaryDirectory() as td:
            _novel, ms, chdir = _make_chapter(Path(td))
            args = _ns(chapter_dir=str(chdir))
            novel_dir, manuscript, out_dir, stem, ext = T._resolve_targets(args)
            self.assertEqual(manuscript.resolve(), ms.resolve())
            self.assertEqual(out_dir, chdir / "03_音频")
            self.assertEqual(stem, "章0001")
            self.assertEqual(ext, "mp3")


def _ns(**kw):
    base = dict(chapter_dir=None, manuscript=None, novel_dir=None, out=None,
               backend=None, voice=None, rate=None, per_scene=False, force=False,
               dry_run=False, config=str(T.DEFAULT_CONFIG), format="json")
    base.update(kw)
    import argparse
    return argparse.Namespace(**base)


class _FakeSynth:
    """替换 _synthesize_scene 的桩：写几十字节假音频，记录调用次数。"""

    def __init__(self):
        self.calls = 0

    def __call__(self, backend, cfg, opts, text, out_path):
        self.calls += 1
        Path(out_path).write_bytes(b"\xff\xf3" + text.encode("utf-8")[:32])


class TestIdempotency(unittest.TestCase):
    def setUp(self):
        self._orig = T._synthesize_scene
        self.fake = _FakeSynth()
        T._synthesize_scene = self.fake

    def tearDown(self):
        T._synthesize_scene = self._orig

    def _run(self, chdir, *extra):
        buf = io.StringIO()
        with redirect_stdout(buf):
            T.main(["--chapter-dir", str(chdir), *extra])
        return json.loads(buf.getvalue())

    def test_skip_when_unchanged_and_force_regenerates(self):
        with tempfile.TemporaryDirectory() as td:
            _novel, _ms, chdir = _make_chapter(Path(td))
            r1 = self._run(chdir)
            self.assertFalse(r1["skipped"])
            self.assertEqual(self.fake.calls, 4)  # 4 场景
            mp3 = chdir / "03_音频" / "章0001.mp3"
            self.assertTrue(mp3.exists())

            r2 = self._run(chdir)
            self.assertTrue(r2["skipped"])
            self.assertEqual(self.fake.calls, 4)  # 未再合成

            r3 = self._run(chdir, "--force")
            self.assertFalse(r3["skipped"])
            self.assertEqual(self.fake.calls, 8)

    def test_regenerates_when_source_changes(self):
        with tempfile.TemporaryDirectory() as td:
            _novel, ms, chdir = _make_chapter(Path(td))
            self._run(chdir)
            ms.write_text(SAMPLE + "\n\n新增一段。\n", encoding="utf-8")
            r = self._run(chdir)
            self.assertFalse(r["skipped"])


class TestBackendUnavailable(unittest.TestCase):
    def test_edge_missing_exits_2_no_file(self):
        orig = T._edge_bin
        T._edge_bin = lambda: None
        try:
            with tempfile.TemporaryDirectory() as td:
                _novel, _ms, chdir = _make_chapter(Path(td))
                buf = io.StringIO()
                with redirect_stdout(buf), self.assertRaises(SystemExit) as cm:
                    T.main(["--chapter-dir", str(chdir), "--backend", "edge"])
                self.assertEqual(cm.exception.code, 2)
                self.assertIn("backend_unavailable", buf.getvalue())
                self.assertFalse((chdir / "03_音频" / "章0001.mp3").exists())
        finally:
            T._edge_bin = orig


class TestWorkspaceAuditTolerates03Audio(unittest.TestCase):
    """回归：章工作区新增第 4 个 03_音频/ 目录，不给 workspace 审计规则增加任何 finding。"""

    def test_03_audio_adds_no_findings(self):
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "01_小说通用工具"))
        from audit.rules.workspace import WorkspaceRule
        from audit.context import AuditContext
        with tempfile.TemporaryDirectory() as td:
            novel, _ms, chdir = _make_chapter(Path(td))
            (chdir / "00_提示词" / "01_正文生成.md").write_text("x", encoding="utf-8")
            (chdir / "01_模型输出" / "01_正文生成.md").write_text("x", encoding="utf-8")

            def codes():
                return sorted(f.code for f in WorkspaceRule().run(AuditContext(novel)))

            before = codes()
            (chdir / "03_音频").mkdir()
            (chdir / "03_音频" / "章0001.mp3").write_bytes(b"\xff\xf3")
            (chdir / "03_音频" / "章0001.json").write_text("{}", encoding="utf-8")
            self.assertEqual(codes(), before)


if __name__ == "__main__":
    unittest.main()
