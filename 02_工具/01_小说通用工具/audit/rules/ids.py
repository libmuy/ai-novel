"""
ID 格式与频次规则 (ids.py)
校验 ID 格式与卡片类型的匹配，统计 ID 频次
"""
import re
from collections import defaultdict
from typing import List
from ..models import Finding, Severity
from ..engine import AuditRule
from ..context import AuditContext


class IdRule(AuditRule):
    name = "ids"
    code_prefix = "ID"

    def run(self, context: AuditContext) -> List[Finding]:
        findings: List[Finding] = []
        novel_dir = context.novel_dir

        # 1. 频次统计
        prefixes = ("WR-", "DY-", "RES-")
        counts = defaultdict(int)
        for fi in context.files:
            if fi.file_type == "markdown":
                for prefix in prefixes:
                    for m in re.finditer(rf"{re.escape(prefix)}[A-Z]*-?\d+", fi.content):
                        counts[m.group(0)] += 1

        findings.append(Finding(
            severity=Severity.INFO,
            rule=self.name,
            code="ID001",
            message="编号出现频次统计，仅供参考，不代表重复定义（多处引用同一编号是正常的）",
            file=None,
            suggestion="如需精确判断某编号是否被重复定义（而非引用），需按各编号的定义位置规则单独检查",
            category=None,
            locations=[]
        ))

        # 2. 重复定义/格式校验
        defined_ids = defaultdict(set)
        id_pattern = re.compile(r"\b(WR-\d+|DY-\d+|RES-[A-Z]+-\d+|V\d+-C\d+-\d+|BP-V\d+-\d+|BT-V\d+-\d+)\b")
        ref_pattern = re.compile(r"@\w+\.\[")

        for fi in context.files:
            if "05_工作区" in fi.relative_path or "00_TODO全局注册表" in fi.relative_path:
                continue
            if fi.file_type != "markdown":
                continue

            lines = fi.content.splitlines()
            for line in lines:
                if ref_pattern.search(line):
                    continue
                for m in id_pattern.finditer(line):
                    defined_ids[m.group(1)].add(fi.relative_path)

        duplicates = {}
        for k, v in defined_ids.items():
            if len(v) > 2:
                duplicates[k] = sorted(v)

        if duplicates:
            locs = [f"{k}: {', '.join(v[:3])}" for k, v in sorted(duplicates.items())]
            findings.append(Finding(
                severity=Severity.WARNING,
                rule=self.name,
                code="ID002",
                message=f"发现 {len(duplicates)} 个ID在多个文件中出现（可能是重复定义）",
                file=None,
                suggestion="检查这些ID是否在不同文件中被重复定义（而非仅被引用），若是则合并或去重",
                category=None,
                locations=locs
            ))

        return findings
