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
    - outline_cold_read_record (str): 03_细纲对照记录.md 内容，默认建一份含 `## 冷读` 分节；传 None 则不建
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

    # 冷读记录（正文）
    cold_read_content = opts.get("cold_read_record")
    if cold_read_content is not None:
        _write(novel_dir / "05_工作区/03_第01部/03_卷01/03_章0001/02_状态/02_正文校验记录.md", cold_read_content)

    # 冷读记录（细纲）——默认建一份，让「细纲定稿必须有冷读记录」的门禁在无关测试里不误触
    outline_cr = opts.get("outline_cold_read_record", "# 细纲对照记录\n## 冷读评审 · 测试\n内容\n")
    if outline_cr is not None:
        _write(novel_dir / "05_工作区/03_第01部/03_卷01/03_章0001/02_状态/03_细纲对照记录.md", outline_cr)

    # 修订文件
    for i in range(1, opts.get("revision_count", 0) + 1):
        _write(novel_dir / f"05_工作区/03_第01部/03_卷01/03_章0001/00_提示词/01_正文生成_修订{i}.md", f"修订 {i}")

    # 细纲落地核对表（03_细纲落地核对.md）；默认建一张全部锚定完的干净表
    # 锚点须能在默认正文（见 manuscript_exists 分支）里逐字找到，否则会触发 PROGRESS006（幽灵锚点）
    landing = opts.get("landing_check",
                        "# 落地核对\n\n- [x] 场景钩子：X\n      → 锚点：「这是一篇测试正文」\n")
    if landing is not None:
        _write(novel_dir / "05_工作区/03_第01部/03_卷01/03_章0001/02_状态/03_细纲落地核对.md", landing)


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

    def _finalized(self, **extra):
        """帮手：正文标「定稿」的最小 fixture，其余流水线件默认齐全。"""
        base = dict(
            progress_table="| 文件 | 状态 |\n|---|---|\n"
                           "| `10_正文/01_第01部/01_卷01/章0001.md` | 定稿 |\n",
            has_changelog=True, merged_upto="章0001",
            cold_read_record="# 记录\n## 冷读1\n内容\n",
        )
        base.update(extra)
        novel_dir = build_novel_fixture(self.tmp, **base)
        declared = progress_report.declared_status(novel_dir)
        rep = progress_report.collect(novel_dir)
        return progress_report.reconcile(novel_dir, declared, rep)

    def test_progress003_no_cold_read_is_error_when_finalized(self):
        """正文标「定稿」却没冷读记录 → PROGRESS003 且级别为 error。"""
        findings = self._finalized(cold_read_record=None)
        p003 = [lv for lv, code, _ in findings if code == "PROGRESS003"]
        self.assertTrue(p003)
        self.assertIn("error", p003)

    def test_progress003_outline_finalized_without_cold_read_is_error(self):
        """细纲标「定稿」却没 03_细纲对照记录.md → PROGRESS003 error。"""
        novel_dir = build_novel_fixture(
            self.tmp,
            progress_table="| 文件 | 状态 |\n|---|---|\n"
                           "| `03_规划/01_第01部/01_卷01/规划_卷01_章0001.md` | 定稿 |\n",
            manuscript_exists=False, landing_check=None,
            outline_cold_read_record=None,
        )
        declared = progress_report.declared_status(novel_dir)
        rep = progress_report.collect(novel_dir)
        findings = progress_report.reconcile(novel_dir, declared, rep)
        hits = [(lv, msg) for lv, code, msg in findings
                if code == "PROGRESS003" and "细纲" in msg]
        self.assertTrue(hits)
        self.assertEqual(hits[0][0], "error")

    def test_progress003_outline_finalized_with_cold_read_ok(self):
        """细纲标「定稿」且有冷读记录 → 无 PROGRESS003 细纲项。"""
        novel_dir = build_novel_fixture(
            self.tmp,
            progress_table="| 文件 | 状态 |\n|---|---|\n"
                           "| `03_规划/01_第01部/01_卷01/规划_卷01_章0001.md` | 定稿 |\n",
            manuscript_exists=False, landing_check=None,
        )
        declared = progress_report.declared_status(novel_dir)
        rep = progress_report.collect(novel_dir)
        findings = progress_report.reconcile(novel_dir, declared, rep)
        self.assertFalse([m for lv, c, m in findings if c == "PROGRESS003" and "细纲" in m])

    def test_progress003_no_cold_read_is_warning_when_pending(self):
        """正文标「待校验」没冷读记录 → PROGRESS003 但只是 warning。"""
        novel_dir = build_novel_fixture(
            self.tmp,
            progress_table="| 文件 | 状态 |\n|---|---|\n"
                           "| `10_正文/01_第01部/01_卷01/章0001.md` | 待校验 |\n",
            cold_read_record=None,
        )
        declared = progress_report.declared_status(novel_dir)
        rep = progress_report.collect(novel_dir)
        findings = progress_report.reconcile(novel_dir, declared, rep)
        lvls = {code: lv for lv, code, _ in findings}
        self.assertEqual(lvls.get("PROGRESS003"), "warning")

    def test_progress005_missing_landing_check_blocks_finalize(self):
        """正文标定稿但没有 03_细纲落地核对.md → PROGRESS005 error。"""
        findings = self._finalized(landing_check=None)
        p005 = [lv for lv, code, _ in findings if code == "PROGRESS005"]
        self.assertEqual(p005, ["error"])

    def test_progress005_open_items_block_finalize(self):
        """落地核对表里还有未勾选项 → PROGRESS005 error。"""
        findings = self._finalized(
            landing_check="# 落地核对\n\n- [ ] 场景钩子：X\n"
                          "      → 锚点：〔待填：正文「≤12字」／❌未落地（原因）〕\n")
        p005 = [lv for lv, code, _ in findings if code == "PROGRESS005"]
        self.assertEqual(p005, ["error"])

    def test_progress005_waived_items_pass(self):
        """全部锚定或显式豁免 → 不报 PROGRESS005。"""
        findings = self._finalized(
            landing_check="# 落地核对\n\n"
                          "- [x] 场景钩子：X\n      → 锚点：正文「某句」\n"
                          "- [x] 要点：Y\n      → 锚点：豁免：本章不涉及\n")
        codes = [code for _, code, _ in findings]
        self.assertNotIn("PROGRESS005", codes)

    def test_progress005_unresolved_x_mark_blocks(self):
        """标了 ❌未落地 又没 waive → 仍拦。"""
        findings = self._finalized(
            landing_check="# 落地核对\n\n- [x] 场景钩子：X\n"
                          "      → 锚点：❌未落地（正文缺这句连接性交代）\n")
        p005 = [lv for lv, code, _ in findings if code == "PROGRESS005"]
        self.assertEqual(p005, ["error"])

    # ---- PROGRESS006：落地核对表锚点在当前正文里找不到（幽灵锚点）----

    def test_progress006_stale_anchor_when_finalized_is_error(self):
        """已勾选的锚点句在正文里找不到逐字匹配、正文已「定稿」 → PROGRESS006 error。"""
        findings = self._finalized(
            manuscript_text="他咬着牙站了起来，一步一步朝矿道口走去。\n",
            landing_check="# 落地核对\n\n- [x] 场景钩子：X\n"
                          "      → 锚点：「他哭着跪了下去」\n")
        p006 = [(lv, m) for lv, code, m in findings if code == "PROGRESS006"]
        self.assertEqual(len(p006), 1)
        self.assertEqual(p006[0][0], "error")
        self.assertIn("他哭着跪了下去", p006[0][1])

    def test_progress006_stale_anchor_when_draft_is_warning(self):
        """同样的幽灵锚点，正文还是「待校验」/草稿阶段 → 只降级为 warning，不拦。"""
        novel_dir = build_novel_fixture(
            self.tmp,
            progress_table="| 文件 | 状态 |\n|---|---|\n"
                           "| `10_正文/01_第01部/01_卷01/章0001.md` | 待校验 |\n",
            manuscript_text="他咬着牙站了起来，一步一步朝矿道口走去。\n",
            cold_read_record="# 记录\n## 冷读1\n内容\n",
            landing_check="# 落地核对\n\n- [x] 场景钩子：X\n"
                          "      → 锚点：「他哭着跪了下去」\n")
        declared = progress_report.declared_status(novel_dir)
        rep = progress_report.collect(novel_dir)
        findings = progress_report.reconcile(novel_dir, declared, rep)
        p006 = [lv for lv, code, _ in findings if code == "PROGRESS006"]
        self.assertEqual(p006, ["warning"])

    def test_progress006_ellipsis_anchor_matches_non_adjacent_text(self):
        """锚点里的 `……` 是"中间还省了别的字"的惯例，不要求两段在正文里紧邻。"""
        findings = self._finalized(
            manuscript_text="他咬着牙站了起来。背上的伤还在渗血。一步一步朝矿道口走去。\n",
            landing_check="# 落地核对\n\n- [x] 场景钩子：X\n"
                          "      → 锚点：「他咬着牙站了起来。……一步一步朝矿道口走去。」\n")
        self.assertNotIn("PROGRESS006", [code for _, code, _ in findings])

    def test_progress006_waived_or_unlanded_anchor_not_checked(self):
        """`❌未落地`/`豁免` 的行本来就不是"已确认落地"的锚点，不该被当幽灵锚点报。"""
        findings = self._finalized(
            manuscript_text="他咬着牙站了起来。\n",
            landing_check="# 落地核对\n\n"
                          "- [x] 场景钩子：X\n      → 锚点：❌未落地（这句没写）\n"
                          "- [x] 要点：Y\n      → 锚点：豁免：本章不涉及\n")
        self.assertNotIn("PROGRESS006", [code for _, code, _ in findings])

    def test_progress006_prose_mentioning_anchor_word_not_checked(self):
        """bullet 正文里顺带提到"锚点"两个字（非 `→ 锚点：` 行）不参与核验。"""
        findings = self._finalized(
            manuscript_text="他咬着牙站了起来。\n",
            landing_check="# 落地核对\n\n"
                          "- [x] 涉及资源：灵心草（取卷纲锚点「≈5 下品」折算）\n"
                          "      → 锚点：「他咬着牙站了起来」\n")
        self.assertNotIn("PROGRESS006", [code for _, code, _ in findings])


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


