#!/usr/bin/env python3
"""audit_consistency.py 测试：覆盖 load_field_vocab 解析和干净小说 run_all_checks。"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "01_小说通用工具"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import audit_consistency as audit
from helpers import make_novel


class TestLoadFieldVocab(unittest.TestCase):
    """load_field_vocab 能正确解析 03_字段词表.md"""

    def test_parses_fields(self):
        with tempfile.TemporaryDirectory() as td:
            # 造一个假的字段词表
            vocab_dir = os.path.join(td, "00_通用模板")
            os.makedirs(vocab_dir)
            vocab_path = os.path.join(vocab_dir, "03_字段词表.md")
            with open(vocab_path, "w", encoding="utf-8") as f:
                f.write("# 字段词表\n\n")
                f.write("| 字段名 | 类型 | 说明 |\n")
                f.write("| --- | --- | --- |\n")
                f.write("| **内力值** | 运算-数值 | 角色内力 |\n")
                f.write("| **境界** | 运算-枚举 | 当前境界 |\n")
                f.write("| **当前心境** | 描述 | 心理状态 |\n")

            # load_field_vocab 会尝试 novel_dir / "00_通用模板" / "03_字段词表.md"
            # 但实际逻辑是从 novel_dir 向上找，我们直接构造路径测试
            # 实际上 audit 的 load_field_vocab 只看文件系统，我们构造一个含 02_数据库 的目录
            novel = os.path.join(td, "01_小说数据", "00_测试")
            os.makedirs(os.path.join(novel, "02_数据库"))
            # 但词表在仓库根的 00_通用模板/ 下
            # audit 的逻辑：先看 novel_dir/00_通用模板/，再看 novel_dir.parent.parent/00_通用模板/
            # 所以我们需要让 novel_dir.parent.parent 有 00_通用模板
            # novel = td/01_小说数据/00_测试
            # novel.parent.parent = td，而 vocab_dir = td/00_通用模板
            # 所以 novel_dir.parent.parent / "00_通用模板" / "03_字段词表.md" 就是 vocab_path

            fields = audit.load_field_vocab(Path(novel))
            self.assertIn("内力值", fields)
            self.assertEqual(fields["内力值"], "运算-数值")
            self.assertIn("境界", fields)
            self.assertEqual(fields["境界"], "运算-枚举")
            self.assertIn("当前心境", fields)
            self.assertEqual(fields["当前心境"], "描述")


class TestRunAllChecksCleanNovel(unittest.TestCase):
    """干净小说跑 run_all_checks 无 error（允许 warning/info）"""

    def test_no_errors(self):
        with tempfile.TemporaryDirectory() as td:
            novel = make_novel(td)
            # 造一个假的 00_通用模板 真实目录 + 符号链接
            real_tmpl = os.path.join(td, "real_通用模板")
            os.makedirs(real_tmpl, exist_ok=True)
            os.symlink(real_tmpl, os.path.join(novel, "00_通用模板"))
            report = audit.run_all_checks(Path(novel))
            errors = [i for i in report["issues"] if i["severity"] == "error"]
            self.assertEqual(errors, [], f"干净小说不应有 error: {errors}")


if __name__ == "__main__":
    unittest.main()
