"""
仓库扫描器 (scanner.py)
负责枚举小说根目录下除排除项外的所有数据文件，并生成 FileInfo 对象
"""
import os
from pathlib import Path
from typing import List, Set
from .models import FileInfo

DEFAULT_EXCLUDES: Set[str] = {
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".idea",
    ".vscode",
    "node_modules",
    "build",
    "dist",
    "target",
    ".tmp",
}

DEFAULT_EXCLUDE_EXTS: Set[str] = {
    ".pyc",
    ".pyo",
    ".pyd",
    ".DS_Store",
}


class RepositoryScanner:
    def __init__(self, novel_dir: Path, custom_ignores: Set[str] = None):
        self.novel_dir = novel_dir.resolve()
        self.ignores = set(DEFAULT_EXCLUDES)
        if custom_ignores:
            self.ignores.update(custom_ignores)

    def scan(self) -> List[FileInfo]:
        file_infos = []
        if not self.novel_dir.exists():
            return file_infos

        for root, dirs, files in os.walk(self.novel_dir):
            # 过滤排除目录
            dirs[:] = [d for d in dirs if d not in self.ignores and not d.startswith(".")]

            root_path = Path(root)
            for f in files:
                if f.startswith(".") or any(f.endswith(ext) for ext in DEFAULT_EXCLUDE_EXTS):
                    continue

                full_path = root_path / f
                try:
                    rel_path = full_path.relative_to(self.novel_dir).as_posix()
                except ValueError:
                    continue

                domain = self._determine_data_domain(rel_path)
                ftype = self._determine_file_type(full_path)
                content = self._read_content(full_path)

                file_infos.append(FileInfo(
                    file_path=full_path,
                    relative_path=rel_path,
                    data_domain=domain,
                    file_type=ftype,
                    content=content
                ))

        file_infos.sort(key=lambda x: x.relative_path)
        return file_infos

    def _determine_data_domain(self, rel_path: str) -> str:
        parts = rel_path.split("/")
        first = parts[0]
        if first in ["01_设定", "02_数据库", "03_规划", "05_工作区", "10_正文"]:
            return first
        return "other"

    def _determine_file_type(self, path: Path) -> str:
        suffix = path.suffix.lower()
        if suffix in [".md", ".markdown"]:
            return "markdown"
        elif suffix in [".toml", ".yaml", ".yml", ".json"]:
            return "config"
        elif suffix in [".txt"]:
            return "text"
        return "other"

    def _read_content(self, path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return ""
