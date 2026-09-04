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


if __name__ == "__main__":
    unittest.main()
