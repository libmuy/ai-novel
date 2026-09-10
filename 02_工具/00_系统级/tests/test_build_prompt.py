# -*- coding: utf-8 -*-
"""build_prompt.py 与 prompt_build 包的单元测试套件。

每个用例在临时目录里搭最小的小说结构，验证提示词拼装的各个环节。
测试不依赖真实小说数据（后者会随创作进展而变），仅验证工具逻辑正确性。
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "01_小说通用工具"))
from prompt_build import assemble, extract, layout as L, leak, progress


def _write(path: Path, text: str):
    """辅助方法：创建并写入文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _read(path: Path) -> str:
    """辅助方法：读文件，不存在返回空串。"""
    return path.read_text(encoding="utf-8") if path.exists() else ""


class TestExtractReadSection(unittest.TestCase):
    """extract.read_section 的测试。"""

    def test_read_section_basic(self):
        """read_section 返回指定标题的整个小节，到下一个同级或更高级标题为止。"""
        text = "# 第一部分\n## 第一小节\nContent A\n## 第二小节\nContent B\n# 第二部分\nContent C\n"
        result = extract.read_section(text, "第一小节")
        self.assertIn("Content A", result)
        self.assertNotIn("Content B", result)
        self.assertIn("## 第一小节", result)

    def test_read_section_fence_aware(self):
        """read_section 跳过围栏内的 `##` 标题（ch0002 历史教训）。"""
        text = """# 任务2：正文
## 执行要求

不改 ## 执行要求 内的设定。

```markdown
## 内部标题
围栏内容不算章节标题。
```

## 下一小节
真的下一小节。
"""
        result = extract.read_section(text, "执行要求")
        # 应该包括围栏内的内容
        self.assertIn("围栏内容不算章节标题", result)
        # 不应该在围栏内的 ## 处停止
        self.assertNotIn("下一小节", result)

    def test_read_section_not_found(self):
        """read_section 找不到标题返回空串。"""
        text = "# 第一部分\n内容\n"
        result = extract.read_section(text, "不存在的标题")
        self.assertEqual(result, "")

    def test_read_section_exclude_heading(self):
        """read_section 可选不包含标题行本身。"""
        text = "## 标题\n内容\n"
        result = extract.read_section(text, "标题", include_heading=False)
        self.assertNotIn("##", result)
        self.assertIn("内容", result)


class TestExtractFencedBlock(unittest.TestCase):
    """extract.fenced_block 的测试。"""

    def test_fenced_block_returns_first_only(self):
        """fenced_block 返回第一个围栏代码块的内容，不含围栏标记，只取到第一个闭合围栏。"""
        text = """前导文本

```python
first block
```

后续文本

```python
second block
```

尾部
"""
        result = extract.fenced_block(text)
        self.assertIn("first block", result)
        self.assertNotIn("second block", result)
        self.assertNotIn("后续文本", result)
        self.assertNotIn("```", result)

    def test_fenced_block_empty_result(self):
        """无围栏时返回空串。"""
        text = "只有纯文本\n没有围栏\n"
        result = extract.fenced_block(text)
        self.assertEqual(result, "")


class TestExtractParseRefs(unittest.TestCase):
    """extract.parse_refs 的测试。"""

    def test_parse_refs_all_types(self):
        """parse_refs 解析 @主角、@人物.[名]、@伏笔.FH-001 等各类引用。"""
        text = "@主角 和 @人物.[李四] 的故事中提及 @伏笔.FH-001"
        refs = extract.parse_refs(text)
        ref_strs = [r.render() for r in refs]
        self.assertIn("@主角", ref_strs)
        self.assertIn("@人物.[李四]", ref_strs)
        self.assertIn("@伏笔.FH-001", ref_strs)

    def test_parse_refs_deduplicates(self):
        """parse_refs 去重。"""
        text = "@主角 登场，@主角 又登场，@人物.[甲] @人物.[甲]"
        refs = extract.parse_refs(text)
        self.assertEqual(len(refs), 2)  # @主角 和 @人物.[甲]


class TestExtractParseCast(unittest.TestCase):
    """extract.parse_cast 的测试。"""

    def test_parse_cast_from_outline(self):
        """parse_cast 从细纲「## 出场对象」表解析出场对象。"""
        text = """## 出场对象

| 对象 | 出场方式 | 备注 |
|---|---|---|
| @主角 | 登场 | |
| @人物.[张三] | 提及 | 已死亡 |
"""
        entries = extract.parse_cast(text)
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0].ref.ref_type, "主角")
        self.assertEqual(entries[1].ref.name, "张三")
        self.assertEqual(entries[1].mode, "提及")

    def test_parse_cast_empty_outline(self):
        """parse_cast 无出场对象表时返回空列表。"""
        text = "## 其他内容\n没有出场对象表\n"
        entries = extract.parse_cast(text)
        self.assertEqual(entries, [])


class TestExtractCardPath(unittest.TestCase):
    """extract.card_path 的测试。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_card_path_protagonist(self):
        """card_path 解析主角档案路径。"""
        _write(self.tmp / "01_设定/00_主角档案.md", "# 主角")
        ref = extract.Ref("主角", "")
        result = extract.card_path(self.tmp, ref)
        self.assertEqual(result, self.tmp / "01_设定/00_主角档案.md")

    def test_card_path_character(self):
        """card_path 解析人物卡路径 @人物.[名]。"""
        _write(self.tmp / "02_数据库/07_人物/07_人物_李四.md", "# 李四")
        ref = extract.Ref("人物", "李四")
        result = extract.card_path(self.tmp, ref)
        self.assertEqual(result, self.tmp / "02_数据库/07_人物/07_人物_李四.md")

    def test_card_path_geography_leaf_name(self):
        """card_path 按地理卡的叶子名（最后一个 _ 后的部分）匹配。

        文件 `02_地理区域_世界_区域_枯港矿城.md` 必须能被 @地名.[枯港矿城] 找到。
        """
        _write(self.tmp / "02_数据库/02_地理区域/02_地理区域_苍玄界_灰壤凡域_枯港矿城.md",
               "# 枯港矿城")
        ref = extract.Ref("地名", "枯港矿城")
        result = extract.card_path(self.tmp, ref)
        self.assertIsNotNone(result)
        self.assertIn("枯港矿城", result.name)

    def test_card_path_not_found(self):
        """card_path 找不到返回 None。"""
        ref = extract.Ref("人物", "不存在的人")
        result = extract.card_path(self.tmp, ref)
        self.assertIsNone(result)


class TestExtractWrRules(unittest.TestCase):
    """extract.wr_rules 的测试。"""

    def test_wr_rules_hard_only(self):
        """wr_rules 返回指定状态（硬）的世界规则行。"""
        text = """## 【世界基本法则】

