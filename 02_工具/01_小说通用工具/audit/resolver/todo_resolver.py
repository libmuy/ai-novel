"""
TODO 解析器 (todo_resolver.py)
解析 TODO 描述的目标对象、ID、状态, 并关联仓库实体
"""
import re
from typing import List, Optional, Dict, Tuple, Any
from ..models import TodoItem, FileInfo
from ..context import AuditContext

TODO_PATTERN = re.compile(r"@(地名|势力|人物|类型|书籍|伏笔)\.\[TODO-([^\]]+)\]")
CATEGORY_KEYWORD_IN_PROGRESS = {
    "地名": "02_地理区域提示.md",
    "势力": "03_势力组织提示.md",
    "人物": "05_主角与核心配角提示.md",
    "类型": "04_资源提示.md",
    "书籍": "08_书籍库提示.md",
    "伏笔": "09_全书卷大纲提示.md",
}
TODO_GLOBAL_PREFIXES = {"FC", "CH", "FH", "BK", "DN"}


class TodoResolver:
    def __init__(self, context: AuditContext):
        self.context = context

    def resolve_todos(self) -> Tuple[List[TodoItem], Dict[str, Any]]:
        todos: List[TodoItem] = []
        registry_ids: set = set()

        # 1. 扫描 00_TODO全局注册表.md
        reg_fi = self.context.file_map.get("02_数据库/00_TODO全局注册表.md")
        if reg_fi:
            for m in re.finditer(r"(TODO-[A-Z]{2}-\d+)", reg_fi.content):
                registry_ids.add(m.group(1))

            # 解析注册表表格中的 STATUS
            for line in reg_fi.content.splitlines():
                line = line.strip()
                if not line.startswith("|") or "---" in line or "原TODO" in line or "描述" in line:
                    continue
                parts = [p.strip() for p in line.split("|")[1:-1]]
                if len(parts) >= 3:
                    todo_id, desc, status = parts[0], parts[1], parts[2]
                    if not todo_id.startswith("TODO-"):
                        continue
                    category = None
                    m = re.match(r"^TODO-([A-Z]{2})-\d+$", todo_id)
                    if m:
                        prefix = m.group(1)
                        category = {"FC": "人物", "CH": "地名", "FH": "伏笔", "BK": "书籍", "DN": "势力"}.get(prefix)
                    todos.append(TodoItem(
                        todo_id=todo_id,
                        description=desc,
                        status=status.upper(),
                        category=category,
                        source_file="02_数据库/00_TODO全局注册表.md",
                        target_object=desc
                    ))

        # 2. 解析 00_进度.md 中的分类状态
        progress_status = self._parse_progress_status()

        return todos, {
            "registry_ids": registry_ids,
            "progress_status": progress_status,
            "has_registry": reg_fi is not None
        }

    def _parse_progress_status(self) -> Dict[str, str]:
        prog_fi = self.context.file_map.get("00_进度.md")
        status = {}
        if not prog_fi:
            return status
        for line in prog_fi.content.splitlines():
            line = line.strip()
            if not line.startswith("|"):
                continue
            for todo_type, keyword in CATEGORY_KEYWORD_IN_PROGRESS.items():
                if keyword in line:
                    cells = [c.strip() for c in line.strip("|").split("|")]
                    if cells:
                        status[todo_type] = cells[-1]
        return status
