# -*- coding: utf-8 -*-
"""build_rule_slices.py 与 audit_rules.py 的 RULE008 单元测试套件。

测试 parse_sections, render_slice, build 函数与 SliceError 异常，
以及 audit_rules.check_slices 的 RULE008 规则代码。
"""
import os
import shutil
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import build_rule_slices as brs
import audit_rules


class TestParseSections(unittest.TestCase):
    """parse_sections 的单元测试——权威版 → [(章标题, 切片名, 正文)]。"""

    def test_parse_sections_splits_by_h2_headings(self):
        """按 ## 二级标题分段；每段返回 (标题, 切片名列表, 正文行)。"""
        text = """# 一级标题

## 第一章
第一章的正文。

## 第二章
第二章的正文。
"""
        result = brs.parse_sections(text)
        self.assertEqual(len(result), 2)

        title1, names1, body1 = result[0]
        self.assertEqual(title1, "第一章")
        self.assertEqual(names1, [])
        self.assertIn("## 第一章", body1)

        title2, names2, body2 = result[1]
        self.assertEqual(title2, "第二章")
        self.assertEqual(names2, [])

    def test_parse_sections_strips_slice_marker_line(self):
        """<!-- slice: 名称 --> 标记不进切片正文——切片里不该看见记号。"""
        text = """## 规则一
<!-- slice: 生成版 -->
这是规则一的正文。
"""
        result = brs.parse_sections(text)
        self.assertEqual(len(result), 1)

        title, names, body = result[0]
        self.assertEqual(title, "规则一")
        self.assertEqual(names, ["生成版"])

        # 标记行不应该在 body 里
        body_text = "\n".join(body)
        self.assertNotIn("<!-- slice:", body_text)
        self.assertIn("这是规则一的正文", body_text)

    def test_parse_sections_multiple_slice_names_comma_separated(self):
        """多个切片名用 , / ， / 、 分隔都能解析。"""
        text = """## 规则A
<!-- slice: 生成版, 校验版 -->
正文 A。

## 规则B
<!-- slice: 生成版，校验版 -->
正文 B。

## 规则C
<!-- slice: 生成版、校验版 -->
正文 C。
"""
        result = brs.parse_sections(text)
        self.assertEqual(len(result), 3)

        # 都应该把两个切片名都解析出来
        for title, names, body in result:
            self.assertEqual(len(names), 2)
            self.assertIn("生成版", names)
            self.assertIn("校验版", names)

    def test_parse_sections_no_marker_returns_empty_slice_list(self):
        """没有标记的章节返回空的切片名列表。"""
        text = """## 不标记的章
这章没有 slice 标记。
"""
        result = brs.parse_sections(text)
        self.assertEqual(len(result), 1)

        title, names, body = result[0]
        self.assertEqual(names, [])

    def test_parse_sections_strips_trailing_blank_lines_and_separator(self):
        """去掉章末尾多余空行与分隔线 ---，生成器重新补。"""
        text = """## 规则
<!-- slice: 生成版 -->
规则正文。


---

## 下一章
下一章。
"""
        result = brs.parse_sections(text)
        self.assertEqual(len(result), 2)

        title1, names1, body1 = result[0]
        # 最后一行不应该是空行或 ---
        self.assertTrue(body1[-1].strip())
        self.assertNotEqual(body1[-1].strip(), "---")


class TestRenderSlice(unittest.TestCase):
    """render_slice 的单元测试——按名称生成切片文件。"""

    def setUp(self):
        """设置测试用的 sections 结构。"""
        self.sections = [
            ("规则一", ["生成版"], ["## 规则一", "生成版的正文。"]),
            ("规则二", ["校验版"], ["## 规则二", "校验版的正文。"]),
            ("规则三", ["生成版", "校验版"], ["## 规则三", "两个版本都包含的正文。"]),
            ("规则四", [], ["## 规则四", "不标记的正文。"]),
        ]

    def test_render_slice_output_starts_with_title(self):
        """输出以 # 标题开头，包含切片名。"""
        output = brs.render_slice("生成版", self.sections, "权威版.md", "通用写作规则")
        self.assertTrue(output.startswith("# 通用写作规则（生成版）\n"))

    def test_render_slice_header_identifies_as_derived_nonauthoritative(self):
        """输出头包含「派生」「禁止手工编辑」「非权威」——满足 §二·A 第 2 条。

        切片自身必须声明非权威性，以便读者知道改规则要改权威版。
        """
        output = brs.render_slice("生成版", self.sections, "权威版.md", "通用写作规则")
        self.assertIn("派生", output)
        self.assertIn("禁止手工编辑", output)
        self.assertIn("非权威", output)

    def test_render_slice_includes_assigned_sections_only(self):
        """输出包含分配给该切片的章节正文，不包含其他章。"""
        output = brs.render_slice("生成版", self.sections, "权威版.md", "通用写作规则")

        # 规则一、规则三 有 生成版 标记，应该在输出里
        self.assertIn("规则一", output)
        self.assertIn("规则三", output)
        self.assertIn("生成版的正文", output)

        # 规则二、规则四 没有 生成版 标记，不应该在输出里
        self.assertNotIn("校验版的正文", output)
        self.assertNotIn("不标记的正文", output)

    def test_render_slice_sections_joined_with_separator(self):
        """多章节用 --- 分隔符连接。"""
        output = brs.render_slice("生成版", self.sections, "权威版.md", "通用写作规则")

        # 规则一和规则三 之间应该有 ---
        self.assertIn("## 规则一", output)
        self.assertIn("---", output)
        self.assertIn("## 规则三", output)

    def test_render_slice_raises_on_unconfigured_name(self):
        """切片名不在 SLICES 里 → SliceError（不是 KeyError）。

        原实现直接 `SLICES[name]`，拼错名字时抛的是裸 KeyError，
        调用方（audit 规则）只会看到一句无意义的堆栈。
        """
        with self.assertRaises(brs.SliceError) as cm:
            brs.render_slice("不存在的切片", self.sections, "权威版.md", "通用写作规则")
        self.assertIn("不存在的切片", str(cm.exception))

    def test_render_slice_raises_when_configured_name_has_no_section(self):
        """切片名已配置、但没有任何章标它 → 也要 SliceError。

        这是另一种错法：标记写漏或写错字，切片会静默变成空文件。
        """
        name = sorted(brs.SLICES)[0]
        sections = [("孤章", ["别的切片名"], ["## 孤章", "正文"])]
        with self.assertRaises(brs.SliceError) as cm:
            brs.render_slice(name, sections, "权威版.md", "通用写作规则")
        self.assertIn(name, str(cm.exception))