| 规则ID | 名称 | 状态 | 内容 |
|---|---|---|---|
| WR-001 | 能量守恒 | 硬 | 凭空生成的能量会......  |
| WR-002 | 魔法反冲 | 软 | 使用魔法时...... |
"""
        hard_rules = extract.wr_rules(text, ("硬",))
        self.assertEqual(len(hard_rules), 1)
        self.assertIn("WR-001", hard_rules[0])
        self.assertNotIn("WR-002", "\n".join(hard_rules))

    def test_wr_rules_empty(self):
        """wr_rules 无对应规则时返回空列表。"""
        text = "## 没有规则\n只有文本\n"
        result = extract.wr_rules(text, ("硬",))
        self.assertEqual(result, [])


class TestExtractSceneBlocks(unittest.TestCase):
    """extract.scene_blocks 的测试。"""

    def test_scene_blocks_split(self):
        """scene_blocks 把【场景列表】分解为 (标题, 正文) 对。"""
        text = """## 【场景列表】

### 第1场景 · 清晨
内容1

### 第2场景 · 黄昏
内容2
"""
        scenes = extract.scene_blocks(text)
        self.assertEqual(len(scenes), 2)
        self.assertIn("第1场景", scenes[0][0])
        self.assertIn("内容1", scenes[0][1])
        self.assertIn("内容2", scenes[1][1])

    def test_scene_blocks_rejects_non_canonical_heading(self):
        """`#### 场景 N：标题`（云端弱模型常见变体）不是 canonical 形态——
        取不到场景 → build_prompt 正文的逐场字数预算会留空洞（PLAN023 / GATE 拦）。"""
        text = ("## 【场景列表】\n\n### 场景概览\n\n| 序号 | 字数 |\n|---|---|\n"
                "| 第1场景 | 900字 |\n\n### 场景详细拆解\n\n#### 场景 1：废矿道\n- 要点\n")
        self.assertEqual(extract.scene_blocks(text), [])


class TestExtractReadSections(unittest.TestCase):
    """extract.read_sections / sections_present —— 「区块被改名 → 静默丢失」是这批测试要锁死的契约。"""

    SRC = """### 【基础档案】
| 字段 | 内容 |
| 姓名 | 张三 |

### 【角色内核】
三要素在此。

