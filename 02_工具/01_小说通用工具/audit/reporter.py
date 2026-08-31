"""
统一报告输出模块 (reporter.py)
支持人类可读文本格式与 JSON 格式
"""
import json
from datetime import datetime, timezone
from typing import List, Dict, Any
from pathlib import Path
from .models import Finding


class AuditReporter:
    def __init__(self, novel_dir: Path, findings: List[Finding]):
        self.novel_dir = novel_dir
        self.findings = findings

    def to_dict(self) -> Dict[str, Any]:
        return {
            "novel_dir": str(self.novel_dir),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "issues": [f.to_dict() for f in self.findings]
        }

    def render_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    def render_text(self) -> str:
        summary = {
            "error": sum(1 for f in self.findings if f.severity == "error"),
            "warning": sum(1 for f in self.findings if f.severity == "warning"),
            "info": sum(1 for f in self.findings if f.severity == "info"),
        }
        lines = [
            f"=== 一致性审查报告: {self.novel_dir} ===",
            f"ERROR: {summary['error']} | WARNING: {summary['warning']} | INFO: {summary['info']}\n"
        ]
        for f in self.findings:
            loc = f.file if f.file else "全局"
            if f.line:
                loc += f":第{f.line}行"
                if f.column:
                    loc += f":第{f.column}列"
            lines.append(f"[{f.severity.upper()}] Code: {f.code} | Rule: {f.rule}")
            lines.append(f"  位置: {loc}")
            lines.append(f"  问题: {f.message}")
            if f.source:
                lines.append(f"  来源: {f.source}")
            if f.target:
                lines.append(f"  目标: {f.target}")
            if f.suggestion:
                lines.append(f"  建议: {f.suggestion}")
            lines.append("")

        return "\n".join(lines)
