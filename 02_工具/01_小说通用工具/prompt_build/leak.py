# -*- coding: utf-8 -*-
"""
仓库内部标识泄漏自检 (leak.py)

依据 `00_通用模板/04_提示词/00_云端提示词生成器.md`「示例去污染规则」第 6 条
（ch0002 修订1 教训，2026-09-04 归口）：云端模型会把**提示词字面**照抄进正文——
ch0002 修订1 出现「**章0001**买的止咳散只剩今日一顿」，源头是修改项里写了
`章0001 买的止咳散`。

因此凡出现在**会被转写成正文**的段落里，一律禁止：章节 ID、卷/部编号、
文件名与仓库路径、`场景N`、`细纲`/`红线包`/`主角档案` 等文件代称，
以及尚未在正文中出现的 `FH-`/`DY-`/`BT-`/`RES-` 编号。

例外（原文）：【你的角色】【输出格式】【输出后自检】这类**不会被转写成正文**的
元指令段落可以正常使用文件名与编号。

扫描范围只限**本工具自己撰写**的段落。逐字内联的源文件区块（细纲全文、红线包、
系统指令…）是「已有数据」，模型读它们是为了照着写，不是照抄字面——那些区块里的
编号由源文件自己负责，工具不越俎代庖去改写它们。
"""
import re
from dataclasses import dataclass

# 元指令段落：不会被转写成正文，允许出现文件名与编号
EXEMPT_SECTIONS = ("【你的角色】", "【输出格式】", "【输出后自检】", "【验收自检】")

PATTERNS: list[tuple[str, re.Pattern, str]] = [
    ("章节ID", re.compile(r"章\s*\d{3,4}"), "改成故事内说法（「昨日」「上一回」「那天」）"),
    ("卷部编号", re.compile(r"(?:第\s*\d{1,2}\s*部|卷\s*\d{1,2}(?!\d))"), "删除，或改成故事内的时间/阶段说法"),
    ("场景编号", re.compile(r"场景\s*\d"), "直接描述那一场发生的事，不要点场景号"),
    ("仓库路径", re.compile(r"[0-9A-Za-z_一-鿿]+/[0-9A-Za-z_一-鿿/]*\.md"), "删除；正文写作不需要知道文件在哪"),
    ("文件代称", re.compile(r"(?<![「【])(细纲|红线包|主角档案|人物卡|节拍表|伏笔册|禁用词表)"), "改成直接陈述那条约束本身"),
    ("登记编号", re.compile(r"\b(?:FH|DY|BT|RES|WR|BP)-[A-Z0-9\-]+"), "改成故事内说法；编号只留在【输出格式】的登记清单里"),
]


@dataclass
class Leak:
    section: str
    line_no: int
    kind: str
    hit: str
    line: str
    fix: str

    def render(self) -> str:
        return (f"  [{self.kind}] {self.section} 第 {self.line_no} 行：「{self.hit}」\n"
                f"      {self.line.strip()[:78]}\n"
                f"      → {self.fix}")


def scan(section_title: str, body: str) -> list[Leak]:
    """扫描一个由本工具撰写的段落。元指令段落直接跳过。"""
    if any(x in section_title for x in EXEMPT_SECTIONS):
        return []
    out: list[Leak] = []
    for i, line in enumerate(body.splitlines(), 1):
        s = line.strip()
        if not s or s.startswith(("<!--", ">>>")):
            continue
        for kind, pat, fix in PATTERNS:
            for m in pat.finditer(line):
                out.append(Leak(section_title, i, kind, m.group(0), line, fix))
    return out


def render_report(leaks: list[Leak]) -> str:
    if not leaks:
        return "内部标识泄漏自检：通过（本工具撰写的叙事指令段落中无仓库内部标识）。"
    by_kind: dict[str, int] = {}
    for lk in leaks:
        by_kind[lk.kind] = by_kind.get(lk.kind, 0) + 1
    head = "、".join(f"{k} {v} 处" for k, v in sorted(by_kind.items()))
    lines = [f"内部标识泄漏自检：发现 {len(leaks)} 处（{head}）",
             "依据「示例去污染规则」第 6 条——云端模型会把提示词字面照抄进正文。", ""]
    lines += [lk.render() for lk in leaks]
    return "\n".join(lines)


# 拼装时必须写进【输出格式】的收尾约束（规则原文要求的那一句）
OUTPUT_FORMAT_GUARD = (
    "- 上述文件名、章节号、场次号、编号（`FH-`/`DY-`/`BT-`/`RES-`/`WR-`）仅用于说明任务与登记产出，"
    "**禁止出现在正文中**；正文里一律改用故事内的说法"
    "（不写「上一章买的药」，写「昨日换回的那半包」）。"
)

# 写否定式约束时必须附带的一句（同源失效模式：把约束条文抄成正文旁白）
NEGATIVE_CONSTRAINT_GUARD = (
    "> **以上否定式约束是写作约束，不是要你在正文里声明它**——靠「不写」来实现。"
    "不要写出「某物全程没有任何反应」这类把约束条文当旁白的句子。"
)
