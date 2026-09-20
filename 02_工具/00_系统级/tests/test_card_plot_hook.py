#!/usr/bin/env python3
"""
人物卡设定层边界（CARDPLOT001/002）与卷纲钩子节奏（HOOK001~005）审计规则回归测试。
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "01_小说通用工具"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from audit import AuditContext
from audit.rules.card_plot import CardPlotRule
from audit.rules.hook_rhythm import HookRhythmRule
from helpers import make_novel

_PROTAGONIST = "# 测试 · 主角人设卡 · 甲某\n\n| 字段 | 必填 | 内容 |\n|---|---|---|\n| 姓名 | (必) | 甲某 |\n"


class _Base(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.novel_dir = Path(make_novel(self.td.name))
        self._write("01_设定/00_主角档案.md", _PROTAGONIST)

    def tearDown(self):
        self.td.cleanup()

    def _write(self, rel, body):
        p = self.novel_dir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")


class TestCardPlot(_Base):
    def _run(self, name, body):
        self._write(f"02_数据库/07_人物/07_人物_{name}.md", body)
        return {f.code: f for f in CardPlotRule().run(AuditContext(self.novel_dir))}

    def test_removed_fields_flagged(self):
        found = self._run("乙某", "# 人物卡 · 乙某\n\n| 当前进度 | 攒了三年 |\n| 主角对其的影响 | 甲某给了他一本书 |\n")
        self.assertIn("CARDPLOT001", found)
        self.assertEqual(len(found["CARDPLOT001"].locations), 2)

    def test_quantity_and_count_in_qualitative_field(self):
        found = self._run("乙某", "# 人物卡 · 乙某\n\n| 高光时刻设计 | 用二十三枚灵石买通散修 |\n| 预计退场方式 | 死亡。首次为主角挡刀 |\n")
        self.assertIn("CARDPLOT002", found)
        self.assertEqual(len(found["CARDPLOT002"].locations), 2)

    def test_protagonist_agent_from_dossier_not_hardcoded(self):
        found = self._run("乙某", "# 人物卡 · 乙某\n\n| 预计退场方式 | 死亡，被甲某反制 |\n")
        self.assertIn("CARDPLOT002", found)

    def test_relation_shape_passive_protagonist_is_ok(self):
        found = self._run("乙某", "# 人物卡 · 乙某\n\n| 关系演变轨迹 | 师父 → 被甲某保护 → 精神传承 → 诀别 |\n")
        self.assertNotIn("CARDPLOT002", found)

    def test_owner_name_digits_not_counted(self):
        found = self._run("三斤", "# 人物卡 · 三斤\n\n| 高光时刻设计 | 三斤在关键时刻选择自保之外的路 |\n")
        self.assertNotIn("CARDPLOT002", found)

    def test_qualitative_text_clean(self):
        found = self._run("乙某", "# 人物卡 · 乙某\n\n| 高光时刻设计 | 因被信任而做出违背自保的选择 |\n| 预计退场方式 | 牺牲，苍凉 |\n")
        self.assertNotIn("CARDPLOT001", found)
        self.assertNotIn("CARDPLOT002", found)

    def test_waiver_and_scope(self):
        found = self._run("乙某", "# 人物卡 · 乙某\n\n| 当前进度 | 攒了三年 | <!-- CARDPLOT-ok: 基线依据 -->\n")
        self.assertNotIn("CARDPLOT001", found)
        self._write("02_数据库/07_人物/07_人物.md", "# 索引\n\n| 当前进度 | 攒了三年 |\n")  # 总索引不查
        self._write("03_规划/x.md", "| 当前进度 | 攒了三年 |\n")                          # 非数据库不查
        found = {f.code: f for f in CardPlotRule().run(AuditContext(self.novel_dir))}
        self.assertNotIn("CARDPLOT001", found)


class TestHookRhythm(_Base):
    HEADER = "# 卷大纲\n\n## 【章节节拍表】\n\n| 章节 | 一句话剧情摘要 | 必用模板 | 核心事件类型 | 钩子类型 |\n|---|---|---|---|---|\n"

    def _run(self, rows):
        body = self.HEADER + "".join(
            f"| 第{i:02d}章 | 摘要 | 00_通用写作规则 | {ev} | {hook} |\n" for i, (ev, hook) in enumerate(rows, 1))
        self._write("03_规划/01_第01部/01_卷01/规划_卷01.md", body)
        return HookRhythmRule().run(AuditContext(self.novel_dir))

    def _codes(self, findings):
        return [f.code for f in findings]

    def test_no_hook_only_at_volume_end(self):
        rows = [("日常/铺垫", "重钩" if i % 2 else "轻钩") for i in range(1, 13)]
        rows[4] = ("日常/铺垫", "无钩")
        self.assertIn("HOOK001", self._codes(self._run(rows)))
        rows[4] = ("日常/铺垫", "轻钩")
        rows[11] = ("感悟/闭环", "无钩")          # 卷末章允许
        self.assertNotIn("HOOK001", self._codes(self._run(rows)))

    def test_run_of_four_same_kind(self):
        rows = [("危机", "重钩")] * 4 + [("日常", "轻钩"), ("危机", "重钩")] * 4
        self.assertIn("HOOK003", self._codes(self._run(rows)))
        rows = [("危机", "重钩")] * 3 + [("日常", "轻钩"), ("危机", "重钩")] * 5
        self.assertNotIn("HOOK003", self._codes(self._run(rows)))

    def test_no_hook_does_not_break_light_run(self):
        rows = [("危机", "重钩")] * 3 + [("日常", "轻钩")] * 3 + [("日常", "无钩"), ("日常", "轻钩")] + [("危机", "重钩")] * 4
        found = [f for f in self._run(rows) if f.code == "HOOK003"]
        self.assertTrue(any("04~08" in f.message for f in found), [f.message for f in found])

    def test_battle_with_light_hook(self):
        rows = [("危机/冲突", "重钩"), ("冲突/战斗", "轻钩")] * 6
        found = [f for f in self._run(rows) if f.code == "HOOK004"]
        self.assertEqual(len(found), 6)

    def test_first_ten_heavy_and_ratio(self):
        rows = [("日常", "轻钩")] * 12
        codes = self._codes(self._run(rows))
        self.assertIn("HOOK002", codes)
        self.assertIn("HOOK005", codes)

    def test_healthy_volume_is_silent(self):
        rows = [("危机", "重钩"), ("日常", "轻钩")] * 6
        self.assertEqual(self._run(rows), [])

    def test_ignores_chapter_outlines(self):
        self._write("03_规划/01_第01部/01_卷01/规划_卷01_章0001.md", self.HEADER + "| 第01章 | x | y | 战斗 | 无钩 |\n")
        self.assertEqual(HookRhythmRule().run(AuditContext(self.novel_dir)), [])


if __name__ == "__main__":
    unittest.main()
