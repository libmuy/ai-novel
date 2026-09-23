# -*- coding: utf-8 -*-
"""run_prompt.py —— 存档头部剥离、路径推导、空产出/截断的退出码、不降级 opencode。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, ".."))
sys.path.insert(0, os.path.join(_HERE, "..", "..", "01_小说通用工具"))
import _llm  # noqa: E402
import run_prompt  # noqa: E402


def _resp(content, finish="stop", reasoning=None):
    msg = {"content": content}
    if reasoning is not None:
        msg["reasoning_content"] = reasoning
    return {"choices": [{"message": msg, "finish_reason": finish}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            "timings": {"prompt_per_second": 500.0, "predicted_per_second": 30.0}}


class TestStripPreamble(unittest.TestCase):
    def test_strips_leading_blockquote_before_first_h1(self):
        text = "> 本地用\n> `build_prompt.py`\n\n# 【你的角色】\n正文\n"
        self.assertEqual(run_prompt._strip_archive_preamble(text), "# 【你的角色】\n正文\n")

    def test_keeps_text_without_preamble(self):
        text = "# 【你的角色】\n正文\n"
        self.assertEqual(run_prompt._strip_archive_preamble(text), text)

    def test_does_not_strip_when_file_does_not_start_with_quote(self):
        text = "随手写的说明\n\n# 【你的角色】\n正文\n"
        self.assertEqual(run_prompt._strip_archive_preamble(text), text)


class TestResolvePaths(unittest.TestCase):
    def _args(self, **kw):
        ns = mock.Mock(prompt=None, out=None, chapter_dir=None, task=None, revision=None)
        for k, v in kw.items():
            setattr(ns, k, v)
        return ns

    def test_chapter_dir_body(self):
        p, o = run_prompt._resolve_paths(self._args(chapter_dir="c/0008", task="正文"))
        self.assertEqual(p, Path("c/0008/00_提示词/01_正文生成.md"))
        self.assertEqual(o, Path("c/0008/01_模型输出/01_正文生成.md"))

    def test_chapter_dir_outline_revision(self):
        p, o = run_prompt._resolve_paths(self._args(chapter_dir="c/0008", task="细纲", revision=2))
        self.assertEqual(p.name, "00_单章细纲_修订2.md")
        self.assertEqual(o.name, "00_单章细纲_修订2.md")  # 与提示词存档同名配对（WS006）

    def test_prompt_mode_requires_out(self):
        with self.assertRaises(SystemExit):
            run_prompt._resolve_paths(self._args(prompt="x.md"))


class TestMainExitCodes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.prompt = Path(self.tmp.name) / "p.md"
        self.prompt.write_text("# 【你的角色】\nhi\n", encoding="utf-8")
        self.out = Path(self.tmp.name) / "out" / "o.md"
        self.argv = ["--prompt", str(self.prompt), "--out", str(self.out)]
        self.cfg = _llm.LlmConfig(base_url="http://x/v1", model="m", api_key=None,
                                  api_key_env="K", api_key_required=False, backend="auto")

    def _run(self, resp, extra=()):
        seen = {}

        def fake_generate(cfg, prompt):
            seen["cfg"] = cfg
            return resp

        with mock.patch.object(_llm, "load_llm_config", return_value=self.cfg), \
             mock.patch.object(_llm, "_probe_base_url", return_value=True), \
             mock.patch.object(run_prompt, "_count_tokens", return_value=3), \
             mock.patch.object(run_prompt, "_generate", side_effect=fake_generate):
            rc = run_prompt.main(self.argv + list(extra))
        return rc, seen.get("cfg")

    def test_ok_writes_output_and_forces_http_backend(self):
        rc, cfg = self._run(_resp("正文"))
        self.assertEqual(rc, 0)
        self.assertEqual(self.out.read_text(encoding="utf-8"), "正文\n")
        self.assertEqual(cfg.backend, "http")  # 配置里是 auto，这里必须被强制成 http

    def test_overrides_are_per_call(self):
        rc, cfg = self._run(_resp("x"), ["--max-tokens", "999", "--temperature", "0.7", "--think"])
        self.assertEqual((cfg.max_tokens, cfg.temperature, cfg.enable_thinking), (999, 0.7, True))
        self.assertEqual(self.cfg.max_tokens, 4096)  # 共享配置对象未被改动

    def test_truncated_returns_3(self):
        rc, _ = self._run(_resp("半截", finish="length"))
        self.assertEqual(rc, 3)

    def test_empty_content_with_finish_stop_returns_3(self):
        rc, _ = self._run(_resp("", finish="stop", reasoning="想了很多"))
        self.assertEqual(rc, 3)

    def test_think_block_stripped(self):
        rc, _ = self._run(_resp("<think>草稿</think>成稿"))
        self.assertEqual(rc, 0)
        self.assertEqual(self.out.read_text(encoding="utf-8"), "成稿\n")

    def test_existing_output_refused_without_force(self):
        self.out.parent.mkdir(parents=True)
        self.out.write_text("旧", encoding="utf-8")
        rc, _ = self._run(_resp("新"))
        self.assertEqual(rc, 1)
        self.assertEqual(self.out.read_text(encoding="utf-8"), "旧")

    def test_dry_run_writes_nothing(self):
        rc, cfg = self._run(_resp("x"), ["--dry-run"])
        self.assertEqual(rc, 0)
        self.assertIsNone(cfg)  # 没有调用生成
        self.assertFalse(self.out.exists())

    def test_endpoint_down_returns_2_without_opencode_fallback(self):
        with mock.patch.object(_llm, "load_llm_config", return_value=self.cfg), \
             mock.patch.object(_llm, "_probe_base_url", return_value=False), \
             mock.patch.object(_llm, "_opencode_chat") as oc:
            rc = run_prompt.main(self.argv)
        self.assertEqual(rc, 2)
        oc.assert_not_called()


if __name__ == "__main__":
    unittest.main()
