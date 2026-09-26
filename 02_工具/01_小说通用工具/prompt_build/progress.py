# -*- coding: utf-8 -*-
"""
前置门禁 (progress.py)

`00_使用说明.md`【前置阻断】：任务路由表的必读数据必须全部为「定稿」。任一前置
缺失、状态不是「定稿」或引用未闭合时，只能输出阻断报告，**不得用假设 / 占位内容 /
旧提示词继续生成**。

实现：读小说 `00_进度.json`（`progress_store.py`），按目标路径精确查成熟度。
"""
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import progress_store  # noqa: E402

PROGRESS_FILE = progress_store.PROGRESS_REL

# 成熟度三态；越靠前越成熟
MATURITY = progress_store.MATURITY


@dataclass
class Blocker:
    what: str          # 缺什么
    path: str          # 相关路径
    status: Optional[str]   # 当前成熟度（None = 进度表里查不到）
    need: str          # 需要达到的成熟度
    next_step: str     # 下一步动作

    def render(self) -> str:
        cur = self.status or "进度表未登记"
        return (f"- **{self.what}**\n"
                f"  - 路径：`{self.path}`\n"
                f"  - 当前：{cur}　→　需要：{self.need}\n"
                f"  - 下一步：{self.next_step}")


class ProgressIndex:
    """`00_进度.json` 的路径 → 成熟度索引。"""

    def __init__(self, novel_dir: Path):
        self.novel_dir = Path(novel_dir)
        self.path_status: dict[str, str] = {}
        self.exists = False
        self.legacy = progress_store.legacy_exists(self.novel_dir)
        self.error: Optional[str] = None
        try:
            self.path_status = progress_store.statuses(self.novel_dir)
        except progress_store.ProgressFormatError as e:
            self.error = str(e)
            return
        self.exists = progress_store.exists(self.novel_dir)

    def status_of(self, path: Path | str) -> Optional[str]:
        """精确匹配查成熟度。`path` 可为绝对路径或小说内相对路径。"""
        return progress_store.status_of(self.path_status, path, self.novel_dir)

    def is_at_least(self, path: Path | str, need: str = "定稿") -> bool:
        st = self.status_of(path)
        if st is None:
            return False
        return MATURITY.index(st) <= MATURITY.index(need)


def render_block_report(novel_name: str, task: str, blockers: list[Blocker]) -> str:
    lines = [
        f"# 阻断报告 · {novel_name} · {task}",
        "",
        "前置数据未达「定稿」，**未生成提示词、未预建任何文件**。",
        "依据 `00_通用模板/00_使用说明.md`【前置阻断】：不得用假设、占位内容或旧提示词继续生成。",
        "",
        f"## 阻断项（{len(blockers)}）",
        "",
    ]
    lines += [b.render() for b in blockers]
    lines += [
        "",
        "## 解除后",
        "",
        "把上述文件推进到「定稿」并更新 `00_进度.json`，重跑本命令即可。",
    ]
    return "\n".join(lines)
