# -*- coding: utf-8 -*-
"""progress_report.py 与 audit/rules/progress.py 的单元测试套件。

每个用例在临时目录里搭最小的小说结构，验证进度对账的各个环节。
测试不依赖真实小说数据，仅验证工具逻辑正确性。
"""
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "01_小说通用工具"))
import progress_report
from audit.context import AuditContext
from audit.rules.progress import ProgressRule
from audit.models import Severity


def _write(path: Path, text: str):
    """辅助方法：创建并写入文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _read(path: Path) -> str:
    """辅助方法：读文件，不存在返回空串。"""
    return path.read_text(encoding="utf-8") if path.exists() else ""


def build_novel_fixture(temp_dir: Path, **opts) -> Path:
    """构建最小可行的小说目录结构。

    参数：
    - progress_table (str): 00_进度.md 的内容，默认为空表
    - outline_exists (bool): 是否创建 03_规划 细纲，默认 True
    - manuscript_exists (bool): 是否创建 10_正文 正文，默认 True
    - manuscript_text (str): 正文内容，包含测试用的汉字
    - has_changelog (bool): 是否创建 01_状态履历.md，默认 False
    - has_opener (bool): 是否创建 00_开篇状态.md，默认 False
    - cold_read_record (str): 02_正文校验记录.md 内容，默认无（无冷读记录）
    - revision_count (int): 创建多少个 01_正文生成_修订*.md 文件，默认 0
    - merged_upto (str): 00_同步状态.md 中的 折叠至章 值，默认 "—"

    返回 novel_dir 路径。
    """
    novel_dir = temp_dir / "小说测试"
    novel_dir.mkdir(exist_ok=True)

    # 进度表（如果给了自定义版本就用，否则空）
    progress_table = opts.get("progress_table", "")
    if progress_table:
        _write(novel_dir / "00_进度.md", progress_table)

    # 细纲
    if opts.get("outline_exists", True):
        outline_text = """# 第一章细纲
## 出场对象
| 对象 | 出场方式 |
|---|---|
| @主角 | 登场 |
"""
        _write(novel_dir / "03_规划/01_第01部/01_卷01/规划_卷01_章0001.md", outline_text)

    # 正文
    if opts.get("manuscript_exists", True):
        manuscript_text = opts.get("manuscript_text", "# 第一章\n这是一篇测试正文。包含一些汉字来测试计数。\n" * 5)
        _write(novel_dir / "10_正文/01_第01部/01_卷01/章0001.md", manuscript_text)

    # 工作区状态
    if opts.get("has_changelog", False):
        _write(novel_dir / "05_工作区/03_第01部/03_卷01/03_章0001/02_状态/01_状态履历.md", "# 履历\n")

    if opts.get("has_opener", False):
        _write(novel_dir / "05_工作区/03_第01部/03_卷01/03_章0001/02_状态/00_开篇状态.md", "# 开篇状态\n")

    # 冷读记录
    cold_read_content = opts.get("cold_read_record")
    if cold_read_content is not None:
        _write(novel_dir / "05_工作区/03_第01部/03_卷01/03_章0001/02_状态/02_正文校验记录.md", cold_read_content)

    # 修订文件
    for i in range(1, opts.get("revision_count", 0) + 1):
        _write(novel_dir / f"05_工作区/03_第01部/03_卷01/03_章0001/00_提示词/01_正文生成_修订{i}.md", f"修订 {i}")

    # 同步状态
    merged_upto = opts.get("merged_upto", "—")
    sync_text = f"# 同步状态\n\n折叠至章: {merged_upto}\n对象总数: 100\n"
    _write(novel_dir / "05_工作区/02_状态/01_最新状态/00_同步状态.md", sync_text)

    return novel_dir


class TestHanCount(unittest.TestCase):
    """han_count 的单元测试。"""

    def test_han_count_basic(self):
        """汉字计数：仅计算 CJK 字符，不计 ASCII、标点、空格。"""
        text = "这是一篇文章。包含123个汉字与English混合。"
        count = progress_report.han_count(text)
        # 这、是、一、篇、文、章、包、含、个、汉、字、与、混、合 = 14 个汉字
        self.assertEqual(count, 14)

    def test_han_count_exclude_headings(self):
        """汉字计数不包含标题行（以 # 开头的行）。"""
        text = """# 标题中有汉字测试
这是正文内容。
## 第二个标题
更多内容。"""
        count = progress_report.han_count(text)
        # 标题行「标题中有汉字测试」(8) 不计
        # 正文「这是正文内容」(6) + 标题「第二个标题」(5,不计) + 「更多内容」(4) = 10
        self.assertEqual(count, 10)

    def test_han_count_no_text(self):
        """无汉字时返回 0。"""
        text = "123 ABC !@# \n"
        count = progress_report.han_count(text)
        self.assertEqual(count, 0)


