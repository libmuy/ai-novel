"""
索引一致性规则 (index.py)
校验 02_数据库 下分类总索引文件与实际卡片文件的对应关系
"""
import re
from pathlib import Path
from typing import List
from ..models import Finding, Severity
from ..engine import AuditRule
from ..context import AuditContext


class IndexRule(AuditRule):
    name = "index"
    code_prefix = "INDEX"

    def run(self, context: AuditContext) -> List[Finding]:
        findings: List[Finding] = []
        novel_dir = context.novel_dir
        db = novel_dir / "02_数据库"
        if not db.exists():
            return findings

        for sub in sorted(db.iterdir()):
            if not sub.is_dir():
                continue
            idx = sub / f"{sub.name}.md"
            idx_rel = idx.relative_to(novel_dir).as_posix()
            if not idx.exists():
                findings.append(Finding(
                    severity=Severity.ERROR,
                    rule=self.name,
                    code="INDEX001",
                    message=f"缺少总索引文件 {idx.name}",
                    file=idx_rel,
                    suggestion=f"按对应卡片模板创建总索引文件 {idx}",
                    category=sub.name,
                    locations=[idx_rel]
                ))
                continue

            # 读取内容解析
            idx_file_info = context.file_map.get(idx_rel)
            text = idx_file_info.content if idx_file_info else (idx.read_text(encoding="utf-8", errors="ignore") if idx.exists() else "")

            raw_linked = re.findall(rf"({re.escape(sub.name)}[^\s\)\(\[\]\`|<>]*\.md)", text)
            linked = set(Path(x).name for x in raw_linked)
            actual = set(f.name for f in sub.glob(f"{sub.name}*.md"))
            actual.discard(idx.name)
            linked.discard(idx.name)

            prefix = f"{sub.name}_"
            is_multilevel = any(
                f[len(prefix):].count("_") >= 1 for f in actual if f.startswith(prefix)
            )

            if is_multilevel:
                depth1_actual = {f for f in actual if f[len(prefix):].count("_") == 0}
                missing_in_index = sorted(depth1_actual - linked)
                if missing_in_index:
                    findings.append(Finding(
                        severity=Severity.WARNING,
                        rule=self.name,
                        code="INDEX002",
                        message="多级层级目录，第一级文件未在总索引登记",
                        file=idx_rel,
                        suggestion=f"在 {idx} 的世界索引表中补充上述文件链接",
                        category=sub.name,
                        locations=[f"{sub.name}/{f}" for f in missing_in_index]
                    ))
            else:
                missing_in_index = sorted(actual - linked)
                missing_files = sorted(linked - actual)
                if missing_in_index:
                    findings.append(Finding(
                        severity=Severity.ERROR,
                        rule=self.name,
                        code="INDEX003",
                        message="文件存在但总索引未链接（孤儿文件）",
                        file=idx_rel,
                        suggestion=f"在 {idx} 的索引表中补充这些文件的链接行",
                        category=sub.name,
                        locations=[f"{sub.name}/{f}" for f in missing_in_index]
                    ))
                if missing_files:
                    findings.append(Finding(
                        severity=Severity.ERROR,
                        rule=self.name,
                        code="INDEX004",
                        message="总索引中提到但文件不存在（悬空链接）",
                        file=idx_rel,
                        suggestion=f"确认这些文件是否被误删，若确认废弃则从 {idx} 索引表中移除对应行",
                        category=sub.name,
                        locations=[f"{sub.name}/{f}" for f in missing_files]
                    ))

        return findings
