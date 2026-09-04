# -*- coding: utf-8 -*-
"""foreshadow_schedule.py 与 audit/rules/foreshadow_schedule.py 的单元测试套件。

测试 ForeshadowScheduleRule 及其辅助函数 _tables, _col, _points。
"""
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "01_小说通用工具"))
from audit.context import AuditContext
from audit.rules.foreshadow_schedule import ForeshadowScheduleRule, _tables, _col, _points, _cells


def _write(path: Path, text: str):
    """辅助方法：创建并写入文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def build_novel_fixture(temp_dir: Path, **opts) -> Path:
    """构建最小可行的小说目录结构。

    参数：
    - volume_count (int): 创建多少卷，默认 1
    - chapters_in_volume (dict): {卷号: [章号列表]}，定义节拍表
    - ledger_bury (str): 新埋表内容（含表头）
    - ledger_progress (str): 推进表内容（含表头）
    - ledger_recover (str): 回收表内容（含表头）
    - outlines (dict): {(卷, 章): 细纲内容} 要创建的单章细纲
    - manuscripts (set): {(卷, 章)} 要创建的正文文件

    返回 novel_dir 路径。
    """
    novel_dir = temp_dir / "小说测试"
    novel_dir.mkdir(exist_ok=True)

    # 如果给了 chapters_in_volume，就创建对应的节拍表
    chapters_in_volume = opts.get("chapters_in_volume", {1: [1]})
    for vol, chapters in chapters_in_volume.items():
        if not chapters:
            continue
        beat_table = "| 章序 | 节拍 |\n|---|---|\n"
        for ch in chapters:
            beat_table += f"| 第{ch:02d}章 | 节拍 |\n"
        _write(novel_dir / f"03_规划/01_第01部/{vol:02d}_卷{vol:02d}/规划_卷{vol:02d}.md", beat_table)

    # 伏笔册（只创建在第一卷，新埋表 + 推进表 + 回收表）
    if chapters_in_volume:
        first_vol = min(chapters_in_volume.keys())
        ledger = ""
        if opts.get("ledger_bury"):
            ledger += opts["ledger_bury"] + "\n\n"
        if opts.get("ledger_progress"):
            ledger += opts["ledger_progress"] + "\n\n"
        if opts.get("ledger_recover"):
            ledger += opts["ledger_recover"] + "\n\n"
        if ledger:
            _write(novel_dir / f"03_规划/01_第01部/{first_vol:02d}_卷{first_vol:02d}/00_伏笔册_卷{first_vol:02d}.md", ledger)

    # 单章细纲
    outlines = opts.get("outlines", {})
    for (vol, ch), content in outlines.items():
        _write(novel_dir / f"03_规划/01_第01部/{vol:02d}_卷{vol:02d}/规划_卷{vol:02d}_章{ch:04d}.md", content)

    # 正文
    manuscripts = opts.get("manuscripts", set())
    for vol, ch in manuscripts:
        _write(novel_dir / f"10_正文/01_第01部/{vol:02d}_卷{vol:02d}/章{ch:04d}.md", f"# 第{ch}章\n测试正文\n")

    return novel_dir


class TestTableParsing(unittest.TestCase):
    """_tables 与 _cells 的单元测试。"""

    def test_tables_preserves_header_across_separator(self):
        """分隔行也以 | 开头，若当成表结束会让整张表解析错位。

        _tables 必须跳过分隔行，不能把它当成表结束。否则表头会丢掉，
        第一条数据行被当成表头，整张表解析错位。这是第一版规则的真实 bug。
        """
        text = """| 伏笔ID | 伏笔名称 |
| :--- | :--- |
| FH-001 | 测试伏笔 |
"""
        results = list(_tables(text))
        self.assertEqual(len(results), 1)
        header, rows = results[0]

        # 表头应该是正确的
        self.assertIn("伏笔ID", header)
        self.assertIn("伏笔名称", header)

        # 数据行应该包含 FH-001
        self.assertEqual(len(rows), 1)
        self.assertIn("FH-001", rows[0])

    def test_tables_splits_by_blank_line(self):
        """_tables 以空行或非表格行切分连续表块。

        两张不同的表，用空行隔开，应该分别解析，返回两个 (header, rows) 对。
        """
        text = """| 伏笔ID | 埋设位置 |
