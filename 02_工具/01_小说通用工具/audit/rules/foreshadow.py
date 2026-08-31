"""
伏笔总纲一致性规则 (foreshadow.py)
校验 @伏笔.FH-xxx 引用是否已在 03_规划/00_伏笔总纲.md（FH 总账）登记。
总纲是全书 FH-ID 唯一权威登记表：新号 = 总纲已用最大号 +1。
"""
import re
from typing import List
from ..models import Finding, Severity
from ..engine import AuditRule
from ..context import AuditContext

LEDGER_PATH = "03_规划/00_伏笔总纲.md"
# 总纲表格行里登记的 FH 号：形如 "| FH-067 | ..."
LEDGER_ROW_RE = re.compile(r"^\|\s*(FH-\d+)\s*\|", re.M)
# 任意文本里的 @伏笔.FH-xxx 引用（总纲用 ID、可不加方括号）
FH_REF_RE = re.compile(r"@伏笔\.\[?(FH-\d+)\]?")


class ForeshadowRule(AuditRule):
    name = "foreshadow"
    code_prefix = "FS"

    def run(self, context: AuditContext) -> List[Finding]:
        findings: List[Finding] = []

        ledger_fi = next((fi for fi in context.files
                          if fi.relative_path.endswith("00_伏笔总纲.md")), None)
        if ledger_fi is None:
            findings.append(Finding(
                severity=Severity.INFO,
                rule=self.name,
                code="FS002",
                message=f"伏笔总纲 {LEDGER_PATH} 不存在，无法校验 FH-ID 全局唯一性",
                file=LEDGER_PATH,
                suggestion="创建 03_规划/00_伏笔总纲.md，登记全书每个 FH-序号的名称与权威含义",
                category=None,
                locations=[LEDGER_PATH],
            ))
            return findings

        registered = set(LEDGER_ROW_RE.findall(ledger_fi.content))
        max_num = max((int(x[3:]) for x in registered), default=0)

        unregistered = []
        for fi in context.files:
            if fi.file_type != "markdown":
                continue
            if fi.data_domain not in ("01_设定", "02_数据库", "03_规划"):
                continue
            if fi.relative_path.endswith("00_伏笔总纲.md"):
                continue
            for idx, line in enumerate(fi.content.splitlines(), 1):
                for m in FH_REF_RE.finditer(line):
                    fid = m.group(1)
                    if fid not in registered:
                        unregistered.append((fi.relative_path, idx, fid))

        if unregistered:
            findings.append(Finding(
                severity=Severity.WARNING,
                rule=self.name,
                code="FS001",
                message=(
                    f"发现 {len(unregistered)} 处 @伏笔.FH-xxx 引用未在伏笔总纲登记"
                    f"（总纲当前已用最大号 {max_num}）"
                ),
                file=None,
                suggestion=(
                    "在 03_规划/00_伏笔总纲.md 为这些 FH 号补登记条目（名称/跨度/权威含义/关联对象）；"
                    "若是新伏笔，号应从总纲已用最大号 +1 起，禁止复用或跳号占位"
                ),
                category="伏笔",
                locations=[f"{p}:第{ln}行（{fid}）" for p, ln, fid in unregistered],
            ))

        return findings
