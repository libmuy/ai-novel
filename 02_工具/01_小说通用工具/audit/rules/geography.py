"""
地理层级规则 (geography.py)
校验地理区域父子双向链接闭合
"""
import re
from typing import List
from ..models import Finding, Severity
from ..engine import AuditRule
from ..context import AuditContext


class GeographyRule(AuditRule):
    name = "geography"
    code_prefix = "GEO"

    def run(self, context: AuditContext) -> List[Finding]:
        findings: List[Finding] = []
        novel_dir = context.novel_dir
        geo_dir = novel_dir / "02_数据库" / "02_地理区域"
        if not geo_dir.exists():
            return findings

        geo_files = {}
        for f in geo_dir.glob("*.md"):
            geo_files[f.name] = f

        world_file = geo_dir / "02_地理区域_苍玄界.md"
        if world_file.exists():
            world_fi = context.file_map.get("02_数据库/02_地理区域/02_地理区域_苍玄界.md")
            world_text = world_fi.content if world_fi else world_file.read_text(encoding="utf-8", errors="ignore")
            prefix = "02_地理区域_苍玄界_"

            actual_regions = set()
            for fname in geo_files:
                if fname.startswith(prefix) and fname != world_file.name:
                    remainder = fname[len(prefix):].replace(".md", "")
                    if "_" not in remainder:
                        actual_regions.add(fname)

            missing_regions = []
            for region_fname in sorted(actual_regions):
                region_name_raw = region_fname[len(prefix):].replace(".md", "")
                if region_name_raw not in world_text:
                    found = False
                    for part in region_name_raw.split("_"):
                        if len(part) >= 2 and part in world_text:
                            found = True
                            break
                    if not found:
                        missing_regions.append(region_fname)

            if missing_regions:
                findings.append(Finding(
                    severity=Severity.WARNING,
                    rule=self.name,
                    code="GEO001",
                    message="区域文件存在但其名称未在世界总索引中出现",
                    file="02_数据库/02_地理区域/02_地理区域_苍玄界.md",
                    suggestion=f"在 {world_file.name} 中补充这些区域的描述条目",
                    category="02_地理区域",
                    locations=[f"02_地理区域/{f}" for f in sorted(missing_regions)]
                ))

            for region_fname in sorted(actual_regions):
                region_file = geo_files.get(region_fname)
                if not region_file:
                    continue
                region_rel = f"02_数据库/02_地理区域/{region_fname}"
                region_fi = context.file_map.get(region_rel)
                region_text = region_fi.content if region_fi else region_file.read_text(encoding="utf-8", errors="ignore")
                region_prefix = region_fname.replace(".md", "") + "_"

                actual_locations = set()
                for fname in geo_files:
                    if fname.startswith(region_prefix) and fname != region_fname:
                        actual_locations.add(fname)

                missing_locations = []
                for loc_fname in sorted(actual_locations):
                    loc_name_raw = loc_fname[len(region_prefix):].replace(".md", "")
                    if loc_name_raw not in region_text:
                        found = False
                        for part in loc_name_raw.split("_"):
                            if len(part) >= 2 and part in region_text:
                                found = True
                                break
                        if not found:
                            missing_locations.append(loc_fname)

                if missing_locations:
                    findings.append(Finding(
                        severity=Severity.WARNING,
                        rule=self.name,
                        code="GEO002",
                        message=f"地名文件存在但其名称未在区域文件 {region_fname} 中出现",
                        file=region_rel,
                        suggestion=f"在 {region_fname} 中补充这些地名的描述条目",
                        category="02_地理区域",
                        locations=[f"02_地理区域/{f}" for f in sorted(missing_locations)]
                    ))

        return findings
