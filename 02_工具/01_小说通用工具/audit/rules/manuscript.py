"""
正文校验规则 (manuscript.py)
校验 10_正文/ 目录的纯净性及对象引用正确性
"""
import re
from typing import List
from ..models import Finding, Severity
from ..engine import AuditRule
from ..context import AuditContext
from ..resolver.reference_resolver import ReferenceResolver


class ManuscriptRule(AuditRule):
    name = "manuscript"
    code_prefix = "MANUSCRIPT"

    def run(self, context: AuditContext) -> List[Finding]:
        findings: List[Finding] = []
        manuscript_files = [fi for fi in context.files if fi.data_domain == "10_正文"]
        if not manuscript_files:
            return findings

        ref_pattern = re.compile(r"@(地名|势力|人物|类型|书籍|伏笔|区域)\.")
        heading_re = re.compile(r"^\s{0,3}#{1,6}\s")
        # MANUSCRIPT004：同一 4~20 字短语紧邻重复一次（中间只隔一个顿号/逗号或空白）。
        # 这是本地手改/局部替换最常留下的"卡壳"痕迹——真实案例：
        # 「现在经脉里那点气走得极慢，走得极慢，走几步就要停一停」（改稿时删了半句忘删旧半句）。
        # 有意的中文重叠修辞（"一下一下""很久很久"）单字重叠单元通常 ≤3 字，min=4 基本不会误伤；
        # 已在全书既有定稿正文上跑过一遍，0 误报。
        stutter_re = re.compile(r"([一-鿿]{4,20})([，,、]\s*)\1")
        resolver = ReferenceResolver(context)

        # MANUSCRIPT003：正文首行是 Markdown 标题（`# 第04章` 之类），
        # 而同书其它章正文直接以散文起头——体例不一致（正文是读者/TTS 成稿，
        # 章号在 `00_进度.md` 与目录里，不进散文）。跨章自校准：只在「多数章不带」时报。
        def _starts_with_heading(fi) -> bool:
            for ln in fi.content.splitlines():
                if ln.strip():
                    return bool(heading_re.match(ln))
            return False

        offenders = [fi for fi in manuscript_files if _starts_with_heading(fi)]
        clean_n = len(manuscript_files) - len(offenders)
        if offenders and clean_n >= max(1, len(manuscript_files) // 2):
            findings.append(Finding(
                severity=Severity.ERROR,
                rule=self.name,
                code="MANUSCRIPT003",
                message=f"{len(offenders)} 章正文以 Markdown 标题起头，与同书其它 {clean_n} 章体例不一致"
                        f"（正文直接散文起头，章号进 `00_进度.md`/目录不进正文）",
                file=offenders[0].relative_path,
                line=1,
                suggestion="删掉正文文件开头的 `# 第NN章` 之类标题行；ch1~3 是范式",
                locations=[f"{fi.relative_path}:第1行" for fi in offenders],
            ))

        for fi in manuscript_files:
            lines = fi.content.splitlines()
            for idx, line in enumerate(lines, 1):
                # 1. 检查数据引用语法残留 (ERROR)
                if ref_pattern.search(line):
                    findings.append(Finding(
                        severity=Severity.ERROR,
                        rule=self.name,
                        code="MANUSCRIPT001",
                        message=f"正文中存在数据引用语法残留「{line.strip()}」",
                        file=fi.relative_path,
                        line=idx,
                        source=line.strip(),
                        suggestion="正文为最终读者成稿，请删除或更正其中的数据层引用语法 @类型.",
                        locations=[f"{fi.relative_path}:第{idx}行"]
                    ))

                # 4. 相邻重复短语（卡壳编辑遗留）
                for m in stutter_re.finditer(line):
                    findings.append(Finding(
                        severity=Severity.WARNING,
                        rule=self.name,
                        code="MANUSCRIPT004",
                        message=f"「{m.group(0)}」——同一短语紧邻重复，像是本地编辑删句时漏删旧半句",
                        file=fi.relative_path,
                        line=idx,
                        source=line.strip(),
                        suggestion="核对是否为编辑遗留的重复片段；确系作者刻意的重叠修辞（少见）可忽略",
                        locations=[f"{fi.relative_path}:第{idx}行"]
                    ))

            # 2. 正文引用对象的有效性校验
            file_refs = resolver.extract_references(fi)
            for ref in file_refs:
                if ref.reference_type == "object" and ref.status == "UNRESOLVED":
                    findings.append(Finding(
                        severity=Severity.ERROR,
                        rule=self.name,
                        code="MANUSCRIPT002",
                        message=f"正文中引用的对象「{ref.entity_name}」在数据库中不存在",
                        file=fi.relative_path,
                        line=ref.source_line,
                        column=ref.source_column,
                        source=ref.raw_text,
                        target=ref.target,
                        suggestion="核对正文中引用的实体名，确保在数据库中存在或订正拼写",
                        category=ref.entity_type,
                        locations=[f"{fi.relative_path}:第{ref.source_line}行"]
                    ))

        return findings