| :--- | :--- |
| FH-001 | 卷01章0001 |

| 伏笔ID | 执行状态 |
| :--- | :--- |
| FH-001 | 已回收 |
"""
        results = list(_tables(text))
        self.assertEqual(len(results), 2)

        header1, rows1 = results[0]
        self.assertIn("埋设位置", header1)

        header2, rows2 = results[1]
        self.assertIn("执行状态", header2)


class TestColFunction(unittest.TestCase):
    """_col 的单元测试。"""

    def test_col_matches_by_substring(self):
        """_col 按子串匹配列名。

        推进表与回收表都含「来源埋设位置」，搜「埋设位置」会匹配两者。
        表分类必须先检「执行状态」/「推进」来区分，不能只靠「埋设位置」。
        这是真实数据 FH-068 的形状：第一版规则在此误报 FS005。
        """
        header = ["伏笔ID", "伏笔名称", "来源埋设位置", "本卷回收章节"]

        # 搜「埋设位置」会匹配「来源埋设位置」
        idx = _col(header, "埋设位置")
        self.assertEqual(idx, 2)
        self.assertEqual(header[idx], "来源埋设位置")

    def test_col_returns_none_for_missing(self):
        """_col 找不到返回 None。"""
        header = ["伏笔ID", "伏笔名称"]
        idx = _col(header, "不存在的列", "也不存在")
        self.assertIsNone(idx)

    def test_col_tries_all_names(self):
        """_col 尝试所有给定的名字，返回第一个匹配的列。"""
        header = ["伏笔ID", "伏笔名称", "拟回收卷章"]
        # 搜 [不存在, 拟回收] 应该匹配 拟回收卷章
        idx = _col(header, "不存在", "拟回收")
        self.assertEqual(idx, 2)


class TestPointsFunction(unittest.TestCase):
    """_points 的单元测试。"""

    def test_points_volume_and_chapter(self):
        """_points('卷01章0001') → [(1, 1)]"""
        result = _points("卷01章0001")
        self.assertEqual(result, [(1, 1)])

    def test_points_volume_only(self):
        """_points('卷02') → [(2, None)]"""
        result = _points("卷02")
        self.assertEqual(result, [(2, None)])

    def test_points_volume_range(self):
        """_points('卷03~卷04') → [(3, None), (4, None)]

        调用者取最后一个作为最晚的点。
        """
        result = _points("卷03~卷04")
        self.assertEqual(result, [(3, None), (4, None)])

    def test_points_inherited_volume(self):
        """_points('卷01章0001（发热）/章0003', 1) → 包含 (1,1) 和 (1,3)

        省略卷号的「章0003」继承前面的卷号「卷01」。
        """
        result = _points("卷01章0001（发热）/章0003")
        self.assertIn((1, 1), result)
        self.assertIn((1, 3), result)

    def test_points_default_volume(self):
        """_points('章0005', default_volume=2) 用默认卷号解析。"""
        result = _points("章0005", default_volume=2)
        self.assertEqual(result, [(2, 5)])


class TestTableClassification(unittest.TestCase):
    """表分类与规则应用的端到端测试。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_fh_in_both_bury_and_progress_tables(self):
        """FH 同时出现在新埋表和推进表时，不应误报 FS005。

        真实数据里 FH-068 的形状：第一版规则在此误报。
        新埋表记录「拟回收卷/章 = 卷03」，推进表的「拟回收卷/章」是空的，
        后者不应覆盖前者，也不应因为推进表的拟回收为空而报 FS005。
        """
        bury_table = """| 伏笔ID | 伏笔名称 | 埋设位置 | 拟回收卷/章 | 状态 |
| :--- | :--- | :--- | :--- | :--- |
| FH-068 | 测试伏笔 | 卷01章0001 | 卷03 | 活跃 |
"""
        progress_table = """| 伏笔ID | 伏笔名称 | 来源埋设位置 | 本卷推进章节 | 状态 |
| :--- | :--- | :--- | :--- | :--- |
| FH-068 | 测试伏笔 | 卷01章0001 | 卷02章0003 | 推进 |
"""
        novel_dir = build_novel_fixture(
            self.tmp,
            chapters_in_volume={1: [1, 2], 2: [1, 2], 3: [1, 2]},
            ledger_bury=bury_table,
            ledger_progress=progress_table,
            outlines={(1, 1): "# 细纲\n@伏笔.FH-068\n"},
            manuscripts=set()
        )

        context = AuditContext(novel_dir)
        rule = ForeshadowScheduleRule()
        findings = rule.run(context)

        # 不应该有 FS005（推进表的空值不应覆盖新埋表的计划）
        codes = [f.code for f in findings]
        self.assertNotIn("FS005", codes)


