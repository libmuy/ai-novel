"""
审计引擎模块 (engine.py)
负责注册 Rule, 运行 Rule, 收集 Finding 并传递给 Reporter
"""
from typing import List, Type, Optional
from pathlib import Path
from .models import Finding
from .context import AuditContext


class AuditRule:
    name: str = "base_rule"
    code_prefix: str = "BASE"

    def run(self, context: AuditContext) -> List[Finding]:
        raise NotImplementedError


class AuditEngine:
    def __init__(self, novel_dir: Path):
        self.novel_dir = novel_dir
        self.rules: List[AuditRule] = []

    def register_rule(self, rule: AuditRule):
        self.rules.append(rule)

    def run(self, rule_filter: Optional[str] = None, context: Optional[AuditContext] = None) -> List[Finding]:
        if context is None:
            context = AuditContext(self.novel_dir)

        all_findings: List[Finding] = []
        for rule in self.rules:
            if rule_filter and rule.name != rule_filter and not rule.name.startswith(rule_filter):
                continue
            findings = rule.run(context)
            all_findings.extend(findings)

        return all_findings