class TestBuild(unittest.TestCase):
    """build 函数的单元测试——从权威版生成所有切片。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_fake_repo(self, authority_content: str) -> Path:
        """在临时目录里构建最小化的仓库结构。"""
        repo = self.tmp / "repo"
        repo.mkdir()
        template_dir = repo / "00_通用模板" / "01_写作规则"
        template_dir.mkdir(parents=True)
        (template_dir / "00_通用写作规则.md").write_text(
            authority_content, encoding="utf-8"
        )
        return repo

    def test_build_returns_dict_keyed_by_all_slices(self):
        """返回字典，键是 SLICES 里的所有切片名。"""
        content = """# 通用写作规则

## 规则一
<!-- slice: 生成版, 校验版 -->
规则一的正文。
"""
        repo = self._make_fake_repo(content)
        result = brs.build(repo)

        self.assertIsInstance(result, dict)
        for name in brs.SLICES:
            self.assertIn(name, result)

    def test_build_raises_error_when_authority_missing(self):
        """权威版文件不存在 → 抛 SliceError。"""
        repo = self.tmp / "empty_repo"
        repo.mkdir()

        with self.assertRaises(brs.SliceError) as cm:
            brs.build(repo)
        self.assertIn("权威版不存在", str(cm.exception))

    def test_build_raises_error_for_unknown_slice_name(self):
        """标记了不在 SLICES 里的切片名 → 抛 SliceError，报告该名称。

        这是打字错误防守——防止把 「生成般」之类的拼错抄到好几个地方才发现。
        """
        content = """# 通用写作规则

## 规则一
<!-- slice: 拼错了的名称 -->
正文。
"""
        repo = self._make_fake_repo(content)

        with self.assertRaises(brs.SliceError) as cm:
            brs.build(repo)
        self.assertIn("拼错了的名称", str(cm.exception))


class TestIdempotence(unittest.TestCase):
    """幂等性——反复生成的结果应该逐字相同。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_repo_with_slices(self) -> Path:
        """构建包含权威版和已有切片的仓库。"""
        repo = self.tmp / "repo"
        repo.mkdir()
        template_dir = repo / "00_通用模板" / "01_写作规则"
        template_dir.mkdir(parents=True)

        authority = """# 通用写作规则

## 规则一
<!-- slice: 生成版, 校验版 -->
规则一的正文很重要。

## 规则二
<!-- slice: 校验版 -->
只在校验版出现的规则。

## 规则三
这个规则没有标记。
"""
        (template_dir / "00_通用写作规则.md").write_text(
            authority, encoding="utf-8"
        )
        return repo

    def test_build_idempotent_successive_calls(self):
        """反复调用 build 返回逐字相同的输出。"""
        repo = self._make_repo_with_slices()
        result1 = brs.build(repo)
        result2 = brs.build(repo)

        for name in brs.SLICES:
            self.assertEqual(result1[name], result2[name])

    def test_build_idempotent_written_and_reread(self):
        """写切片到磁盘，再重跑 build，结果与磁盘上的一致——固定点。

        这是 RULE008 能成立的前提：切片生成是确定性的、无状态的。
        """
        repo = self._make_repo_with_slices()
        rendered = brs.build(repo)

        # 写到磁盘
        for name, content in rendered.items():
            out_path = repo / brs.SLICES[name][0]
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(content, encoding="utf-8")

        # 再跑一次 build，应该和磁盘上的一样
        rendered_again = brs.build(repo)
        for name in brs.SLICES:
            disk_content = (repo / brs.SLICES[name][0]).read_text(encoding="utf-8")
            self.assertEqual(rendered_again[name], disk_content)


