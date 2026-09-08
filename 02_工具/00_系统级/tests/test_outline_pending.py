#!/usr/bin/env python3
"""细纲【待确认清单】门禁（A5）：outline_pending 规则

- PLAN020 error：正文声明定稿/待校验，而细纲【待确认清单】仍有未裁决项
- PLAN021 warning：细纲已定稿但待确认项没清零
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "01_小说通用工具"))

from helpers import make_novel  # noqa: E402
from audit import AuditContext  # noqa: E402
from audit.rules.outline_pending import OutlinePendingRule, _open_items  # noqa: E402

OUTLINE_OPEN = """# 单章细纲 · 第01章

## 【待确认清单】

R3 冷读的实质问题已处理。

当前遗留待作者过目：

1. **灵心草量/值**：按细纲值处理，若要精确请裁。
2. **周莽给监工碎矿石**：定性为"喝茶钱"。
"""

OUTLINE_RESOLVED = """# 单章细纲 · 第01章

## 【待确认清单】

R3 冷读的实质问题已处理。

**4 条已全部裁决（2026-09-08 用户）：**

1. 灵心草量/值：按细纲值 —— 已确认。
2. 周莽给监工碎矿石：定性「喝茶钱」 —— 已确认。
"""


def _novel(td, outline_body, manuscript_status):
    novel = Path(make_novel(td))
    (novel / "03_规划" / "01_第01部" / "01_卷01").mkdir(parents=True, exist_ok=True)
    (novel / "03_规划" / "01_第01部" / "01_卷01" / "规划_卷01_章0001.md").write_text(
        outline_body, encoding="utf-8")
    (novel / "10_正文" / "01_第01部" / "01_卷01").mkdir(parents=True, exist_ok=True)
    (novel / "10_正文" / "01_第01部" / "01_卷01" / "章0001.md").write_text(
        "正文正文正文。", encoding="utf-8")
    (novel / "00_进度.md").write_text(
        "# 进度\n\n| 产出 | 状态 | 说明 |\n|---|---|---|\n"
        f"| 细纲 | `规划_卷01_章0001.md` | 定稿 |\n"
        f"| 正文 | `章0001.md` | {manuscript_status} |\n",
        encoding="utf-8")
    return novel


class TestOutlinePending(unittest.TestCase):

    def _codes(self, novel):
        return {f.code: f for f in OutlinePendingRule().run(AuditContext(novel))}

    def test_open_items_detects_numbered_after_trigger(self):
        self.assertEqual(len(_open_items(OUTLINE_OPEN.split("## 【待确认清单】")[1])), 2)

    def test_resolved_items_not_flagged(self):
        self.assertEqual(_open_items(OUTLINE_RESOLVED.split("## 【待确认清单】")[1]), [])

    def test_plan020_when_manuscript_final(self):
        with tempfile.TemporaryDirectory() as td:
            codes = self._codes(_novel(td, OUTLINE_OPEN, "定稿"))
            self.assertIn("PLAN020", codes)
            self.assertIn("章0001", "".join(codes["PLAN020"].locations))

    def test_plan021_when_only_outline_final(self):
        with tempfile.TemporaryDirectory() as td:
            codes = self._codes(_novel(td, OUTLINE_OPEN, "待回填"))
            self.assertNotIn("PLAN020", codes)
            self.assertIn("PLAN021", codes)

    def test_clean_when_resolved(self):
        with tempfile.TemporaryDirectory() as td:
            codes = self._codes(_novel(td, OUTLINE_RESOLVED, "定稿"))
            self.assertNotIn("PLAN020", codes)
            self.assertNotIn("PLAN021", codes)


if __name__ == "__main__":
    unittest.main()
