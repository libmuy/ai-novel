#!/usr/bin/env python3
"""
ai-novel 仓库一致性审查脚本（Agent 可读版）v3.0

用法:
    python3 audit_consistency.py <小说目录路径> [--format json|text] [--rule RULE_NAME] [--severity error|warning|info] [--strict] [--auto-fix]

向下兼容入口，调用重构后的 audit 模块引擎。
"""
import argparse
import json
import sys
from pathlib import Path

# 确保能导入 audit 模块
sys.path.insert(0, str(Path(__file__).resolve().parent))

from audit import AuditEngine, AuditContext, AuditReporter
from audit.rules.filesystem import FilesystemRule
from audit.rules.index import IndexRule
from audit.rules.ids import IdRule
from audit.rules.geography import GeographyRule
from audit.rules.state import StateRule
from audit.rules.reference import ReferenceRule
from audit.rules.manuscript import ManuscriptRule
from audit.rules.planning import PlanningRule
from audit.rules.setting import SettingRule
from audit.rules.todo import TodoRule
from audit.rules.foreshadow import ForeshadowRule
from audit.rules.state_registry import StateRegistryRule
from audit.rules.relation import RelationRule


def load_field_vocab(novel_dir: Path):
    from audit.rules.state import _load_field_vocab
    from audit.context import AuditContext
    context = AuditContext(novel_dir)
    return _load_field_vocab(novel_dir, context)


def run_all_checks(novel_dir: Path) -> dict:
    engine = get_default_engine(novel_dir)
    context = AuditContext(novel_dir)
    findings = engine.run(context=context)
    reporter = AuditReporter(novel_dir, findings)
    return reporter.to_dict()


def get_default_engine(novel_dir: Path) -> AuditEngine:
    engine = AuditEngine(novel_dir)
    engine.register_rule(FilesystemRule())
    engine.register_rule(IndexRule())
    engine.register_rule(IdRule())
    engine.register_rule(GeographyRule())
    engine.register_rule(StateRule())
    engine.register_rule(ReferenceRule())
    engine.register_rule(ManuscriptRule())
    engine.register_rule(PlanningRule())
    engine.register_rule(SettingRule())
    engine.register_rule(TodoRule())
    engine.register_rule(ForeshadowRule())
    engine.register_rule(StateRegistryRule())
    engine.register_rule(RelationRule())
    return engine


def main():
    ap = argparse.ArgumentParser(description="ai-novel 仓库一致性审查 v3.0（模块化架构）")
    ap.add_argument("novel_dir", help="小说数据目录路径")
    ap.add_argument("--format", choices=["json", "text"], default="json", help="输出格式，默认 json，可选 text")
    ap.add_argument("--rule", help="仅运行指定的 Rule (如 filesystem, index, state, geography, ids)")
    ap.add_argument("--severity", choices=["error", "warning", "info"], help="仅输出指定级别或更高权重的 Finding")
    ap.add_argument("--strict", action="store_true", help="严格模式：当存在 WARNING 或 ERROR 时返回非 0 退出码")
    ap.add_argument("--auto-fix", action="store_true", help="在审查前自动调用 auto_link_placeholders.py 进行占位符回补修复")

    args = ap.parse_args()
    novel_dir = Path(args.novel_dir).resolve()

    if not novel_dir.exists():
        print(json.dumps({"error": f"目录不存在: {novel_dir}"}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)

    if args.auto_fix:
        try:
            import importlib.util
            script_dir = Path(__file__).resolve().parent
            auto_link_path = script_dir / "auto_link_placeholders.py"
            if auto_link_path.exists():
                spec = importlib.util.spec_from_file_location("auto_link_placeholders", auto_link_path)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                mod.run_auto_link(novel_dir, dry_run=False)
        except Exception as e:
            print(f"Warning: auto-fix 运行失败: {e}", file=sys.stderr)

    engine = get_default_engine(novel_dir)
    context = AuditContext(novel_dir)
    findings = engine.run(rule_filter=args.rule, context=context)

    if args.severity:
        sev_order = {"error": 3, "warning": 2, "info": 1}
        min_level = sev_order.get(args.severity, 1)
        findings = [f for f in findings if sev_order.get(f.severity, 0) >= min_level]

    reporter = AuditReporter(novel_dir, findings)

    if args.format == "json":
        print(reporter.render_json())
    else:
        print(reporter.render_text())

    if args.strict:
        has_error_or_warning = any(f.severity in ["error", "warning"] for f in findings)
        if has_error_or_warning:
            sys.exit(1)
    else:
        has_error = any(f.severity == "error" for f in findings)
        if has_error:
            sys.exit(1)


if __name__ == "__main__":
    main()