### 【创作标签】
标志性细节。
"""

    def test_all_present(self):
        out = extract.read_sections(self.SRC, ["【基础档案】", "【角色内核】", "【创作标签】"])
        self.assertIn("张三", out)
        self.assertIn("三要素在此", out)
        self.assertIn("标志性细节", out)

    def test_partial_missing_is_silently_skipped(self):
        """缺的节不报错、不占位——这正是需要上层用 sections_present 兜的行为。"""
        out = extract.read_sections(self.SRC, ["【基础档案】", "【修行档案】", "【角色内核】"])
        self.assertIn("张三", out)
        self.assertIn("三要素在此", out)
        self.assertNotIn("修行档案", out)

    def test_all_missing_returns_empty(self):
        self.assertEqual(extract.read_sections(self.SRC, ["【不存在】", "【也不存在】"]), "")

    def test_sections_present_reports_hits_in_order(self):
        present = extract.sections_present(
            self.SRC, ["【创作标签】", "【修行档案】", "【基础档案】"])
        self.assertEqual(present, ["【创作标签】", "【基础档案】"])

    def test_sections_present_exact_match_only(self):
        """带后缀的标题不算命中——与 read_section 的 `title == want` 一致。"""
        src = "### 【角色内核】（三要素）\n内容\n"
        self.assertEqual(extract.sections_present(src, ["【角色内核】"]), [])


class TestExtractCardFields(unittest.TestCase):
    """extract.card_fields / field_value —— 三列卡的「必填」列不得当值返回。"""

    def test_two_column_card(self):
        src = "| 字段 | 内容 |\n|---|---|\n| 所在地 | 枯港矿城 |\n"
        self.assertEqual(extract.field_value(src, "所在地"), "枯港矿城")

    def test_three_column_card_takes_content_column(self):
        src = "| 字段 | 必填 | 内容 |\n|---|---|---|\n| 姓名 | (必) | 柳禾 |\n"
        self.assertEqual(extract.field_value(src, "姓名"), "柳禾")

    def test_three_column_card_empty_content_returns_empty_not_flag(self):
        """内容列留空时，旧实现会回退返回 `(必)`——回归这个 bug。"""
        src = "| 字段 | 必填 | 内容 |\n|---|---|---|\n| 姓名 | (必) |  |\n"
        self.assertEqual(extract.field_value(src, "姓名"), "")

    def test_field_not_found(self):
        src = "| 字段 | 内容 |\n|---|---|\n| 姓名 | 柳禾 |\n"
        self.assertEqual(extract.field_value(src, "外号"), "")

    def test_card_fields_builds_table_and_drops_missing(self):
        src = "| 字段 | 必填 | 内容 |\n|---|---|---|\n| 姓名 | (必) | 柳禾 |\n| 性别 | | 女 |\n"
        out = extract.card_fields(src, ["姓名", "外号", "性别"])
        self.assertIn("| 姓名 | 柳禾 |", out)
        self.assertIn("| 性别 | 女 |", out)
        self.assertNotIn("外号", out)


class TestExtractSectionTitles(unittest.TestCase):
    """extract.section_titles —— 只取 lv<=max_level；事件模板追加判定靠它。"""

    def test_respects_max_level(self):
        text = "# H1\n## H2\n### H3\n#### H4\n"
        self.assertEqual(extract.section_titles(text, max_level=3), ["H1", "H2", "H3"])

    def test_fence_aware(self):
        text = "## 真标题\n```\n## 围栏内不是标题\n```\n"
        self.assertEqual(extract.section_titles(text), ["真标题"])


class TestExtractTableRows(unittest.TestCase):
    """extract.table_rows —— 分隔行剔除；行首引用符让整表不可见。"""

    def test_drops_separator_row(self):
        rows = extract.table_rows("| a | b |\n|---|---|\n| 1 | 2 |\n")
        self.assertEqual(rows, [["a", "b"], ["1", "2"]])

    def test_blockquoted_table_is_invisible(self):
        """`> |` 开头 → 整表取不到（已知脆性，锁死以便日后有意识地改）。"""
        self.assertEqual(extract.table_rows("> | a | b |\n> | 1 | 2 |\n"), [])


class TestExtractDyBlock(unittest.TestCase):
    """extract.dy_block —— 标题必须含 DY-ID 才命中。"""

    SRC = "### 道义条目 · DY-001\n正文一。\n\n### 道义条目 · DY-002\n正文二。\n"

    def test_matches_when_id_in_heading(self):
        self.assertIn("正文一", extract.dy_block(self.SRC, "DY-001"))
        self.assertNotIn("正文二", extract.dy_block(self.SRC, "DY-001"))

    def test_no_id_in_heading_returns_empty(self):
        src = "### 道义条目一\n正文。\n"
        self.assertEqual(extract.dy_block(src, "DY-001"), "")


class TestExtractLedgerRows(unittest.TestCase):
    """extract.ledger_rows —— 首列必须是裸 ID；加粗即漏。"""

    def test_bare_id_matches(self):
        src = "| FH-069 | 埋 | 第3章 |\n| FH-070 | 收 | 第9章 |\n"
        rows = extract.ledger_rows(src, ["FH-069"])
        self.assertEqual(len(rows), 1)
        self.assertIn("FH-069", rows[0])

    def test_bold_id_does_not_match(self):
        src = "| **FH-069** | 埋 | 第3章 |\n"
        self.assertEqual(extract.ledger_rows(src, ["FH-069"]), [])


class TestExtractTailText(unittest.TestCase):
    """extract.tail_text —— 剥标题与分隔线，取尾 N 字。"""

    def test_strips_headings_and_rules(self):
        text = "# 章标题\n正文第一段。\n---\n正文最后一段。\n"
        out = extract.tail_text(text, chars=100)
        self.assertNotIn("章标题", out)
        self.assertNotIn("---", out)
        self.assertIn("正文最后一段", out)

    def test_truncates_to_tail(self):
        text = "正文\n" + "甲乙丙丁" * 50
        self.assertEqual(len(extract.tail_text(text, chars=20)), 20)


class TestExtractWrRulesNoFilter(unittest.TestCase):
    """extract.wr_rules(states=None) —— 用来判断「规则在、只是没一条命中状态」。"""

    def test_none_returns_all_wr_rows(self):
        text = ("## 【世界基本法则】\n\n| 规则ID | 名称 | 状态 | 内容 |\n|---|---|---|---|\n"
                "| WR-001 | a | 硬 | x |\n| WR-002 | b | 软 | y |\n")
        self.assertEqual(len(extract.wr_rules(text, states=None)), 2)
        self.assertEqual(len(extract.wr_rules(text, ("硬",))), 1)


class TestProgressIndex(unittest.TestCase):
    """progress.ProgressIndex 的测试。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_progress_index_status_of(self):
        """ProgressIndex.status_of 按后缀匹配查成熟度。"""
        _write(self.tmp / "00_进度.md", """
| 文件 | 状态 |
|---|---|
| `03_规划/01_第01部/01_卷01/规划_卷01_章0001.md` | 定稿 |
| `10_正文/01_第01部/01_卷01/章0001.md` | 待校验 |
""")
        idx = progress.ProgressIndex(self.tmp)
        self.assertEqual(idx.status_of(self.tmp / "03_规划/01_第01部/01_卷01/规划_卷01_章0001.md"), "定稿")
        self.assertEqual(idx.status_of(self.tmp / "10_正文/01_第01部/01_卷01/章0001.md"), "待校验")

    def test_progress_index_is_at_least(self):
        """is_at_least(path, "定稿") 对定稿返回 True，对待校验/草稿/未记录返回 False。"""
        _write(self.tmp / "00_进度.md", """
| 文件 | 状态 |
|---|---|
| `规划_卷01_章0001.md` | 定稿 |
| `规划_卷01_章0002.md` | 待校验 |
| `规划_卷01_章0003.md` | 草稿 |
""")
        idx = progress.ProgressIndex(self.tmp)
        self.assertTrue(idx.is_at_least(self.tmp / "规划_卷01_章0001.md", "定稿"))
        self.assertFalse(idx.is_at_least(self.tmp / "规划_卷01_章0002.md", "定稿"))
        self.assertFalse(idx.is_at_least(self.tmp / "规划_卷01_章0003.md", "定稿"))
        self.assertFalse(idx.is_at_least(self.tmp / "不存在的文件.md", "定稿"))

    def test_progress_index_not_exists(self):
        """00_进度.md 不存在时，ProgressIndex.exists 为 False。"""
        idx = progress.ProgressIndex(self.tmp)
        self.assertFalse(idx.exists)


