# -*- coding: utf-8 -*-
"""_llm.py —— json_mode 开关 与 summarize_chapter 的单元测试。"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import _llm  # noqa: E402


def _mk_cfg(**kw):
    d = dict(base_url="http://x/v1", model="m", api_key=None, api_key_env="K",
             api_key_required=False, backend="http")
    d.update(kw)
    return _llm.LlmConfig(**d)


class _FakeResp:
    def __init__(self, text):
        self._b = text.encode("utf-8")

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class TestJsonModeSwitch(unittest.TestCase):
    """http 后端：json_mode=False 时请求体不得带 response_format。"""

    def _capture_body(self, *, json_mode):
        seen = {}

        def fake_urlopen(req, timeout=None):
            seen["body"] = json.loads(req.data.decode("utf-8"))
            return _FakeResp(json.dumps(
                {"choices": [{"message": {"content": "结果文本"}}]}))

        orig = _llm.urllib.request.urlopen
        _llm.urllib.request.urlopen = fake_urlopen
        try:
            out = _llm.chat(_mk_cfg(), "s", "u", json_mode=json_mode)
        finally:
            _llm.urllib.request.urlopen = orig
        return out, seen["body"]

    def test_json_mode_true_sets_response_format(self):
        _out, body = self._capture_body(json_mode=True)
        self.assertEqual(body.get("response_format"), {"type": "json_object"})

    def test_json_mode_false_omits_response_format(self):
        out, body = self._capture_body(json_mode=False)
        self.assertNotIn("response_format", body)
        self.assertEqual(out, "结果文本")

    def test_prose_mode_strips_think_block(self):
        def fake_urlopen(req, timeout=None):
            return _FakeResp(json.dumps(
                {"choices": [{"message": {"content":
                    "<think>盘算一下</think>\n真正的摘要正文。"}}]}))

        orig = _llm.urllib.request.urlopen
        _llm.urllib.request.urlopen = fake_urlopen
        try:
            out = _llm.chat(_mk_cfg(), "s", "u", json_mode=False)
        finally:
            _llm.urllib.request.urlopen = orig
        self.assertEqual(out, "真正的摘要正文。")


class TestSummarizeChapter(unittest.TestCase):
    def test_empty_text_raises(self):
        with self.assertRaises(_llm.LlmError):
            _llm.summarize_chapter(_mk_cfg(), "   ")

    def test_retries_once_when_too_long(self):
        calls = []

        def fake_chat(cfg, system, user, *, json_mode=True):
            calls.append(user)
            return "长" * 400 if len(calls) == 1 else "短摘要。"

        orig = _llm.chat
        _llm.chat = fake_chat
        try:
            out = _llm.summarize_chapter(_mk_cfg(), "正文" * 100, target_chars=300)
        finally:
            _llm.chat = orig
        self.assertEqual(len(calls), 2)
        self.assertEqual(out, "短摘要。")
        self.assertIn("300 字以内", calls[1])


if __name__ == "__main__":
    unittest.main()