class TestFS003OverdueBury(unittest.TestCase):
    """FS003 埋设逾期的单元测试。

    FS003 条件：伏笔册说埋在某章，那章正文已写完，
    但该章细纲里却找不到这个 FH。
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_fs003_fires_when_manuscript_exists_but_outline_missing_fh(self):
        """正文已落位但细纲没写伏笔 → FS003 火。"""
        bury_table = """| 伏笔ID | 伏笔名称 | 埋设位置 | 拟回收卷/章 | 状态 |
| :--- | :--- | :--- | :--- | :--- |
| FH-001 | 测试伏笔 | 卷01章0001 | 卷02 | 活跃 |
"""
        # 正文存在，但细纲没有 FH-001
        novel_dir = build_novel_fixture(
            self.tmp,
            chapters_in_volume={1: [1, 2], 2: [1, 2]},
            ledger_bury=bury_table,
            outlines={(1, 1): "# 细纲\n没有伏笔\n"},
            manuscripts={(1, 1)}  # 正文已写
        )

        context = AuditContext(novel_dir)
        rule = ForeshadowScheduleRule()
        findings = rule.run(context)

        codes = [f.code for f in findings]
        self.assertIn("FS003", codes)

        # 找到 FS003 记录
        fs003 = [f for f in findings if f.code == "FS003"][0]
        self.assertEqual(len(fs003.locations), 1)
        self.assertIn("FH-001", fs003.locations[0])

    def test_fs003_does_not_fire_when_outline_contains_fh(self):
        """细纲里有伏笔 → 不报 FS003。"""
        bury_table = """| 伏笔ID | 伏笔名称 | 埋设位置 | 拟回收卷/章 | 状态 |
| :--- | :--- | :--- | :--- | :--- |
| FH-001 | 测试伏笔 | 卷01章0001 | 卷02 | 活跃 |
"""
        novel_dir = build_novel_fixture(
            self.tmp,
            chapters_in_volume={1: [1, 2], 2: [1, 2]},
            ledger_bury=bury_table,
            outlines={(1, 1): "# 细纲\n@伏笔.FH-001\n"},
            manuscripts={(1, 1)}
        )

        context = AuditContext(novel_dir)
        rule = ForeshadowScheduleRule()
        findings = rule.run(context)

        codes = [f.code for f in findings]
        self.assertNotIn("FS003", codes)

    def test_fs003_does_not_fire_when_manuscript_not_written(self):
        """埋设章的正文还没写 → 不报 FS003（不逾期）。"""
        bury_table = """| 伏笔ID | 伏笔名称 | 埋设位置 | 拟回收卷/章 | 状态 |
