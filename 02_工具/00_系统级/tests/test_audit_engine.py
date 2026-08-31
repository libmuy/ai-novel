#!/usr/bin/env python3
"""
全量审计单元测试套件 (test_audit_engine.py)
测试 RepositoryScanner, ReferenceResolver, TodoResolver, 各 Rule 校验、Reporter 与 DummyRule
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

# Add search path for modules
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "01_小说通用工具"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from audit import AuditEngine, AuditContext, RepositoryScanner, AuditReporter, AuditRule, Finding, Severity
from audit.resolver.reference_resolver import ReferenceResolver
from audit.resolver.todo_resolver import TodoResolver
from audit.rules.filesystem import FilesystemRule
from audit.rules.index import IndexRule
from audit.rules.reference import ReferenceRule
from audit.rules.todo import TodoRule
from audit.rules.setting import SettingRule
from audit.rules.planning import PlanningRule
from audit.rules.manuscript import ManuscriptRule
from audit.rules.geography import GeographyRule
from audit.rules.ids import IdRule
from helpers import make_novel, _write_changelog


class DummyRule(AuditRule):
    name = "dummy"
    code_prefix = "DUMMY"

    def run(self, context: AuditContext) -> list[Finding]:
        return [Finding(
            severity=Severity.INFO,
            rule=self.name,
            code="DUMMY001",
            message="Dummy rule triggered",
            file="dummy.md"
        )]


class TestAuditEngine(unittest.TestCase):

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.novel_dir = Path(make_novel(self.td.name))

    def tearDown(self):
        self.td.cleanup()

    def test_repository_scan(self):
        # 创建 01_设定 目录与文件
        (self.novel_dir / "01_设定").mkdir(exist_ok=True)
        (self.novel_dir / "01_设定" / "00_小说概念.md").write_text("# 概念", encoding="utf-8")
        scanner = RepositoryScanner(self.novel_dir)
        files = scanner.scan()
        self.assertTrue(len(files) > 0)
        domains = set(f.data_domain for f in files)
        self.assertIn("01_设定", domains)
        self.assertIn("05_工作区", domains)

    def test_dummy_rule_registration(self):
        engine = AuditEngine(self.novel_dir)
        engine.register_rule(DummyRule())
        context = AuditContext(self.novel_dir)
        findings = engine.run(rule_filter="dummy", context=context)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].code, "DUMMY001")

    def test_broken_reference(self):
        # 写入一个断链文件
        broken_file = self.novel_dir / "03_规划" / "test_plan.md"
        broken_file.write_text("引用了 @人物.不存在的人 以及 [坏链接](not_exist.md)", encoding="utf-8")

        context = AuditContext(self.novel_dir)
        rule = ReferenceRule()
        findings = rule.run(context)
        codes = [f.code for f in findings]
        self.assertIn("REF001", codes)
        self.assertIn("REF005", codes)

    def test_manuscript_purity(self):
        ms_dir = self.novel_dir / "10_正文"
        ms_dir.mkdir(exist_ok=True)
        ms_file = ms_dir / "正文_001.md"
        ms_file.write_text("主角来到了 @地名.枯港矿城", encoding="utf-8")

        context = AuditContext(self.novel_dir)
        rule = ManuscriptRule()
        findings = rule.run(context)
        codes = [f.code for f in findings]
        self.assertIn("MANUSCRIPT001", codes)

    def test_todo_done_missing_entity(self):
        db_dir = self.novel_dir / "02_数据库"
        db_dir.mkdir(exist_ok=True)
        reg_file = db_dir / "00_TODO全局注册表.md"
        reg_file.write_text("# TODO注册表\n| TODO-FC-999 | 虚构人物 | DONE |\n|---|---|---|\n| TODO-FC-999 | 虚构人物 | DONE |", encoding="utf-8")

        context = AuditContext(self.novel_dir)
        rule = TodoRule()
        findings = rule.run(context)
        codes = [f.code for f in findings]
        self.assertIn("TODO006", codes)

    def test_todo_forward_ref_missing_intro_volume(self):
        plan_dir = self.novel_dir / "03_规划"
        plan_dir.mkdir(exist_ok=True)
        plan_file = plan_dir / "规划.md"
        plan_file.write_text(
            "# 全书规划\n\n第三部登场的 @势力.[TODO-001]\n\n"
            "## 待创建条目\n"
            "- [ ] @势力.[TODO-001]（类型：势力；需求：后期揭示的隐藏势力；提及位置：规划.md）\n"
            "- [ ] @人物.[TODO-002]（类型：人物；需求：最终反派；预计引入卷：第3部/卷09；提及位置：规划.md）\n",
            encoding="utf-8",
        )

        context = AuditContext(self.novel_dir)
        rule = TodoRule()
        findings = rule.run(context)
        todo007 = [f for f in findings if f.code == "TODO007"]
        self.assertEqual(len(todo007), 1)
        # 只有缺「预计引入卷」的 TODO-001 那一行被标记
        self.assertIn("第6行", "".join(todo007[0].locations))
        self.assertEqual(len(todo007[0].locations), 1)

    def test_todo_forward_ref_with_intro_volume_ok(self):
        plan_dir = self.novel_dir / "03_规划"
        plan_dir.mkdir(exist_ok=True)
        plan_file = plan_dir / "规划.md"
        plan_file.write_text(
            "# 全书规划\n\n## 待创建条目\n"
            "- [ ] @人物.[TODO-001]（类型：人物；需求：最终反派；预计引入卷：第3部/卷09；提及位置：规划.md）\n",
            encoding="utf-8",
        )

        context = AuditContext(self.novel_dir)
        rule = TodoRule()
        findings = rule.run(context)
        self.assertEqual([f for f in findings if f.code == "TODO007"], [])

    def test_path_traversal_prevention(self):
        broken_file = self.novel_dir / "01_设定" / "test_escape.md"
        broken_file.write_text("逃逸 [秘密](../../secret.md)", encoding="utf-8")

        context = AuditContext(self.novel_dir)
        rule = ReferenceRule()
        findings = rule.run(context)
        codes = [f.code for f in findings]
        self.assertIn("REF004", codes)

    # ---- W4.3 state_registry ----

    def _setup_state_registry_novel(self, changelog_rows, whitelist_rows=None):
        from audit.rules.state_registry import StateRegistryRule
        card_dir = self.novel_dir / "02_数据库" / "07_人物"
        card_dir.mkdir(parents=True, exist_ok=True)
        (card_dir / "07_人物_张三.md").write_text(
            "# 人物卡 · 张三\n\n## 动态字段清单\n\n"
            "| 字段 | 类型 | 基线初值 |\n|---|---|---|\n"
            "| 境界 | 运算-枚举 | 炼气一层 |\n| 所在地 | 描述 | |\n",
            encoding="utf-8",
        )
        fac_dir = self.novel_dir / "02_数据库" / "03_势力组织"
        fac_dir.mkdir(parents=True, exist_ok=True)
        (fac_dir / "03_势力组织_黑石会.md").write_text(
            "# 势力 · 黑石会\n\n## 动态字段清单\n\n"
            "| 字段 | 类型 | 基线初值 |\n|---|---|---|\n"
            "| 与主角互动状态 | 运算-枚举 | 敌对 |\n",
            encoding="utf-8",
        )
        if whitelist_rows is not None:
            wl = self.novel_dir / "05_工作区" / "00_全局" / "00_状态对象白名单.md"
            wl.parent.mkdir(parents=True, exist_ok=True)
            lines = ["# 白名单\n", "| 对象ID | 类型 | 说明 |", "| --- | --- | --- |"]
            lines += [f"| {r} | — | test |" for r in whitelist_rows]
            wl.write_text("\n".join(lines) + "\n", encoding="utf-8")
        ch_dir = self.novel_dir / "05_工作区" / "01_第01部" / "01_卷01" / "章0001"
        _write_changelog(str(ch_dir / "04_本章状态履历.md"), changelog_rows)
        context = AuditContext(self.novel_dir)
        return {f.code: f for f in StateRegistryRule().run(context)}

    def test_state_registry_unregistered_object(self):
        # 张三 有卡；苏彦 是笔误 → STATE022
        found = self._setup_state_registry_novel([
            ["角色.张三", "境界", "运算-枚举", "炼气一层"],
            ["角色.苏彦", "境界", "运算-枚举", "炼气一层"],
        ])
        self.assertIn("STATE022", found)
        self.assertIn("苏彦", "".join(found["STATE022"].locations))
        self.assertNotIn("张三", "".join(found["STATE022"].locations))

    def test_state_registry_undeclared_field(self):
        # 内力值 不在张三的动态字段清单 → STATE023
        found = self._setup_state_registry_novel([
            ["角色.张三", "境界", "运算-枚举", "炼气二层"],
            ["角色.张三", "内力值", "运算-数值", "+50"],
        ])
        self.assertIn("STATE023", found)
        self.assertIn("内力值", "".join(found["STATE023"].locations))
        self.assertNotIn("STATE022", found)

    def test_state_registry_whitelist_suppresses(self):
        found = self._setup_state_registry_novel(
            [["角色.某矿工甲", "所在地", "描述", "枯港矿城"]],
            whitelist_rows=["角色.某矿工甲"],
        )
        self.assertNotIn("STATE022", found)

    def test_state_registry_clean(self):
        found = self._setup_state_registry_novel([
            ["角色.张三", "境界", "运算-枚举", "炼气一层"],
            ["势力.黑石会", "与主角互动状态", "运算-枚举", "敌对"],
        ])
        self.assertNotIn("STATE022", found)
        self.assertNotIn("STATE023", found)


if __name__ == "__main__":
    unittest.main()
