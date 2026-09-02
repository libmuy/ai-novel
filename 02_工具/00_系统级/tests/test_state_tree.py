#!/usr/bin/env python3
"""state_tree.py 特征测试：锁住当前行为，后续各波按计划修改预期。"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import state_tree as st
from helpers import make_novel, write_table, StubLlm


class TestNumericMerge(unittest.TestCase):
    """test_numeric_merge: 基线 100，履历 -20 → 80；履历 55（无符号）→ 55"""

    def test_subtract(self):
        with tempfile.TemporaryDirectory() as td:
            novel = make_novel(td, baseline_records=[
                ["角色.苏砚", "内力值", "运算-数值", "100"],
            ], chapters={
                "03_第01部/03_卷01/03_章0001": [
                    ["角色.苏砚", "内力值", "运算-数值", "-20", "1", "2026-01-01", "-"],
                ],
            })
            bl = st.baseline_dir(novel)
            paths = st.iter_workspace_changelogs(novel)
            records, _ = st.fold_all(bl, paths)
            m = {r["value"] for r in records if r["object_id"] == "角色.苏砚" and r["field"] == "内力值"}
            self.assertEqual(m, {"80"})

    def test_absolute_set(self):
        with tempfile.TemporaryDirectory() as td:
            novel = make_novel(td, baseline_records=[
                ["角色.苏砚", "内力值", "运算-数值", "100"],
            ], chapters={
                "03_第01部/03_卷01/03_章0001": [
                    ["角色.苏砚", "内力值", "运算-数值", "55", "1", "2026-01-01", "-"],
                ],
            })
            bl = st.baseline_dir(novel)
            paths = st.iter_workspace_changelogs(novel)
            records, _ = st.fold_all(bl, paths)
            m = {r["value"] for r in records if r["object_id"] == "角色.苏砚" and r["field"] == "内力值"}
            self.assertEqual(m, {"55"})


class TestEnumMerge(unittest.TestCase):
    """test_enum_merge: 枚举直接替换"""

    def test_replace(self):
        with tempfile.TemporaryDirectory() as td:
            novel = make_novel(td, baseline_records=[
                ["角色.苏砚", "境界", "运算-枚举", "炼气一层"],
            ], chapters={
                "03_第01部/03_卷01/03_章0001": [
                    ["角色.苏砚", "境界", "运算-枚举", "炼气二层", "1", "2026-01-01", "-"],
                ],
            })
            bl = st.baseline_dir(novel)
            paths = st.iter_workspace_changelogs(novel)
            records, _ = st.fold_all(bl, paths)
            m = {r["value"] for r in records if r["object_id"] == "角色.苏砚" and r["field"] == "境界"}
            self.assertEqual(m, {"炼气二层"})


class TestListMerge(unittest.TestCase):
    """test_list_merge: +培元丹,-破草鞋 正确增删"""

    def test_add_remove(self):
        with tempfile.TemporaryDirectory() as td:
            novel = make_novel(td, baseline_records=[
                ["角色.苏砚", "持有物品", "运算-列表", "锈铁剑,破草鞋"],
            ], chapters={
                "03_第01部/03_卷01/03_章0001": [
                    ["角色.苏砚", "持有物品", "运算-列表", "+培元丹,-破草鞋", "1", "2026-01-01", "-"],
                ],
            })
            bl = st.baseline_dir(novel)
            paths = st.iter_workspace_changelogs(novel)
            records, _ = st.fold_all(bl, paths)
            m = {r["value"] for r in records if r["object_id"] == "角色.苏砚" and r["field"] == "持有物品"}
            self.assertEqual(m, {"锈铁剑,培元丹"})


class TestListNoPrefixRaises(unittest.TestCase):
    """test_list_no_prefix_raises: 无前缀元素抛 StateMergeError"""

    def test_no_prefix_raises(self):
        with tempfile.TemporaryDirectory() as td:
            novel = make_novel(td, baseline_records=[
                ["角色.苏砚", "持有物品", "运算-列表", "锈铁剑"],
            ], chapters={
                "03_第01部/03_卷01/03_章0001": [
                    ["角色.苏砚", "持有物品", "运算-列表", "培元丹", "1", "2026-01-01", "-"],
                ],
            })
            bl = st.baseline_dir(novel)
            paths = st.iter_workspace_changelogs(novel)
            with self.assertRaises(st.StateMergeError):
                st.fold_all(bl, paths)


class TestDescriptiveFirstTime(unittest.TestCase):
    """test_descriptive_first_time: 旧值为空时字面写入，不调 LLM"""

    def test_first_time_no_llm(self):
        with tempfile.TemporaryDirectory() as td:
            novel = make_novel(td, baseline_records=[
                ["角色.苏砚", "当前心境", "描述", "无"],
            ], chapters={
                "03_第01部/03_卷01/03_章0001": [
                    ["角色.苏砚", "当前心境", "描述", "愤怒", "1", "2026-01-01", "-"],
                ],
            })
            bl = st.baseline_dir(novel)
            paths = st.iter_workspace_changelogs(novel)
            stub = StubLlm()
            records, _ = st.fold_all(bl, paths, resolver=stub)
            m = {r["value"] for r in records if r["object_id"] == "角色.苏砚" and r["field"] == "当前心境"}
            self.assertEqual(m, {"愤怒"})
            self.assertEqual(stub.call_count, 0)


class TestDescriptiveMergeCallsLlmOnce(unittest.TestCase):
    """test_descriptive_merge_calls_llm_once: 多个描述字段变更只调一次 LLM"""

    def test_calls_once(self):
        with tempfile.TemporaryDirectory() as td:
            novel = make_novel(td, baseline_records=[
                ["角色.苏砚", "当前心境", "描述", "平静"],
                ["角色.苏砚", "身体状况", "描述", "健康"],
            ], chapters={
                "03_第01部/03_卷01/03_章0001": [
                    ["角色.苏砚", "当前心境", "描述", "愤怒", "1", "2026-01-01", "-"],
                    ["角色.苏砚", "身体状况", "描述", "左臂骨折", "1", "2026-01-01", "-"],
                ],
            })
            bl = st.baseline_dir(novel)
            paths = st.iter_workspace_changelogs(novel)
            stub = StubLlm()
            records, _ = st.fold_all(bl, paths, resolver=stub)
            # stub 把两个 pending 打包成一次调用
            self.assertEqual(stub.call_count, 1)


class TestReplayIdempotent(unittest.TestCase):
    """test_replay_idempotent: 同输入折两次，records_diff 为空"""

    def test_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            novel = make_novel(td, baseline_records=[
                ["角色.苏砚", "内力值", "运算-数值", "100"],
            ], chapters={
                "03_第01部/03_卷01/03_章0001": [
                    ["角色.苏砚", "内力值", "运算-数值", "-20", "1", "2026-01-01", "-"],
                ],
            })
            bl = st.baseline_dir(novel)
            paths = st.iter_workspace_changelogs(novel)
            r1, _ = st.fold_all(bl, paths)
            r2, _ = st.fold_all(bl, paths)
            diff = st.records_diff(r1, r2)
            self.assertEqual(diff, [])


class TestParseNumberGarbage(unittest.TestCase):
    """test_parse_number_garbage: 八十 / +2O 抛 StateMergeError"""

    def test_chinese_number(self):
        with self.assertRaises(st.StateMergeError):
            st.parse_number("八十", "角色.苏砚", "内力值")

    def test_letter_in_number(self):
        with self.assertRaises(st.StateMergeError):
            st.parse_number("+2O", "角色.苏砚", "内力值")


class TestPruneKeeps00Files(unittest.TestCase):
    """test_prune_keeps_00_files: 类目目录清空时 00_说明.md 必须仍在"""

    def test_keeps_00_files(self):
        with tempfile.TemporaryDirectory() as td:
            # 先写一个对象
            novel = make_novel(td, baseline_records=[
                ["角色.苏砚", "内力值", "运算-数值", "100"],
            ])
            bl = st.baseline_dir(novel)
            # 写入状态树
            st.write_state_tree(bl, [
                {"object_id": "角色.苏砚", "field": "内力值", "type": "运算-数值", "value": "100", "meta": {}},
            ])
            cat_dir = os.path.join(bl, "01_角色")
            # 手动创建 00_说明.md（write_state_tree 不创建它）
            with open(os.path.join(cat_dir, "00_说明.md"), "w") as f:
                f.write("# 说明\n")
            self.assertTrue(os.path.isfile(os.path.join(cat_dir, "00_说明.md")))

            # 现在写入空记录——prune 应删除角色文件但保留 00_说明.md
            st.write_state_tree(bl, [], prune=True)
            self.assertTrue(os.path.isfile(os.path.join(cat_dir, "00_说明.md")))


class TestWriteStateTreeAtomic(unittest.TestCase):
    """test_write_state_tree_atomic: 写完后目录内无 *.tmp.* 残留"""

    def test_no_tmp_residue(self):
        with tempfile.TemporaryDirectory() as td:
            novel = make_novel(td, baseline_records=[
                ["角色.苏砚", "内力值", "运算-数值", "100"],
            ])
            bl = st.baseline_dir(novel)
            st.write_state_tree(bl, [
                {"object_id": "角色.苏砚", "field": "内力值", "type": "运算-数值", "value": "100", "meta": {}},
            ])
            cat_dir = os.path.join(bl, "01_角色")
            tmp_files = [f for f in os.listdir(cat_dir) if ".tmp." in f]
            self.assertEqual(tmp_files, [])


class TestPipeInValue(unittest.TestCase):
    """test_pipe_in_value: 愤怒\\|不甘 能正确解析成一个值"""

    def test_pipe_escaped(self):
        with tempfile.TemporaryDirectory() as td:
            # 写一个含转义竖线的文件
            path = os.path.join(td, "test.md")
            with open(path, "w", encoding="utf-8") as f:
                f.write("| 对象ID | 字段 | 类型 | 值 |\n")
                f.write("| --- | --- | --- | --- |\n")
                f.write("| 角色.苏砚 | 当前心境 | 描述 | 愤怒\\|不甘 |\n")
            records = st.parse_md_table(path)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["value"], "愤怒|不甘")

    def test_pipe_unescaped_raises(self):
        """未转义的竖线导致列数不符 → 抛错"""
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "test.md")
            with open(path, "w", encoding="utf-8") as f:
                f.write("| 对象ID | 字段 | 类型 | 值 |\n")
                f.write("| --- | --- | --- | --- |\n")
                f.write("| 角色.苏砚 | 当前心境 | 描述 | 愤怒|不甘 |\n")
            with self.assertRaises(st.StateMergeError):
                st.parse_md_table(path, strict=True)


class TestWrongColumnCountRaises(unittest.TestCase):
    """test_wrong_column_count_raises: 少于 4 列抛 StateMergeError"""

    def test_three_columns(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "test.md")
            with open(path, "w", encoding="utf-8") as f:
                f.write("| 对象ID | 字段 | 类型 |\n")
                f.write("| --- | --- | --- |\n")
                f.write("| 角色.苏砚 | 内力值 | 运算-数值 |\n")
            with self.assertRaises(st.StateMergeError):
                st.parse_md_table(path, strict=True)


class TestUnknownTypeRaises(unittest.TestCase):
    """test_unknown_type_raises: 全角破折号类型抛 StateMergeError"""

    def test_fullwidth_dash(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "test.md")
            with open(path, "w", encoding="utf-8") as f:
                f.write("| 对象ID | 字段 | 类型 | 值 |\n")
                f.write("| --- | --- | --- | --- |\n")
                f.write("| 角色.苏砚 | 境界 | 运算—枚举 | 炼气二层 |\n")
            with self.assertRaises(st.StateMergeError):
                st.parse_md_table(path, strict=True)


class TestChapterOpeners(unittest.TestCase):
    """W4.1：--write-chapter-openers 生成 00_开篇状态.md，按细纲「## 出场对象」裁剪"""

    def _novel(self, td):
        novel = make_novel(td, baseline_records=[
            ["角色.苏砚", "境界", "运算-枚举", "凡人"],
            ["角色.柳禾", "身体状况", "描述", "肺痨晚期"],
            ["势力.黑石会", "与主角互动状态", "运算-枚举", "敌对"],
        ], chapters={
            "03_第01部/03_卷01/03_章0001": [
                ["角色.苏砚", "境界", "运算-枚举", "炼气一层", "1", "2026-01-01", "修改"],
            ],
            "03_第01部/03_卷01/04_章0002": [],
        })
        # 主角档案（供 protagonist_state_id）
        os.makedirs(os.path.join(novel, "01_设定"), exist_ok=True)
        with open(os.path.join(novel, "01_设定", "00_主角档案.md"), "w", encoding="utf-8") as f:
            f.write("# 主角\n\n| 字段 | 必填 | 内容 |\n|---|---|---|\n| 姓名 | (必) | 苏砚 |\n")
        # 章0002 细纲：只声明苏砚 + 黑石会
        pd = os.path.join(novel, "03_规划", "01_第01部", "01_卷01")
        os.makedirs(pd, exist_ok=True)
        with open(os.path.join(pd, "规划_卷01_章0002.md"), "w", encoding="utf-8") as f:
            f.write("# 细纲\n\n## 出场对象\n\n| 对象ID | 出场方式 |\n|---|---|\n"
                    "| `@主角` | 登场 |\n| `@势力.[黑石会]` | 提及 |\n")
        return novel

    def test_openers_written_and_filtered(self):
        import build_state_snapshot as bss
        with tempfile.TemporaryDirectory() as td:
            novel = self._novel(td)
            written = bss.write_chapter_openers(novel, verbose=False)
            self.assertEqual(len(written), 2)

            # 章0001：无细纲 → 全量，且为「基线」（早于本章无履历）
            o1 = open(os.path.join(novel, "05_工作区/03_第01部/03_卷01/03_章0001/02_状态/00_开篇状态.md")).read()
            self.assertIn("角色.苏砚 | 境界 | 运算-枚举 | 凡人", o1)   # 基线值，未折叠本章
            self.assertIn("未找到单章细纲", o1)

            # 章0002：有细纲，只留 苏砚 + 黑石会；苏砚境界已折叠章0001 → 炼气一层
            o2 = open(os.path.join(novel, "05_工作区/03_第01部/03_卷01/04_章0002/02_状态/00_开篇状态.md")).read()
            self.assertIn("角色.苏砚 | 境界 | 运算-枚举 | 炼气一层", o2)
            self.assertNotIn("角色.柳禾", o2)      # 被出场对象清单裁掉
            self.assertIn("势力.黑石会", o2)

    def test_cast_parsing(self):
        with tempfile.TemporaryDirectory() as td:
            novel = self._novel(td)
            plan = os.path.join(novel, "03_规划/01_第01部/01_卷01/规划_卷01_章0002.md")
            cast = st.parse_chapter_cast(plan, "角色.苏砚")
            self.assertEqual(cast, {"角色.苏砚", "势力.黑石会"})


