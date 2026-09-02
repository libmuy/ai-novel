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
            wl = self.novel_dir / "05_工作区" / "02_状态" / "00_状态对象白名单.md"
            wl.parent.mkdir(parents=True, exist_ok=True)
            lines = ["# 白名单\n", "| 对象ID | 类型 | 说明 |", "| --- | --- | --- |"]
            lines += [f"| {r} | — | test |" for r in whitelist_rows]
            wl.write_text("\n".join(lines) + "\n", encoding="utf-8")
        ch_dir = self.novel_dir / "05_工作区" / "03_第01部" / "03_卷01" / "03_章0001" / "02_状态"
        _write_changelog(str(ch_dir / "01_状态履历.md"), changelog_rows)
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

    # ---- W5 relation ----

    def _run_relation_rule(self, changelog_rows):
        from audit.rules.relation import RelationRule
        card_dir = self.novel_dir / "02_数据库" / "07_人物"
        card_dir.mkdir(parents=True, exist_ok=True)
        for name in ("柳禾", "苏砚"):
            (card_dir / f"07_人物_{name}.md").write_text(f"# 人物卡 · {name}\n", encoding="utf-8")
        ch_dir = self.novel_dir / "05_工作区" / "03_第01部" / "03_卷01" / "03_章0001" / "02_状态"
        _write_changelog(str(ch_dir / "01_状态履历.md"), changelog_rows)
        context = AuditContext(self.novel_dir)
        return {f.code: f for f in RelationRule().run(context)}

    def test_relation_id_unsorted_flagged(self):
        found = self._run_relation_rule([
            ["关系.苏砚&柳禾", "关系性质", "运算-枚举", "血亲·母子"],
        ])
        self.assertIn("RELATION001", found)
        self.assertIn("关系.柳禾&苏砚", "".join(found["RELATION001"].locations))

    def test_relation_id_two_seps_flagged(self):
        found = self._run_relation_rule([
            ["关系.甲&乙&丙", "关系性质", "运算-枚举", "结盟"],
        ])
        self.assertIn("RELATION001", found)

    def test_relation_id_unregistered_end_flagged(self):
        found = self._run_relation_rule([
            ["关系.张三&苏砚", "关系性质", "运算-枚举", "敌对"],
        ])
        self.assertIn("RELATION001", found)

    def test_relation_id_clean(self):
        found = self._run_relation_rule([
            ["关系.柳禾&苏砚", "关系性质", "运算-枚举", "血亲·母子"],
            ["关系.柳禾&苏砚", "亲疏", "运算-枚举", "至亲"],
        ])
        self.assertNotIn("RELATION001", found)

    # ---- plan_beat：卷大纲章节归属单一权威 ----

    def _run_plan_beat(self, body: str):
        from audit.rules.plan_beat import PlanBeatRule
        vol = self.novel_dir / "03_规划" / "01_第01部" / "01_卷01" / "规划_卷01.md"
        vol.parent.mkdir(parents=True, exist_ok=True)
        vol.write_text(body, encoding="utf-8")
        return {f.code: f for f in PlanBeatRule().run(AuditContext(self.novel_dir))}

    _BEAT = (
        "## 【章节节拍表】\n\n"
        "| 章节 | 一句话剧情摘要 | 必用模板 | 核心事件类型 | 钩子类型 |\n"
        "|---|---|---|---|---|\n"
        "| 第01章 | @主角 被 @人物.[马铁秤] 克扣工钱 | 05_开篇三章指南 | 入戏/危机 | 重钩 |\n"
        "| 第04章 | @主角 借古玉入道，@人物.[周莽] 赠 @资源.[灵心草] | 09_主角突破卡模板 | 突破/晋升 | 轻钩 |\n"
        "| 第10章 | @主角 随 @人物.[周莽] 发现古符文矿道（埋 @伏笔.FH-003） | 00_通用写作规则 | 危机/冲突 | 重钩 |\n"
    )

    def test_plan_beat_chapter_column_flagged(self):
        body = self._BEAT + (
            "\n## 【角色与关系】\n\n### 本卷新出场配角\n\n"
            "| 角色 | 关联卡 | 出场章节 | 职能 |\n|---|---|---|---|\n"
            "| @人物.[周莽] | 07_人物_周莽.md | 第04章 | 师父 |\n"
        )
        found = self._run_plan_beat(body)
        self.assertIn("PLAN_BEAT001", found)
        self.assertIn("出场章节", "".join(found["PLAN_BEAT001"].locations))

    def test_plan_beat_banned_index_section(self):
        body = self._BEAT + "\n## 【出场对象ID清单】（供任务11检索）\n\n| ID | 出场章节 |\n|---|---|\n| @主角 | 全卷 |\n"
        found = self._run_plan_beat(body)
        self.assertIn("PLAN_BEAT001", found)

    def test_plan_beat_fh_not_in_summary(self):
        # FH-068 埋设标第01章，但第01章摘要没 @引用 FH-068
        body = self._BEAT + (
            "\n## 【资源与伏笔规划】\n\n### 本卷埋设伏笔列表\n\n"
            "| 伏笔ID | 名称 | 类型 | 埋设章节 | 回收 |\n|---|---|---|---|---|\n"
            "| @伏笔.FH-068 | 活矿邪法 | 中 | 第01章（轻埋） | 卷3 |\n"
        )
        found = self._run_plan_beat(body)
        self.assertIn("PLAN_BEAT002", found)
        self.assertIn("FH-068", "".join(found["PLAN_BEAT002"].locations))

    def test_plan_beat_clean(self):
        # 埋设伏笔章节与摘要一致；无章节列
        body = self._BEAT + (
            "\n## 【资源与伏笔规划】\n\n### 本卷埋设伏笔列表\n\n"
            "| 伏笔ID | 名称 | 类型 | 埋设章节 | 回收 |\n|---|---|---|---|---|\n"
            "| @伏笔.FH-003 | 深矿古修之迹 | 中 | 第10章 | 卷4 |\n"
            "\n### 本卷新出场配角\n\n| 角色 | 关联卡 | 职能/定位 |\n|---|---|---|\n"
            "| @人物.[周莽] | 07_人物_周莽.md | 师父，老矿头 |\n"
        )
        found = self._run_plan_beat(body)
        self.assertNotIn("PLAN_BEAT001", found)
        self.assertNotIn("PLAN_BEAT002", found)

    # ---- STATE015 回归：drift 检查必须带描述合并缓存 ----

    def test_state_drift_uses_merge_cache(self):
        """已并入含描述变更的章后，_check_state_drift 用缓存折叠——不得报 STATE015。"""
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        import state_tree as st
        from audit.rules.state import StateRule

        nd = self.novel_dir
        (nd / "05_工作区/02_状态/00_基线状态/01_角色").mkdir(parents=True, exist_ok=True)
        (nd / "05_工作区/02_状态/00_基线状态/01_角色/01_角色_苏砚.md").write_text(
            "# 全局状态 · 角色.苏砚\n\n> 对象ID: 角色.苏砚\n\n"
            "| 对象ID | 字段 | 类型 | 值 |\n| --- | --- | --- | --- |\n"
            "| 角色.苏砚 | 当前心境 | 描述 | 平静 |\n", encoding="utf-8")
        ch = nd / "05_工作区/03_第01部/03_卷01/03_章0001/02_状态"
        _write_changelog(str(ch / "01_状态履历.md"),
                         [["角色.苏砚", "当前心境", "描述", "警惕而克制", "0001", "2026-01-01", "修改"]])
        # 描述合并缓存（模拟 merge_chapter_state.py --merge-pending 已写）
        merged = "平静，但已生出警惕而克制之心"
        st.append_merge_cache(str(nd), [{
            "对象": "角色.苏砚", "字段": "当前心境",
            "旧值sha": st.value_fingerprint("平静"),
            "新值sha": st.value_fingerprint("警惕而克制"),
            "合并文本": merged, "章": "03_第01部/03_卷01/03_章0001", "时间": "2026-01-01",
        }])
        # 写 01_最新状态（== 基线 ⊕ 履历，用缓存）+ manifest 折叠至章0001
        cache = st.load_merge_cache(str(nd))
        recs, _ = st.fold_all(str(nd / "05_工作区/02_状态/00_基线状态"),
                              st.iter_workspace_changelogs(str(nd)), cache=cache, resolver=None)
        st.write_state_tree(str(nd / "05_工作区/02_状态/01_最新状态"), recs,
                            folded_chapter="03_第01部/03_卷01/03_章0001")

        codes = {f.code for f in StateRule().run(AuditContext(nd))}
        self.assertNotIn("STATE015", codes)
        self.assertNotIn("STATE016", codes)


if __name__ == "__main__":
    unittest.main()
