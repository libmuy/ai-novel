"""
枚举值域规则 (enum_domain.py) —— W6.3

`运算-枚举` 字段此前只校验「字段名合法」「类型对得上」，**不校验取值**。
于是云端写回一个 `炼气1层`／`敌视`／`已损毁`，本地照单全收——它不会报错，
只会安静地在状态树里长出一个和 `炼气一层`／`敌对`／`损毁` 并列的影子值，
之后所有比较、级联、快照都按两个不同的值处理。

`03_字段词表.md` 第三列本来就叫「合法枚举值 / 示例说明」，闭集字段的值域已经写在那儿；
W6.3 只是给闭集加 `（闭集）` 记号，让它从「给人看的示例」变成「机器可校验的值域」。

- STATE026 error   `运算-枚举` 取值不在该字段的闭集值域内
- STATE027 warning 值域表根本没加载上（软链断了等）——本轮什么都没校验，必须明说

**值域按对象类别分别生效**：`对象终态` 在角色类是 `活跃/死亡/退场/暂离`，
在关系类是 `活跃/终结`——所以解析要跟着 `### N. X类对象` 分节走，不能拍平成
「字段名 → 值域」一张表。

未标 `（闭集）` 的枚举字段视为开放值域（`境界` 随修炼体系扩展、`持有者`/`隶属`
是 `@引用` 形态），不做值校验——宁可漏报，不误报。
"""
import re
from typing import Dict, List, Set, Tuple

from ..models import Finding, Severity
from ..engine import AuditRule
from ..context import AuditContext

VOCAB_REL = "00_通用模板/03_字段词表.md"
CLOSED_MARK = "（闭集）"
ENUM_TYPE = "运算-枚举"

STATE_TREES = ("05_工作区/02_状态/00_基线状态", "05_工作区/02_状态/01_最新状态")
CHANGELOG_NAME = "01_状态履历.md"

# `### 1. 角色类对象 (`角色.[姓名]`)` → 类别前缀「角色」
_SECTION_RE = re.compile(r"^#{2,4}\s*\d*\.?\s*(\S+?)类对象")
_BACKTICK_RE = re.compile(r"`([^`]+)`")
# 值里出现这些形态说明是「模式」而非「定值」，整条值域作废（避免把示例当合法值）
_PATTERN_HINT = ("@", "...", "…", "等）", "等 ")


def parse_closed_domains(text: str) -> Dict[Tuple[str, str], Set[str]]:
    """`03_字段词表.md` → {(对象类别, 字段名): 允许值集合}，只收标了 `（闭集）` 的行。"""
    out: Dict[Tuple[str, str], Set[str]] = {}
    category = None
    for line in text.splitlines():
        m = _SECTION_RE.match(line.strip())
        if m:
            category = m.group(1)
            continue
        st = line.strip()
        if not st.startswith("|") or category is None:
            continue
        cells = [c.strip() for c in st.split("|")[1:-1]]
        if len(cells) < 3:
            continue
        field = cells[0].replace("**", "").strip()
        if cells[1].strip() != ENUM_TYPE or CLOSED_MARK not in cells[2]:
            continue
        domain = _parse_domain(cells[2])
        if domain:
            out[(category, field)] = domain
    return out


def _parse_domain(cell: str) -> Set[str]:
    """第三列 → 允许值集合。反引号列举优先，否则按 `/` 切。

    只要单元格里出现开放性记号（`...` `…` `等` `@引用` 形态），**整格作废返回空集**——
    不是只丢掉那一个词。「A / B / ...」的语义是「至少还有别的」，把 {A, B} 当成闭集
    会把任何合法的第三个值判成违规，比不检查更糟。
    """
    cell = cell.replace(CLOSED_MARK, "")
    if any(h in cell for h in _PATTERN_HINT):
        return set()
    ticked = [t.strip() for t in _BACKTICK_RE.findall(cell) if t.strip()]
    if len(ticked) >= 2:
        return set(ticked)
    # 无反引号的写法（如 `赤贫 / 温饱 / 小康 / 富足 / 巨富`）：去掉尾部括号注释再切
    bare = re.sub(r"[（(][^）)]*[）)]", "", cell)
    parts = [p.strip().replace("**", "") for p in bare.split("/") if p.strip()]
    return set(parts) if len(parts) >= 2 else set()


def find_unparsable_closed_rows(text: str) -> List[str]:
    """标了 `（闭集）` 却解析不出值域的行——多半是记号标错了地方。

    不报出来的话，这种行会安静地退化成「不校验」，和「校验通过」在报告里没有区别。
    """
    out, category = [], None
    for line in text.splitlines():
        m = _SECTION_RE.match(line.strip())
        if m:
            category = m.group(1)
            continue
        st = line.strip()
        if not st.startswith("|") or category is None:
            continue
        cells = [c.strip() for c in st.split("|")[1:-1]]
        if len(cells) < 3 or cells[1].strip() != ENUM_TYPE or CLOSED_MARK not in cells[2]:
            continue
        if not _parse_domain(cells[2]):
            field = cells[0].replace("**", "").strip()
            out.append(f"{category}.{field}：标了 {CLOSED_MARK} 但值域解析为空——"
                       f"该格含开放性记号（... / 等 / @引用）或列举不足 2 项")
    return out