class TestPreflightOutline(unittest.TestCase):
    """preflight_outline（`--preflight 细纲 <N>`）的单元测试。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_pass_when_clean_and_has_cold_read(self):
        """结构/引用/leak 均无 Finding、冷读记录非空 → PASS。"""
        novel_dir = build_novel_fixture(self.tmp)  # 默认出场对象表无【场景列表】段，不触发 PLAN023

        ok, text = progress_report.preflight_outline(novel_dir, 1)

        self.assertTrue(ok)
        self.assertIn("PASS", text)
        self.assertIn("章0001", text)
        self.assertIn("冷读记录：1 节", text)

    def test_fail_when_no_chapter_found(self):
        """章号在 03_规划/ 下找不到对应细纲文件 → FAIL，不抛异常。"""
        novel_dir = build_novel_fixture(self.tmp)

        ok, text = progress_report.preflight_outline(novel_dir, 999)

        self.assertFalse(ok)
        self.assertIn("未找到", text)
        self.assertIn("章0999", text)

    def test_fail_when_no_cold_read_record(self):
        """结构/引用/leak 都干净，但没有冷读记录 → 仍判 FAIL（冷读记录项拦）。"""
        novel_dir = build_novel_fixture(self.tmp, outline_cold_read_record=None)

        ok, text = progress_report.preflight_outline(novel_dir, 1)

        self.assertFalse(ok)
        self.assertIn("FAIL", text)
        self.assertIn("冷读记录：0 节", text)

    def test_fail_when_scene_structure_not_canonical(self):
        """【场景列表】下没有 `### 第N场景` 标题（PLAN023）→ 结构项拦，判 FAIL。"""
        novel_dir = build_novel_fixture(self.tmp, outline_exists=False)
        _write(novel_dir / "03_规划/01_第01部/01_卷01/规划_卷01_章0001.md", """# 第一章细纲
## 出场对象
| 对象 | 出场方式 |
|---|---|
| @主角 | 登场 |

## 【场景列表】

#### 场景 1：起笔
非 canonical 的场景小标题，extract.scene_blocks 抓不到。
""")

        ok, text = progress_report.preflight_outline(novel_dir, 1)

        self.assertFalse(ok)
        self.assertIn("结构（planning）：1 项", text)
        self.assertIn("PLAN023", text)

    def test_fail_when_multiple_chapters_share_number(self):
        """跨部/卷同章号撞在一起 → preflight 拒绝猜测，报「匹配到多个」。"""
        novel_dir = build_novel_fixture(self.tmp)
        _write(novel_dir / "03_规划/02_第02部/01_卷01/规划_卷01_章0001.md",
               (novel_dir / "03_规划/01_第01部/01_卷01/规划_卷01_章0001.md").read_text(encoding="utf-8"))

        ok, text = progress_report.preflight_outline(novel_dir, 1)

        self.assertFalse(ok)
        self.assertIn("匹配到多个", text)


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
