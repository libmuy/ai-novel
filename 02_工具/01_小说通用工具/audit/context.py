"""
审计上下文模块 (context.py)
持有全仓扫描所得的 FileInfo 列表，并建立索引供各个 Rule 共享使用 (只读)
"""
from pathlib import Path
from typing import List, Dict, Optional, Set
from .models import FileInfo, Reference, TodoItem
from .scanner import RepositoryScanner


class AuditContext:
    def __init__(self, novel_dir: Path, scanner: Optional[RepositoryScanner] = None):
        self.novel_dir = novel_dir.resolve()
        self.scanner = scanner or RepositoryScanner(self.novel_dir)
        self.files: List[FileInfo] = self.scanner.scan()
        self.file_map: Dict[str, FileInfo] = {f.relative_path: f for f in self.files}

        # 建立全局缓存结构
        self.references: List[Reference] = []
        self.todos: List[TodoItem] = []
        self.entities: Dict[str, Dict[str, List[str]]] = {}  # category -> entity_name -> list[rel_paths]
        self._build_entity_index()

    def get_files_by_domain(self, domain: str) -> List[FileInfo]:
        return [f for f in self.files if f.data_domain == domain]

    def get_files_by_extension(self, ext: str) -> List[FileInfo]:
        return [f for f in self.files if f.file_path.suffix.lower() == ext.lower()]

    def file_exists(self, rel_path: str) -> bool:
        return rel_path in self.file_map or (self.novel_dir / rel_path).exists()

    def _build_entity_index(self):
        """扫描 02_数据库 及已知设定下的实体生成索引"""
        db_dir = self.novel_dir / "02_数据库"
        if not db_dir.exists():
            return

        cat_map = {
            "地名": "02_地理区域",
            "区域": "02_地理区域",
            "势力": "03_势力组织",
            "人物": "07_人物",
            "书籍": "06_书籍",
            "类型": "04_资源",
            "修炼体系": "01_修炼体系",
            "伏笔": "00_伏笔册",  # 规划下的伏笔册等
        }

        for cat, sub in cat_map.items():
            self.entities[cat] = {}
            sub_dir = db_dir / sub
            if sub_dir.exists():
                for f in sub_dir.rglob("*.md"):
                    rel = f.relative_to(self.novel_dir).as_posix()
                    name = f.stem
                    # 清理前缀如 07_人物_
                    clean_name = name
                    if "_" in name:
                        parts = name.split("_")
                        if len(parts) >= 2 and (parts[0].isdigit() or parts[0] in cat_map):
                            clean_name = "_".join(parts[2:]) if len(parts) >= 3 and parts[0].isdigit() else parts[-1]

                    if clean_name not in self.entities[cat]:
                        self.entities[cat][clean_name] = []
                    self.entities[cat][clean_name].append(rel)

                    # 同时也保留文件名全名
                    if name not in self.entities[cat]:
                        self.entities[cat][name] = []
                    self.entities[cat][name].append(rel)