class TestLeak(unittest.TestCase):
    """leak.scan 的测试。"""

    def test_leak_scan_flags_chapter_id(self):
        """leak.scan 标记非豁免段落里的章 ID 如 章0001。"""
        leaks = leak.scan("【任务】", "上一章（章0001）买的药不见了。")
        self.assertTrue(any("章0001" in lk.hit for lk in leaks))

    def test_leak_scan_exempt_sections(self):
        """leak.scan 跳过【输出格式】等豁免段落。"""
        leaks = leak.scan("【输出格式】", "落位到 `03_规划/01_第01部/01_卷01/规划_卷01_章0001.md`。")
        self.assertEqual(leaks, [])

    def test_leak_scan_no_leaks(self):
        """无泄漏时返回 []。"""
        leaks = leak.scan("【任务】", "主角醒了过来。")
        self.assertEqual(leaks, [])


class TestLayout(unittest.TestCase):
    """layout.resolve 的测试。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_layout_resolve_canonical_paths(self):
        """resolve 为第1部第1卷第2章生成规范路径。"""
        _write(self.tmp / "03_规划/01_第01部/01_卷01/.placeholder", "")
        _write(self.tmp / "10_正文/01_第01部/01_卷01/.placeholder", "")

        layout = L.resolve(self.tmp, part=1, volume=1, chapter=2)

        self.assertEqual(layout.chapter, 2)
        self.assertIn("第01部", str(layout.chapter_dir))
        self.assertIn("卷01", str(layout.chapter_dir))
        self.assertIn("章0002", str(layout.chapter_dir))
        # outline 和 manuscript 应该在规划/正文层
        self.assertIn("03_规划", str(layout.outline))
        self.assertIn("10_正文", str(layout.manuscript))

    def test_layout_workspace_dir_numbering(self):
        """章工作区目录编号：00/01/02 被标准子目录占用，章从 03 开始连续编号。

        已有 03_章0001，新建章0002 应得 04_章0002。
        """
        # 搭工作区
        _write(self.tmp / "05_工作区/03_第01部/03_卷01/03_章0001/02_状态/.placeholder", "")

        layout = L.resolve(self.tmp, part=1, volume=1, chapter=2)

        # 第二章应该用 04_ 前缀
        self.assertTrue(layout.chapter_dir.name.startswith("04_"))
        self.assertIn("章0002", layout.chapter_dir.name)

    def test_layout_reuse_existing_chapter_dir(self):
        """resolve 复用已存在的章目录，而不是创建新编号。"""
        # 建立 05_章0002
        existing = self.tmp / "05_工作区/03_第01部/03_卷01/05_章0002"
        _write(existing / "02_状态/.placeholder", "")

        layout = L.resolve(self.tmp, part=1, volume=1, chapter=2)

        self.assertEqual(layout.chapter_dir, existing)

    def test_layout_prebuild_creates_files_not_overwrite(self):
        """prebuild 只创建不存在的文件，不覆盖已存在的。"""
        layout = L.resolve(self.tmp, part=1, volume=1, chapter=1)

        # 预先创建目标文件
        target = layout.manuscript
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("original content\n", encoding="utf-8")

        # 调用 prebuild
        created = L.prebuild(layout, "00_单章细纲.md", target)

        # 目标文件内容不变
        self.assertEqual(target.read_text(encoding="utf-8"), "original content\n")
        # 其他文件被创建
        output_file = layout.output_dir / "00_单章细纲.md"
        self.assertTrue(output_file.exists())


class TestAssemble(unittest.TestCase):
    """assemble.build_manuscript 和 build_outline 的端到端测试。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.repo_root = Path(__file__).resolve().parents[3]  # /srv/unsafe/ai-novel

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_novel_fixture(self, with_chapter: int = 1):
        """创建最小可行的小说目录结构。返回 (novel_dir, layout)。"""
        novel_dir = self.tmp / "00_小说"
        novel_dir.mkdir()

        # 创建 00_进度.md（标记大纲定稿）
        _write(novel_dir / "00_进度.md", """
| 文件 | 状态 |
|---|---|
| `03_规划/01_第01部/01_卷01/规划_卷01.md` | 定稿 |
| `03_规划/01_第01部/01_卷01/规划_卷01_章0001.md` | 定稿 |
""")

        # 创建最小设定
        _write(novel_dir / "01_设定/00_主角档案.md", "# 主角\n主角的档案。\n")
        _write(novel_dir / "01_设定/00_红线包.md", "# 红线包\n基本约束。\n")
        _write(novel_dir / "01_设定/00_文风.md", "## 文风\n文风指南。\n")
        _write(novel_dir / "01_设定/00_禁用词表.md", "## 禁用词\n禁用词列表。\n")
        _write(novel_dir / "01_设定/00_小说概念.md",
               "## 【世界基本法则】\n| 规则ID | 名称 | 状态 | 内容 |\n|---|---|---|---|\n"
               "| WR-001 | 规则1 | 硬 | 内容1 |\n\n"
               "## 信息与认知法则\n认知规则。\n")
        _write(novel_dir / "01_设定/05_核心道义.md", "## DY-001 规则\n道义内容。\n")

        # 创建大纲
        outline_text = """# 第一章细纲

## 出场对象

| 对象 | 出场方式 | 备注 |
|---|---|---|
| @主角 | 登场 | |
| @人物.[李四] | 提及 | |

## 【场景列表】

### 第1场景 · 开始
500字。醒来。

### 第2场景 · 结束
500字。事件。

## 道义与感悟

无
"""
        _write(novel_dir / "03_规划/01_第01部/01_卷01/规划_卷01_章0001.md", outline_text)

        # 创建卷大纲
        volume_plan = """# 卷01大纲

## 【章节节拍表】

| 章节 | 一句话剧情摘要 | 必用模板 | 核心事件类型 | 钩子类型 |
|---|---|---|---|---|
| 第01章 | 主角醒来 | — | — | — |
"""
        _write(novel_dir / "03_规划/01_第01部/01_卷01/规划_卷01.md", volume_plan)

        # 创建开篇状态
        _write(novel_dir / "05_工作区/03_第01部/03_卷01/02_状态/00_开篇状态.md",
               "# 开篇状态\n## 主角\n- 位置：家里\n- 状态：睡眠中\n")

        # 创建一个人物卡
        _write(novel_dir / "02_数据库/07_人物/07_人物_李四.md",
               "# 李四\n## 身份\n配角\n## 背景\n故事中的配角。\n")

        layout = L.resolve(novel_dir, part=1, volume=1, chapter=1)
        return novel_dir, layout

    def test_build_manuscript_inline_content(self):
        """build_manuscript 生成的提示词包含人物卡内容（说明卡被内联了）。"""
        novel_dir, layout = self._make_novel_fixture()

        ctx = assemble.Ctx(
            novel_dir=novel_dir,
            repo_root=self.repo_root,
            layout=layout,
            novel_name="小说"
        )
        prompt = assemble.build_manuscript(ctx)
        rendered = prompt.render()

        # 检查六段骨架都在
        self.assertIn("【你的角色】", rendered)
        self.assertIn("【必读规则】", rendered)
        self.assertIn("【已有数据】", rendered)
        self.assertIn("【任务】", rendered)
        self.assertIn("【输出格式】", rendered)
        self.assertIn("【输出后自检】", rendered)

        # 检查人物卡被内联了
        self.assertIn("李四", rendered)
        self.assertIn("配角", rendered)

    def test_build_manuscript_no_leaks(self):
        """build_manuscript 的产出 leaks() 为空（无内部标识泄漏）。"""
        novel_dir, layout = self._make_novel_fixture()

        ctx = assemble.Ctx(
            novel_dir=novel_dir,
            repo_root=self.repo_root,
            layout=layout,
            novel_name="小说"
        )
        prompt = assemble.build_manuscript(ctx)

        # leaks() 本身已排除元指令段落，所以这里要求的是**一条都没有**。
        # 放宽成「只查某几类」会让这条测试失去意义：ch0002 那次泄漏
        # （修改项里写了「章0001 买的止咳散」）正是被当成「不关键」放过去的。
        leaks = prompt.leaks()
        self.assertEqual(
            leaks, [],
            "本工具撰写的叙事指令段落出现内部标识：\n" +
            "\n".join(lk.render() for lk in leaks))

    def test_build_manuscript_lettered_sections(self):
        """【已有数据】区块编号为 A. B. C. 无间隔。"""
        novel_dir, layout = self._make_novel_fixture()

        ctx = assemble.Ctx(
            novel_dir=novel_dir,
            repo_root=self.repo_root,
            layout=layout,
            novel_name="小说"
        )
        prompt = assemble.build_manuscript(ctx)
        rendered = prompt.render()

        # 手写字母前缀曾经断号（A/B/D/E）。这里直接把实际序列取出来比对，
        # 而不是「有 D 就顺便查一下 C」——后者在断成 A/B/D 时照样通过。
        data_section = rendered[rendered.find("# 【已有数据】"):rendered.find("# 【任务】")]
        letters = re.findall(r"^## ([A-Z])\. ", data_section, re.M)
        self.assertGreaterEqual(len(letters), 3, "【已有数据】区块太少，测不出编号连续性")
        expected = [chr(ord("A") + i) for i in range(len(letters))]
        self.assertEqual(letters, expected,
                         f"【已有数据】字母编号不连续：{letters}")

    def test_build_outline_prose_output_false(self):
        """build_outline 设置 prose_output=False，使得 leaks() 返回 []（细纲里编号是必需的）。"""
        novel_dir, layout = self._make_novel_fixture()

        ctx = assemble.Ctx(
            novel_dir=novel_dir,
            repo_root=self.repo_root,
            layout=layout,
            novel_name="小说"
        )
        prompt = assemble.build_outline(ctx)

        # prose_output 应该为 False
        self.assertFalse(prompt.prose_output)
        # leaks() 对细纲产出应该返回 []（即使文本包含"细纲"等）
        leaks = prompt.leaks()
        self.assertEqual(leaks, [])


