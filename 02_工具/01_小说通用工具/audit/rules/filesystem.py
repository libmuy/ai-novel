"""
文件系统审计规则 (filesystem.py)
检查标准顶层目录与符号链接
"""
from typing import List
from ..models import Finding, Severity
from ..engine import AuditRule
from ..context import AuditContext

STANDARD_TOP_DIRS = ["01_设定", "02_数据库", "03_规划", "05_工作区", "10_正文"]


class FilesystemRule(AuditRule):
    name = "filesystem"
    code_prefix = "FS"

    def run(self, context: AuditContext) -> List[Finding]:
        findings: List[Finding] = []
        novel_dir = context.novel_dir

        # 检查标准顶层目录
        missing = [d for d in STANDARD_TOP_DIRS if not (novel_dir / d).exists()]
        if missing:
            findings.append(Finding(
                severity=Severity.WARNING,
                rule=self.name,
                code="FS001",
                message=f"缺失标准顶层目录: {missing}",
                file=str(novel_dir),
                suggestion=f"从 00_通用模板/05_项目骨架模板/ 下对应目录复制骨架，在 {novel_dir} 下建立: {missing}",
                category=None,
                locations=missing
            ))

        # 检查符号链接
        link = novel_dir / "00_通用模板"
        if not link.exists():
            findings.append(Finding(
                severity=Severity.ERROR,
                rule=self.name,
                code="FS002",
                message=f"{link} 不存在",
                file=str(link),
                suggestion=f"执行 ln -s ../../00_通用模板 {link} 建立符号链接",
                category=None,
                locations=[str(link)]
            ))
        elif not link.is_symlink():
            findings.append(Finding(
                severity=Severity.ERROR,
                rule=self.name,
                code="FS002",
                message=f"{link} 存在但不是符号链接，可能被误拷贝为实体目录，会导致模板数据重复/失步",
                file=str(link),
                suggestion=f"备份后删除该实体目录，重新执行 ln -s ../../00_通用模板 {link}",
                category=None,
                locations=[str(link)]
            ))

        return findings