| :--- | :--- | :--- | :--- | :--- |
| FH-001 | 测试伏笔 | 卷01章0001 | 卷02 | 活跃 |
"""
        novel_dir = build_novel_fixture(
            self.tmp,
            chapters_in_volume={1: [1, 2], 2: [1, 2]},
            ledger_bury=bury_table,
            outlines={(1, 1): "# 细纲\n没有伏笔\n"},
            manuscripts=set()  # 正文未写
        )

        context = AuditContext(novel_dir)
        rule = ForeshadowScheduleRule()
        findings = rule.run(context)

        codes = [f.code for f in findings]
        self.assertNotIn("FS003", codes)


class TestFS004OverdueRecover(unittest.TestCase):
    """FS004 回收逾期的单元测试。

    FS004 条件：拟回收的卷/章已经写完，卷册回收表里
    该 FH 的执行状态不含「已回收」。
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_fs004_fires_when_planned_chapter_written_no_recovery_entry(self):
        """拟回收章已写，但回收表无登记 → FS004 火。"""
        bury_table = """| 伏笔ID | 伏笔名称 | 埋设位置 | 拟回收卷/章 | 状态 |
| :--- | :--- | :--- | :--- | :--- |
| FH-001 | 测试伏笔 | 卷01章0001 | 卷01章0002 | 活跃 |
"""
        # 没有回收表，所以没有已回收记录
        novel_dir = build_novel_fixture(
            self.tmp,
            chapters_in_volume={1: [1, 2]},
            ledger_bury=bury_table,
            outlines={(1, 1): "# 细纲\n@伏笔.FH-001\n"},
            manuscripts={(1, 1), (1, 2)}  # 包括回收章
        )

        context = AuditContext(novel_dir)
        rule = ForeshadowScheduleRule()
        findings = rule.run(context)

        codes = [f.code for f in findings]
        self.assertIn("FS004", codes)

    def test_fs004_does_not_fire_when_recovery_status_contains_recovered(self):
        """回收表标记「已回收」→ 不报 FS004。"""
        bury_table = """| 伏笔ID | 伏笔名称 | 埋设位置 | 拟回收卷/章 | 状态 |
| :--- | :--- | :--- | :--- | :--- |
| FH-001 | 测试伏笔 | 卷01章0001 | 卷01章0002 | 活跃 |
"""
        recover_table = """| 伏笔ID | 伏笔名称 | 来源埋设位置 | 本卷回收章节 | 执行状态 |
| :--- | :--- | :--- | :--- | :--- |
| FH-001 | 测试伏笔 | 卷01章0001 | 卷01章0002 | 已回收 |
"""
        novel_dir = build_novel_fixture(
            self.tmp,
            chapters_in_volume={1: [1, 2]},
            ledger_bury=bury_table,
            ledger_recover=recover_table,
            outlines={(1, 1): "# 细纲\n@伏笔.FH-001\n"},
            manuscripts={(1, 1), (1, 2)}
        )

        context = AuditContext(novel_dir)
        rule = ForeshadowScheduleRule()
        findings = rule.run(context)

        codes = [f.code for f in findings]
        self.assertNotIn("FS004", codes)

    def test_fs004_does_not_fire_when_planned_chapter_not_written(self):
        """拟回收章还没写 → 不报 FS004（不逾期）。"""
        bury_table = """| 伏笔ID | 伏笔名称 | 埋设位置 | 拟回收卷/章 | 状态 |
| :--- | :--- | :--- | :--- | :--- |
| FH-001 | 测试伏笔 | 卷01章0001 | 卷01章0002 | 活跃 |
"""
        novel_dir = build_novel_fixture(
            self.tmp,
            chapters_in_volume={1: [1, 2]},
            ledger_bury=bury_table,
            outlines={(1, 1): "# 细纲\n@伏笔.FH-001\n"},
            manuscripts={(1, 1)}  # 只有埋设章，没有回收章
        )

        context = AuditContext(novel_dir)
        rule = ForeshadowScheduleRule()
        findings = rule.run(context)

        codes = [f.code for f in findings]
        self.assertNotIn("FS004", codes)

    def test_fs004_volume_granularity_last_chapter_missing(self):
        """卷粒度：卷末章缺失时不报 FS004。

        拟回收 = 卷01（整卷），卷 01 的节拍表标记最后一章是 01 章，
        但该章未写 → 不报 FS004（卷未完成）。
        """
        bury_table = """| 伏笔ID | 伏笔名称 | 埋设位置 | 拟回收卷/章 | 状态 |
| :--- | :--- | :--- | :--- | :--- |
| FH-001 | 测试伏笔 | 卷01章0001 | 卷01 | 活跃 |
"""
        novel_dir = build_novel_fixture(
            self.tmp,
            chapters_in_volume={1: [1, 2]},  # 卷 01 末章是 02
            ledger_bury=bury_table,
            outlines={(1, 1): "# 细纲\n@伏笔.FH-001\n"},
            manuscripts={(1, 1)}  # 只有 01 章，没有末章 02
        )

        context = AuditContext(novel_dir)
        rule = ForeshadowScheduleRule()
        findings = rule.run(context)

        codes = [f.code for f in findings]
        self.assertNotIn("FS004", codes)

    def test_fs004_volume_granularity_last_chapter_present(self):
        """卷粒度：卷末章存在时报 FS004。

        拟回收 = 卷01（整卷），卷 01 的节拍表标记最后一章是 02 章，
        该章已写 → 报 FS004（卷已完成但未登记回收）。
        """
        bury_table = """| 伏笔ID | 伏笔名称 | 埋设位置 | 拟回收卷/章 | 状态 |
| :--- | :--- | :--- | :--- | :--- |
| FH-001 | 测试伏笔 | 卷01章0001 | 卷01 | 活跃 |
"""
        novel_dir = build_novel_fixture(
            self.tmp,
            chapters_in_volume={1: [1, 2]},  # 卷 01 末章是 02
            ledger_bury=bury_table,
            outlines={(1, 1): "# 细纲\n@伏笔.FH-001\n"},
            manuscripts={(1, 1), (1, 2)}  # 包括末章 02
        )

        context = AuditContext(novel_dir)
        rule = ForeshadowScheduleRule()
        findings = rule.run(context)

        codes = [f.code for f in findings]
        self.assertIn("FS004", codes)

    def test_fs004_volume_without_beat_table_not_overdue(self):
        """卷无节拍表（末章未知）→ 不报 FS004。

        拟回收 = 卷02，但卷 02 没有对应的规划_卷02.md，所以末章数无法确定。
        无法判断卷是否已完成 → 不报逾期。
        """
        bury_table = """| 伏笔ID | 伏笔名称 | 埋设位置 | 拟回收卷/章 | 状态 |
| :--- | :--- | :--- | :--- | :--- |
| FH-001 | 测试伏笔 | 卷01章0001 | 卷02 | 活跃 |
"""
        novel_dir = build_novel_fixture(
            self.tmp,
            chapters_in_volume={1: [1]},  # 只有卷 01，没有卷 02
            ledger_bury=bury_table,
            outlines={(1, 1): "# 细纲\n@伏笔.FH-001\n"},
            manuscripts={(1, 1)}
        )

        context = AuditContext(novel_dir)
        rule = ForeshadowScheduleRule()
        findings = rule.run(context)

        codes = [f.code for f in findings]
        self.assertNotIn("FS004", codes)


