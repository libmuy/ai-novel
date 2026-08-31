"""
审计数据模型模块 (models.py)
定义 Finding, AuditContext 数据结构, FileInfo, Reference, TodoItem 等对象
"""
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Set
from pathlib import Path


class Severity:
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass
class Finding:
    severity: str  # error | warning | info
    rule: str
    code: str
    message: str
    file: Optional[str] = None
    line: Optional[int] = None
    column: Optional[int] = None
    source: Optional[str] = None
    target: Optional[str] = None
    suggestion: Optional[str] = None

    # 向后兼容辅助属性
    category: Optional[str] = None
    locations: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        res = {
            "severity": self.severity,
            "rule": self.rule,
            "code": self.code,
            "message": self.message,
            "file": self.file,
            "line": self.line,
            "column": self.column,
            "source": self.source,
            "target": self.target,
            "suggestion": self.suggestion,
            # 兼容旧结构
            "check": self.code.lower() if self.code else self.rule,
            "category": self.category,
            "detail": self.message,
            "locations": self.locations if self.locations else ([f"{self.file}:第{self.line}行"] if self.file and self.line else ([self.file] if self.file else [])),
            "suggested_action": self.suggestion or "",
        }
        return res


@dataclass
class FileInfo:
    file_path: Path
    relative_path: str
    data_domain: str  # 01_设定 | 02_数据库 | 03_规划 | 05_工作区 | 10_正文 | other
    file_type: str    # markdown | text | config | state | other
    content: str


@dataclass
class Reference:
    source_file: str
    source_line: int
    source_column: int
    reference_type: str  # object | markdown_link | relative_path
    raw_text: str
    target: str
    resolved_target: Optional[str] = None
    status: str = "UNRESOLVED"  # RESOLVED | UNRESOLVED | AMBIGUOUS | ESCAPED | TYPE_MISMATCH | DEPRECATED
    entity_type: Optional[str] = None
    entity_name: Optional[str] = None
    anchor: Optional[str] = None


@dataclass
class TodoItem:
    todo_id: str
    description: str
    status: str  # TODO | IN_PROGRESS | DONE
    category: Optional[str] = None
    source_file: Optional[str] = None
    source_line: Optional[int] = None
    target_object: Optional[str] = None
