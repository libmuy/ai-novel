# -*- coding: utf-8 -*-
"""audit_rules.py（规则层审计）的单元测试。

每个用例在临时目录里搭一个**最小规则层**，只验证目标规则的触发/不触发，
不依赖真实仓库内容（真实仓库的结论会随规则层演进而变）。
"""
import os
import shutil
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import audit_rules as ar  # noqa: E402

REAL_CONFIG = Path(__file__).resolve().parents[1] / "rules_audit.config.toml"


def _write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class RulesAuditTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        with open(REAL_CONFIG, "rb") as fh:
            self.cfg = tomllib.load(fh)
        ar._BASENAME_CACHE.clear()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        ar._BASENAME_CACHE.clear()

    def _codes(self, only=None):
        ar._BASENAME_CACHE.clear()
        return {f.code: f for f in ar.run_all(self.tmp, self.cfg, only)}

    # ---- RULE001 死链 ----

    def test_dead_ref_flagged(self):
        _write(self.tmp / "AGENTS.md", "详见 `00_通用模板/根本没有这个文件.md`。\n")
        found = self._codes("RULE001")
        self.assertIn("RULE001", found)
        self.assertIn("根本没有这个文件.md", "".join(found["RULE001"].locations))

    def test_live_ref_ok(self):
        _write(self.tmp / "00_通用模板/00_使用说明.md", "# 使用说明\n")
        _write(self.tmp / "AGENTS.md", "详见 `00_通用模板/00_使用说明.md`。\n")
        self.assertNotIn("RULE001", self._codes("RULE001"))

    def test_placeholder_ref_not_flagged(self):
        _write(self.tmp / "AGENTS.md",
               "落位 `03_规划/《部》/《卷》/规划_卷XX_章XXXX.md`，示例 `人物_姓名.md`。\n")
        self.assertNotIn("RULE001", self._codes("RULE001"))

    def test_historical_name_not_flagged(self):
        """文档写明「不再产出 03_本章初始状态.md」不是死链。"""
        _write(self.tmp / "AGENTS.md", "本章不再产出 `03_本章初始状态.md`。\n")
        self.assertNotIn("RULE001", self._codes("RULE001"))

    # ---- RULE002 索引计数 ----

    def test_declared_count_drift_flagged(self):
        for i in range(4):
            _write(self.tmp / f"00_通用模板/01_写作规则/0{i}_x.md", "x\n")
        _write(self.tmp / "00_通用模板/99_速查手册.md", "- **写作规则（13 个）**：……\n")
        found = self._codes("RULE002")
        self.assertIn("RULE002", found)
        self.assertIn("实际 4 个", found["RULE002"].message)

    def test_declared_count_match_ok(self):
        for i in range(3):
            _write(self.tmp / f"00_通用模板/01_写作规则/0{i}_x.md", "x\n")
        _write(self.tmp / "00_通用模板/99_速查手册.md", "- **写作规则（3 个）**：……\n")
        self.assertNotIn("RULE002", self._codes("RULE002"))

    # ---- RULE003 技能索引 ----

    def _skill_layout(self, index_body, files):
        base = self.tmp / "00_通用模板/03_任务技能/02_小说级"
        for fn in files:
            _write(base / fn, "# 技能\n")
        _write(base / "index.md", index_body)

    def test_skill_missing_from_index_flagged(self):
        self._skill_layout("| 技能 | 触发词 | 文件 |\n| 甲 | x | `01_甲.md` |\n",
                           ["01_甲.md", "02_乙.md"])
        found = self._codes("RULE003")
        self.assertIn("RULE003", found)
        self.assertIn("02_乙.md", "".join(found["RULE003"].locations))

    def test_skill_index_prose_ref_not_phantom(self):
        """索引正文会提到别处的文件（00_使用说明.md），不该被当成幽灵技能条目。"""
        _write(self.tmp / "00_通用模板/00_使用说明.md", "# 使用说明\n")
        self._skill_layout(
            "字段以 `00_使用说明.md` 为准。\n\n| 技能 | 文件 |\n| 甲 | `01_甲.md` |\n",
            ["01_甲.md"])
        self.assertNotIn("RULE003", self._codes("RULE003"))

    # ---- RULE004 派生切片 ----

    def test_slice_extra_section_flagged(self):
        _write(self.tmp / "00_通用模板/01_写作规则/00_通用写作规则.md",
               "# 通用写作规则\n\n## 一、固定文风比例\n\nx\n")
        _write(self.tmp / "00_通用模板/01_写作规则/00_通用写作规则_生成版.md",
               "# 生成版\n\n## 一、固定文风比例\n\nx\n\n## 九、切片私自新增的规则\n\ny\n")
        found = self._codes("RULE004")
        self.assertIn("RULE004", found)
        self.assertIn("切片私自新增的规则", "".join(found["RULE004"].locations))

    def test_slice_subset_ok(self):
        _write(self.tmp / "00_通用模板/01_写作规则/00_通用写作规则.md",
               "# 通用写作规则\n\n## 一、固定文风比例（基准）\n\nx\n\n## 二、结构\n\ny\n")
        _write(self.tmp / "00_通用模板/01_写作规则/00_通用写作规则_生成版.md",
               "# 生成版\n\n## 一、固定文风比例\n\nx\n")
        self.assertNotIn("RULE004", self._codes("RULE004"))

    # ---- RULE005 权威位置表 ----

    def test_authority_table_missing_file_flagged(self):
        _write(self.tmp / "00_通用模板/00_系统架构规范.md",
               "| 事实 | 唯一权威位置 |\n|---|---|\n| 伏笔含义 | `03_规划/没有这个.md` |\n")
        self.assertIn("RULE005", self._codes("RULE005"))

    def test_authority_table_ok(self):
        _write(self.tmp / "00_通用模板/03_字段词表.md", "# 字段词表\n")
        _write(self.tmp / "00_通用模板/00_系统架构规范.md",
               "| 事实 | 唯一权威位置 |\n|---|---|\n| 字段 | `00_通用模板/03_字段词表.md` |\n")
        self.assertNotIn("RULE005", self._codes("RULE005"))

    # ---- RULE006 重复规范正文 ----

    _LONG = "新增任何伏笔必须同步登记进总纲，禁止在下游文件里改写它的权威含义"

    def test_duplicate_normative_text_flagged(self):
        _write(self.tmp / "00_通用模板/00_使用说明.md", f"- {self._LONG}。\n")
        _write(self.tmp / "00_通用模板/02_卡片模板/05_卷级伏笔跟踪册模板.md", f"> {self._LONG}。\n")
        found = self._codes("RULE006")
        self.assertIn("RULE006", found)
        self.assertIn("00_使用说明.md", "".join(found["RULE006"].locations))

    def test_pointer_sentence_not_flagged(self):
        """「见 `<权威文件>`」正是 §二·A 要求的写法，多处出现是对的。"""
        line = "伏笔号的分配规则与权威含义详见 `03_规划/00_伏笔总纲.md`，此处不复述\n"
        _write(self.tmp / "00_通用模板/00_使用说明.md", line)
        _write(self.tmp / "00_通用模板/02_卡片模板/05_卷级伏笔跟踪册模板.md", line)
        self.assertNotIn("RULE006", self._codes("RULE006"))

    def test_exempt_group_not_flagged(self):
        """通用写作规则的两份声明过的派生切片之间重复，是设计如此。"""
        _write(self.tmp / "00_通用模板/01_写作规则/00_通用写作规则.md", f"- {self._LONG}。\n")
        _write(self.tmp / "00_通用模板/01_写作规则/00_通用写作规则_生成版.md", f"- {self._LONG}。\n")
        self.assertNotIn("RULE006", self._codes("RULE006"))

    # ---- RULE007 工具硬编码路径 ----

    def _tool(self, body):
        _write(self.tmp / "00_通用模板/05_项目骨架模板/01_设定/.gitkeep", "")
        _write(self.tmp / "02_工具/01_小说通用工具/t.py", body)

    def test_tool_path_dangling_flagged(self):
        """白名单文件从 00_ 改名到 03_、规则代码常量漏改 —— 就是这个场景。"""
        self._tool('WHITELIST = "05_工作区/02_状态/00_状态对象白名单.md"\n')
        _write(self.tmp / "01_小说数据/00_某书/05_工作区/02_状态/03_状态对象白名单.md", "# 白名单\n")
        found = self._codes("RULE007")
        self.assertIn("RULE007", found)
        self.assertIn("00_状态对象白名单.md", "".join(found["RULE007"].locations))

    def test_tool_path_resolved_in_real_novel_ok(self):
        self._tool('WHITELIST = "05_工作区/02_状态/03_状态对象白名单.md"\n')
        _write(self.tmp / "01_小说数据/00_某书/05_工作区/02_状态/03_状态对象白名单.md", "# 白名单\n")
        self.assertNotIn("RULE007", self._codes("RULE007"))

    def test_tool_path_resolved_in_skeleton_ok(self):
        self._tool('PROGRESS = "05_工作区/02_状态/03_状态对象白名单.md"\n')
        _write(self.tmp / "00_通用模板/05_项目骨架模板/05_工作区/02_状态/03_状态对象白名单.md", "x\n")
        self.assertNotIn("RULE007", self._codes("RULE007"))

    # ---- 真实仓库：规则层不得有 error ----

    def test_real_repo_has_no_rule_errors(self):
        repo = Path(__file__).resolve().parents[3]
        ar._BASENAME_CACHE.clear()
        findings = ar.run_all(repo, self.cfg)
        errors = [f for f in findings if f.severity == ar.ERROR]
        self.assertEqual(
            errors, [],
            "规则层出现 error：\n" + "\n".join(f"{f.code} {f.message}" for f in errors))


if __name__ == "__main__":
    unittest.main()
