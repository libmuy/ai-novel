"""
数据库章节边界校验 (db_chapter.py)

`00_系统架构规范.md` §七【数据库章节边界】+ §二·A：某事在第几章的**唯一权威**是
该卷 `规划_卷NN.md`【章节节拍表】对应行摘要的 `@引用`。`02_数据库/` 下的对象卡
正文里出现「第N章」「卷N第M章」「chNN」「场景N」这类**小说结构编号**，就是把已有
权威复制成第二份——会漂移（`04_资源_法宝.md` 的「首次出场章节」已经细到「场景3」），
还会污染写作提示词（卡片被内联进正文提示词时，把「第24章损毁」这类跨章剧透一起带进
模型上下文）。

- DBCHAP001 (warning)：`02_数据库/` 卡片正文命中小说章节/场景编号。
  首轮定 warning——存量卡片（如 苍玄 `04_资源` / `07_人物`）迁移期间不阻断 `check.sh`；
  存量清零后可升 error 并加回归测试。

**只匹配小说结构编号**（`第N章` / `第一章` / `卷N第` / `卷N第M章` / `chNN` / `场景N` /
`章NNNN`）。§七允许的世界内时间（「上古年间」「万年前」「创建年代」）与 in-world 书籍
章节本就不匹配，不误报。

**逐行豁免**：确需保留时在该行任意位置加 `<!-- DBCHAP-ok: 理由 -->`。
"""
import re
from typing import List
from ..models import Finding, Severity
from ..engine import AuditRule
from ..context import AuditContext

# 章节级编号——本项目对「第N章」类编号唯一的判定口径，供本规则与 `redline.py`
# （红线包 REDLINE004：卷级视图，允许"卷1"/"本卷"但不允许逐章编号）共用，
# 不各自维护一份、避免判定标准悄悄分叉（`00_系统架构规范.md` §二·A）。
CHAPTER_LEVEL_PATTERNS = [
    re.compile(r"第\s*\d{1,4}\s*章"),
    re.compile(r"第[一二三四五六七八九十百零两]{1,5}章"),
    re.compile(r"(?<![A-Za-z])[cC][hH]\s*\d{1,4}(?![A-Za-z0-9])"),
    re.compile(r"(?<!\d)章\s*\d{4}(?!\d)"),
]

# 本规则专用：数据库卡片不该有任何粒度的小说结构编号，额外拦卷级/场景级
# （红线包是卷级视图，"卷1"/"本卷"合法，不能共用这两条，故不并入上面的共享集合）。
_VOLUME_SCENE_PATTERNS = [
    re.compile(r"卷\s*\d{1,4}\s*第"),
    re.compile(r"场景\s*[\d一二三四五六七八九十]{1,3}"),
]

_PATTERNS = CHAPTER_LEVEL_PATTERNS + _VOLUME_SCENE_PATTERNS

_WAIVER = re.compile(r"<!--\s*DBCHAP-ok:")


class DbChapterRule(AuditRule):
    name = "db_chapter"
    code_prefix = "DBCHAP"

    def run(self, context: AuditContext) -> List[Finding]:
        findings: List[Finding] = []
        hits: dict = {}  # rel_path -> list[(lineno, 命中片段)]

        for fi in context.files:
            if fi.data_domain != "02_数据库":
                continue
            if fi.file_type != "markdown":
                continue
            for idx, line in enumerate(fi.content.splitlines(), 1):
                if _WAIVER.search(line):
                    continue
                for pat in _PATTERNS:
                    m = pat.search(line)
                    if m:
                        hits.setdefault(fi.relative_path, []).append((idx, m.group(0)))
                        break

        for rel_path, occ in sorted(hits.items()):
            locs = [f"{rel_path}:第{ln}行（{frag}）" for ln, frag in occ]
            findings.append(Finding(
                severity=Severity.WARNING,
                rule=self.name,
                code="DBCHAP001",
                message=(
                    f"数据库卡片正文出现小说章节/场景编号，{len(occ)} 处"
                    f"（{'、'.join(sorted({f for _, f in occ}))}）"
                ),
                file=rel_path,
                line=occ[0][0],
                suggestion=(
                    "把「第几章发生」挪回所属卷 `规划_卷NN.md`【章节节拍表】对应行摘要的 "
                    "`@引用`；卡片只留长期定位（关联伏笔/势力/道义用 `@伏笔.`/`@势力.`/`@道义.`）。"
                    "见 `00_系统架构规范.md` §七、§二·A。确需保留加 `<!-- DBCHAP-ok: 理由 -->`"
                ),
                category="02_数据库",
                locations=locs,
            ))

        return findings