class TestEventTemplates(unittest.TestCase):
    """assemble._event_templates —— 标准细纲字段【道义与感悟】不得把事件与感悟卡模板误拉进来。"""

    _EAI = "02_卡片模板/10_事件与感悟卡模板.md"
    _BP = "02_卡片模板/09_主角突破卡模板.md"

    def _paths(self, outline):
        return [p for _, p in assemble._event_templates(outline)]

    def test_standard_field_does_not_trigger(self):
        """每份细纲都有的「## 【道义与感悟】」不算「本章需要事件与感悟卡」的信号。"""
        outline = ("# 细纲\n## 【基础信息】\n| 关联卷大纲节点 | 第4章 / 核心事件类型：突破/晋升 |\n"
                   "## 【场景列表】\n### 第1场景\n内容\n## 【道义与感悟】\n| 本章落地道义 | X |\n")
        self.assertEqual(self._paths(outline), [self._BP])

    def test_core_event_type_decides_when_present(self):
        outline = ("# 细纲\n## 【基础信息】\n| 关联卷大纲节点 | 第3章 / 核心事件类型：抉择/道义 / 钩子：重钩 |\n"
                   "## 【道义与感悟】\n内容\n")
        self.assertEqual(self._paths(outline), [self._EAI])

    def test_fallback_scan_excludes_standard_headings(self):
        """没有「关联卷大纲节点」字段（旧格式）时退回扫标题，仍剔除标准字段。"""
        outline = "# 细纲\n## 出场对象\n表\n## 【场景列表】\n### 第1场景\nx\n## 道义与感悟\n无\n"
        self.assertEqual(self._paths(outline), [])

    def test_fallback_scan_still_catches_real_event_block(self):
        outline = "# 细纲\n## 出场对象\n表\n## 【战斗设计】\n打\n## 道义与感悟\n无\n"
        self.assertEqual(self._paths(outline), ["02_卡片模板/08_战斗结算模板.md"])