class TestDeclaredStatusAndLookup(unittest.TestCase):
    """declared_status 与 lookup 的测试。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_declared_status_parses_table(self):
        """declared_status 从 00_进度.md 表格行解析 {路径: 成熟度}。"""
        novel_dir = self.tmp / "小说"
        novel_dir.mkdir()

        table = """| 文件 | 状态 |
|---|---|
| `03_规划/01_第01部/01_卷01/规划_卷01_章0001.md` | 定稿 |
| `10_正文/01_第01部/01_卷01/章0001.md` | 待校验 |
"""
        _write(novel_dir / "00_进度.md", table)

        result = progress_report.declared_status(novel_dir)

        self.assertEqual(result["03_规划/01_第01部/01_卷01/规划_卷01_章0001.md"], "定稿")
        self.assertEqual(result["10_正文/01_第01部/01_卷01/章0001.md"], "待校验")

    def test_declared_status_skips_separator_row(self):
        """declared_status 跳过分隔符行 |---|---|。"""
        novel_dir = self.tmp / "小说"
        novel_dir.mkdir()

        table = """| 文件 | 状态 |
|---|---|
| `文件.md` | 定稿 |
"""
        _write(novel_dir / "00_进度.md", table)

        result = progress_report.declared_status(novel_dir)

        # 应该只有 1 项，不含分隔符行
        self.assertEqual(len(result), 1)
        self.assertIn("文件.md", result)

    def test_lookup_matches_by_suffix(self):
        """lookup 按路径后缀匹配（既可全路径也可裸文件名）。"""
        novel_dir = self.tmp / "小说"
        novel_dir.mkdir()

        declared = {
            "章0001.md": "定稿",
            "03_规划/01_第01部/01_卷01/规划_卷01_章0002.md": "待校验"
        }

        # 全路径匹配
        path1 = novel_dir / "10_正文/01_第01部/01_卷01/章0001.md"
        result1 = progress_report.lookup(declared, path1, novel_dir)
        self.assertEqual(result1, "定稿")

        # 完整路径也应该匹配
        path2 = novel_dir / "03_规划/01_第01部/01_卷01/规划_卷01_章0002.md"
        result2 = progress_report.lookup(declared, path2, novel_dir)
        self.assertEqual(result2, "待校验")

    def test_lookup_returns_none_for_missing(self):
        """lookup 找不到返回 None。"""
        novel_dir = self.tmp / "小说"
        declared = {"某文件.md": "定稿"}

        path = novel_dir / "10_正文/不存在的文件.md"
        result = progress_report.lookup(declared, path, novel_dir)

        self.assertIsNone(result)


class TestCollect(unittest.TestCase):
    """collect 的单元测试。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_collect_merges_chapters(self):
        """collect 从 03_规划 和 10_正文 采集章节，合并成单个 Chapter 对象。"""
        novel_dir = build_novel_fixture(self.tmp, outline_exists=True, manuscript_exists=True)

        rep = progress_report.collect(novel_dir)

        # 应该有 1 个章节
        self.assertEqual(len(rep.chapters), 1)
        ch = rep.chapters[0]
        # 细纲和正文都应该被采集
        self.assertIsNotNone(ch.outline)
        self.assertIsNotNone(ch.manuscript)

    def test_collect_words_count(self):
        """collect 设置 words 为正文的 han_count。"""
        manuscript_text = "这是文章。" * 10  # 4 汉字 * 10 = 40 汉字（不计点号）
        novel_dir = build_novel_fixture(
            self.tmp,
            manuscript_text=manuscript_text,
            manuscript_exists=True
        )

        rep = progress_report.collect(novel_dir)

        self.assertEqual(len(rep.chapters), 1)
        self.assertEqual(rep.chapters[0].words, 40)

    def test_collect_cold_rounds(self):
        """collect 计数 02_正文校验记录.md 中的 ## 冷读… 分节。"""
        cold_record = """# 校验记录

## 冷读轮次1
- 发现 A

## 冷读轮次2
- 发现 B

## 其他内容
不算冷读
"""
        novel_dir = build_novel_fixture(
            self.tmp,
            cold_read_record=cold_record
        )

        rep = progress_report.collect(novel_dir)

        self.assertEqual(rep.chapters[0].cold_rounds, 2)

    def test_collect_revision_rounds(self):
        """collect 计数 00_提示词 下的 01_正文生成_修订*.md 文件。"""
        novel_dir = build_novel_fixture(self.tmp, revision_count=3)

        rep = progress_report.collect(novel_dir)

        self.assertEqual(rep.chapters[0].revision_rounds, 3)

    def test_collect_merged_flag(self):
        """collect 根据 00_同步状态.md 的 折叠至章 设置 merged 标志。

        章号 <= 折叠至章 时 merged=True，否则 False。
        """
        # 折叠至 章0001
        novel_dir = build_novel_fixture(self.tmp, merged_upto="章0001")
        rep = progress_report.collect(novel_dir)
        self.assertTrue(rep.chapters[0].merged)

        # 折叠至 章0000（或 —）时都不折叠
        temp2 = Path(tempfile.mkdtemp())
        try:
            novel_dir2 = build_novel_fixture(temp2, merged_upto="—")
            rep2 = progress_report.collect(novel_dir2)
            self.assertFalse(rep2.chapters[0].merged)
        finally:
            shutil.rmtree(temp2, ignore_errors=True)


