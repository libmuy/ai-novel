"""
全仓通用引用校验规则 (reference.py)
检查 @实体引用、Markdown 链接、相对路径链接
"""
import re
from typing import List
from ..models import Finding, Severity
from ..engine import AuditRule
from ..context import AuditContext
from ..resolver.reference_resolver import ReferenceResolver

# 名称类引用漏方括号——resolver 的 OBJECT_REF_PATTERN 不认 物品/关系，这里补一道纯文本扫描。
# `@物品.[矿钉]` / `@关系.[甲&乙]` 方括号强制；`@角色.苏砚` / `@主角` / `@伏笔.FH-xxx` 用 ID、不在此列。
_BARE_NAMED_REF = re.compile(r"@(物品|关系)\.(?!\[)([^\s\n\r\t，。！？；：、（）\[\]`|]+)")

CATEGORY_DIR_MAP = {
    "地名": "02_地理区域",
    "区域": "02_地理区域",
    "势力": "03_势力组织",
    "人物": "07_人物",
    "书籍": "06_书籍",
    "类型": "04_资源",
    "资源": "04_资源",
}

# 名称类引用必须用方括号（见 00_使用说明.md【引用语法】：方括号是强制的）。
# @主角 无名称、@道义/@伏笔 用 ID，均不在此列。
BRACKET_REQUIRED_TYPES = {"地名", "势力", "人物", "类型", "书籍", "区域", "资源"}


class ReferenceRule(AuditRule):
    name = "reference"
    code_prefix = "REF"

    def run(self, context: AuditContext) -> List[Finding]:
        findings: List[Finding] = []
        resolver = ReferenceResolver(context)
        refs = resolver.resolve_all()

        for ref in refs:
            # 区分 1. 对象引用
            if ref.reference_type == "object":
                if ref.status == "UNRESOLVED":
                    target_dir = CATEGORY_DIR_MAP.get(ref.entity_type, "02_数据库")
                    findings.append(Finding(
                        severity=Severity.ERROR,
                        rule=self.name,
                        code="REF001",
                        message=f"引用了真实名字「{ref.entity_name}」，但在 02_数据库/{target_dir}/ 下未找到匹配文件",
                        file=ref.source_file,
                        line=ref.source_line,
                        column=ref.source_column,
                        source=ref.raw_text,
                        target=ref.target,
                        suggestion="确认该名字是否有拼写错误；如果是应该存在但尚未建卡的实体，改写为 @类型.[TODO-序号] 占位符并登记到全局注册表；如果是已废弃名称，删除或更正该处引用",
                        category=ref.entity_type,
                        locations=[f"{ref.source_file}:第{ref.source_line}行"]
                    ))
                elif ref.status == "AMBIGUOUS":
                    findings.append(Finding(
                        severity=Severity.WARNING,
                        rule=self.name,
                        code="REF002",
                        message=f"引用「{ref.entity_name}」在数据库中存在重名或歧义实体",
                        file=ref.source_file,
                        line=ref.source_line,
                        column=ref.source_column,
                        source=ref.raw_text,
                        target=ref.target,
                        suggestion="避免重名或在卡片文件名中加上更具体的修饰前缀",
                        category=ref.entity_type,
                        locations=[f"{ref.source_file}:第{ref.source_line}行"]
                    ))

                # 检查名称类引用是否漏了方括号（方括号是强制的）
                if ref.entity_type in BRACKET_REQUIRED_TYPES and ".[" not in ref.raw_text \
                        and not ref.target.startswith("TODO-"):
                    findings.append(Finding(
                        severity=Severity.ERROR,
                        rule=self.name,
                        code="REF003",
                        message=f"名称类引用 {ref.raw_text} 未加方括号；无分隔符时解析器会把后续正文吞进对象名",
                        file=ref.source_file,
                        line=ref.source_line,
                        column=ref.source_column,
                        source=ref.raw_text,
                        target=ref.target,
                        suggestion=f"改写为 @{ref.entity_type}.[{ref.entity_name}]",
                        category=ref.entity_type,
                        locations=[f"{ref.source_file}:第{ref.source_line}行"]
                    ))

            # 区分 2. Markdown 链接 / 相对路径
            elif ref.reference_type in ["markdown_link", "relative_path"]:
                if ref.status == "ESCAPED":
                    findings.append(Finding(
                        severity=Severity.ERROR,
                        rule=self.name,
                        code="REF004",
                        message=f"路径链接「{ref.target}」逃逸出小说根目录外",
                        file=ref.source_file,
                        line=ref.source_line,
                        column=ref.source_column,
                        source=ref.raw_text,
                        target=ref.target,
                        suggestion="修正相对路径，不要包含超出仓库根目录的 ../",
                        locations=[f"{ref.source_file}:第{ref.source_line}行"]
                    ))
                elif ref.status == "UNRESOLVED":
                    findings.append(Finding(
                        severity=Severity.ERROR,
                        rule=self.name,
                        code="REF005",
                        message=f"Markdown 链接的目标文件「{ref.target}」不存在",
                        file=ref.source_file,
                        line=ref.source_line,
                        column=ref.source_column,
                        source=ref.raw_text,
                        target=ref.target,
                        suggestion="确认目标文件路径是否拼写正确，或创建该文件",
                        locations=[f"{ref.source_file}:第{ref.source_line}行"]
                    ))

        # ── REF003 补扫：resolver 不解析 @物品./@关系.，这里纯文本查方括号 ──
        # 与 resolver 同口径——只扫权威数据子树，跳过 05_工作区/（生产过程归档）。
        for fi in context.files:
            if fi.file_type != "markdown" or fi.data_domain not in (
                    "03_规划", "01_设定", "02_数据库", "10_正文"):
                continue
            for idx, line in enumerate(fi.content.splitlines(), 1):
                for m in _BARE_NAMED_REF.finditer(line):
                    findings.append(Finding(
                        severity=Severity.ERROR,
                        rule=self.name,
                        code="REF003",
                        message=f"名称类引用 @{m.group(1)}.{m.group(2)} 未加方括号（`@{m.group(1)}.[…]` 强制）",
                        file=fi.relative_path,
                        line=idx,
                        source=m.group(0),
                        target=f"{m.group(1)}.{m.group(2)}",
                        suggestion=f"改写为 @{m.group(1)}.[{m.group(2)}]",
                        category=m.group(1),
                        locations=[f"{fi.relative_path}:第{idx}行"],
                    ))

        return findings