class TestFhRegistryBlock(unittest.TestCase):
    """assemble._fh_registry_block —— 总纲 / 卷册的异构登记行按来源分组，不裸拼成坏表。"""

    def test_groups_by_source_and_id(self):
        lg = ["| FH-067 | 弃矿沟古玉共鸣 | 整书 | ... | 卷2 | 活跃 |"]
        vl = ["| FH-067 | 弃矿沟古玉共鸣 | ... | 活跃(已埋设) |",
              "| FH-067 | 弃矿沟古玉共鸣 | ... | 已回收（阶段性） |"]
        out = assemble._fh_registry_block(["FH-067"], lg, vl)
        self.assertIn("**FH-067**", out)
        self.assertEqual(out.count("〔伏笔总纲〕"), 1)
        self.assertEqual(out.count("〔卷伏笔册〕"), 2)
        # 写明「以节拍摘要为准」，别让云端拿登记行判断推进/回收
        self.assertIn("节拍摘要为准", out)

    def test_empty_when_no_rows(self):
        self.assertEqual(assemble._fh_registry_block(["FH-001"], [], []), "")

    def test_bold_id_row_still_grouped(self):
        vl = ["| **FH-068** | 活矿邪法 | ... |"]
        out = assemble._fh_registry_block(["FH-068"], [], vl)
        self.assertIn("**FH-068**", out)
        self.assertIn("〔卷伏笔册〕", out)


class TestBuildPromptCLI(unittest.TestCase):
    """build_prompt.py 的 CLI 集成测试。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.repo_root = Path(__file__).resolve().parents[3]
        self.build_prompt_py = self.repo_root / "02_工具/01_小说通用工具/build_prompt.py"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_novel_fixture_draft(self):
        """创建大纲为草稿状态的小说。"""
        novel_dir = self.tmp / "00_小说"
        novel_dir.mkdir()

        # 大纲标记为草稿（不是定稿）
        _write(novel_dir / "00_进度.md", """
| 文件 | 状态 |
|---|---|
| `03_规划/01_第01部/01_卷01/规划_卷01_章0001.md` | 草稿 |
""")

        # 最小设定
        _write(novel_dir / "01_设定/00_红线包.md", "# 红线包\n约束。\n")

        # 大纲
        _write(novel_dir / "03_规划/01_第01部/01_卷01/规划_卷01_章0001.md",
               "# 大纲\n## 出场对象\n| 对象 |\n|---|\n| @主角 |\n")

        return novel_dir

    def test_cli_gate_blocks_draft_outline(self):
        """CLI：大纲为草稿时阻断，返回码 2，无提示词文件写出。"""
        novel_dir = self._make_novel_fixture_draft()

        result = subprocess.run(
            [sys.executable, str(self.build_prompt_py),
             "--novel", str(novel_dir),
             "--task", "正文",
             "--chapter", "1"],
            capture_output=True,
            text=True
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("阻断报告", result.stdout)
        # 不应该写出提示词存档
        archive = novel_dir / "05_工作区/03_第01部/03_卷01/03_章0001/00_提示词/01_正文生成.md"
        self.assertFalse(archive.exists())


class TestPrevSummaryFile(unittest.TestCase):
    """assemble.read_prev_summary / write_prev_summary —— 上章摘要文件读写。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_missing_and_placeholder_return_none(self):
        f = self.tmp / "上章摘要.md"
        self.assertIsNone(assemble.read_prev_summary(f))
        _write(f, "")
        self.assertIsNone(assemble.read_prev_summary(f))
        _write(f, ">>> 待人工确认：上章摘要缺失\n>>> 提示\n")
        self.assertIsNone(assemble.read_prev_summary(f))

    def test_roundtrip_plain_text_no_marker(self):
        f = self.tmp / "上章摘要.md"
        assemble.write_prev_summary(f, "  苏砚吞灵草突破，经脉受损。  ")
        raw = f.read_text(encoding="utf-8")
        self.assertNotIn("<!--", raw)                        # 不留任何标记
        self.assertEqual(assemble.read_prev_summary(f), "苏砚吞灵草突破，经脉受损。")