class TestRelationId(unittest.TestCase):
    """W5：关系对象 ID `关系.<甲>&<乙>` 规范。"""

    def test_normalize_sorts_endpoints(self):
        self.assertEqual(st.normalize_relation_id("关系.苏砚&柳禾"), "关系.柳禾&苏砚")
        self.assertEqual(st.normalize_relation_id("关系.柳禾&苏砚"), "关系.柳禾&苏砚")
        # 裸形式（无前缀）也接受
        self.assertEqual(st.normalize_relation_id("苏砚&柳禾"), "关系.柳禾&苏砚")

    def test_split_rejects_malformed(self):
        for bad in ("关系.甲乙", "关系.甲&乙&丙", "关系.&乙", "关系.甲&", "关系.甲&甲"):
            with self.assertRaises(st.RelationIdError):
                st.split_relation_id(bad)

    def test_split_ok(self):
        self.assertEqual(st.split_relation_id("关系.柳禾&苏砚"), ("柳禾", "苏砚"))

    def test_cast_contains_relation_one_end(self):
        cast = {"角色.苏砚", "势力.黑石会"}
        # 一端在场即覆盖
        self.assertTrue(st.cast_contains(cast, "关系.柳禾&苏砚"))
        # 两端都不在场
        self.assertFalse(st.cast_contains(cast, "关系.周莽&柳禾"))
        # 普通对象仍是直接 in
        self.assertTrue(st.cast_contains(cast, "角色.苏砚"))
        self.assertFalse(st.cast_contains(cast, "角色.柳禾"))


class TestHoldingsReverseIndex(unittest.TestCase):
    """W5：write_state_tree 生成 00_持有物品反查.md 派生视图（manifest=True 时）。"""

    def test_reverse_index_generated(self):
        with tempfile.TemporaryDirectory() as td:
            latest = os.path.join(td, "01_最新状态")
            st.write_state_tree(latest, [
                {"object_id": "物品.矿钉", "field": "持有者", "type": "运算-枚举", "value": "@角色.[苏砚]"},
                {"object_id": "物品.古玉", "field": "持有者", "type": "运算-枚举", "value": "角色.苏砚"},
                {"object_id": "物品.骨傀", "field": "持有者", "type": "运算-枚举", "value": "无人"},
            ], manifest=True)
            rev = open(os.path.join(latest, st.HOLDINGS_REVERSE_FILENAME), encoding="utf-8").read()
            self.assertIn("| 苏砚 | 古玉、矿钉 |", rev)
            self.assertIn("无人持有", rev)
            # 派生文件不被 load_state_tree 当成状态
            recs = st.load_state_tree(latest)
            self.assertTrue(all(not r["object_id"].startswith("持有者") for r in recs))


if __name__ == "__main__":
    unittest.main()