class TestCheckSlicesViaAuditRules(unittest.TestCase):
    """audit_rules.check_slices 的 RULE008 单元测试。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _setup_repo_with_config(self, slice_enabled: bool = True) -> Path:
        """构建仓库、权威版、已有切片、配置文件。"""
        repo = self.tmp / "repo"
        repo.mkdir()

        # 建目录
        template_dir = repo / "00_通用模板" / "01_写作规则"
        template_dir.mkdir(parents=True)
        audit_dir = repo / "02_工具" / "00_系统级"
        audit_dir.mkdir(parents=True)

        # 权威版
        authority = """# 通用写作规则

## 规则一
<!-- slice: 生成版, 校验版 -->
规则一的权威版正文。

## 规则二
<!-- slice: 校验版 -->
只在校验版的规则。
"""
        (template_dir / "00_通用写作规则.md").write_text(
            authority, encoding="utf-8"
        )

        # 生成初始切片
        rendered = brs.build(repo)
        for name, content in rendered.items():
            out_path = repo / brs.SLICES[name][0]
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(content, encoding="utf-8")

        # 配置文件
        config_content = f"""[slice]
enabled = {str(slice_enabled).lower()}
"""
        (audit_dir / "rules_audit.config.toml").write_text(config_content, encoding="utf-8")

        return repo

    def test_check_slices_returns_empty_when_slices_match(self):
        """切片与重出结果一致 → 返回 []。"""
        repo = self._setup_repo_with_config(slice_enabled=True)
        findings = audit_rules.check_slices(repo, self._load_config(repo))
        self.assertEqual(findings, [])

    def test_check_slices_detects_hand_edited_slice(self):
        """切片被手改（改一个字） → 返回 RULE008 Finding。

        重演历史真实发生的问题：校验版把「登记到伏笔登记表」改成「登记到伏笔跟踪册」。
        """
        repo = self._setup_repo_with_config(slice_enabled=True)

        # 手工改一个切片
        slice_path = repo / brs.SLICES["生成版"][0]
        content = slice_path.read_text(encoding="utf-8")
        modified = content.replace("权威版正文", "被手改过的正文")
        slice_path.write_text(modified, encoding="utf-8")

        findings = audit_rules.check_slices(repo, self._load_config(repo))
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].code, "RULE008")
        self.assertEqual(findings[0].severity, audit_rules.ERROR)

    def test_check_slices_returns_empty_when_disabled(self):
        """配置禁用切片检查 enabled=False → 返回 []。"""
        repo = self._setup_repo_with_config(slice_enabled=False)

        # 改切片
        slice_path = repo / brs.SLICES["生成版"][0]
        content = slice_path.read_text(encoding="utf-8")
        modified = content.replace("权威版正文", "被改过")
        slice_path.write_text(modified, encoding="utf-8")

        # 配置禁用了，所以不报错
        findings = audit_rules.check_slices(repo, self._load_config(repo))
        self.assertEqual(findings, [])

    def test_check_slices_finding_locations_names_drifted_file(self):
        """Finding 的 locations 名出问题的切片文件。"""
        repo = self._setup_repo_with_config(slice_enabled=True)

        # 改生成版
        slice_path = repo / brs.SLICES["生成版"][0]
        content = slice_path.read_text(encoding="utf-8")
        modified = content.replace("权威版正文", "改过")
        slice_path.write_text(modified, encoding="utf-8")

        findings = audit_rules.check_slices(repo, self._load_config(repo))
        self.assertEqual(len(findings), 1)

        # locations 应该包含生成版的路径
        locations_text = " ".join(findings[0].locations)
        self.assertIn("生成版", locations_text)

    def _load_config(self, repo_root: Path) -> dict:
        """从仓库里加载 rules_audit.config.toml。"""
        config_path = repo_root / "02_工具" / "00_系统级" / "rules_audit.config.toml"
        with open(config_path, "rb") as f:
            return tomllib.load(f)


class TestRealRepoSanity(unittest.TestCase):
    """真实仓库健全性检查——切片必须与重出结果逐字相同。"""

    def test_real_repo_slices_match_rendered(self):
        """运行 brs.build 对真实仓库根，所有切片文件与重出结果逐字相同。

        仓库里的切片必须始终等于重出结果，否则 RULE008 会红。
        """
        repo_root = Path(__file__).resolve().parents[3]

        # 能生成
        try:
            rendered = brs.build(repo_root)
        except brs.SliceError as e:
            self.fail(f"无法生成切片：{e}")

        # 生成的结果与磁盘上的逐字相同
        for name, content in rendered.items():
            out_path = repo_root / brs.SLICES[name][0]
            if out_path.exists():
                disk_content = out_path.read_text(encoding="utf-8")
                self.assertEqual(
                    disk_content, content,
                    f"{brs.SLICES[name][0]} 与重出结果不同"
                )


if __name__ == "__main__":
    unittest.main()
