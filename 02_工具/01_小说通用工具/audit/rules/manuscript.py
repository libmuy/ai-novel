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

        # MANUSCRIPT003：正文首行的「章名」标题规范。体例 = 首个非空行是 `# 章名`，
        # 章名逐字照抄单章细纲【基础信息】「章名」字段；章号只进进度表/目录、不进正文。
        # 三条子判定（同码分列，各自聚合一条 Finding）：
        #   a) 编号式标题（`# 第NN章 …`）——无论全书体例，一律报；
        #   b) 首行标题与细纲「章名」字段不一致（细纲存在且填了该字段时才比对）；
        #   c) 跨章体例自校准（双向）：多数章带标题 → 缺标题的报；多数章散文起头 →
        #      多带标题的报。这样「全书带标题」与「全书散文起头」两种体例都合法，
        #      但同书必须一致，且带的那批不能是编号式、不能与细纲对不上。
        def _first_line(fi) -> str:
            for ln in fi.content.splitlines():
                if ln.strip():
                    return ln
            return ""

        def _split_path(rel: str):
            m = re.search(r"(?:正文|规划)_卷(\d+)_章(\d{4})\.md$", rel)
            return (m.group(1), m.group(2)) if m else None

        def _outline_title(vol: str, chapter_no: str) -> str:
            """同章单章细纲【基础信息】的「章名」字段值；无细纲/无字段返回 ""。"""
            for fi in context.files:
                if fi.data_domain != "03_规划":
                    continue
                sp = _split_path(fi.relative_path)
                if sp and sp == (vol, chapter_no) and fi.relative_path.rsplit("/", 1)[-1].startswith("规划_"):
                    for ln in fi.content.splitlines():
                        s = ln.strip()
                        if not s.startswith("|"):
                            continue
                        cells = [c.strip() for c in s.strip("|").split("|")]
                        if cells and cells[0] == "章名":
                            return cells[2] if len(cells) >= 3 else (cells[1] if len(cells) == 2 else "")
            return ""

        numeric: list = []   # a) 编号式标题（同时排除出体例统计，免得再叠一条「缺标题」）
        mismatch: list = []  # b) 与细纲章名不一致
        titled = []          # 首行是合规标题的章
        prose = []           # 首行是散文的章
        numeric_re = re.compile(r"^#{1,6}\s*第\s*[0-9０-９一二三四五六七八九十百千]+\s*章")

        for fi in manuscript_files:
            ln = _first_line(fi)
            if heading_re.match(ln):
                if numeric_re.match(ln):
                    numeric.append((fi, ln.strip()))
                    continue
                titled.append(fi)
                sp = _split_path(fi.relative_path)
                want = _outline_title(*sp) if sp else ""
                got = re.sub(r"^#{1,6}\s*", "", ln).strip()
                if want and got != want:
                    mismatch.append((fi, got, want))
            else:
                prose.append(fi)

        if numeric:
            findings.append(Finding(
                severity=Severity.ERROR,
                rule=self.name,
                code="MANUSCRIPT003",
                message=f"{len(numeric)} 章正文以编号式标题起头（{numeric[0][1]!r} 等）——"
                        f"章号只进进度表/目录，不进正文",
                file=numeric[0][0].relative_path,
                line=1,
                suggestion="首行改为 `# 章名`（照抄细纲【基础信息】「章名」字段），或删除标题行",
                locations=[f"{fi.relative_path}:第1行" for fi, _ in numeric],
            ))

        if mismatch:
            findings.append(Finding(
                severity=Severity.ERROR,
                rule=self.name,
                code="MANUSCRIPT003",
                message=f"{len(mismatch)} 章正文首行标题与细纲【基础信息】「章名」字段不一致"
                        f"（如正文 {mismatch[0][1]!r} vs 细纲 {mismatch[0][2]!r}）",
                file=mismatch[0][0].relative_path,
                line=1,
                suggestion="细纲「章名」字段是权威——改正文首行对齐它，或改细纲后同步正文",
                locations=[f"{fi.relative_path}:第1行" for fi, _, _ in mismatch],
            ))

        total = len(titled) + len(prose)
        if titled and len(titled) >= max(1, total // 2):
            # 体例 = 带标题：缺的报
            missing = [fi for fi in prose]
            if missing:
                findings.append(Finding(
                    severity=Severity.ERROR,
                    rule=self.name,
                    code="MANUSCRIPT003",
                    message=f"{len(missing)} 章正文缺首行章名标题，与同书其它 {len(titled)} 章体例不一致"
                            f"（本书体例：首行 `# 章名`）",
                    file=missing[0].relative_path,
                    line=1,
                    suggestion="在正文首行补 `# 章名`（照抄细纲【基础信息】「章名」字段）",
                    locations=[f"{fi.relative_path}:第1行" for fi in missing],
                ))
        elif prose and len(prose) >= max(1, total // 2) and titled:
            # 体例 = 散文起头：多带标题的报（排除已按编号式/不一致报过的，仍按体例列全）
            findings.append(Finding(
                severity=Severity.ERROR,
                rule=self.name,
                code="MANUSCRIPT003",
                message=f"{len(titled)} 章正文以 Markdown 标题起头，与同书其它 {len(prose)} 章"
                        f"「散文直接起头」体例不一致",
                file=titled[0].relative_path,
                line=1,
                suggestion="删掉正文首行标题行，或全书统一改为 `# 章名` 体例并补细纲章名字段",
                locations=[f"{fi.relative_path}:第1行" for fi in titled],
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
