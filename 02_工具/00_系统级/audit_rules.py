#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
规则层一致性审查 (audit_rules.py)

`audit_consistency.py` 审的是**小说数据**；本脚本审的是**规则层自身**——
`00_通用模板/` + `AGENTS.md` + `README.md`。

为什么需要它：`00_系统架构规范.md` §二·A 明文写着「规则层自身也适用单一权威原则」，
并列出了规则层的权威位置表；但规则层此前**没有任何确定性检查**，全靠人记得同步。
而按 git churn，规则层恰恰是本仓最不稳定的部分（`00_使用说明.md` 70 次改动、
`00_云端提示词生成器.md` 49 次、`AGENTS.md` 41 次、`99_速查手册.md` 35 次）。
「能写成确定性检查的，就不该再靠人眼」——技能 `04_单章质量验收.md`「通读发现归口」
对小说数据说的这句话，对规则层同样成立。

规则代码
--------
| 代码      | 级别    | 检查                                                       |
|-----------|---------|------------------------------------------------------------|
| RULE001   | error   | 规则层引用的 `xxx.md` 文件不存在（死链）                     |
| RULE002   | warning | 索引里声明的文件数与实际目录不符（如「写作规则（13 个）」）   |
| RULE003   | error   | 技能索引 ↔ 技能文件不是双向一一对应                          |
| RULE004   | warning | 派生切片长出了权威版没有的小节（切片只许少、不许多）          |
| RULE005   | error   | §二·A 权威位置表点名的权威文件不存在                         |
| RULE006   | warning | 同一段规范正文逐字出现在多个规则文件（§二·A 病根）            |
| RULE007   | error   | 工具硬编码的数据路径在骨架和所有实有小说里都不存在（改名漏改）|
| RULE008   | error   | 脚本派生的规则切片与按权威版重出的结果不一致（切片被手改）    |

用法
----
    audit_rules.py [仓库根] [--format json|text] [--rule RULE001] [--strict]

