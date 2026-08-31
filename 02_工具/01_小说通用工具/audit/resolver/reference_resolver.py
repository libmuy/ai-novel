"""
引用解析器 (reference_resolver.py)
解析对象引用 (@类型.名称), Markdown 文件链接, 相对路径链接, 并检测断链与逃逸
"""
import re
import urllib.parse
from pathlib import Path
from typing import List, Tuple, Optional
from ..models import Reference, FileInfo
from ..context import AuditContext

# @类型.名称 引用正则
# 支持 @人物.苏砚, @势力.黑石会, @地名.枯港矿城, @类型.[TODO-xxx], @类型.[苏砚]
OBJECT_REF_PATTERN = re.compile(
    r"@(?P<type>地名|势力|人物|类型|书籍|伏笔|区域|资源|修炼体系)\.(?:\[(?P<bracket_target>[^\]]+)\]|(?P<raw_target>[^\s\n\r\t，。！？；：、（）“”‘’«»〈〉《》`~!@#$%^&*()+=|\\{}:;\"'\''<>,/?]+))"
)

# Markdown 链接正则 [label](url)
MD_LINK_PATTERN = re.compile(
    r"\[(?P<label>[^\]]*)\]\((?P<url>[^\s\)]+)\)"
)

KNOWN_EXTERNAL_ENTITIES = {
    "人物": {"苏砚": "01_设定/00_主角档案.md"}
}


class ReferenceResolver:
    def __init__(self, context: AuditContext):
        self.context = context

    def resolve_all(self) -> List[Reference]:
        refs: List[Reference] = []
        for fi in self.context.files:
            if fi.file_type != "markdown":
                continue
            refs.extend(self.extract_references(fi))
        return refs

    def extract_references(self, fi: FileInfo) -> List[Reference]:
        refs: List[Reference] = []
        lines = fi.content.splitlines()

        for idx, line in enumerate(lines, 1):
            # 1. 解析对象引用 @类型.名称
            for m in OBJECT_REF_PATTERN.finditer(line):
                ref_type = m.group("type")
                bracket_target = m.group("bracket_target")
                raw_target = m.group("raw_target")
                target = bracket_target if bracket_target is not None else raw_target
                col = m.start() + 1

                raw_text = m.group(0)

                ref = Reference(
                    source_file=fi.relative_path,
                    source_line=idx,
                    source_column=col,
                    reference_type="object",
                    raw_text=raw_text,
                    target=target,
                    entity_type=ref_type,
                    entity_name=self._normalize_entity_name(target)
                )
                self.resolve_object_reference(ref)
                refs.append(ref)

            # 2. 解析 Markdown 链接 [label](target)
            for m in MD_LINK_PATTERN.finditer(line):
                url = m.group("url").strip()
                col = m.start() + 1
                raw_text = m.group(0)

                # 排除网络 URL, mailto, etc.
                if url.startswith(("http://", "https://", "ftp://", "mailto:", "data:")):
                    continue

                ref = Reference(
                    source_file=fi.relative_path,
                    source_line=idx,
                    source_column=col,
                    reference_type="markdown_link" if not url.startswith(("../", "./")) else "relative_path",
                    raw_text=raw_text,
                    target=url,
                )
                self.resolve_link_reference(ref, fi)
                refs.append(ref)

        return refs

    def _normalize_entity_name(self, name: str) -> str:
        if name.startswith("TODO-"):
            return name
        m = re.split(r"[，。！？；：、（）“”‘’\s\[\]]", name)
        return m[0] if m else name

    def resolve_object_reference(self, ref: Reference):
        if not ref.entity_name:
            ref.status = "UNRESOLVED"
            return

        # 如果是 TODO 占位符
        if ref.entity_name.startswith("TODO-"):
            ref.status = "TODO"
            return

        if ref.entity_type == "伏笔":
            ref.status = "RESOLVED"
            return

        # 校验 Known external entities
        if ref.entity_type in KNOWN_EXTERNAL_ENTITIES and ref.entity_name in KNOWN_EXTERNAL_ENTITIES[ref.entity_type]:
            ref.resolved_target = KNOWN_EXTERNAL_ENTITIES[ref.entity_type][ref.entity_name]
            ref.status = "RESOLVED"
            return

        # 查询 Context 实体索引
        matches = self.context.entities.get(ref.entity_type, {}).get(ref.entity_name, [])
        if not matches:
            # 兼容模糊匹配
            all_cat_matches = self.context.entities.get(ref.entity_type, {})
            found = []
            for k, rels in all_cat_matches.items():
                if ref.entity_name in k:
                    found.extend(rels)
            matches = list(set(found))

        if len(matches) == 1:
            ref.resolved_target = matches[0]
            ref.status = "RESOLVED"
        elif len(matches) > 1:
            ref.resolved_target = matches[0]
            ref.status = "AMBIGUOUS"
        else:
            ref.status = "UNRESOLVED"

    def resolve_link_reference(self, ref: Reference, source_fi: FileInfo):
        url = ref.target
        # 解离锚点 #anchor
        anchor = None
        if "#" in url:
            url_part, anchor = url.split("#", 1)
            ref.anchor = anchor
        else:
            url_part = url

        if not url_part:
            # 纯锚点链接 #heading
            ref.status = "RESOLVED"
            return

        # 解码 URL 转码 (如 %20)
        url_decoded = urllib.parse.unquote(url_part)

        source_path = source_fi.file_path
        target_path = (source_path.parent / url_decoded).resolve()

        # 路径逃逸校验
        try:
            target_rel = target_path.relative_to(self.context.novel_dir).as_posix()
        except ValueError:
            ref.status = "ESCAPED"
            return

        if target_path.exists():
            ref.resolved_target = target_rel
            ref.status = "RESOLVED"
        else:
            ref.resolved_target = target_rel
            ref.status = "UNRESOLVED"
