# -*- coding: utf-8 -*-
"""progress_store.py 的单元测试：00_进度.json 的读写与 schema 校验。"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "01_小说通用工具"))
import progress_store as ps


class TestLoadSave(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_load_missing_returns_empty(self):
        self.assertEqual(ps.load(self.tmp), {})
        self.assertEqual(ps.statuses(self.tmp), {})

    def test_exists_and_legacy_exists(self):
        self.assertFalse(ps.exists(self.tmp))
        self.assertFalse(ps.legacy_exists(self.tmp))
        (self.tmp / "00_进度.md").write_text("旧文件", encoding="utf-8")
        self.assertTrue(ps.legacy_exists(self.tmp))
        self.assertFalse(ps.exists(self.tmp))

    def test_save_then_load_round_trip(self):
        entries = {
            "01_设定/00_小说概念.md": ps.Entry("定稿"),
            "03_规划/01_第01部/01_卷01/规划_卷01.md": ps.Entry("定稿", "2026-09-05"),
            "02_数据库/04_资源/": ps.Entry("待校验"),
        }
        ps.save(self.tmp, entries)
        loaded = ps.load(self.tmp)
        self.assertEqual(loaded, entries)
        self.assertTrue(ps.exists(self.tmp))

    def test_save_writes_stable_sorted_format(self):
        ps.save(self.tmp, {
            "03_规划/01_第01部/01_卷01/规划_卷01.md": ps.Entry("定稿"),
            "01_设定/00_小说概念.md": ps.Entry("定稿"),
        })
        text = ps.path(self.tmp).read_text(encoding="utf-8")
        obj = json.loads(text)
        self.assertEqual(obj["version"], 1)
        # key 排序：01_设定 应该排在 03_规划 前面
        keys = list(obj["files"].keys())
        self.assertEqual(keys, sorted(keys))
        self.assertTrue(text.endswith("\n"))

    def test_save_is_atomic_no_leftover_tmp(self):
        ps.save(self.tmp, {"01_设定/00_小说概念.md": ps.Entry("定稿")})
        self.assertFalse((self.tmp / "00_进度.json.tmp").exists())

    def test_statuses_returns_plain_dict(self):
        ps.save(self.tmp, {"01_设定/00_小说概念.md": ps.Entry("定稿")})
        self.assertEqual(ps.statuses(self.tmp), {"01_设定/00_小说概念.md": "定稿"})


class TestValidate(unittest.TestCase):
    def test_valid_minimal(self):
        entries = ps.validate({"version": 1, "files": {}})
        self.assertEqual(entries, {})

    def test_valid_with_date(self):
        entries = ps.validate({"version": 1, "files": {
            "10_正文/01_第01部/01_卷01/正文_卷01_章0001.md": {"status": "定稿", "date": "2026-09-05"},
        }})
        self.assertEqual(
            entries["10_正文/01_第01部/01_卷01/正文_卷01_章0001.md"],
            ps.Entry("定稿", "2026-09-05"))

    def test_rejects_non_dict_top_level(self):
        with self.assertRaises(ps.ProgressFormatError):
            ps.validate([])

    def test_rejects_unknown_top_level_field(self):
        with self.assertRaises(ps.ProgressFormatError):
            ps.validate({"version": 1, "files": {}, "note": "不行"})

    def test_rejects_wrong_version(self):
        with self.assertRaises(ps.ProgressFormatError):
            ps.validate({"version": 2, "files": {}})

    def test_rejects_files_not_dict(self):
        with self.assertRaises(ps.ProgressFormatError):
            ps.validate({"version": 1, "files": []})

    def test_rejects_bare_filename_key(self):
        with self.assertRaises(ps.ProgressFormatError):
            ps.validate({"version": 1, "files": {"文件.md": {"status": "定稿"}}})

    def test_rejects_non_canonical_prefix(self):
        with self.assertRaises(ps.ProgressFormatError):
            ps.validate({"version": 1, "files": {
                "05_工作区/02_状态/01_最新状态/00_同步状态.md": {"status": "定稿"}}})

    def test_rejects_absolute_path_key(self):
        with self.assertRaises(ps.ProgressFormatError):
            ps.validate({"version": 1, "files": {"/01_设定/x.md": {"status": "定稿"}}})

    def test_rejects_dotdot_key(self):
        with self.assertRaises(ps.ProgressFormatError):
            ps.validate({"version": 1, "files": {"01_设定/../x.md": {"status": "定稿"}}})

    def test_rejects_key_not_md_or_dir(self):
        with self.assertRaises(ps.ProgressFormatError):
            ps.validate({"version": 1, "files": {"01_设定/x.txt": {"status": "定稿"}}})

    def test_rejects_placeholder_key(self):
        for bad in (
            "03_规划/01_第01部/0N_卷0N/规划_卷0N.md",
            "03_规划/01_第01部/NN_卷NN/规划_卷NN.md",
            "03_规划/XX/规划.md",
        ):
            with self.assertRaises(ps.ProgressFormatError):
                ps.validate({"version": 1, "files": {bad: {"status": "定稿"}}})

    def test_accepts_directory_key(self):
        entries = ps.validate({"version": 1, "files": {
            "02_数据库/04_资源/": {"status": "待校验"}}})
        self.assertIn("02_数据库/04_资源/", entries)

    def test_rejects_unknown_status(self):
        with self.assertRaises(ps.ProgressFormatError):
            ps.validate({"version": 1, "files": {"01_设定/x.md": {"status": "待修改"}}})

    def test_rejects_extra_field_in_entry(self):
        with self.assertRaises(ps.ProgressFormatError):
            ps.validate({"version": 1, "files": {
                "01_设定/x.md": {"status": "定稿", "说明": "一段很长的叙事"}}})

    def test_rejects_bad_date_format(self):
        with self.assertRaises(ps.ProgressFormatError):
            ps.validate({"version": 1, "files": {
                "01_设定/x.md": {"status": "定稿", "date": "2026/09/05"}}})

    def test_rejects_entry_not_dict(self):
        with self.assertRaises(ps.ProgressFormatError):
            ps.validate({"version": 1, "files": {"01_设定/x.md": "定稿"}})


class TestLoadInvalidFile(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_load_bad_json_raises(self):
        (self.tmp / "00_进度.json").write_text("不是 JSON", encoding="utf-8")
        with self.assertRaises(ps.ProgressFormatError):
            ps.load(self.tmp)

    def test_load_bad_schema_raises(self):
        (self.tmp / "00_进度.json").write_text(
            json.dumps({"version": 1, "files": {"文件.md": {"status": "定稿"}}}),
            encoding="utf-8")
        with self.assertRaises(ps.ProgressFormatError):
            ps.load(self.tmp)


class TestLookupHelpers(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_normalize_key_from_absolute_path(self):
        p = self.tmp / "01_设定/00_小说概念.md"
        self.assertEqual(ps.normalize_key(self.tmp, p), "01_设定/00_小说概念.md")

    def test_normalize_key_from_relative_path(self):
        self.assertEqual(
            ps.normalize_key(self.tmp, Path("01_设定/00_小说概念.md")),
            "01_设定/00_小说概念.md")

    def test_status_of_exact_match_only(self):
        sts = {"10_正文/01_第01部/01_卷01/正文_卷01_章0001.md": "定稿"}
        self.assertEqual(
            ps.status_of(sts, self.tmp / "10_正文/01_第01部/01_卷01/正文_卷01_章0001.md", self.tmp),
            "定稿")
        # 只是文件名相同、目录不同——不该命中（精确匹配，不是后缀匹配）
        self.assertIsNone(
            ps.status_of(sts, self.tmp / "10_正文/别的部/别的卷/正文_卷01_章0001.md", self.tmp))

    def test_category_status_exact_for_directory_keyword(self):
        sts = {"02_数据库/04_资源/": "待校验"}
        self.assertEqual(ps.category_status(sts, "02_数据库/04_资源/"), "待校验")

    def test_category_status_suffix_for_file_keyword(self):
        sts = {"03_规划/01_第01部/01_卷01/规划_卷01.md": "定稿"}
        self.assertEqual(ps.category_status(sts, "规划_卷01.md"), "定稿")

    def test_category_status_none_when_absent(self):
        self.assertIsNone(ps.category_status({}, "02_数据库/04_资源/"))

    def test_is_at_least(self):
        self.assertTrue(ps.is_at_least("定稿", "定稿"))
        self.assertTrue(ps.is_at_least("定稿", "待校验"))
        self.assertFalse(ps.is_at_least("待校验", "定稿"))
        self.assertFalse(ps.is_at_least(None, "定稿"))
        self.assertFalse(ps.is_at_least("未知状态", "定稿"))


class TestRemove(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_remove_existing_key(self):
        ps.save(self.tmp, {
            "01_设定/00_小说概念.md": ps.Entry("定稿"),
            "10_正文/01_第01部/01_卷01/正文_卷01_章0001.md": ps.Entry("定稿"),
        })
        existed = ps.remove(self.tmp, "10_正文/01_第01部/01_卷01/正文_卷01_章0001.md")
        self.assertTrue(existed)
        self.assertEqual(ps.statuses(self.tmp), {"01_设定/00_小说概念.md": "定稿"})

    def test_remove_missing_key_is_noop(self):
        ps.save(self.tmp, {"01_设定/00_小说概念.md": ps.Entry("定稿")})
        existed = ps.remove(self.tmp, "10_正文/01_第01部/01_卷01/正文_卷01_章0001.md")
        self.assertFalse(existed)
        self.assertEqual(ps.statuses(self.tmp), {"01_设定/00_小说概念.md": "定稿"})

    def test_remove_from_missing_file_is_noop(self):
        existed = ps.remove(self.tmp, "01_设定/00_小说概念.md")
        self.assertFalse(existed)
        self.assertFalse(ps.exists(self.tmp))


class TestEmptyDocument(unittest.TestCase):
    def test_empty_document_is_valid_and_parses_to_empty(self):
        text = ps.empty_document()
        obj = json.loads(text)
        self.assertEqual(ps.validate(obj), {})
        self.assertTrue(text.endswith("\n"))


if __name__ == "__main__":
    unittest.main()