class TestReconcile(unittest.TestCase):
    """reconcile 及三种漂移码 PROGRESS001/002/003 的测试。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_progress001_missing_canonical_file(self):
        """PROGRESS001：进度表声明了成熟度，但 canonical 产出文件不存在。"""
        novel_dir = self.tmp / "小说"
        novel_dir.mkdir()

        # 进度表声明了一个不存在的文件
        _write(novel_dir / "00_进度.md", """| 文件 | 状态 |
|---|---|
| `10_正文/01_第01部/01_卷01/章0001.md` | 定稿 |
""")

        declared = progress_report.declared_status(novel_dir)
        rep = progress_report.Report(novel_dir=novel_dir, novel_name="小说")
        findings = progress_report.reconcile(novel_dir, declared, rep)

        # 应该有 PROGRESS001 错误
        codes = [code for _, code, _ in findings]
        self.assertIn("PROGRESS001", codes)
        # 应该指向那个缺失的文件
        self.assertTrue(any("10_正文" in msg for _, _, msg in findings))

    def test_progress001_skip_placeholder_paths(self):
        """PROGRESS001 不报包含 0N/NN/XX 等占位符的路径。

        这些是规则系统中通配写法，不是真实文件路径。
        """
        novel_dir = self.tmp / "小说"
        novel_dir.mkdir()

        # 进度表用占位符写法
        _write(novel_dir / "00_进度.md", """| 文件 | 状态 |
|---|---|
| `03_规划/01_第01部/0N_卷0N/规划_卷0N_章0N.md` | 定稿 |
""")

        declared = progress_report.declared_status(novel_dir)
        rep = progress_report.Report(novel_dir=novel_dir, novel_name="小说")
        findings = progress_report.reconcile(novel_dir, declared, rep)

        # 不应该报 PROGRESS001（占位符不需要存在）
        codes = [code for _, code, _ in findings]
        self.assertNotIn("PROGRESS001", codes)

    def test_progress001_skip_non_canonical_paths(self):
        """PROGRESS001 只管 01_设定/02_数据库/03_规划/10_正文 开头的路径。

        工作区文件或其他非 canonical 路径不检查。
        """
        novel_dir = self.tmp / "小说"
        novel_dir.mkdir()

        # 进度表里有工作区文件（非 canonical）
        _write(novel_dir / "00_进度.md", """| 文件 | 状态 |