退出码：有 error → 1；`--strict` 时有 warning 也 → 1。
配置见同目录 `rules_audit.config.toml`。
"""
import argparse
import difflib
import json
import os
import re
import sys
import tomllib
from collections import defaultdict
from pathlib import Path

CONFIG_FILENAME = "rules_audit.config.toml"

ERROR, WARNING, INFO = "error", "warning", "info"


class Finding:
    def __init__(self, severity, code, message, locations=None, suggestion=""):
        self.severity = severity
        self.code = code
        self.message = message
        self.locations = locations or []
        self.suggestion = suggestion

    def to_dict(self):
        return {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "locations": self.locations,
            "suggested_action": self.suggestion,
        }


# ---------------------------------------------------------------- 载入

def load_config(repo_root: Path, override: Path | None = None) -> dict:
    path = override or (Path(__file__).resolve().parent / CONFIG_FILENAME)
    with open(path, "rb") as fh:
        return tomllib.load(fh)


def collect_rule_files(repo_root: Path, cfg: dict) -> list[Path]:
    """规则层的 markdown 文件清单（相对仓库根去重、排序）。"""
    excludes = [repo_root / e for e in cfg["scope"].get("exclude", [])]
    out: set[Path] = set()
    for root in cfg["scope"]["roots"]:
        p = repo_root / root
        if p.is_file() and p.suffix == ".md":
            out.add(p)
        elif p.is_dir():
            for f in p.rglob("*.md"):
                if any(_is_within(f, x) for x in excludes):
                    continue
                out.add(f)
    return sorted(out)


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _rel(repo_root: Path, p: Path) -> str:
    return p.relative_to(repo_root).as_posix()


# ---------------------------------------------------------------- RULE001 死链

_MD_REF_RE = re.compile(r"`([^`\n]{2,160}?\.md)`")


def check_dead_refs(repo_root, cfg, rule_files) -> list[Finding]:
    markers = cfg["ref"]["placeholder_markers"]
    conditional = set(cfg["ref"].get("conditional", []))
    historical = set(cfg["ref"].get("historical", []))

    known = set()
    for dirpath, dirs, files in os.walk(repo_root):
        dirs[:] = [d for d in dirs
                   if d not in {".git", "__pycache__", ".venv", "node_modules"}
                   and not d.startswith(".")]
        for f in files:
            known.add(Path(dirpath, f).relative_to(repo_root).as_posix())
    by_basename = defaultdict(list)
    for k in known:
        by_basename[k.rsplit("/", 1)[-1]].append(k)

    dead = []
    for f in rule_files:
        rel = _rel(repo_root, f)
        for lineno, line in enumerate(f.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            for m in _MD_REF_RE.finditer(line):
                ref = m.group(1).strip()
                if any(mk in ref for mk in markers) or ref in conditional:
                    continue
                # 已废止的旧文件名：文档里提到它，是为了说明「它没有了」
                if ref in historical or ref.rsplit("/", 1)[-1] in historical:
                    continue
                base = ref.rsplit("/", 1)[-1]
                if any(k.endswith(ref) for k in known) or by_basename.get(base):
                    continue
                dead.append(f"{rel}:{lineno} → `{ref}`")

    if not dead:
        return []
    return [Finding(
        ERROR, "RULE001",
        f"规则层有 {len(dead)} 处 `.md` 引用指向不存在的文件（死链）",
        dead,
        "改成实际路径，或删除该引用；确属「按条件才创建」的文件，登记到 "
        "rules_audit.config.toml 的 [ref].conditional",
    )]


# ---------------------------------------------------------------- RULE002 计数

def check_declared_counts(repo_root, cfg) -> list[Finding]:
    findings = []
    for spec in cfg.get("index", {}).get("declared_counts", []):
        src = repo_root / spec["file"]
        target_dir = repo_root / spec["dir"]
        if not src.exists() or not target_dir.is_dir():
            continue
        text = src.read_text(encoding="utf-8", errors="ignore")
        m = re.search(spec["pattern"], text)
        if not m:
            continue
        declared = int(m.group(1))
        actual = len([p for p in target_dir.glob("*.md")])
        if declared != actual:
            findings.append(Finding(
                WARNING, "RULE002",
                f"{spec['file']} 声明「{spec['dir'].rsplit('/', 1)[-1]} {declared} 个」，"
                f"实际 {actual} 个",
                [f"{spec['file']}（模式 {spec['pattern']}）", f"{spec['dir']}/ 实有 {actual} 个 .md"],
                "更新该索引的计数与条目清单，或说明差额原因",
            ))
    return findings


# ---------------------------------------------------------------- RULE003 技能索引

def check_skill_indexes(repo_root, cfg) -> list[Finding]:
    findings = []
    for spec in cfg.get("skill", {}).get("indexes", []):
        index = repo_root / spec["index"]
        sdir = repo_root / spec["dir"]
        if not index.exists() or not sdir.is_dir():
            continue
        text = index.read_text(encoding="utf-8", errors="ignore")
        listed = set(re.findall(r"`([^`\n]+\.md)`", text))
        listed = {x.rsplit("/", 1)[-1] for x in listed}
        actual = {p.name for p in sdir.glob("*.md") if p.name != "index.md"}

        missing = sorted(actual - listed)      # 有文件、索引没列
        # 索引正文里也会提到别处的文件（00_使用说明.md 等）；只有全仓都找不到的才算幽灵条目
        phantom = sorted(x for x in listed - actual if not _find_basename(repo_root, x))
        if missing:
            findings.append(Finding(
                ERROR, "RULE003",
                f"{spec['index']} 漏列了 {len(missing)} 个技能文件",
                [f"{spec['dir']}/{x}" for x in missing],
                "在索引表补上这些技能的行（技能名 / 触发词 / 文件）；"
                "技能不进索引 = Agent 按「技能发现协议」永远找不到它",
            ))
        if phantom:
            findings.append(Finding(
                ERROR, "RULE003",
                f"{spec['index']} 列了 {len(phantom)} 个不存在的技能文件",
                [f"{spec['index']} → {x}" for x in phantom],
                "删除该行，或补建对应技能文件",
            ))
    return findings


# ---------------------------------------------------------------- RULE004 派生切片

_HEADING_RE = re.compile(r"^(#{2,4})\s+(.+?)\s*$")


def _headings(path: Path) -> list[str]:
    out = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        m = _HEADING_RE.match(line)
        if m:
            out.append(m.group(2).strip())
    return out


def _norm_heading(h: str) -> str:
    h = re.sub(r"^[一二三四五六七八九十]+、", "", h)
    h = re.sub(r"^\d+(\.\d+)*\s*", "", h)
    return re.sub(r"[（(].*?[)）]", "", h).strip()


def check_derived_slices(repo_root, cfg) -> list[Finding]:
    findings = []
    for spec in cfg.get("derived", {}).get("slices", []):
        auth = repo_root / spec["authority"]
        sli = repo_root / spec["slice"]
        if not auth.exists() or not sli.exists():
            continue
        auth_norm = {_norm_heading(h) for h in _headings(auth)}
        extra = [h for h in _headings(sli) if _norm_heading(h) not in auth_norm]
        if extra:
            findings.append(Finding(
                WARNING, "RULE004",
                f"派生切片 {spec['slice']} 有 {len(extra)} 个小节在权威版 "
                f"{spec['authority']} 里找不到对应",
                [f"{spec['slice']} → 「{h}」" for h in extra],
                "切片是权威版的有损压缩，只许少、不许多。要么把该节先写进权威版，"
                "要么从切片删除——否则规则会在切片里分叉",
            ))
    return findings


# ---------------------------------------------------------------- RULE005 权威位置表

def check_authority_table(repo_root, cfg) -> list[Finding]:
    src = repo_root / cfg["authority"]["table_file"]
    if not src.exists():
        return []
    text = src.read_text(encoding="utf-8", errors="ignore")
    markers = cfg["ref"]["placeholder_markers"]

    missing = []
    for lineno, line in enumerate(text.splitlines(), 1):
        s = line.strip()
        if not (s.startswith("|") and s.count("|") >= 3):
            continue
        for ref in re.findall(r"`([^`\n]+\.md)`", s):
            if any(mk in ref for mk in markers):
                continue
            base = ref.rsplit("/", 1)[-1]
            hit = any((repo_root / r).exists() for r in
                      [ref, f"00_通用模板/{ref}"]) or _find_basename(repo_root, base)
            if not hit:
                missing.append(f"{cfg['authority']['table_file']}:{lineno} → `{ref}`")

    if not missing:
        return []
    return [Finding(
        ERROR, "RULE005",
        f"§二·A 权威位置表点名了 {len(missing)} 个不存在的权威文件",
        missing,
        "权威位置表是单一权威原则的落地入口，指向的文件必须真实存在",
    )]


_BASENAME_CACHE: dict[str, set] = {}


def _find_basename(repo_root: Path, base: str) -> bool:
    key = str(repo_root)
    if key not in _BASENAME_CACHE:
        s = set()
        for dirpath, dirs, files in os.walk(repo_root):
            dirs[:] = [d for d in dirs if not d.startswith(".") and d != "__pycache__"]
            s.update(files)
        _BASENAME_CACHE[key] = s
    return base in _BASENAME_CACHE[key]


# ---------------------------------------------------------------- RULE006 重复规范正文

_SPLIT_RE = re.compile(r"[。；\n]")
_LEAD_RE = re.compile(r"^[-*>|#\s\[\]x…]+")
# 「见 `<权威文件>`」式的指针句，正是 §二·A 要求的写法本身——在多处出现是对的，不是病。
_POINTER_RE = re.compile(r"(见|详见|参见|以)\s*`[^`]+\.md`")


def check_duplicate_text(repo_root, cfg, rule_files) -> list[Finding]:
    dcfg = cfg["duplicate"]
    min_len = dcfg["min_length"]
    groups = [set(g) for g in dcfg.get("exempt_groups", [])]
    exempt_sentences = set(dcfg.get("exempt_sentences", []))
    exempt_prefixes = tuple(dcfg.get("exempt_prefixes", []))

    sentences: dict[str, set] = defaultdict(set)
    for f in rule_files:
        rel = _rel(repo_root, f)
        for line in f.read_text(encoding="utf-8", errors="ignore").splitlines():
            s = _LEAD_RE.sub("", line.strip())
            for piece in _SPLIT_RE.split(s):
                piece = piece.strip()
                if len(piece) >= min_len and not piece.startswith("|"):
                    sentences[piece].add(rel)

    dups = []
    for sent, files in sentences.items():
        if len(files) < 2 or sent in exempt_sentences:
            continue
        if exempt_prefixes and sent.startswith(exempt_prefixes):
            continue
        if _POINTER_RE.search(sent):
            continue
        if any(files <= g for g in groups):
            continue
        dups.append((sent, sorted(files)))

    if not dups:
        return []
    dups.sort(key=lambda x: -len(x[0]))
    return [Finding(
        WARNING, "RULE006",
        f"发现 {len(dups)} 段规范正文逐字出现在多个规则文件（单一权威原则 §二·A）",
        [f"[{len(fs)} 文件] {s[:70]}…\n        " + "\n        ".join(fs)
         for s, fs in dups],
        "定出这条规格的唯一权威位置（见 §二·A 规则层权威位置表），其余各处改写成"
        "「见 `<权威文件>`」的指针；确需并列/派生的，登记到 rules_audit.config.toml "
        "的 [duplicate].exempt_groups 并写明理由",
    )]


# ---------------------------------------------------------------- RULE007 工具硬编码路径

_DATA_PATH_RE = re.compile(
    r"[\"']((?:01_设定|02_数据库|03_规划|05_工作区|10_正文)/[^\"'\n]*?)[\"']")


def check_tool_paths(repo_root, cfg) -> list[Finding]:
    tcfg = cfg["tool_path"]
    skeleton = repo_root / tcfg["skeleton"]
    if not skeleton.is_dir():
        return []
    excludes = tcfg.get("exclude", [])
    allow_missing = set(tcfg.get("allow_missing", []))

    # 基线 = 骨架模板 ∪ 所有实有小说目录。工具里的路径常量只要在其中**任意一处**
    # 落得了地，就说明它还是活的；一处都落不了地，才是改名漏改（白名单文件就是这么
    # 静默失效的：文档与骨架都从 00_ 改到 03_，规则代码的常量没跟着改）。
    bases = [skeleton]
    novels_root = repo_root / "01_小说数据"
    if novels_root.is_dir():
        bases += [d for d in sorted(novels_root.iterdir()) if d.is_dir()]

    hits: dict[str, list] = defaultdict(list)
    for root in tcfg["tool_roots"]:
        base = repo_root / root
        if not base.is_dir():
            continue
        for py in base.rglob("*.py"):
            rel_py = _rel(repo_root, py)
            if any(x in rel_py for x in excludes):
                continue
            for lineno, line in enumerate(py.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                for m in _DATA_PATH_RE.finditer(line):
                    hits[m.group(1)].append(f"{rel_py}:{lineno}")

    dangling = []
    for path, where in sorted(hits.items()):
        if any(c in path for c in "*{}<>%") or path in allow_missing:
            continue
        if " " in path:          # 被拆行的字符串字面量，不是路径常量
            continue
        if any((b / path).exists() for b in bases):
            continue
        dangling.append(f"`{path}` ← " + "、".join(where[:3]))

    if not dangling:
        return []
    return [Finding(
        ERROR, "RULE007",
        f"工具里硬编码的 {len(dangling)} 个数据路径在骨架模板和所有实有小说里都不存在",
        dangling,
        "对齐骨架模板的实际文件名（改名时规则代码常漏改，白名单文件就这么静默失效过）；"
        "确属按需创建的路径，登记到 rules_audit.config.toml 的 [tool_path].allow_missing",
    )]


# ---------------------------------------------------------------- RULE008 切片重出

def check_slices(repo_root, cfg) -> list[Finding]:
    """切片必须与「按权威版 slice 标记重出」的结果逐字一致。

    RULE004 只查结构（切片多长出了章）；改字它拦不住——而历史上真实发生的
    正是改字（校验版把「登记到伏笔登记表」抄成「登记到伏笔跟踪册」，
    把读者指向了下游而非权威）。
    """
    if not cfg.get("slice", {}).get("enabled", False):
        return []
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        import build_rule_slices as brs
    except ImportError:
        return []
    try:
        rendered = brs.build(repo_root)
    except Exception as e:
        return [Finding(ERROR, "RULE008", f"规则切片无法重出：{e}", [],
                        "跑 `02_工具/00_系统级/build_rule_slices.py --list` 看标记是否写对")]

    stale = []
    for name, content in rendered.items():
        rel = brs.SLICES[name][0]
        p = repo_root / rel
        cur = p.read_text(encoding="utf-8") if p.exists() else ""
        if cur != content:
            n = sum(1 for _ in difflib.unified_diff(
                cur.splitlines(), content.splitlines(), lineterm="", n=0))
            stale.append(f"{rel}（{n} 行与重出结果不同）")
    if not stale:
        return []
    return [Finding(
        ERROR, "RULE008",
        f"{len(stale)} 个派生切片与权威版不一致",
        stale,
        "跑 `02_工具/00_系统级/build_rule_slices.py --write` 重出；"
        "要改规则请改权威版 `00_通用写作规则.md`，切片是派生物、不该手改",
    )]


# ---------------------------------------------------------------- 汇总输出

CHECKS = {
    "RULE001": lambda rr, cfg, rf: check_dead_refs(rr, cfg, rf),
    "RULE002": lambda rr, cfg, rf: check_declared_counts(rr, cfg),
    "RULE003": lambda rr, cfg, rf: check_skill_indexes(rr, cfg),
    "RULE004": lambda rr, cfg, rf: check_derived_slices(rr, cfg),
    "RULE005": lambda rr, cfg, rf: check_authority_table(rr, cfg),
    "RULE006": lambda rr, cfg, rf: check_duplicate_text(rr, cfg, rf),
    "RULE007": lambda rr, cfg, rf: check_tool_paths(rr, cfg),
    "RULE008": lambda rr, cfg, rf: check_slices(rr, cfg),
}


def run_all(repo_root: Path, cfg: dict, only: str | None = None) -> list[Finding]:
    rule_files = collect_rule_files(repo_root, cfg)
    findings = []
    for code, fn in CHECKS.items():
        if only and code != only:
            continue
        findings.extend(fn(repo_root, cfg, rule_files))
    order = {ERROR: 0, WARNING: 1, INFO: 2}
    findings.sort(key=lambda f: (order[f.severity], f.code))
    return findings


def render_text(repo_root: Path, findings: list[Finding]) -> str:
    n_err = sum(1 for f in findings if f.severity == ERROR)
    n_warn = sum(1 for f in findings if f.severity == WARNING)
    lines = [f"=== 规则层审查报告: {repo_root} ===",
             f"ERROR: {n_err} | WARNING: {n_warn}", ""]
    if not findings:
        lines.append("规则层一致性检查全部通过。")
    for f in findings:
        lines.append(f"[{f.severity.upper()}] {f.code}")
        lines.append(f"  问题: {f.message}")
        for loc in f.locations[:40]:
            lines.append(f"    - {loc}")
        if len(f.locations) > 40:
            lines.append(f"    …（另有 {len(f.locations) - 40} 处）")
        if f.suggestion:
            lines.append(f"  建议: {f.suggestion}")
        lines.append("")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="规则层一致性审查（00_通用模板/ + AGENTS.md）")
    ap.add_argument("repo_root", nargs="?", default=None,
                    help="仓库根目录，默认脚本所在仓库")
    ap.add_argument("--format", choices=["json", "text"], default="text")
    ap.add_argument("--rule", help="只跑某条规则，如 RULE006")
    ap.add_argument("--config", help="覆盖 rules_audit.config.toml 路径")
    ap.add_argument("--strict", action="store_true",
                    help="严格模式：有 WARNING 也返回非 0")
    args = ap.parse_args()

    repo_root = (Path(args.repo_root).resolve() if args.repo_root
                 else Path(__file__).resolve().parents[2])
    cfg = load_config(repo_root, Path(args.config).resolve() if args.config else None)

    if args.rule and args.rule not in CHECKS:
        sys.exit(f"未知规则 {args.rule}，可选：{', '.join(CHECKS)}")

    findings = run_all(repo_root, cfg, args.rule)

    if args.format == "json":
        print(json.dumps({
            "repo_root": str(repo_root),
            "summary": {
                "error": sum(1 for f in findings if f.severity == ERROR),
                "warning": sum(1 for f in findings if f.severity == WARNING),
            },
            "findings": [f.to_dict() for f in findings],
        }, ensure_ascii=False, indent=2))
    else:
        print(render_text(repo_root, findings))

    if any(f.severity == ERROR for f in findings):
        sys.exit(1)
    if args.strict and any(f.severity == WARNING for f in findings):
        sys.exit(1)


if __name__ == "__main__":
    main()
