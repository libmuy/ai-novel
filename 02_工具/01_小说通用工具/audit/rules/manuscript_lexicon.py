"""
正文禁用词校验 (manuscript_lexicon.py)

读每本小说 `01_设定/00_禁用词表.md`，对 `10_正文/**/*.md` 逐行匹配，命中即报。
用于拦截「现代科技/计量/材料词穿帮」这类确定性问题（见 ch1 #6 手电光柱）。

- LEXICON000 (info)：未建 `01_设定/00_禁用词表.md`，本规则跳过。
- LEXICON001 (warning)：正文命中禁用词。首轮定 warning——存量正文（如 ch1「手电」）
  待细纲重出后重写，届时可把本规则升为 error 并加回归测试。

词表格式（不使用竖线等 Markdown 特殊字符做分隔）：
    ## 包含        —— 之后每行一个词，纯子串匹配（缺省区，可省略标题）
    ## 正则        —— 之后每行一个 Python 正则
    ## 待确认      —— 之后的词【只登记不启用】（如"分钟/公里"是否真的没有对应概念，待用户定）
  行尾 `  //` 之后为「为什么禁」注释，不参与匹配；`#` 开头的行为普通注释。
"""
import re
from typing import List, Tuple
from ..models import Finding, Severity
from ..engine import AuditRule
from ..context import AuditContext

LEXICON_REL = "01_设定/00_禁用词表.md"

_SECTION_RE = re.compile(r"^#{2,}\s*(包含|子串|正则|待确认|待定)\s*$")
_COMMENT_SPLIT = re.compile(r"\s+//\s*|\s+——\s*")


def _parse_lexicon(text: str) -> List[Tuple[str, str, str]]:
    """→ [(term, mode, reason)]；mode ∈ {'sub','re'}；仅返回【启用】的词（跳过待确认区）。"""
    out: List[Tuple[str, str, str]] = []
    mode = "sub"          # 缺省：子串
    enforced = True
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        sec = _SECTION_RE.match(line.strip())
        if sec:
            key = sec.group(1)
            mode = "re" if key == "正则" else "sub"
            enforced = key not in ("待确认", "待定")
            continue
        if line.lstrip().startswith("#") or line.lstrip().startswith(">"):
            continue
        parts = _COMMENT_SPLIT.split(line, maxsplit=1)
        term = parts[0].strip()
        reason = parts[1].strip() if len(parts) > 1 else ""
        if not term or not enforced:
            continue
        out.append((term, mode, reason))
    return out


class ManuscriptLexiconRule(AuditRule):
    name = "manuscript_lexicon"
    code_prefix = "LEXICON"

    def run(self, context: AuditContext) -> List[Finding]:
        findings: List[Finding] = []
        fi_lex = context.file_map.get(LEXICON_REL)
        if fi_lex is None:
            findings.append(Finding(
                severity=Severity.INFO, rule=self.name, code="LEXICON000",
                message=f"未建 {LEXICON_REL}，正文禁用词校验跳过",
                file=None,
                suggestion=f"参照 00_通用模板/01_写作规则/08_禁用词表说明.md 建立 {LEXICON_REL}",
                category="01_设定", locations=[LEXICON_REL],
            ))
            return findings

        terms = _parse_lexicon(fi_lex.content)
        if not terms:
            return findings

        compiled: List[Tuple[str, str, "re.Pattern[str]"]] = []
        for term, mode, reason in terms:
            try:
                pat = re.compile(term if mode == "re" else re.escape(term))
            except re.error:
                findings.append(Finding(
                    severity=Severity.WARNING, rule=self.name, code="LEXICON002",
                    message=f"禁用词表正则无法编译：{term}",
                    file=LEXICON_REL, category="01_设定",
                    suggestion="修正 ## 正则 区该行的正则语法", locations=[LEXICON_REL],
                ))
                continue
            compiled.append((term, reason, pat))

        # term -> [(rel_path, lineno, 命中文本)]
        hits: dict = {}
        for fi in context.files:
            if fi.data_domain != "10_正文":
                continue
            for idx, line in enumerate(fi.content.splitlines(), 1):
                for term, reason, pat in compiled:
                    if pat.search(line):
                        hits.setdefault((term, reason), []).append(
                            f"{fi.relative_path}:第{idx}行")

        for (term, reason), locs in sorted(hits.items()):
            why = f"（{reason}）" if reason else ""
            findings.append(Finding(
                severity=Severity.WARNING, rule=self.name, code="LEXICON001",
                message=f"正文命中禁用词「{term}」{why}，{len(locs)} 处",
                file=None,
                suggestion=f"改用符合本世界设定的表达替换「{term}」；确认是穿帮后更新 {LEXICON_REL}",
                category="10_正文", locations=locs,
            ))
        return findings
