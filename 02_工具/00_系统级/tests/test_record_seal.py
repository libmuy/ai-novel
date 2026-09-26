# -*- coding: utf-8 -*-
"""冷读记录封印（record_seal.py）与进度门禁 PROGRESS006/007 的回归测试。

背景：章0007 正文出现过「手写冷读记录（占位符指纹、0 条发现）」和「用英文引号写落地核对锚点」，
两者都绕过了旧门禁（只数 `## 冷读` 标题 / 只认「」引号）。
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

_TOOLS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "01_小说通用工具")
sys.path.insert(0, _TOOLS)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ""))
import progress_report
import record_seal
import review_manuscript as rm
from test_progress_report import build_novel_fixture


def _result(**kw):
    d = {"mode": "manuscript", "passes": "both", "critics_used": ["opencode/mimo·无参照"],
         "critics_unavailable": [], "findings": [], "lexicon": [], "fingerprint": "0a1b2c3d4e5f",
         "degraded": None}
    d.update(kw)
    return d


class TestRecordSeal(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.rec = Path(self.td.name) / "rec.md"

    def tearDown(self):
        self.td.cleanup()

    def _script_text(self, **kw):
        rm._append_record(self.rec, _result(**kw))
        return self.rec.read_text(encoding="utf-8")

    def _seal(self, body):
        return body + "\n" + record_seal.seal_line(body) + "\n"

    def test_script_written_record_counts_even_for_new_chapters(self):
        t = self._script_text()
        n, rejected = record_seal.count_cold_rounds(t, chapter_no=99, legacy_max_chapter=6)
        self.assertEqual((n, rejected), (1, []))

    def test_tampered_record_rejected(self):
        t = self._script_text().replace("无参照", "带参照", 1)
        n, rejected = record_seal.count_cold_rounds(t, 99, 6)
        self.assertEqual(n, 0)
        self.assertIn("校验码", rejected[0])

    def test_handwritten_record_rejected_for_new_chapter_but_legacy_ok_for_old(self):
        hand = "## 冷读 R1（2026-09-20）\n\n- 评审器：四路并行\n- 指纹：a1b2c3d4e5f6（示例值）\n### 发现（0 条）\n"
        self.assertEqual(record_seal.count_cold_rounds(hand, 7, 6)[0], 0)      # 章7：不认
        self.assertEqual(record_seal.count_cold_rounds(hand, 6, 6)[0], 1)      # 封印前的遗留章：宽限

    def test_placeholder_fingerprint_rejected_even_if_sealed(self):
        body = ("## 冷读评审 · 2026-09-20 10:00\n\n> 脚本：`review_manuscript.py`（manuscript / both 遍）\n"
                "> 评审器：opencode/mimo\n> 目标文件指纹：a1b2c3d4e5f6")
        status, reason = record_seal.check_section(self._seal(body))
        self.assertEqual(status, "invalid")
        self.assertIn("占位符", reason)

    def test_no_critics_rejected_even_if_sealed(self):
        body = ("## 冷读评审 · 2026-09-20 10:00\n\n> 脚本：`review_manuscript.py`（manuscript / both 遍）\n"
                "> 评审器：（无）\n> 目标文件指纹：0a1b2c3d4e5f")
        self.assertEqual(record_seal.check_section(self._seal(body))[0], "invalid")

    def test_missing_script_line_rejected(self):
        body = "## 冷读评审 · x\n\n> 评审器：a\n> 目标文件指纹：0a1b2c3d4e5f"
        self.assertEqual(record_seal.check_section(self._seal(body))[0], "invalid")

    def test_triage_sections_after_record_do_not_break_seal(self):
        t = self._script_text() + "\n\n## Claude 主 Agent 分诊\n\n- 随便写点什么\n"
        self.assertEqual(record_seal.count_cold_rounds(t, 99, 6)[0], 1)


class TestAnchorFormatGate(unittest.TestCase):
    MS = "他把粗布按上去。炭条蹭过布面。"

    def test_ascii_quotes_are_flagged_not_skipped(self):
        chk = '- [x] 要点\n      → 锚点："粗布按上去"\n'
        stale = progress_report.stale_landing_anchors(chk, self.MS)
        self.assertEqual(len(stale), 1)
        self.assertIn("「」", stale[0])

    def test_proper_quotes_still_pass(self):
        chk = '- [x] 要点\n      → 锚点：「粗布按上去」\n'
        self.assertEqual(progress_report.stale_landing_anchors(chk, self.MS), [])

    def test_waived_unfilled_and_unlanded_lines_are_not_flagged(self):
        chk = ("- [x] a\n      → 锚点：豁免：跨场景落地\n"
               "- [ ] b\n      → 锚点：〔待填：正文里的原句〕\n"
               "- [x] c\n      → 锚点：❌未落地（细纲要求但正文没写）\n")
        self.assertEqual(progress_report.stale_landing_anchors(chk, self.MS), [])


class TestProgress007Integration(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._old = (progress_report.LEGACY_COLD_MAX_CHAPTER_MANUSCRIPT,)
        progress_report.LEGACY_COLD_MAX_CHAPTER_MANUSCRIPT = 0   # 让测试里的章0001 当作「新章」

    def tearDown(self):
        progress_report.LEGACY_COLD_MAX_CHAPTER_MANUSCRIPT = self._old[0]

    def _findings(self, record):
        novel_dir = build_novel_fixture(
            self.tmp,
            progress={"10_正文/01_第01部/01_卷01/正文_卷01_章0001.md": "定稿"},
            cold_read_record=record, has_changelog=True, merged_upto="03_第01部/03_卷01/0001")
        declared = progress_report.declared_status(novel_dir)
        rep = progress_report.collect(novel_dir)
        return progress_report.reconcile(novel_dir, declared, rep)

    def test_handwritten_cold_read_gives_progress007_and_progress003(self):
        f = self._findings("# 校验记录\n## 冷读 R1\n\n- 指纹：a1b2c3d4e5f6（示例值）\n")
        codes = [c for _lv, c, _m in f]
        self.assertIn("PROGRESS007", codes)
        self.assertIn("PROGRESS003", codes)          # 不被认可 → 视同没有冷读
        self.assertTrue(all(lv == "error" for lv, c, _ in f if c in ("PROGRESS007", "PROGRESS003")))

    def test_script_sealed_record_gives_no_progress007(self):
        rec = Path(tempfile.mkdtemp()) / "r.md"
        rm._append_record(rec, _result())
        f = self._findings("# 校验记录\n" + rec.read_text(encoding="utf-8"))
        self.assertNotIn("PROGRESS007", [c for _lv, c, _m in f])


if __name__ == "__main__":
    unittest.main()