|---|---|
| `05_工作区/02_状态/01_最新状态/00_同步状态.md` | 定稿 |
| `01_状态履历.md` | 待校验 |
""")

        declared = progress_report.declared_status(novel_dir)
        rep = progress_report.Report(novel_dir=novel_dir, novel_name="小说")
        findings = progress_report.reconcile(novel_dir, declared, rep)

        # 不应该报 PROGRESS001（非 canonical 路径被跳过）
        codes = [code for _, code, _ in findings]
        self.assertNotIn("PROGRESS001", codes)

    def test_progress002_unregistered_manuscript(self):
        """PROGRESS002：正文文件存在，但 00_进度.md 没登记。"""
        novel_dir = build_novel_fixture(
            self.tmp,
            progress_table="",  # 进度表为空，未登记任何文件
            manuscript_exists=True
        )

        declared = progress_report.declared_status(novel_dir)
        rep = progress_report.collect(novel_dir)
        findings = progress_report.reconcile(novel_dir, declared, rep)

        # 应该有 PROGRESS002 警告
        codes = [code for _, code, _ in findings]
        self.assertIn("PROGRESS002", codes)

    def test_progress002_registered_no_warning(self):
        """PROGRESS002 不报已在进度表中登记的文件。"""
        novel_dir = build_novel_fixture(
            self.tmp,
            progress_table="""| 文件 | 状态 |
|---|---|
| `03_规划/01_第01部/01_卷01/规划_卷01_章0001.md` | 定稿 |
| `10_正文/01_第01部/01_卷01/章0001.md` | 待校验 |
""",
            manuscript_exists=True,
            outline_exists=True,
            cold_read_record="# 记录\n## 冷读1\n测试\n"
        )

        declared = progress_report.declared_status(novel_dir)
        rep = progress_report.collect(novel_dir)
        findings = progress_report.reconcile(novel_dir, declared, rep)

        # 不应该有关于该文件的 PROGRESS002
        codes = [code for _, code, _ in findings]
        self.assertNotIn("PROGRESS002", codes)

    def test_progress003_missing_changelog_when_finalized(self):
        """PROGRESS003：正文标「定稿」但缺 02_状态/01_状态履历.md。"""
        novel_dir = build_novel_fixture(
            self.tmp,
            progress_table="""| 文件 | 状态 |
|---|---|
| `10_正文/01_第01部/01_卷01/章0001.md` | 定稿 |
""",
            has_changelog=False  # 没有履历
        )

        declared = progress_report.declared_status(novel_dir)
        rep = progress_report.collect(novel_dir)
        findings = progress_report.reconcile(novel_dir, declared, rep)

        # 应该有 PROGRESS003 警告（缺履历）
        codes = [code for _, code, _ in findings]
        self.assertIn("PROGRESS003", codes)

    def test_progress003_unmerged_when_finalized(self):
        """PROGRESS003：正文标「定稿」有履历，但折叠至章线未到。"""
        novel_dir = build_novel_fixture(
            self.tmp,
            progress_table="""| 文件 | 状态 |
|---|---|
| `10_正文/01_第01部/01_卷01/章0001.md` | 定稿 |
""",
            has_changelog=True,
            merged_upto="—"  # 未折叠
        )

        declared = progress_report.declared_status(novel_dir)
        rep = progress_report.collect(novel_dir)
        findings = progress_report.reconcile(novel_dir, declared, rep)

        # 应该有 PROGRESS003 警告（未折叠）
        codes = [code for _, code, _ in findings]
        self.assertIn("PROGRESS003", codes)

    def test_progress003_no_cold_read_records(self):
        """PROGRESS003：正文标「定稿」或「待校验」但无冷读记录。"""
        novel_dir = build_novel_fixture(
            self.tmp,
            progress_table="""| 文件 | 状态 |
