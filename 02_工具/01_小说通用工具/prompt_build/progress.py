# -*- coding: utf-8 -*-
"""
前置门禁 (progress.py)

`00_使用说明.md`【前置阻断】：任务路由表的必读数据必须全部为「定稿」。任一前置
缺失、状态不是「定稿」或引用未闭合时，只能输出阻断报告，**不得用假设 / 占位内容 /
旧提示词继续生成**。

实现：把小说 `00_进度.md` 里所有 Markdown 表格行解析成「行内出现的路径 → 成熟度」，
再按目标路径查。路径匹配用后缀比对，容忍进度表写相对路径或带反引号。
"""
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

PROGRESS_FILE = "00_进度.md"

# 成熟度三态（`00_进度.md`【状态图例】）；越靠前越成熟
MATURITY = ("定稿", "待校验", "草稿")

_CODE_PATH_RE = re.compile(r"`([^`\n]+?\.md)`")
_STATUS_RE = re.compile(r"(定稿|待校验|草稿)")


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
    """`00_进度.md` 的路径 → 成熟度索引。"""

    def __init__(self, novel_dir: Path):
        self.novel_dir = Path(novel_dir)
        self.path_status: dict[str, str] = {}
        self.exists = False
        src = self.novel_dir / PROGRESS_FILE
        if not src.exists():
            return
        self.exists = True
        for line in src.read_text(encoding="utf-8", errors="ignore").splitlines():
            s = line.strip()
            if not s.startswith("|"):
                continue
            cells = [c.strip() for c in s.strip("|").split("|")]
            if len(cells) < 2 or all(set(c) <= set(":- ") for c in cells):
                continue
            paths = [p for c in cells for p in _CODE_PATH_RE.findall(c)]
            if not paths:
                continue
            # 状态取行内最靠前出现的成熟度词（进度表的「状态」列在路径列之后）
            status = None
            for c in cells:
                m = _STATUS_RE.search(c)
                if m:
                    status = m.group(1)
                    break
            if status is None:
                continue
            for p in paths:
                # 后写的行覆盖先写的（进度表下方是更新的复述）
                self.path_status.setdefault(p.strip(), status)

    def status_of(self, path: Path | str) -> Optional[str]:
        """按后缀匹配查成熟度。`path` 可为绝对路径或小说内相对路径。"""
        want = Path(path)
        try:
            want_rel = want.relative_to(self.novel_dir).as_posix()
        except ValueError:
            want_rel = want.as_posix()
        for recorded, status in self.path_status.items():
            r = recorded.lstrip("./")
            if want_rel == r or want_rel.endswith("/" + r) or r.endswith("/" + want_rel):
                return status
        return None

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
        "把上述文件推进到「定稿」并更新 `00_进度.md`，重跑本命令即可。",
    ]
    return "\n".join(lines)
