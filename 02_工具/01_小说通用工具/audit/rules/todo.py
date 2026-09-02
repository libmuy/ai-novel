"""
TODO 双向一致性规则 (todo.py)
检查 TODO 描述与实际实体卡片的存在性、TODO 状态漂移、未登记全局注册表等
"""
import re
from collections import defaultdict
from typing import List
from ..models import Finding, Severity
from ..engine import AuditRule
from ..context import AuditContext
from ..resolver.todo_resolver import TodoResolver, TODO_PATTERN, TODO_GLOBAL_PREFIXES, CATEGORY_KEYWORD_IN_PROGRESS

# 「待创建条目」区块起始行（标题或引导句）
_PENDING_SECTION_RE = re.compile(r"待创建条目")
# 一行里出现 TODO 占位符（类型不限，比 TODO_PATTERN 宽）
_TODO_INLINE_RE = re.compile(r"\[TODO-[^\]]+\]")
# 该行看起来是一条「待创建条目」登记（而非正文里的占位引用）
_PENDING_ENTRY_HINT_RE = re.compile(r"需求|提及位置|类型[:：]")
# 前向引用必填字段：预计引入卷 / 引入部
_INTRO_VOLUME_RE = re.compile(r"引入卷|引入部|预计引入")


class TodoRule(AuditRule):
    name = "todo"
    code_prefix = "TODO"

    def run(self, context: AuditContext) -> List[Finding]:
        findings: List[Finding] = []
        resolver = TodoResolver(context)
        todos, meta = resolver.resolve_todos()

        # 1. 全局注册表存在性
        if not meta["has_registry"]:
            findings.append(Finding(
                severity=Severity.WARNING,
                rule=self.name,
                code="TODO001",
                message="全局TODO注册表 02_数据库/00_TODO全局注册表.md 不存在",
                file="02_数据库/00_TODO全局注册表.md",
                suggestion="创建全局TODO注册表，定义所有TODO占位符的全局唯一ID",
                category=None,
                locations=["02_数据库/00_TODO全局注册表.md"]
            ))

        registry_ids = meta["registry_ids"]

        # 2. 检查 TODO 引用是否已在全局注册表登记
        orphans = []
        findings_placeholder = defaultdict(lambda: defaultdict(set))

        for fi in context.files:
            if fi.data_domain in ["01_设定", "02_数据库", "03_规划"]:
                if fi.relative_path.endswith("00_TODO全局注册表.md"):
                    continue
                text = fi.content
                for m in TODO_PATTERN.finditer(text):
                    typ, todo_id = m.group(1), m.group(2)
                    if "序号" in todo_id or "xx" in todo_id.lower() or "示例" in todo_id:
                        continue
                    findings_placeholder[typ][fi.relative_path].add(todo_id)

                    global_match = re.match(r"^([A-Z]{2})-(\d+)$", todo_id)
                    if global_match:
                        prefix = global_match.group(1)
                        if prefix not in TODO_GLOBAL_PREFIXES or f"TODO-{prefix}-{global_match.group(2)}" not in registry_ids:
                            orphans.append((fi.relative_path, typ, todo_id))
                    else:
                        orphans.append((fi.relative_path, typ, todo_id))

        if meta["has_registry"]:
            if orphans:
                findings.append(Finding(
                    severity=Severity.WARNING,
                    rule=self.name,
                    code="TODO002",
                    message=f"发现 {len(orphans)} 处TODO引用未在全局注册表中登记",
                    file=None,
                    suggestion="在 00_TODO全局注册表.md 中为这些TODO条目创建对应的全局ID，或修正引用",
                    category=None,
                    locations=[loc for loc, _, _ in orphans]
                ))
            else:
                findings.append(Finding(
                    severity=Severity.INFO,
                    rule=self.name,
                    code="TODO003",
                    message=f"全局TODO注册表校验通过：{len(registry_ids)} 个全局ID，所有引用均已登记",
                    file="02_数据库/00_TODO全局注册表.md",
                    suggestion="无需操作",
                    category=None,
                    locations=[]
                ))

        # 3. 检查残留占位符 (Stale Placeholders)
        progress_status = meta["progress_status"]
        for typ, files in findings_placeholder.items():
            final_status = progress_status.get(typ, "")
            is_final = "定稿" in final_status
            total = sum(len(v) for v in files.values())
            source_category = CATEGORY_KEYWORD_IN_PROGRESS.get(typ, "").rstrip("/")
            findings.append(Finding(
                severity=Severity.WARNING if is_final else Severity.INFO,
                rule=self.name,
                code="TODO004",
                message=f"@{typ}.[TODO-*] 共 {total} 处，源分类状态「{final_status or '未知'}」，" + ("源数据已定稿仍有残留占位符，应回补" if is_final else "源数据尚未定稿，占位符暂属正常"),
                file=None,
                suggestion=(
                    f"逐条核对 {typ} 类占位符对应的真实条目（参考已定稿的 {source_category} 分类数据），"
                    f"将 @{typ}.[TODO-序号] 替换为真实 @引用，并在来源文件的【待创建条目】表中勾除该条目"
                    if is_final else "待源分类定稿后再回补，暂不处理"
                ),
                category=typ,
                locations=sorted(files.keys())
            ))

        # 3b. 规划层（任务 05~11 产出）前向引用 TODO 须带「预计引入卷」
        for fi in context.files:
            if fi.data_domain != "03_规划":
                continue
            if fi.relative_path.endswith("00_TODO全局注册表.md"):
                continue
            lines = fi.content.splitlines()
            in_section = False
            missing = []
            for idx, raw in enumerate(lines, start=1):
                if _PENDING_SECTION_RE.search(raw):
                    in_section = True
                    continue
                if not in_section:
                    continue
                stripped = raw.strip()
                # 空行不结束区块（区块内可能有空行）；遇到新的二级/三级标题才结束
                if stripped.startswith("#"):
                    in_section = False
                    continue
                if not _TODO_INLINE_RE.search(raw):
                    continue
                if not _PENDING_ENTRY_HINT_RE.search(raw):
                    continue
                if not _INTRO_VOLUME_RE.search(raw):
                    missing.append((idx, stripped))
            if missing:
                findings.append(Finding(
                    severity=Severity.WARNING,
                    rule=self.name,
                    code="TODO007",
                    message=(
                        f"{fi.relative_path}：规划层「待创建条目」有 {len(missing)} 条 TODO 缺「预计引入卷」字段；"
                        "任务 05~11 的 TODO 仅限跨弧前向对象/承重对象，须注明预计引入的部/卷"
                    ),
                    file=fi.relative_path,
                    line=missing[0][0],
                    suggestion=(
                        "为每条前向引用 TODO 补「预计引入卷：<部/卷编号>」；"
                        "若该对象实为本卷近场对象，应改为当场创建实名实体而非登记 TODO"
                    ),
                    category=None,
                    locations=[f"{fi.relative_path}:第{ln}行" for ln, _ in missing]
                ))

        # 4. 双向一致性检查：TODO 与实体卡片实际状态对比
        for todo in todos:
            target_name = todo.target_object
            if not target_name:
                continue
            cat = todo.category
            entity_exists = False
            if cat and cat in context.entities and target_name in context.entities[cat]:
                entity_exists = True

            # 情况 A / D: TODO 尚未关闭或 IN_PROGRESS, 但对象已存在
            if entity_exists and todo.status in ["TODO", "IN_PROGRESS"]:
                findings.append(Finding(
                    severity=Severity.WARNING,
                    rule=self.name,
                    code="TODO005",
                    message=f"TODO「{todo.todo_id}」（{todo.description}）尚未关闭（状态：{todo.status}），但对应实体对象已存在",
                    file=todo.source_file,
                    line=todo.source_line,
                    suggestion=f"将 TODO「{todo.todo_id}」的状态更新为 DONE，或更正占位符引用",
                    category=todo.category,
                    locations=[todo.source_file] if todo.source_file else []
                ))

            # 情况 B: TODO 标记完成 (DONE), 但对象不存在
            if not entity_exists and todo.status == "DONE":
                findings.append(Finding(
                    severity=Severity.ERROR,
                    rule=self.name,
                    code="TODO006",
                    message=f"TODO「{todo.todo_id}」（{todo.description}）已标记为 DONE，但对应实体数据对象在仓库中不存在",
                    file=todo.source_file,
                    line=todo.source_line,
                    suggestion=f"按规范在 02_数据库 中补全「{todo.description}」卡片，或将 TODO 状态更正为 TODO / IN_PROGRESS",
                    category=todo.category,
                    locations=[todo.source_file] if todo.source_file else []
                ))

        return findings
