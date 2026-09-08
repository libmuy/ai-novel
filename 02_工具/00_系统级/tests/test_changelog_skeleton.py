#!/usr/bin/env python3
"""履历骨架生成器（A4）：build_state_snapshot.py --changelog-skeleton

从细纲「## 出场对象」+ 开篇状态 + 卡片动态字段清单，生成预填的 01_状态履历.md 骨架。
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "01_小说通用工具"))

from helpers import make_novel  # noqa: E402
import build_state_snapshot as bss  # noqa: E402

OUTLINE = """# 单章细纲 · 第02章

## 出场对象

| 对象ID | 出场方式 | 备注 |
|---|---|---|
| `@主角` | 登场 | 视角人物 |
| `@人物.[周莽]` | 登场 | 新登场老矿工 |
| `@物品.[古玉]` | 状态变动 | 首次发热 |
| `@势力.[黑石会]` | 提及 | 背景 |
| `@伏笔.FH-001` | 状态变动 | 推进 |
| `@关系.[周莽&苏砚]` | 张力驱动 | 首次登记 |

> 灵心草为一次性消耗资源，不入出场对象表。

## 【场景列表】
（略）

## 【本章突破卡】

| 字段 | 内容 |
|---|---|
| 突破 | 凡人 → 玄元道 1级（炼气·初期） |
"""

ZHOUMANG_CARD = """# 人物卡 · 周莽

## 动态字段清单

| 字段 | 类型 | 基线初值 |
|---|---|---|
| 境界 | 运算-枚举 | 凡人 |
| 身体状况 | 描述 | 咳嗽 |
| 对象终态 | 运算-枚举 | 活跃 |
"""

PROT_CARD = """# 主角档案

| 字段 | 值 |
|---|---|
| 姓名 | 本名 | 苏砚 |
"""


def _setup(td):
    novel = make_novel(td, baseline_records=[
        ["角色.苏砚", "境界", "运算-枚举", "凡人"],
        ["角色.苏砚", "身体状况", "描述", "健康"],
        ["物品.古玉", "持有者", "运算-枚举", "@角色.[苏砚]"],
        ["物品.古玉", "物品状态", "运算-枚举", "完好"],
    ])
    os.makedirs(os.path.join(novel, "01_设定"), exist_ok=True)
    open(os.path.join(novel, "01_设定", "00_主角档案.md"), "w", encoding="utf-8").write(PROT_CARD)
    os.makedirs(os.path.join(novel, "02_数据库", "07_人物"), exist_ok=True)
    open(os.path.join(novel, "02_数据库", "07_人物", "07_人物_周莽.md"),
         "w", encoding="utf-8").write(ZHOUMANG_CARD)
    plan_dir = os.path.join(novel, "03_规划", "01_第01部", "01_卷01")
    os.makedirs(plan_dir, exist_ok=True)
    open(os.path.join(plan_dir, "规划_卷01_章0002.md"), "w", encoding="utf-8").write(OUTLINE)
    chap = os.path.join(novel, "05_工作区", "01_第01部", "01_卷01", "02_章0002")
    os.makedirs(os.path.join(chap, "02_状态"), exist_ok=True)
    return novel, chap


def _gen(chap, novel, **kw):
    bss.build_changelog_skeleton(chap, novel_dir=novel, verbose=False, **kw)
    return open(os.path.join(chap, "02_状态", "01_状态履历.md"), encoding="utf-8").read()


class TestChangelogSkeleton(unittest.TestCase):

    def test_existing_object_gets_modify_rows_with_placeholder(self):
        with tempfile.TemporaryDirectory() as td:
            novel, chap = _setup(td)
            out = _gen(chap, novel)
            self.assertIn("| 角色.苏砚 | 境界 | 运算-枚举 | 〔待填", out)
            self.assertIn("| 0002 | ", out)
            self.assertRegex(out, r"角色\.苏砚 \| 境界 .*修改")

    def test_new_character_from_dynamic_field_list(self):
        with tempfile.TemporaryDirectory() as td:
            novel, chap = _setup(td)
            out = _gen(chap, novel)
            self.assertRegex(out, r"角色\.周莽 \| 境界 .*新建")
            self.assertRegex(out, r"角色\.周莽 \| 身体状况 .*新建")
            self.assertIn("07_人物_周莽.md", out)

    def test_item_gets_physical_and_soul_link_rows(self):
        with tempfile.TemporaryDirectory() as td:
            novel, chap = _setup(td)
            out = _gen(chap, novel)
            self.assertIn("| 物品.古玉 | 物理状态 | 描述 |", out)
            self.assertIn("| 物品.古玉 | 神魂链接 | 描述 |", out)

    def test_new_relation_has_required_enum_fields(self):
        with tempfile.TemporaryDirectory() as td:
            novel, chap = _setup(td)
            out = _gen(chap, novel)
            for f in ("关系性质", "亲疏", "公开程度", "关系概述"):
                self.assertRegex(out, rf"关系\.周莽&苏砚 \| {f} ")

    def test_mention_only_object_gets_comment_not_rows(self):
        with tempfile.TemporaryDirectory() as td:
            novel, chap = _setup(td)
            out = _gen(chap, novel)
            self.assertNotRegex(out, r"\| 势力\.黑石会 \|")
            self.assertIn("势力.黑石会", out)  # 作为注释出现

    def test_foreshadow_not_a_state_object(self):
        with tempfile.TemporaryDirectory() as td:
            novel, chap = _setup(td)
            out = _gen(chap, novel)
            self.assertNotRegex(out, r"\| 伏笔\.")

    def test_consumable_footer_bullet_no_row(self):
        with tempfile.TemporaryDirectory() as td:
            novel, chap = _setup(td)
            out = _gen(chap, novel)
            self.assertIn("灵心草", out.split("本章已在正文体现")[1])
            self.assertNotRegex(out, r"\| 物品\.灵心草 \|")

    def test_breakthrough_hint_near_realm_row(self):
        with tempfile.TemporaryDirectory() as td:
            novel, chap = _setup(td)
            out = _gen(chap, novel)
            self.assertIn("突破卡：凡人 → 玄元道", out)

    def test_refuses_overwrite_without_force(self):
        with tempfile.TemporaryDirectory() as td:
            novel, chap = _setup(td)
            _gen(chap, novel)
            with self.assertRaises(SystemExit):
                _gen(chap, novel)
            _gen(chap, novel, force=True)  # force ok


if __name__ == "__main__":
    unittest.main()
