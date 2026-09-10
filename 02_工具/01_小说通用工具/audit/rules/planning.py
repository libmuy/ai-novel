"""
规划校验规则 (planning.py)
校验 03_规划/ 目录下对数据库/伏笔/人物/地理/正文章节的引用
"""
import re
from typing import List
from ..models import Finding, Severity
from ..engine import AuditRule
from ..context import AuditContext
from ..resolver.reference_resolver import ReferenceResolver

CHAPTER_REF_PATTERN = re.compile(r"第(?P<chap>\d+)章(?:\s*→\s*(?P<path>10_正文/[^\s\n\r]+))?")

# 单章细纲文件名：规划_卷NN_章NNNN.md
OUTLINE_FILE_PATTERN = re.compile(r"规划_卷\d+_章\d+\.md$")
# 细纲场景段的 canonical 结构：`## 【场景列表】` 下每场一个 `### 第N场景` 标题
# （见 规划_卷01_章0004.md）。`build_prompt.py --task 正文` 的 `extract.scene_blocks`
# 只认这个形态——认不到就取不到逐场字数预算，正文提示词会留 `>>>` 空洞。
SCENE_LIST_HEADING = re.compile(r"^##\s*【?场景列表】?\s*$", re.M)
SCENE_HEADING = re.compile(r"^#{3,4}\s+第\s*\d+\s*场景", re.M)


class PlanningRule(AuditRule):
    name = "planning"
    code_prefix = "PLAN"

    def run(self, context: AuditContext) -> List[Finding]:
        findings: List[Finding] = []
        plan_files = [fi for fi in context.files if fi.data_domain == "03_规划"]
        if not plan_files:
            return findings

        resolver = ReferenceResolver(context)

        for fi in plan_files:
            lines = fi.content.splitlines()
            for idx, line in enumerate(lines, 1):
                # 检查 规划 -> 正文 章节链接
                for m in CHAPTER_REF_PATTERN.finditer(line):
                    chap_num = m.group("chap")
                    chap_path = m.group("path")
                    if chap_path:
                        if not context.file_exists(chap_path):
                            findings.append(Finding(
                                severity=Severity.ERROR,
                                rule=self.name,
                                code="PLAN001",
                                message=f"规划中记录的章节路径「{chap_path}」不存在",
                                file=fi.relative_path,
                                line=idx,
                                source=m.group(0),
                                target=chap_path,
                                suggestion=f"创建对应正文章节 {chap_path} 或更新规划文件中的相对路径",
                                category="03_规划",
                                locations=[f"{fi.relative_path}:第{idx}行"]
                            ))

            # PLAN023：单章细纲场景段结构必须是 `### 第N场景`（canonical，见 ch4）
            if OUTLINE_FILE_PATTERN.search(fi.relative_path):
                mhead = SCENE_LIST_HEADING.search(fi.content)
                if mhead:
                    tail = fi.content[mhead.end():]
                    nxt = re.search(r"^##\s", tail, re.M)
                    body = tail[:nxt.start()] if nxt else tail
                    if not SCENE_HEADING.search(body):
                        findings.append(Finding(
                            severity=Severity.ERROR,
                            rule=self.name,
                            code="PLAN023",
                            message="单章细纲【场景列表】下没有 `### 第N场景` 标题——"
                                    "`build_prompt.py --task 正文` 取不到逐场字数预算，正文提示词会留空洞",
                            file=fi.relative_path,
                            line=fi.content[:mhead.start()].count("\n") + 1,
                            source="## 【场景列表】",
                            target="### 第N场景",
                            suggestion="把场景段改成 canonical 结构：每场一个 `### 第N场景` 标题 + "
                                       "`| 字段 | 内容 |` 表（场景序号/地点/字数/功能/内容简述/出场角色/"
                                       "涉及资源/涉及伏笔/场景钩子）+ `**场景要点**`，见 "
                                       "`03_规划/01_第01部/01_卷01/规划_卷01_章0004.md`",
                            category="03_规划",
                            locations=[f"{fi.relative_path}:第{fi.content[:mhead.start()].count(chr(10)) + 1}行"]
                        ))

            # 检查对象引用
            file_refs = resolver.extract_references(fi)
            for ref in file_refs:
                if ref.reference_type == "object" and ref.status == "UNRESOLVED":
                    findings.append(Finding(
                        severity=Severity.ERROR,
                        rule=self.name,
                        code="PLAN002",
                        message=f"规划文档中引用的 [{ref.entity_type}]「{ref.entity_name}」不存在",
                        file=fi.relative_path,
                        line=ref.source_line,
                        column=ref.source_column,
                        source=ref.raw_text,
                        target=ref.target,
                        suggestion=f"确认 {ref.entity_name} 的拼写，或在 02_数据库 中创建对应卡片",
                        category=ref.entity_type,
                        locations=[f"{fi.relative_path}:第{ref.source_line}行"]
                    ))

        return findings