class TestChapterOpenerCLI(unittest.TestCase):
    """build_state_snapshot.py --chapter-opener —— 单章开篇状态物化。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.repo_root = Path(__file__).resolve().parents[3]
        self.bss = self.repo_root / "02_工具/01_小说通用工具/build_state_snapshot.py"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _novel(self):
        nd = self.tmp / "00_小说"
        (nd / "05_工作区/02_状态/00_基线状态").mkdir(parents=True)
        _write(nd / "05_工作区/02_状态/00_基线状态/00_说明.md", "# 基线\n> 只读。\n")
        _write(nd / "05_工作区/02_状态/00_基线状态/01_角色/01_角色_苏砚.md",
               "| 对象ID | 字段 | 类型 | 值 |\n| --- | --- | --- | --- |\n"
               "| 角色.苏砚 | 境界 | 运算-枚举 | 凡人 |\n")
        _write(nd / "05_工作区/02_状态/00_基线状态/01_角色/01_角色_柳禾.md",
               "| 对象ID | 字段 | 类型 | 值 |\n| --- | --- | --- | --- |\n"
               "| 角色.柳禾 | 身体状况 | 描述 | 肺痨晚期 |\n")
        _write(nd / "01_设定/00_主角档案.md",
               "# 主角\n| 字段 | 必填 | 内容 |\n|---|---|---|\n| 姓名 | (必) | 苏砚 |\n")
        return nd

    def test_single_chapter_opener_canonical_header_and_cast_trim(self):
        nd = self._novel()
        # 章0002 细纲只点名苏砚
        _write(nd / "03_规划/01_第01部/01_卷01/规划_卷01_章0002.md",
               "# 细纲\n## 出场对象\n| 对象ID | 出场方式 |\n|---|---|\n| `@主角` | 登场 |\n")
        chap_state = nd / "05_工作区/03_第01部/03_卷01/04_章0002/02_状态"
        r = subprocess.run(
            [sys.executable, str(self.bss), "--chapter-opener", str(chap_state),
             "--novel-dir", str(nd)],
            capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        body = (chap_state / "00_开篇状态.md").read_text(encoding="utf-8")
        self.assertTrue(body.startswith("# 本章开篇状态 · 03_第01部/03_卷01/04_章0002"))
        self.assertIn("角色.苏砚", body)
        self.assertNotIn("角色.柳禾", body)      # 被出场对象清单裁掉

    def test_missing_baseline_exits_1(self):
        nd = self.tmp / "空小说"
        (nd / "05_工作区/03_第01部/03_卷01/03_章0001/02_状态").mkdir(parents=True)
        r = subprocess.run(
            [sys.executable, str(self.bss), "--chapter-opener",
             str(nd / "05_工作区/03_第01部/03_卷01/03_章0001/02_状态"),
             "--novel-dir", str(nd)],
            capture_output=True, text=True)
        self.assertEqual(r.returncode, 1)
        self.assertIn("基线", r.stdout + r.stderr)


class TestPreparePhaseCLI(unittest.TestCase):
    """build_prompt.py PREPARE 阶段：--dry-run 零副作用；正式跑物化开篇状态。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.repo_root = Path(__file__).resolve().parents[3]
        self.build_prompt_py = self.repo_root / "02_工具/01_小说通用工具/build_prompt.py"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _novel(self):
        nd = self.tmp / "00_小说"
        nd.mkdir()
        _write(nd / "00_进度.md",
               "| 文件 | 状态 |\n|---|---|\n"
               "| `03_规划/01_第01部/01_卷01/规划_卷01.md` | 定稿 |\n")
        _write(nd / "01_设定/00_红线包.md", "# 红线包\n约束。\n")
        _write(nd / "01_设定/00_主角档案.md",
               "# 主角\n| 字段 | 必填 | 内容 |\n|---|---|---|\n| 姓名 | (必) | 苏砚 |\n")
        _write(nd / "03_规划/01_第01部/01_卷01/规划_卷01.md",
               "# 卷01大纲\n## 【章节节拍表】\n"
               "| 章节 | 一句话剧情摘要 | 必用模板 | 核心事件类型 | 钩子类型 |\n"
               "|---|---|---|---|---|\n| 第01章 | 主角醒来 | — | — | — |\n")
        (nd / "05_工作区/02_状态/00_基线状态").mkdir(parents=True)
        _write(nd / "05_工作区/02_状态/00_基线状态/00_说明.md", "# 基线\n> 只读。\n")
        _write(nd / "05_工作区/02_状态/00_基线状态/01_角色/01_角色_苏砚.md",
               "| 对象ID | 字段 | 类型 | 值 |\n| --- | --- | --- | --- |\n"
               "| 角色.苏砚 | 境界 | 运算-枚举 | 凡人 |\n")
        return nd

    def _run(self, nd, *extra):
        return subprocess.run(
            [sys.executable, str(self.build_prompt_py), "--novel", str(nd),
             "--task", "细纲", "--chapter", "1", *extra],
            capture_output=True, text=True)

    def test_dry_run_writes_nothing(self):
        nd = self._novel()
        r = self._run(nd, "--dry-run")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("--dry-run", r.stdout)
        opener = nd / "05_工作区/03_第01部/03_卷01/03_章0001/02_状态/00_开篇状态.md"
        archive = nd / "05_工作区/03_第01部/03_卷01/03_章0001/00_提示词/00_单章细纲.md"
        self.assertFalse(opener.exists(), "--dry-run 不该物化开篇状态")
        self.assertFalse(archive.exists(), "--dry-run 不该写提示词存档")

    def test_real_run_materializes_opener(self):
        nd = self._novel()
        r = self._run(nd)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        opener = nd / "05_工作区/03_第01部/03_卷01/03_章0001/02_状态/00_开篇状态.md"
        self.assertTrue(opener.exists())
        self.assertTrue(opener.read_text(encoding="utf-8").startswith("# 本章开篇状态"))
        self.assertIn("开篇状态", r.stdout)

    def test_gate_blocks_when_prev_changelog_missing(self):
        """章2 细纲：上一章正文落位但履历还没写 → GATE 阻断（开篇状态会漏上章变化）。"""
        nd = self._novel()
        _write(nd / "00_进度.md",
               "| 文件 | 状态 |\n|---|---|\n"
               "| `03_规划/01_第01部/01_卷01/规划_卷01.md` | 定稿 |\n")
        _write(nd / "10_正文/01_第01部/01_卷01/章0001.md", "苏砚醒来。\n" * 50)
        r = subprocess.run(
            [sys.executable, str(self.build_prompt_py), "--novel", str(nd),
             "--task", "细纲", "--chapter", "2"],
            capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        self.assertIn("上一章状态履历未写", r.stdout)


class TestManifest(unittest.TestCase):
    """任务输入清单（manifest.py）与派生视图。"""

    def setUp(self):
        from prompt_build import manifest
        self.manifest = manifest
        self.repo_root = Path(__file__).resolve().parents[3]

    def test_real_manifest_parses(self):
        man = self.manifest.load(self.repo_root)
        self.assertEqual(set(man.tasks), {"正文", "细纲"})
        for t in man.tasks.values():
            self.assertTrue(t.steps)
            for st in t.steps:
                self.assertIn(st.into, t.segments)

    def test_bad_resolver_rejected(self):
        toml = ('[task."正文"]\nsegments = ["a"]\n\n'
                '[[task."正文".step]]\ninto = "a"\nsource = "resolver:不存在的"\nmode = "resolver"\n')
        with self.assertRaises(self.manifest.ManifestError):
            self.manifest.parse(toml)

    def test_sections_mode_needs_sections(self):
        toml = ('[task."正文"]\nsegments = ["a"]\n\n'
                '[[task."正文".step]]\ninto = "a"\nsource = "tpl:x.md"\nmode = "sections"\n')
        with self.assertRaises(self.manifest.ManifestError):
            self.manifest.parse(toml)

    def test_into_must_be_in_segments(self):
        toml = ('[task."正文"]\nsegments = ["a"]\n\n'
                '[[task."正文".step]]\ninto = "b"\nsource = "authored:no_invent"\nmode = "authored"\n')
        with self.assertRaises(self.manifest.ManifestError):
            self.manifest.parse(toml)

    def test_derived_view_matches_toml(self):
        import build_prompt_manifest as bpm
        man = self.manifest.load(self.repo_root)
        want = bpm.render(man)
        have = (self.repo_root / bpm.VIEW_REL).read_text(encoding="utf-8")
        self.assertEqual(have, want,
                         "任务输入清单.md 与 TOML 不一致——跑 build_prompt_manifest.py --write")


class TestManifestGolden(TestAssemble):
    """清单驱动拼装的字节稳定性：合成 fixture 渲染出的提示词哈希锁定。

    用 fixture（不用真实小说数据——后者随创作变）。改 assemble.py / 清单 / resolver
    导致 fixture 渲染变化时本测试失败：若是无意的回归，查原因；若是有意的（Phase 2
    瘦身），重跑本测试取新哈希填进 GOLDEN，并在提交信息里说明变了什么。
    """

    # Phase 1（清单驱动重构）落地时逐字节核对过 == 重构前。
    # Phase 2（2026-09-06）正文清单收窄（fixture 是第 1 章，走 opening 分支）：
    #   - 删 07_单章细纲模板 整份内联（写手用填好的细纲）
    #   - 人物卡只取【基础/修行/内核/与主角关系/创作标签】五块（势力/地理卡仍整份）
    #   - 开篇三章设计指南只取 一~五 节（去「使用范围」「七、适配说明」meta）
    #   - 顺带修好：「六、契约自检清单」以前因标题名写错（找「六、开篇三章契约自检清单」、
    #     实际是「六、三章整体契约自检清单」）从没被单独提取过——现在正确落进【输出后自检】，
    #     不再靠整份内联夹带；ch1-3 提示词因此更贴规格。
    # 合并 master 的 ch0003 分支（2026-09-06）后哈希再变：
    #   - MANUSCRIPT：① _strip_template_framing 剥掉 task2 里模板自带的「【任务】写第X章」+
    #     「## 执行要求」两行；② 00_通用写作规则_校验版 与 00_红线包/00_文风 加了 §6.3
    #     「计谋/推演类内心活动限制」，随内联进【必读规则】/【输出后自检】。
    #   - OUTLINE：00_通用写作规则_生成版 §6.3 随内联进【必读模板】。
    # 2026-09-07：_event_templates 修好误触发——标准细纲字段（每份都有的【道义与感悟】）
    #   里的「道义」二字不再把「事件与感悟卡模板」误拉进正文提示词；有「关联卷大纲节点」
    #   字段时优先按其中的「核心事件类型/必用模板」判定。fixture 走后一路（无该字段、
    #   section 扫描剔除标准字段），不再内联 10_事件与感悟卡模板 → MANUSCRIPT 哈希缩小。
    # 2026-09-09：build_prompt 四段化（GATE/PREPARE/ASSEMBLE/EMIT）。OUTLINE 哈希两处变：
    #   ① _rv_opener_state_outline 的「开篇状态未物化」占位文案改了措辞（fixture 章1 的
    #      opener 落卷级目录、layout 查章级 → 命中占位分支）；
    #   ② 07_单章细纲模板.md「## 0. 上下文滑动窗口」注释行改写（摘要由 build_prompt 拼装时
    #      LLM 生成、作者发云端前自行复核，不再是「定稿后 Agent 填入」）——该模板整份内联进
    #      细纲提示词，措辞两次微调各改一次哈希。
    #   MANUSCRIPT 不变（正文不内联细纲模板，opener 走 layout step、缺失静默留空）。
    # 2026-09-09（古籍政策 + 细纲缺陷根治）：`00_通用写作规则` §八【版权层】checklist 改口径
    #   （现实古籍文句正文可用、只须不具名）→ 随 `_校验版` 切片内联进 MANUSCRIPT 与 OUTLINE 两者。
    #   `07_单章细纲模板` 加「出场对象只放建卡对象/白名单」「内容简述禁结构指代」「新设定不计已有设定」
    #   「轻埋伏笔不剧透」，只内联进 OUTLINE。
    # 2026-09-10（弱模型胜任 · 第一期）：`07_单章细纲模板` 出场对象注加「配角一律 `@人物.[姓名]`，
    #   `@角色.X` 是持有者/师承字段写法、别用在本表」——只内联进 OUTLINE。
    GOLDEN_MANUSCRIPT = "c915b798afafa87c81792de177ae90b7b7945cabf0c412bc9d445f29c64c29e7"
    GOLDEN_OUTLINE = "731e3ecbecdda5c5403b11290fc7287c05061540795430ec610d44ca9138e657"

    def _hash(self, text):
        import hashlib
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def test_manuscript_render_stable(self):
        novel_dir, layout = self._make_novel_fixture()
        ctx = assemble.Ctx(novel_dir=novel_dir, repo_root=self.repo_root,
                           layout=layout, novel_name="小说")
        h = self._hash(assemble.build_manuscript(ctx).render())
        if self.GOLDEN_MANUSCRIPT == "8fb1b2f5c8d1e2a3":
            self.skipTest(f"GOLDEN_MANUSCRIPT 未填，当前：{h}")
        self.assertEqual(h, self.GOLDEN_MANUSCRIPT)

    def test_outline_render_stable(self):
        novel_dir, layout = self._make_novel_fixture()
        ctx = assemble.Ctx(novel_dir=novel_dir, repo_root=self.repo_root,
                           layout=layout, novel_name="小说")
        h = self._hash(assemble.build_outline(ctx).render())
        if self.GOLDEN_OUTLINE == "8fb1b2f5c8d1e2a3":
            self.skipTest(f"GOLDEN_OUTLINE 未填，当前：{h}")
        self.assertEqual(h, self.GOLDEN_OUTLINE)


if __name__ == "__main__":
    unittest.main()