def _iter_state_rows(context: AuditContext):
    """(相对路径, 行号, 对象ID, 字段, 类型, 值) —— 基线/最新状态树 + 全部履历。"""
    for fi in context.files:
        rel = fi.relative_path
        is_tree = any(rel.startswith(t + "/") for t in STATE_TREES)
        is_changelog = rel.startswith("05_工作区/") and rel.endswith(CHANGELOG_NAME)
        if not (is_tree or is_changelog):
            continue
        if rel.rsplit("/", 1)[-1].startswith("00_"):
            continue
        for i, line in enumerate(fi.content.splitlines(), 1):
            st = line.strip()
            if not st.startswith("|"):
                continue
            parts = [p.strip() for p in st.split("|")[1:-1]]
            if len(parts) < 4:
                continue
            oid, field, ftype, val = parts[0], parts[1], parts[2], parts[3]
            if oid in ("对象ID", "对象 ID", "ID") or oid.startswith((":-", "---")):
                continue
            yield rel, i, oid, field, ftype, val


class EnumDomainRule(AuditRule):
    name = "enum_domain"
    code_prefix = "STATE"

    def run(self, context: AuditContext) -> List[Finding]:
        fi = context.file_map.get(VOCAB_REL)
        text = fi.content if fi else ""
        if not text:
            # 词表在小说目录外（经 00_通用模板 软链），扫不到就按仓库根找
            for base in (context.novel_dir, context.novel_dir.parent.parent):
                p = base / VOCAB_REL
                if p.exists():
                    text = p.read_text(encoding="utf-8", errors="ignore")
                    break
        domains = parse_closed_domains(text)
        if not domains:
            # 取不到值域就**明说**，不要静默返回空。
            # 「查不到东西」和「查过了没问题」在报告里长得一样，是本仓反复踩过的坑：
            # 规则安静地什么都没做，看上去却像通过了。
            return [Finding(
                severity=Severity.WARNING, rule=self.name, code="STATE027",
                message=("未能从 `00_通用模板/03_字段词表.md` 解析出任何闭集值域，"
                         "本轮**没有校验**任何枚举取值"),
                file=VOCAB_REL,
                suggestion=("确认小说目录下的 `00_通用模板` 软链没断（`ls -l <小说目录>/00_通用模板`）；"
                            "若词表里确实一个 `（闭集）` 记号都没有，那是预期的，"
                            "补记号见词表第二节说明"),
                category="05_工作区", locations=[VOCAB_REL])]

        findings: List[Finding] = []
        unparsable = find_unparsable_closed_rows(text)
        if unparsable:
            findings.append(Finding(
                severity=Severity.WARNING, rule=self.name, code="STATE027",
                message=f"{len(unparsable)} 行标了 {CLOSED_MARK} 却解析不出值域，这些字段**未被校验**",
                file=VOCAB_REL,
                suggestion=f"要么把该格改成纯列举（≥2 项、不含 ... / 等 / @引用），"
                           f"要么去掉 {CLOSED_MARK} 记号明确它是开放值域",
                category="05_工作区", locations=unparsable))

        bad: List[str] = []
        for rel, ln, oid, field, ftype, val in _iter_state_rows(context):
            if ftype != ENUM_TYPE or "." not in oid:
                continue
            category = oid.split(".", 1)[0]
            allowed = domains.get((category, field))
            if not allowed:
                continue
            v = val.strip()
            if not v or v in allowed:
                continue
            near = _closest(v, allowed)
            hint = f"；最接近的合法值是「{near}」" if near else ""
            bad.append(f"{rel}:{ln} {oid}「{field}」= 「{v}」，"
                       f"不在值域 {{{' / '.join(sorted(allowed))}}} 内{hint}")

        if not bad:
            return findings
        return findings + [Finding(
            severity=Severity.ERROR, rule=self.name, code="STATE026",
            message=f"{len(bad)} 处 `运算-枚举` 取值不在字段的闭集值域内",
            file=None,
            suggestion=("改成值域内的合法值；确需扩展值域，去 `00_通用模板/03_字段词表.md` "
                        "对应对象类别的字段行里加值（那里是值域的唯一权威）。"
                        "枚举值写歪不会报错、只会在状态树里长出一个影子值，"
                        "之后所有比较与级联都按两个不同的值处理"),
            category="05_工作区", locations=bad)]


def _closest(value: str, allowed: Set[str]) -> str:
    """给个最接近的合法值当提示——枚举写歪多半是同义词或错别字。"""
    import difflib
    hit = difflib.get_close_matches(value, sorted(allowed), n=1, cutoff=0.4)
    return hit[0] if hit else ""
