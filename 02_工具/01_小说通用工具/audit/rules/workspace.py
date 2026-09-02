"""
工作区结构与编号审计规则 (workspace.py)
检查工作区递归架构、编号规则、禁止旧目录以及提示词与模型输出配对
"""
import re
from pathlib import Path
from typing import List
from ..models import Finding, Severity
from ..engine import AuditRule
from ..context import AuditContext

BANNED_NAMES = [
    "00_全局",
    "历史回填",
    "本级工作资料",
    "本部级工作资料",
    "本卷级工作资料",
    "本章工作资料",
]


class WorkspaceRule(AuditRule):
    name = "workspace"
    code_prefix = "WS"

    def run(self, context: AuditContext) -> List[Finding]:
        findings: List[Finding] = []
        novel_dir = context.novel_dir
        workspace_dir = novel_dir / "05_工作区"

        if not workspace_dir.exists():
            return findings

        # 1. 检查是否存在被禁止的旧版抽象目录/文件
        for path in workspace_dir.rglob("*"):
            rel_parts = path.relative_to(workspace_dir).parts
            for banned in BANNED_NAMES:
                if banned in rel_parts:
                    findings.append(Finding(
                        severity=Severity.ERROR,
                        rule=self.name,
                        code="WS001",
                        message=f"工作区存在废弃或禁止的旧架构目录/文件: {banned}",
                        file=str(path),
                        suggestion=f"将 {banned} 重构迁移至标准的 [00_提示词, 01_模型输出, 02_状态] 层级中，并清理旧路径",
                        category="05_工作区",
                        locations=[str(path.relative_to(novel_dir))]
                    ))

        # 2. 检查子目录编号合规性（从00开始，无重复，连续不跳号，两位数字前缀）
        self._check_dir_numbering(workspace_dir, novel_dir, findings)

        # 3. 检查 00_提示词 与 01_模型输出 的同名文件配对情况
        self._check_prompt_output_pairing(workspace_dir, novel_dir, findings)

        return findings

    def _check_dir_numbering(self, current_dir: Path, novel_dir: Path, findings: List[Finding]):
        # 状态树目录（00_基线状态、01_最新状态、00_基线候选）下的类目子目录使用固定领域分类（01_角色/02_物品/...），不适用从 00_ 开始连续无跳号规则
        if current_dir.name in ("00_基线状态", "01_最新状态", "00_基线候选"):
            return

        subdirs = [d for d in current_dir.iterdir() if d.is_dir() and not d.name.startswith(".")]
        if not subdirs:
            return

        # 遍历所有直系子目录
        pattern = re.compile(r"^(\d{2})_(.+)$")
        numbered_subdirs = []
        unformatted = []

        for d in subdirs:
            m = pattern.match(d.name)
            if m:
                num = int(m.group(1))
                numbered_subdirs.append((num, d.name, d))
            else:
                unformatted.append(d)

        rel_parent = current_dir.relative_to(novel_dir)

        if unformatted:
            for d in unformatted:
                findings.append(Finding(
                    severity=Severity.ERROR,
                    rule=self.name,
                    code="WS002",
                    message=f"目录 {d.name} 未遵循两位数字编号前缀规则 (如 00_xxxx)",
                    file=str(d),
                    suggestion="对该目录增加两位数字编号前缀，例如 00_...",
                    category="05_工作区",
                    locations=[str(d.relative_to(novel_dir))]
                ))

        if numbered_subdirs:
            numbered_subdirs.sort(key=lambda x: x[0])
            nums = [x[0] for x in numbered_subdirs]

            # 检查编号重复
            seen = set()
            duplicates = set()
            for n in nums:
                if n in seen:
                    duplicates.add(n)
                seen.add(n)

            if duplicates:
                findings.append(Finding(
                    severity=Severity.ERROR,
                    rule=self.name,
                    code="WS003",
                    message=f"目录 {rel_parent} 下的直接子目录存在重复编号: {sorted(list(duplicates))}",
                    file=str(current_dir),
                    suggestion="重新排序并重命名直系子目录，保证编号唯一",
                    category="05_工作区",
                    locations=[str(rel_parent)]
                ))

            # 检查从 00 开始且连续不跳号
            if nums and nums[0] != 0:
                findings.append(Finding(
                    severity=Severity.ERROR,
                    rule=self.name,
                    code="WS004",
                    message=f"目录 {rel_parent} 下的直接子目录编号未从 00 开始 (起始为 {nums[0]:02d})",
                    file=str(current_dir),
                    suggestion="调整编号，使首个子目录从 00_ 开始",
                    category="05_工作区",
                    locations=[str(rel_parent)]
                ))

            for i in range(len(nums) - 1):
                if nums[i+1] != nums[i] + 1 and nums[i+1] != nums[i]:
                    findings.append(Finding(
                        severity=Severity.ERROR,
                        rule=self.name,
                        code="WS005",
                        message=f"目录 {rel_parent} 下的直接子目录编号存在跳号: {nums[i]:02d} -> {nums[i+1]:02d}",
                        file=str(current_dir),
                        suggestion="重新编排编号，使其连续不留空号",
                        category="05_工作区",
                        locations=[str(rel_parent)]
                    ))

        # 递归检查子目录
        for d in subdirs:
            self._check_dir_numbering(d, novel_dir, findings)

    def _check_prompt_output_pairing(self, workspace_dir: Path, novel_dir: Path, findings: List[Finding]):
        # 每个 00_提示词/<x>.md 需有配对的 01_模型输出/<x>.md（含冷读循环的 <原名>_修订N.md，
        # 见技能 04_单章质量验收.md / AGENTS.md §七）。反向（输出无对应提示词）不检查——
        # 历史归档稿如 <原名>_v1_旧提示词_<日期>.md 是允许的孤儿输出。
        # 扫描所有 00_提示词 目录
        for prompt_dir in workspace_dir.rglob("00_提示词"):
            if not prompt_dir.is_dir():
                continue
            parent = prompt_dir.parent
            output_dir = parent / "01_模型输出"

            # 遍历 prompt_dir 下的所有 markdown 文件
            for prompt_file in prompt_dir.rglob("*.md"):
                rel_path = prompt_file.relative_to(prompt_dir)
                matching_output = output_dir / rel_path
                if not matching_output.exists():
                    findings.append(Finding(
                        severity=Severity.INFO,
                        rule=self.name,
                        code="WS006",
                        message=f"提示词文件 {prompt_file.name} 缺少对应的模型输出文件",
                        file=str(prompt_file),
                        suggestion=f"请确认模型运行后是否在 {output_dir / rel_path} 产出了对应输出",
                        category="05_工作区",
                        locations=[str(prompt_file.relative_to(novel_dir))]
                    ))