class TestFS005NoRecoveryPlan(unittest.TestCase):
    """FS005 无回收计划的单元测试。

    FS005 条件：本卷新埋的 FH，「拟回收卷/章」空着。
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_fs005_fires_when_plan_is_empty(self):
        """拟回收卷/章为空（—、-、无、待定 等）→ FS005 火。"""
        bury_table = """| 伏笔ID | 伏笔名称 | 埋设位置 | 拟回收卷/章 | 状态 |
| :--- | :--- | :--- | :--- | :--- |
| FH-001 | 测试伏笔 | 卷01章0001 | — | 活跃 |
"""
        novel_dir = build_novel_fixture(
            self.tmp,
            chapters_in_volume={1: [1]},
            ledger_bury=bury_table,
            outlines={(1, 1): "# 细纲\n@伏笔.FH-001\n"},
            manuscripts=set()
        )

        context = AuditContext(novel_dir)
        rule = ForeshadowScheduleRule()
        findings = rule.run(context)

        codes = [f.code for f in findings]
        self.assertIn("FS005", codes)

    def test_fs005_does_not_fire_when_plan_exists(self):
        """拟回收卷/章有值 → 不报 FS005。"""
        bury_table = """| 伏笔ID | 伏笔名称 | 埋设位置 | 拟回收卷/章 | 状态 |
