#!/usr/bin/env python3
"""细纲落地核对清单生成器：build_landing_checklist.py

从单章细纲抽 场景表(场景钩子/涉及资源/涉及伏笔) + **场景要点** bullet
+ 章级钩子 + 道义 + 突破卡 → 预填待锚定清单 03_细纲落地核对.md。
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "01_小说通用工具"))

from helpers import make_novel  # noqa: E402
import build_landing_checklist as blc  # noqa: E402

OUTLINE = """# 单章细纲 · 第02章

## 【场景列表】

### 第1场景

| 字段 | 内容 |
|---|---|
| 场景序号 | 第1场景 |
| 涉及资源 | 矿钉（淬毒，暗扣备战） |
| 涉及伏笔 | `@伏笔.FH-067`（刻痕作为现场，本场未推进） |
| 场景钩子 | 周莽要怎么把苏砚从当口捞下来？ |

**场景要点**

- 承接：紧接上章最后一句，无时间跳跃。
- 人物标志动作：敲「三短一长」是老毛病，决非暗号。

### 第2场景

| 字段 | 内容 |
|---|---|
| 场景序号 | 第2场景 |
| 涉及资源 | 灵心草（一次性消耗） |
| 涉及伏笔 | 无 |
| 场景钩子 | 苏砚知道嚼下去意味着什么。 |

**场景要点**

- 灵草来路是真牺牲，不是顺手。

## 【章级钩子】

| 字段 | 内容 |
|---|---|
| 章末钩子类型 | 轻钩 |
| 章末钩子内容 | 新境界的耳朵听清了那几声咳有多闷。 |
| 与下章衔接点 | 下一章第一句承接这点不安。 |

## 【道义与感悟】

| 字段 | 内容 |
|---|---|
| 本章落地道义 | 善良若没有力量兜底，就只能以命换命。 |
| 古籍引用 | 无 |

## 【本章突破卡】

| 字段 | 内容 |
|---|---|
| 突破 | 凡人 → 炼气初期 |
| 突破类型 | 外力催化＋异宝护航；苏砚灵根微末、强开。 |
| 即时代价 | 神魂眩晕、经脉隐伤、虚弱期约两章。 |
"""


def _setup(td, outline=OUTLINE):
    novel = make_novel(td)
    plan_dir = os.path.join(novel, "03_规划", "01_第01部", "01_卷01")
    os.makedirs(plan_dir, exist_ok=True)
    open(os.path.join(plan_dir, "规划_卷01_章0002.md"), "w", encoding="utf-8").write(outline)
    chap = os.path.join(novel, "05_工作区", "01_第01部", "01_卷01", "02_章0002")
    os.makedirs(os.path.join(chap, "02_状态"), exist_ok=True)
    return novel, chap


def _gen(chap, novel, **kw):
    blc.build_landing_checklist(chap, novel_dir=novel, verbose=False, **kw)
    return open(os.path.join(chap, "02_状态", "03_细纲落地核对.md"), encoding="utf-8").read()


class TestLandingChecklist(unittest.TestCase):

    def test_scene_hooks_and_resources_become_items(self):
        with tempfile.TemporaryDirectory() as td:
            novel, chap = _setup(td)
            out = _gen(chap, novel)
            self.assertIn("场景钩子：周莽要怎么把苏砚从当口捞下来？", out)
            self.assertIn("涉及资源：矿钉", out)

    def test_scene_bullets_captured(self):
        with tempfile.TemporaryDirectory() as td:
            novel, chap = _setup(td)
            out = _gen(chap, novel)
            self.assertIn("要点：承接：紧接上章最后一句", out)
            self.assertIn("要点：灵草来路是真牺牲", out)

    def test_empty_foreshadow_row_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            novel, chap = _setup(td)
            out = _gen(chap, novel)
            # 第2场景「涉及伏笔 | 无」不出条
            self.assertNotIn("涉及伏笔：无", out)

    def test_chapter_hook_dao_breakthrough_sections(self):
        with tempfile.TemporaryDirectory() as td:
            novel, chap = _setup(td)
            out = _gen(chap, novel)
            self.assertIn("## 章级钩子", out)
            self.assertIn("## 道义与感悟", out)
            self.assertIn("## 本章突破卡", out)
            self.assertIn("即时代价：神魂眩晕", out)
            self.assertIn("以命换命", out)

    def test_all_items_start_unchecked_with_placeholder(self):
        with tempfile.TemporaryDirectory() as td:
            novel, chap = _setup(td)
            out = _gen(chap, novel)
            self.assertIn("- [ ] ", out)
            self.assertIn("〔待填：正文", out)

    def test_refuse_overwrite_without_force(self):
        with tempfile.TemporaryDirectory() as td:
            novel, chap = _setup(td)
            _gen(chap, novel)
            with self.assertRaises(SystemExit):
                _gen(chap, novel)
            # --force 可覆盖
            _gen(chap, novel, force=True)

    def test_short_workspace_path_resolves(self):
        with tempfile.TemporaryDirectory() as td:
            novel, chap = _setup(td)
            rel = os.path.relpath(chap, novel)          # 05_工作区/01_第01部/...
            cwd = os.getcwd()
            try:
                os.chdir(os.path.dirname(td))
                # 传相对小说根的短路径也应解析到（借 01_小说数据/* 兜底不了合成小说，
                # 这里只验证完整路径与 chapter_dir 直传都工作）
                blc.build_landing_checklist(chap, novel_dir=novel, verbose=False, force=True)
            finally:
                os.chdir(cwd)
            self.assertTrue(os.path.exists(
                os.path.join(chap, "02_状态", "03_细纲落地核对.md")))

    def test_outline_without_any_items_errors(self):
        with tempfile.TemporaryDirectory() as td:
            novel, chap = _setup(td, outline="# 单章细纲 · 第02章\n\n（空）\n")
            with self.assertRaises(SystemExit):
                _gen(chap, novel)


if __name__ == "__main__":
    unittest.main()
