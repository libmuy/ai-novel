#!/usr/bin/env python3
"""折叠后 state 家族审计门禁（A3）：

merge_chapter_state.py 折叠 + 写树之后跑 state/relation/enum_domain 家族审计，
有 ERROR 就回滚最新状态树、不打「合并完成」、退出码 2。
既有错误也拦——终结「另账处理」。
"""
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "01_小说通用工具"))

from helpers import make_novel  # noqa: E402
import merge_chapter_state as mcs  # noqa: E402

MERGE = os.path.join(os.path.dirname(__file__), "..", "..",
                     "01_小说通用工具", "merge_chapter_state.py")

# 最小字段词表：角色类，对象终态是闭集
VOCAB = """# 字段词表

## 二、 官方注册字段表

### 1. 角色类对象 (`角色.[姓名]`)

| 字段名 | 细分类型 | 合法枚举值 / 示例说明 | 适用场景 |
|---|---|---|---|
| **境界** | 运算-枚举 | `凡人` / ... 开放值域 | 修为 |
| **身体状况** | 描述 | 健康 / 受伤 | 伤势 |
| **对象终态** | 运算-枚举 | `活跃` / `死亡` / `退场` / `暂离`（闭集） | 生命状态 |
"""


def _setup(td):
    novel = make_novel(
        td,
        baseline_records=[
            ["角色.示例", "境界", "运算-枚举", "凡人"],
            ["角色.示例", "对象终态", "运算-枚举", "活跃"],
        ],
    )
    tmpl = os.path.join(novel, "00_通用模板")
    os.makedirs(tmpl, exist_ok=True)
    with open(os.path.join(tmpl, "03_字段词表.md"), "w", encoding="utf-8") as f:
        f.write(VOCAB)
    # 放行合成对象（否则 STATE022：数据库里没有同名卡片）
    wl = os.path.join(novel, "05_工作区/02_状态/03_状态对象白名单.md")
    with open(wl, "w", encoding="utf-8") as f:
        f.write("# 状态对象白名单\n\n| 对象ID | 说明 |\n|---|---|\n"
                "| 角色.示例 | 测试合成对象 |\n")
    return novel


def _write_changelog(novel, chap, rows):
    d = os.path.join(novel, "05_工作区", chap, "02_状态")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, "01_状态履历.md")
    lines = ["| 对象ID | 字段 | 类型 | 值 | 章节号 | 变更时间 | 变更类型 |",
             "| --- | --- | --- | --- | --- | --- | --- |"]
    for r in rows:
        lines.append("| " + " | ".join(r) + " |")
    with open(p, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return os.path.join(novel, "05_工作区", chap)


def _run_merge(chapter_dir, *extra):
    return subprocess.run(
        [sys.executable, MERGE, "--chapter-dir", chapter_dir, "--no-llm", *extra],
        capture_output=True, text=True,
    )


class TestMergeAuditGate(unittest.TestCase):

    def test_clean_fold_passes_and_prints_banner(self):
        with tempfile.TemporaryDirectory() as td:
            novel = _setup(td)
            chap = _write_changelog(
                novel, "03_第01部/03_卷01/03_章0001",
                [["角色.示例", "身体状况", "描述", "受伤", "0001", "x", "修改"]])
            r = _run_merge(chap)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("合并完成", r.stdout)

    def test_post_fold_enum_error_blocks_and_rolls_back(self):
        with tempfile.TemporaryDirectory() as td:
            novel = _setup(td)
            live_obj = os.path.join(
                novel, "05_工作区/02_状态/01_最新状态/01_角色/01_角色_示例.md")
            chap = _write_changelog(
                novel, "03_第01部/03_卷01/03_章0001",
                # 通过 validate_changelog（对象终态在词表、类型对、变更类型合法），
                # 但值「活着」不在闭集 → 折叠后 STATE026
                [["角色.示例", "对象终态", "运算-枚举", "活着", "0001", "x", "修改"]])
            r = _run_merge(chap)
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("阻断", r.stdout)
            self.assertNotIn("合并完成", r.stdout)
            # 已回滚：最新状态树里不该出现「活着」
            if os.path.exists(live_obj):
                self.assertNotIn("活着", open(live_obj, encoding="utf-8").read())

    def test_skip_audit_gate_bypasses(self):
        with tempfile.TemporaryDirectory() as td:
            novel = _setup(td)
            chap = _write_changelog(
                novel, "03_第01部/03_卷01/03_章0001",
                [["角色.示例", "对象终态", "运算-枚举", "活着", "0001", "x", "修改"]])
            r = _run_merge(chap, "--skip-audit-gate")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("合并完成", r.stdout)


if __name__ == "__main__":
    unittest.main()
