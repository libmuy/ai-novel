"""review_manuscript.py 的离线单元测试（不调 opencode / 不联网）。"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "01_小说通用工具"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import review_manuscript as R  # noqa: E402


class TestExtractJsonObj(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(R._extract_json_obj('{"findings":[]}'), {"findings": []})

    def test_with_think_and_prose(self):
        raw = '<think>让我看看</think>\n这是结果：\n{"findings":[{"severity":"high"}]}\n完毕'
        self.assertEqual(R._extract_json_obj(raw), {"findings": [{"severity": "high"}]})

    def test_fenced(self):
        raw = '```json\n{"findings":[1,2]}\n```'
        self.assertEqual(R._extract_json_obj(raw), {"findings": [1, 2]})

    def test_garbage_returns_empty(self):
        self.assertEqual(R._extract_json_obj("no json here"), {})


class TestOpencodeJsonlParse(unittest.TestCase):
    def test_collects_text_events(self):
        import json as _j

        class FakeProc:
            returncode = 0
            stderr = ""
            stdout = "\n".join([
                _j.dumps({"type": "step_start", "part": {}}),
                _j.dumps({"type": "text", "part": {"text": '{"findings":'}}),
                _j.dumps({"type": "text", "part": {"text": '[]}'}}),
                _j.dumps({"type": "step_finish", "part": {"reason": "stop"}}),
            ])

        orig = R.subprocess.run
        R.subprocess.run = lambda *a, **k: FakeProc()
        try:
            raw, err = R._run_opencode("m", "p", 10)
        finally:
            R.subprocess.run = orig
        self.assertEqual(err, "")
        self.assertEqual(R._extract_json_obj(raw), {"findings": []})

    def test_error_event(self):
        import json as _j

        class FakeProc:
            returncode = 0
            stderr = ""
            stdout = _j.dumps({"type": "error", "part": {"message": "rate limited"}})

        orig = R.subprocess.run
        R.subprocess.run = lambda *a, **k: FakeProc()
        try:
            raw, err = R._run_opencode("m", "p", 10)
        finally:
            R.subprocess.run = orig
        self.assertTrue(err)
        self.assertEqual(raw, "")


class TestAppendRecordPreservesContent(unittest.TestCase):
    def test_append(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "rec.md"
            p.write_text("# 原有\n\n> 人工内容不可覆盖\n", encoding="utf-8")
            R._append_record(p, {
                "mode": "manuscript", "passes": "1",
                "critics_used": ["opencode/x·无参照"], "critics_unavailable": [],
                "claude_subagent_requested": False,
                "lexicon": [{"message": "命中「手电」", "locations": ["a.md:第1行"]}],
                "findings": [{"severity": "high", "kind": "不闭合", "where": "x",
                              "problem": "y", "fix_hint": "z", "_source": "s"}],
            })
            out = p.read_text(encoding="utf-8")
            self.assertIn("人工内容不可覆盖", out)
            self.assertIn("冷读评审", out)
            self.assertIn("命中「手电」", out)
            self.assertIn("🔴 [不闭合]", out)


class TestCriticFamily(unittest.TestCase):
    def test_opencode_entry(self):
        self.assertEqual(R._critic_family("opencode/mimo-v2.5-free·无参照"), "opencode")

    def test_local_qwen_entry(self):
        self.assertEqual(R._critic_family("local_qwen·带参照"), "local_qwen")

    def test_two_passes_same_family(self):
        used = ["opencode/mimo·无参照", "opencode/mimo·带参照"]
        self.assertEqual({R._critic_family(u) for u in used}, {"opencode"})


class TestDegradedSingleCritic(unittest.TestCase):
    """成功家族数 < 2 时结果带 degraded=single_critic；≥2 家族时不降级。

    直接跑 main() 需要真实 opencode/网络，这里改为核对 §「评审器池」段落里
    与家族计数直接相关的逻辑：len(used)（家族×遍数）不能代表家族数，
    必须去重成 family 集合。"""

    def test_len_used_overcounts_family(self):
        # 同一家族跑两遍（无参照/带参照）产生 2 条 used，但只 1 个家族。
        used = ["opencode/mimo·无参照", "opencode/mimo·带参照"]
        families = {R._critic_family(u) for u in used}
        self.assertEqual(len(used), 2)
        self.assertEqual(len(families), 1)  # 若误用 len(used) 当家族数会被判定为"不降级"，是错的

    def test_single_family_is_degraded(self):
        used = ["opencode/mimo·无参照", "opencode/mimo·带参照"]
        families = {R._critic_family(u) for u in used}
        self.assertTrue(len(families) < 2)

    def test_two_families_not_degraded(self):
        used = ["opencode/mimo·无参照", "local_qwen·无参照"]
        families = {R._critic_family(u) for u in used}
        self.assertFalse(len(families) < 2)

    def test_claude_subagent_requested_counts_as_family(self):
        used = ["opencode/mimo·无参照"]
        families = {R._critic_family(u) for u in used}
        families.add("claude_subagent")
        self.assertFalse(len(families) < 2)


class TestAppendRecordDegradedWarning(unittest.TestCase):
    def test_degraded_line_present(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "rec.md"
            R._append_record(p, {
                "mode": "manuscript", "passes": "both",
                "critics_used": ["opencode/m·无参照", "opencode/m·带参照"],
                "critics_unavailable": [],
                "claude_subagent_requested": False,
                "lexicon": [], "findings": [],
                "degraded": "single_critic",
            })
            out = p.read_text(encoding="utf-8")
            self.assertIn("degraded=single_critic", out)
            self.assertIn("⚠️", out)

    def test_no_warning_when_not_degraded(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "rec.md"
            R._append_record(p, {
                "mode": "manuscript", "passes": "both",
                "critics_used": ["opencode/m·无参照", "local_qwen·无参照"],
                "critics_unavailable": [],
                "claude_subagent_requested": False,
                "lexicon": [], "findings": [],
                "degraded": None,
            })
            out = p.read_text(encoding="utf-8")
            self.assertNotIn("degraded=single_critic", out)


class TestResolveTargetsChapterDir(unittest.TestCase):
    def _mk_novel(self, root: Path):
        for d in ["01_设定", "10_正文/01_第01部/01_卷01", "03_规划/01_第01部/01_卷01",
                  "05_工作区/03_第01部/03_卷01/03_章0001/02_状态"]:
            (root / d).mkdir(parents=True, exist_ok=True)
        (root / "01_设定/00_小说概念.md").write_text("# 概念", encoding="utf-8")
        (root / "01_设定/00_主角档案.md").write_text("# 主角", encoding="utf-8")
        (root / "10_正文/01_第01部/01_卷01/章0001.md").write_text("正文", encoding="utf-8")
        (root / "03_规划/01_第01部/01_卷01/规划_卷01_章0001.md").write_text("细纲", encoding="utf-8")

    def test_manuscript_mode(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "00_小说"
            root.mkdir()
            self._mk_novel(root)

            class A:
                chapter_dir = str(root / "05_工作区/03_第01部/03_卷01/03_章0001")
                manuscript = novel_dir = mode = record = None
            nd, mode, tgt, ref, rec = R._resolve_targets(A())
            self.assertEqual(nd, root.resolve())
            self.assertEqual(mode, "manuscript")
            self.assertTrue(str(tgt).endswith("10_正文/01_第01部/01_卷01/章0001.md"))
            self.assertTrue(str(rec).endswith("03_章0001/02_状态/02_正文校验记录.md"))
            self.assertIn("细纲", ref)
            self.assertIn("世界基本法则", ref)

    def test_outline_mode(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "00_小说"
            root.mkdir()
            self._mk_novel(root)

            class A:
                chapter_dir = str(root / "05_工作区/03_第01部/03_卷01/03_章0001")
                manuscript = novel_dir = record = None
                mode = "outline"
            nd, mode, tgt, ref, rec = R._resolve_targets(A())
            self.assertEqual(mode, "outline")
            self.assertTrue(str(tgt).endswith("规划_卷01_章0001.md"))
            self.assertTrue(str(rec).endswith("03_细纲对照记录.md"))


if __name__ == "__main__":
    unittest.main()