| :--- | :--- | :--- | :--- | :--- |
| FH-001 | 测试伏笔 | 卷01章0001 | 卷02 | 活跃 |
"""
        novel_dir = build_novel_fixture(
            self.tmp,
            chapters_in_volume={1: [1], 2: [1]},
            ledger_bury=bury_table,
            outlines={(1, 1): "# 细纲\n@伏笔.FH-001\n"},
            manuscripts=set()
        )

        context = AuditContext(novel_dir)
        rule = ForeshadowScheduleRule()
        findings = rule.run(context)

        codes = [f.code for f in findings]
        self.assertNotIn("FS005", codes)


class TestConsistentCase(unittest.TestCase):
    """完全一致的伏笔册应产生零对账项。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_fully_consistent_ledger_produces_zero_findings(self):
        """完整的一致情况：埋设、推进、回收都在位 → 零发现。"""
        bury_table = """| 伏笔ID | 伏笔名称 | 埋设位置 | 拟回收卷/章 | 状态 |
| :--- | :--- | :--- | :--- | :--- |
| FH-001 | 测试伏笔A | 卷01章0001 | 卷02章0001 | 活跃 |
| FH-002 | 测试伏笔B | 卷01章0002 | 卷01章0003 | 活跃 |
"""
        recover_table = """| 伏笔ID | 伏笔名称 | 来源埋设位置 | 本卷回收章节 | 执行状态 |
| :--- | :--- | :--- | :--- | :--- |
| FH-002 | 测试伏笔B | 卷01章0002 | 卷01章0003 | 已回收 |
"""
        novel_dir = build_novel_fixture(
            self.tmp,
            chapters_in_volume={1: [1, 2, 3], 2: [1]},
            ledger_bury=bury_table,
            ledger_recover=recover_table,
            outlines={
                (1, 1): "# 细纲\n@伏笔.FH-001\n",
                (1, 2): "# 细纲\n@伏笔.FH-002\n",
            },
            manuscripts=set()  # 没写任何正文，所以都不逾期
        )

        context = AuditContext(novel_dir)
        rule = ForeshadowScheduleRule()
        findings = rule.run(context)

        self.assertEqual(len(findings), 0)


class TestFindingShape(unittest.TestCase):
    """Finding 结构的单元测试。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_findings_grouped_by_code_with_locations(self):
        """多个 FH 违反同一条规则时，grouped 成一个 Finding，locations 列表包含所有违例。"""
        bury_table = """| 伏笔ID | 伏笔名称 | 埋设位置 | 拟回收卷/章 | 状态 |
| :--- | :--- | :--- | :--- | :--- |
| FH-001 | 伏笔A | 卷01章0001 | — | 活跃 |
| FH-002 | 伏笔B | 卷01章0001 | — | 活跃 |
"""
        novel_dir = build_novel_fixture(
            self.tmp,
            chapters_in_volume={1: [1]},
            ledger_bury=bury_table,
            outlines={(1, 1): "# 细纲\n@伏笔.FH-001\n@伏笔.FH-002\n"},
            manuscripts=set()
        )

        context = AuditContext(novel_dir)
        rule = ForeshadowScheduleRule()
        findings = rule.run(context)

        # 应该有一个 FS005 Finding
        fs005_findings = [f for f in findings if f.code == "FS005"]
        self.assertEqual(len(fs005_findings), 1)

        # 该 Finding 的 locations 应该包含两个违例
        fs005 = fs005_findings[0]
        self.assertEqual(len(fs005.locations), 2)
        self.assertTrue(any("FH-001" in loc for loc in fs005.locations))
        self.assertTrue(any("FH-002" in loc for loc in fs005.locations))

        # 检查 severity 是 WARNING
        self.assertEqual(str(fs005.severity).lower(), "warning")


if __name__ == "__main__":
    unittest.main()
