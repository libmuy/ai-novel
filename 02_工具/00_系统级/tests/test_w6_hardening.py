# -*- coding: utf-8 -*-
"""W6 硬化单元测试套件。

测试：
1. state_lock.py (W6.2) — `state_write_lock`, `acquire_until_exit`, `StateLockError`, `LOCK_FILENAME`
2. state_tree.py (W6.1) — 指纹函数和 manifest 验证
3. enum_domain.py (W6.3) — 枚举值域校验
"""
import json
import os
import shutil
import socket
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "01_小说通用工具"))

import state_lock
import state_tree
from state_lock import state_write_lock, acquire_until_exit, StateLockError, LOCK_FILENAME
from state_tree import (
    tree_fingerprint, changelog_fingerprint, parse_manifest_fields,
    verify_manifest_fingerprints, render_manifest, NONE_MARKER, MANIFEST_FILENAME
)
from audit.context import AuditContext
from audit.rules.enum_domain import EnumDomainRule, parse_closed_domains, _parse_domain


def _write(path: Path, text: str):
    """辅助方法：创建并写入文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class TestStateLock(unittest.TestCase):
    """state_lock.py 的单元测试 (W6.2)"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.state_root = self.tmp / "state_root"
        self.state_root.mkdir(exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_acquiring_creates_lock_file_and_releasing_removes_it(self):
        """获取锁创建锁文件，释放时删除。"""
        lock_path = self.state_root / LOCK_FILENAME
        self.assertFalse(lock_path.exists())

        with state_write_lock(self.state_root, tool="test_tool"):
            self.assertTrue(lock_path.exists())

        self.assertFalse(lock_path.exists())

    def test_lock_file_contains_json_with_pid_host_tool(self):
        """锁文件内容是 JSON，包含 pid、host、tool 字段。"""
        lock_path = self.state_root / LOCK_FILENAME

        with state_write_lock(self.state_root, tool="merge_chapter_state.py"):
            with open(lock_path, encoding="utf-8") as f:
                data = json.load(f)

        self.assertEqual(data["pid"], os.getpid())
        self.assertEqual(data["host"], socket.gethostname())
        self.assertEqual(data["tool"], "merge_chapter_state.py")
        self.assertIn("started", data)
        self.assertIn("epoch", data)

    def test_second_acquire_raises_state_lock_error_naming_holding_tool(self):
        """另一个进程持有锁时，获取失败，错误消息包含工具名。"""
        lock_path = self.state_root / LOCK_FILENAME

        with state_write_lock(self.state_root, tool="first_tool"):
            # 尝试在同一个工具（这里用嵌套的方式模拟）获取锁会失败
            with self.assertRaises(StateLockError) as cm:
                with state_write_lock(self.state_root, tool="second_tool"):
                    pass

            self.assertIn("first_tool", str(cm.exception))

    def test_stale_takeover_dead_pid_succeeds(self):
        """同机且持锁进程已死（PID 已不存在）→ 接管残锁成功。"""
        lock_path = self.state_root / LOCK_FILENAME
        dead_pid = 999999

        # 手工写入一个残锁（死亡进程）
        lock_data = {
            "pid": dead_pid,
            "host": socket.gethostname(),
            "tool": "dead_tool",
            "started": time.strftime("%Y-%m-%d %H:%M:%S"),
            "epoch": time.time() - 100,
        }
        with open(lock_path, "w", encoding="utf-8") as f:
            json.dump(lock_data, f, ensure_ascii=False)

        # 获取应该成功（接管残锁）
        with state_write_lock(self.state_root, tool="new_tool", verbose=False):
            self.assertTrue(lock_path.exists())

        self.assertFalse(lock_path.exists())

    def test_cross_host_fresh_lock_raises_error(self):
        """跨机且持锁时间很短（假设对方进程还活着）→ 获取失败。"""
        lock_path = self.state_root / LOCK_FILENAME

        # 手工写入一个「跨机、很新」的锁
        lock_data = {
            "pid": 12345,
            "host": "其他机器",
            "tool": "remote_tool",
            "started": time.strftime("%Y-%m-%d %H:%M:%S"),
            "epoch": time.time(),  # 刚写的
        }
        with open(lock_path, "w", encoding="utf-8") as f:
            json.dump(lock_data, f, ensure_ascii=False)

        # 获取应该失败
        with self.assertRaises(StateLockError):
            with state_write_lock(self.state_root, tool="test_tool"):
                pass

    def test_cross_host_stale_lock_succeeds(self):
        """跨机且超过阈值时间（假设对方进程已死）→ 接管成功。"""
        lock_path = self.state_root / LOCK_FILENAME

        # 手工写入一个「跨机、很旧」的锁
        lock_data = {
            "pid": 12345,
            "host": "其他机器",
            "tool": "remote_tool",
            "started": "2020-01-01 00:00:00",
            "epoch": time.time() - 7200,  # 2小时前，超过默认 30 分钟
        }
        with open(lock_path, "w", encoding="utf-8") as f:
            json.dump(lock_data, f, ensure_ascii=False)

        # 获取应该成功
        with state_write_lock(self.state_root, tool="new_tool", verbose=False):
            self.assertTrue(lock_path.exists())

        self.assertFalse(lock_path.exists())

    def test_corrupt_lock_file_treated_as_stale(self):
        """损坏的锁文件（非 JSON）→ 作为残锁，接管成功。"""
        lock_path = self.state_root / LOCK_FILENAME

        # 写入垃圾数据
        with open(lock_path, "w", encoding="utf-8") as f:
            f.write("this is not json garbage")

        # 获取应该成功（损坏视为残锁）
        with state_write_lock(self.state_root, tool="test_tool", verbose=False):
            self.assertTrue(lock_path.exists())

        self.assertFalse(lock_path.exists())

    def test_releasing_does_not_delete_lock_taken_over_by_another_pid(self):
        """释放锁时不删除已被另一个 PID 接管的锁文件。

        误删别人接管后新建的锁会让互斥失效，两个进程可能同时写状态树。
        """
        lock_path = self.state_root / LOCK_FILENAME

        # 获取锁
        with state_write_lock(self.state_root, tool="first_tool", verbose=False):
            self.assertTrue(lock_path.exists())

            # 在 with 块内，手工覆盖为另一个 PID 的锁
            different_pid = os.getpid() + 9999
            lock_data = {
                "pid": different_pid,
                "host": socket.gethostname(),
                "tool": "takeover_tool",
                "started": time.strftime("%Y-%m-%d %H:%M:%S"),
                "epoch": time.time(),
            }
            with open(lock_path, "w", encoding="utf-8") as f:
                json.dump(lock_data, f, ensure_ascii=False)

        # with 块退出时，文件应该还在（不应被我们删掉）
        self.assertTrue(lock_path.exists(), "释放不应该删除别人接管后的锁")

    def test_acquire_until_exit_returns_lock_path(self):
        """acquire_until_exit 返回锁文件路径。"""
        lock_path = acquire_until_exit(self.state_root, tool="test_tool", verbose=False)

        # 路径应该指向正确的锁文件
        self.assertTrue(lock_path.endswith(LOCK_FILENAME) or LOCK_FILENAME in lock_path)
        # 文件应该存在（因为还没退出）
        self.assertTrue(os.path.exists(lock_path))


class TestStateTreeFingerprints(unittest.TestCase):
    """state_tree.py 指纹相关函数的单元测试 (W6.1)"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.state_dir = self.tmp / "state"
        self.state_dir.mkdir(exist_ok=True)
        self.novel_dir = self.tmp / "novel"
        self.novel_dir.mkdir(exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_tree_fingerprint_nonexistent_dir_returns_none_marker(self):
        """不存在的目录 → 返回 NONE_MARKER。"""
        nonexistent = self.tmp / "nonexistent"
        result = tree_fingerprint(nonexistent)
        self.assertEqual(result, NONE_MARKER)

    def test_tree_fingerprint_stable_and_changes_on_md_modification(self):
        """指纹稳定不变，但 .md 文件改动后会变化。"""
        _write(self.state_dir / "01_角色" / "01_角色_测试.md", "# 测试\n| 测试 | 测试 | 测试 | 测试 |")

        fp1 = tree_fingerprint(self.state_dir)
        fp2 = tree_fingerprint(self.state_dir)
        self.assertEqual(fp1, fp2, "指纹应该稳定")

        # 修改文件
        _write(self.state_dir / "01_角色" / "01_角色_测试.md", "# 修改\n| 修改 | 修改 | 修改 | 修改 |")
        fp3 = tree_fingerprint(self.state_dir)
        self.assertNotEqual(fp1, fp3, "文件改动后指纹应该变化")

    def test_tree_fingerprint_ignores_exclude_names(self):
        """exclude_names 中的文件应该被忽略。"""
        _write(self.state_dir / "01_角色" / "01_角色_测试.md", "内容A")
        _write(self.state_dir / "00_同步状态.md", "内容B")

        fp_with_exclude = tree_fingerprint(self.state_dir, exclude_names={"00_同步状态.md"})

        # 修改被排除的文件
        _write(self.state_dir / "00_同步状态.md", "内容B改动")
        fp_after = tree_fingerprint(self.state_dir, exclude_names={"00_同步状态.md"})

        self.assertEqual(fp_with_exclude, fp_after, "被排除的文件改动不应影响指纹")

    def test_tree_fingerprint_ignores_non_md_files(self):
        """非 .md 文件应该被忽略。"""
        _write(self.state_dir / "01_角色" / "01_角色_测试.md", "内容")
        _write(self.state_dir / "01_角色" / "readme.txt", "这是 txt 文件")

        fp1 = tree_fingerprint(self.state_dir)

        # 修改 txt 文件
        _write(self.state_dir / "01_角色" / "readme.txt", "txt 改动")
        fp2 = tree_fingerprint(self.state_dir)

        self.assertEqual(fp1, fp2, "非 .md 文件改动不应影响指纹")

    def test_render_manifest_contains_fingerprint_lines(self):
        """render_manifest 输出包含三个指纹行。"""
        manifest = render_manifest(
            folded_chapter="03_第01部/03_卷01/03_章0001",
            tool="test_tool",
            n_objects=5,
            n_records=20,
            baseline_sha="aaaa1111",
            changelog_sha="bbbb2222",
            latest_sha="cccc3333"
        )

        self.assertIn("- 基线指纹:", manifest)
        self.assertIn("- 履历指纹:", manifest)
        self.assertIn("- 最新状态指纹:", manifest)
        self.assertIn("aaaa1111", manifest)
        self.assertIn("bbbb2222", manifest)
        self.assertIn("cccc3333", manifest)

    def test_parse_manifest_fields_roundtrip(self):
        """render_manifest → parse_manifest_fields 往返不丢数据。"""
        manifest = render_manifest(
            folded_chapter="test_chapter",
            tool="merge_chapter_state.py",
            n_objects=10,
            n_records=50,
            baseline_sha="base_fp",
            changelog_sha="chg_fp",
            latest_sha="latest_fp"
        )

        # 写入目录
        manifest_path = self.state_dir / MANIFEST_FILENAME
        manifest_path.write_text(manifest, encoding="utf-8")

        # 解析
        fields = parse_manifest_fields(self.state_dir)

        self.assertEqual(fields.get("基线指纹"), "base_fp")
        self.assertEqual(fields.get("履历指纹"), "chg_fp")
        self.assertEqual(fields.get("最新状态指纹"), "latest_fp")
        self.assertEqual(fields.get("折叠至章"), "test_chapter")

    def test_verify_manifest_fingerprints_returns_none_for_old_manifest(self):
        """老 manifest 没有指纹字段 → verify 返回 None（无从核对）。"""
        # 写一个没有指纹行的 manifest
        old_manifest = """# 05_工作区/02_状态/01_最新状态 · 同步状态

> 由状态脚本自动写入。

- 折叠至章: 03_第01部/03_卷01/03_章0001
- 最后运行工具: test_tool
"""
        manifest_path = self.novel_dir / "05_工作区" / "02_状态" / "01_最新状态" / MANIFEST_FILENAME
        _write(manifest_path, old_manifest)

        result = verify_manifest_fingerprints(self.novel_dir)
        self.assertIsNone(result, "老 manifest 应返回 None")


class TestEnumDomain(unittest.TestCase):
    """enum_domain.py 的单元测试 (W6.3)"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.novel_dir = self.tmp / "novel"
        self.novel_dir.mkdir(exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_parse_domain_backtick_form_four_values(self):
        """`活跃` / `死亡` / `退场` / `暂离`（闭集）→ 4值集合。"""
        cell = "`活跃` / `死亡` / `退场` / `暂离`（闭集）"
        result = _parse_domain(cell)
        self.assertEqual(result, {"活跃", "死亡", "退场", "暂离"})

    def test_parse_domain_bare_slash_form_five_values(self):
        """赤贫 / 温饱 / 小康 / 富足 / 巨富（闭集）→ 5值集合。"""
        cell = "赤贫 / 温饱 / 小康 / 富足 / 巨富（闭集）"
        result = _parse_domain(cell)
        self.assertEqual(result, {"赤贫", "温饱", "小康", "富足", "巨富"})

    def test_parse_domain_pattern_hint_returns_empty_set(self):
        """`@角色.[示例角色]` / `无人` 中含 @ → 返回空集（形态示例，非定值）。

        这是「形态示例」不是「定值列举」，当成值域会把示例名当合法值，误报太多。
        """
        cell = "`@角色.[示例角色]` / `无人`（闭集）"
        result = _parse_domain(cell)
        self.assertEqual(result, set(), "含 @ 应返回空集")

    def test_parse_domain_ellipsis_returns_empty_set(self):
        """所有值都是模式提示（... 或 @）→ 返回空集。"""
        cell = "`...` / `…`（闭集）"
        result = _parse_domain(cell)
        self.assertEqual(result, set())

    def test_parse_closed_domains_category_scoped(self):
        """按对象类别分别存储值域。角色.对象终态 和 关系.对象终态 应有不同值集。"""
        vocab = """
### 1. 角色类对象 (`角色.[姓名]`)
| 字段 | 类型 | 合法值 / 说明 |
| --- | --- | --- |
| 对象终态 | 运算-枚举 | `活跃` / `死亡` / `退场` / `暂离`（闭集） |

### 2. 关系类对象 (`关系.[甲&乙]`)
| 字段 | 类型 | 合法值 / 说明 |
| --- | --- | --- |
| 对象终态 | 运算-枚举 | `活跃` / `终结`（闭集） |
"""
        result = parse_closed_domains(vocab)

        self.assertIn(("角色", "对象终态"), result)
        self.assertIn(("关系", "对象终态"), result)

        self.assertEqual(result[("角色", "对象终态")], {"活跃", "死亡", "退场", "暂离"})
        self.assertEqual(result[("关系", "对象终态")], {"活跃", "终结"})

    def test_parse_closed_domains_skips_non_closed_marks(self):
        """没有（闭集）标记的行应被跳过。"""
        vocab = """
### 1. 角色类对象 (`角色.[姓名]`)
| 字段 | 类型 | 合法值 / 说明 |
| --- | --- | --- |
| 对象终态 | 运算-枚举 | `活跃` / `死亡` / `退场` / `暂离` |
| 状态 | 运算-枚举 | 开放字段，无闭集 |
"""
        result = parse_closed_domains(vocab)

        # 对象终态 没有（闭集）标记，所以不应在结果里
        self.assertNotIn(("角色", "对象终态"), result)
        self.assertNotIn(("角色", "状态"), result)

    def test_enum_domain_rule_state026_for_invalid_value(self):
        """枚举值不在值域内 → STATE026 错误。"""
        # 建立最小化的小说结构
        _write(self.novel_dir / "02_数据库" / "placeholder", "")
        _write(self.novel_dir / "05_工作区" / "02_状态" / "01_最新状态" / "01_角色" / "01_角色_测试.md",
               "# 状态\n| 角色.张三 | 对象终态 | 运算-枚举 | 非法值 |")

        # 构造词表：对象终态 只允许 活跃/死亡/退场/暂离
        vocab_path = self.novel_dir / "00_通用模板" / "03_字段词表.md"
        _write(vocab_path, """
### 1. 角色类对象 (`角色.[姓名]`)
| 字段 | 类型 | 合法值 / 说明 |
| --- | --- | --- |
| 对象终态 | 运算-枚举 | `活跃` / `死亡` / `退场` / `暂离`（闭集） |
""")

        context = AuditContext(self.novel_dir)
        rule = EnumDomainRule()
        findings = rule.run(context)

        # 应该有 STATE026 错误
        state026 = [f for f in findings if f.code == "STATE026"]
        self.assertTrue(len(state026) > 0, "应该报告 STATE026 错误")

        # locations 应包含非法值 "非法值"
        self.assertTrue(any("非法值" in f.locations[0] for f in state026 if f.locations),
                       "locations 应包含非法值")

    def test_enum_domain_rule_no_error_for_valid_value(self):
        """枚举值在值域内 → 不报错。"""
        _write(self.novel_dir / "02_数据库" / "placeholder", "")
        _write(self.novel_dir / "05_工作区" / "02_状态" / "01_最新状态" / "01_角色" / "01_角色_测试.md",
               "# 状态\n| 角色.张三 | 对象终态 | 运算-枚举 | 活跃 |")

        vocab_path = self.novel_dir / "00_通用模板" / "03_字段词表.md"
        _write(vocab_path, """
### 1. 角色类对象 (`角色.[姓名]`)
| 字段 | 类型 | 合法值 / 说明 |
| --- | --- | --- |
| 对象终态 | 运算-枚举 | `活跃` / `死亡` / `退场` / `暂离`（闭集） |
""")

        context = AuditContext(self.novel_dir)
        rule = EnumDomainRule()
        findings = rule.run(context)

        # 不应有 STATE026
        state026 = [f for f in findings if f.code == "STATE026"]
        self.assertEqual(len(state026), 0, "合法值不应报错")

    def test_enum_domain_rule_category_specific_domains(self):
        """校验使用对象类别特定的值域。

        角色.X 行的对象终态=终结 应被标记（终结属于关系），
        关系.甲&乙 行的对象终态=终结 不应被标记。
        """
        _write(self.novel_dir / "02_数据库" / "placeholder", "")
        _write(self.novel_dir / "05_工作区" / "02_状态" / "01_最新状态" / "01_角色" / "01_角色_测试.md",
               "# 状态\n| 角色.张三 | 对象终态 | 运算-枚举 | 终结 |")
        _write(self.novel_dir / "05_工作区" / "02_状态" / "01_最新状态" / "06_关系" / "06_关系_张三&李四.md",
               "# 状态\n| 关系.张三&李四 | 对象终态 | 运算-枚举 | 终结 |")

        vocab_path = self.novel_dir / "00_通用模板" / "03_字段词表.md"
        _write(vocab_path, """
### 1. 角色类对象 (`角色.[姓名]`)
| 字段 | 类型 | 合法值 / 说明 |
| --- | --- | --- |
| 对象终态 | 运算-枚举 | `活跃` / `死亡` / `退场` / `暂离`（闭集） |

### 2. 关系类对象 (`关系.[甲&乙]`)
| 字段 | 类型 | 合法值 / 说明 |
| --- | --- | --- |
| 对象终态 | 运算-枚举 | `活跃` / `终结`（闭集） |
""")

        context = AuditContext(self.novel_dir)
        rule = EnumDomainRule()
        findings = rule.run(context)

        state026 = [f for f in findings if f.code == "STATE026"]
        # 应该有 1 个错误（角色.张三 的 终结 非法）
        self.assertTrue(any("角色.张三" in f.locations[0] for f in state026 if f.locations),
                       "角色.张三 的终结应被标记为错误")
        self.assertFalse(any("关系.张三&李四" in f.locations[0] for f in state026 if f.locations),
                        "关系.甲&乙 的终结应被允许")

    def test_enum_domain_rule_state027_vocabulary_not_found(self):
        """词表无法加载 → STATE027 警告。

        「查不到」和「查过了没问题」在报告里不能长得一样。
        """
        # 故意不创建词表
        _write(self.novel_dir / "02_数据库" / "placeholder", "")
        _write(self.novel_dir / "05_工作区" / "02_状态" / "01_最新状态" / "01_角色" / "01_角色_测试.md",
               "# 状态\n| 角色.张三 | 对象终态 | 运算-枚举 | 活跃 |")

        # 不创建 00_通用模板 软链或实文件，确保找不到词表
        context = AuditContext(self.novel_dir)
        rule = EnumDomainRule()
        findings = rule.run(context)

        # 应该有 STATE027 警告
        state027 = [f for f in findings if f.code == "STATE027"]
        self.assertTrue(len(state027) > 0, "找不到词表应报 STATE027 警告")


if __name__ == "__main__":
    unittest.main()