|---|---|
| `10_正文/01_第01部/01_卷01/章0001.md` | 定稿 |
""",
            cold_read_record=None  # 无冷读记录文件
        )

        declared = progress_report.declared_status(novel_dir)
        rep = progress_report.collect(novel_dir)
        findings = progress_report.reconcile(novel_dir, declared, rep)

        # 应该有 PROGRESS003 警告（无冷读）
        codes = [code for _, code, _ in findings]
        self.assertIn("PROGRESS003", codes)

    def test_reconcile_fully_consistent(self):
        """完全一致的小说应产生零对账项。"""
        novel_dir = build_novel_fixture(
            self.tmp,
            progress_table="""| 文件 | 状态 |
|---|---|
| `03_规划/01_第01部/01_卷01/规划_卷01_章0001.md` | 定稿 |
| `10_正文/01_第01部/01_卷01/章0001.md` | 定稿 |
""",
            has_changelog=True,
            merged_upto="章0001",
            cold_read_record="# 记录\n## 冷读1\n内容\n"
        )

        declared = progress_report.declared_status(novel_dir)
        rep = progress_report.collect(novel_dir)
        findings = progress_report.reconcile(novel_dir, declared, rep)

        # 应该没有任何对账项
        self.assertEqual(len(findings), 0)


class TestProgressRule(unittest.TestCase):
    """ProgressRule 的单元测试（审计集成）。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_progress_rule_reports_drifts(self):
        """ProgressRule.run 在发现漂移时返回 Finding 列表，包含对应的代码。"""
        novel_dir = build_novel_fixture(
            self.tmp,
            progress_table="""| 文件 | 状态 |
|---|---|
| `10_正文/01_第01部/01_卷01/章不存在.md` | 定稿 |
""",
            manuscript_exists=False
        )

        context = AuditContext(novel_dir)
        rule = ProgressRule()
        findings = rule.run(context)

        # 应该有 findings
        self.assertGreater(len(findings), 0)
        # 应该包含 PROGRESS001
        codes = [f.code for f in findings]
        self.assertIn("PROGRESS001", codes)

    def test_progress_rule_no_progress_file(self):
        """00_进度.md 不存在时，ProgressRule 返回空列表。"""
        novel_dir = self.tmp / "小说"
        novel_dir.mkdir()
        # 不创建 00_进度.md

        context = AuditContext(novel_dir)
        rule = ProgressRule()
        findings = rule.run(context)

        # 应该返回空列表
        self.assertEqual(findings, [])

    def test_progress_rule_groups_by_code(self):
        """ProgressRule 把同代码的多条消息分组到一个 Finding 的 locations 列表。"""
        novel_dir = self.tmp / "小说"
        novel_dir.mkdir()

        # 创建进度表，声明两个不存在的文件
        _write(novel_dir / "00_进度.md", """| 文件 | 状态 |
|---|---|
| `10_正文/01_第01部/01_卷01/章0001.md` | 定稿 |
| `10_正文/01_第01部/01_卷01/章0002.md` | 定稿 |
""")

        context = AuditContext(novel_dir)
        rule = ProgressRule()
        findings = rule.run(context)

        # 找 PROGRESS001
        progress001_findings = [f for f in findings if f.code == "PROGRESS001"]
        self.assertEqual(len(progress001_findings), 1)

        # 该 Finding 应该有 2 个 locations（两个缺失的文件）
        self.assertEqual(len(progress001_findings[0].locations), 2)


class TestRenderDerived(unittest.TestCase):
    """render_derived 的单元测试。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_render_derived_header_marks(self):
        """render_derived 输出包含 派生 和 禁止手工编辑 的自我标识。"""
        novel_dir = build_novel_fixture(self.tmp)
        rep = progress_report.collect(novel_dir)

        output = progress_report.render_derived(rep)

        self.assertIn("派生", output)
        self.assertIn("禁止手工编辑", output)

    def test_render_derived_chapter_rows(self):
        """render_derived 输出表格中包含每个章节的一行。"""
        novel_dir = build_novel_fixture(self.tmp, manuscript_exists=True)
        rep = progress_report.collect(novel_dir)

        output = progress_report.render_derived(rep)

        # 应该包含「章0001」
        self.assertIn("章0001", output)
        # 应该包含表格标记（|）
        self.assertIn("|", output)


if __name__ == "__main__":
    unittest.main()
