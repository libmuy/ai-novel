# -*- coding: utf-8 -*-
"""redline_stamp.py 单元测试——红线包 §八 上游指纹戳。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "01_小说通用工具"))
import redline_stamp as rs


EIGHT_2COL = (
    "## 八、刷新触发\n\n"
    "本包是蒸馏视图，下列文件变更时必须回头重核。\n\n"
    "| 权威来源变更 | 重核本包 |\n"
    "|---|---|\n"
    "| `01_设定/00_文风.md` | 第四节 |\n"
    "| `01_设定/00_禁用词表.md`（附注） | 第六节 |\n"
    "| **进卷**（每卷开写前） | 全部 |\n"
)


def _body(eight=EIGHT_2COL):
    return "# 测试 · 常驻红线包\n\n## 一、主角人设红线\n\n- 定性约束。\n\n" + eight


class TestRedlineStamp(unittest.TestCase):

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.nd = Path(self.td.name) / "novel"
        (self.nd / "01_设定").mkdir(parents=True)
        (self.nd / "01_设定" / "00_文风.md").write_text("# 文风\n初版。\n", encoding="utf-8")
        (self.nd / "01_设定" / "00_禁用词表.md").write_text("# 禁用词\n手电\n", encoding="utf-8")
        self.redline = self.nd / "01_设定" / "00_红线包.md"
        self.redline.write_text(_body(), encoding="utf-8")

    def tearDown(self):
        self.td.cleanup()

    def _check(self):
        return rs.check_stamps(self.nd)

    def test_check_before_stamp_reports_missing_column(self):
        problems = self._check()
        self.assertEqual(len(problems), 1)
        self.assertIn("列", problems[0])

    def test_write_then_check_clean(self):
        new_text, notes = rs.write_stamps(self.nd)
        self.redline.write_text(new_text, encoding="utf-8")
        self.assertIn(rs.STAMP_HEADER, new_text)
        self.assertEqual(self._check(), [])

    def test_write_only_touches_stamp_column(self):
        before = self.redline.read_text(encoding="utf-8")
        new_text, _ = rs.write_stamps(self.nd)
        # 非表格正文逐行未变
        b_lines = [l for l in before.splitlines() if not l.strip().startswith("|")]
        a_lines = [l for l in new_text.splitlines() if not l.strip().startswith("|")]
        self.assertEqual(b_lines, a_lines)
        # 第一列文字保留
        self.assertIn("`01_设定/00_文风.md`", new_text)
        self.assertIn("（附注）", new_text)
        self.assertIn("第四节", new_text)

    def test_no_file_row_gets_dash(self):
        new_text, _ = rs.write_stamps(self.nd)
        jinjuan = [l for l in new_text.splitlines() if "进卷" in l][0]
        self.assertTrue(jinjuan.rstrip().endswith(f"| {rs.NO_FILE} |"))

    def test_check_detects_upstream_drift(self):
        new_text, _ = rs.write_stamps(self.nd)
        self.redline.write_text(new_text, encoding="utf-8")
        (self.nd / "01_设定" / "00_文风.md").write_text("# 文风\n改了。\n", encoding="utf-8")
        problems = self._check()
        self.assertEqual(len(problems), 1)
        self.assertIn("00_文风.md", problems[0])
        self.assertIn("第四节", problems[0])

    def test_check_detects_missing_upstream(self):
        new_text, _ = rs.write_stamps(self.nd)
        self.redline.write_text(new_text, encoding="utf-8")
        (self.nd / "01_设定" / "00_禁用词表.md").unlink()
        problems = self._check()
        self.assertTrue(any("不存在" in p for p in problems))

    def test_section_eight_missing_raises(self):
        self.redline.write_text("# 测试\n\n## 一、只有这一节\n\n正文。\n", encoding="utf-8")
        with self.assertRaises(rs.RedlineError):
            rs.parse_table(self.redline.read_text(encoding="utf-8"))

    def test_repo_root_path_resolves_via_walkup(self):
        # novel/ 下无 00_通用模板，造一个真实仓库根让 walk-up 命中
        repo = Path(self.td.name)
        (repo / "00_通用模板" / "x").mkdir(parents=True)
        (repo / "00_通用模板" / "x" / "up.md").write_text("upstream\n", encoding="utf-8")
        eight = (EIGHT_2COL.rstrip()
                 + "\n| `00_通用模板/x/up.md` | 第七节 |\n")
        self.redline.write_text(_body(eight), encoding="utf-8")
        new_text, notes = rs.write_stamps(self.nd)
        self.assertFalse(any("缺失" in n for n in notes))
        self.assertIn(rs.value_fingerprint("upstream\n"), new_text)

    def test_idempotent_write(self):
        t1, _ = rs.write_stamps(self.nd)
        self.redline.write_text(t1, encoding="utf-8")
        t2, _ = rs.write_stamps(self.nd)
        self.assertEqual(t1, t2)


if __name__ == "__main__":
    unittest.main()
