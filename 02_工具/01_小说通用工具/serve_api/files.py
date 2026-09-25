# -*- coding: utf-8 -*-
"""单文件级操作：分组列表、读写越界校验、kind/desc/icon 推导。"""
from pathlib import Path

from progress_report import han_count  # noqa: E402  同目录的 01_小说通用工具/progress_report.py

from . import model

_TEXT_EXT = {".md", ".txt", ".json", ".jsonl", ".toml", ".csv"}
_RENDER_CAP = 512 * 1024
_ALLOWED_ROOTS = ("05_工作区", "03_规划", "10_正文")
_WS_SUBDIRS = (("00_提示词", "ph-arrow-square-in"), ("01_模型输出", "ph-arrow-square-out"),
               ("02_状态", "ph-shield-check"), ("03_音频", "ph-waveform"))


def _rel(novel_dir: Path, p: Path) -> str:
    return p.resolve().relative_to(novel_dir.resolve()).as_posix()


def _file_kind(p: Path) -> str:
    if p.suffix.lower() == ".mp3":
        return "audio"
    if p.suffix.lower() == ".md" and "正文" in p.name:
        return "prose"
    return "code"


def _file_desc(p: Path) -> str:
    parent = p.parent.name
    if parent == "00_提示词":
        return "输入 · 提示词"
    if parent == "01_模型输出":
        return "输出 · 模型产出"
    if parent == "02_状态":
        return "状态"
    if p.suffix.lower() == ".mp3":
        return model._fmt_hms(model._mp3_duration_seconds(p, model._safe_size(p)))
    if _file_kind(p) == "prose":
        try:
            return f"{han_count(p.read_text(encoding='utf-8', errors='ignore'))} 字"
        except OSError:
            return ""
    return ""


def _file_icon(p: Path) -> str:
    if p.suffix.lower() == ".mp3":
        return "ph-headphones"
    if p.suffix.lower() == ".json":
        return "ph-brackets-curly"
    n = p.name
    if "冷读" in n:
        return "ph-eyeglasses"
    if "校验" in n or "对照" in n:
        return "ph-check-square"
    if "伏笔" in n:
        return "ph-git-branch"
    if "细纲" in n or "大纲" in n:
        return "ph-list-dashes"
    return "ph-file-text"


def _file_entry(novel_dir: Path, p: Path) -> dict:
    return {"path": _rel(novel_dir, p), "name": p.name, "size": model._fmt_size(model._safe_size(p)),
            "desc": _file_desc(p), "icon": _file_icon(p), "kind": _file_kind(p)}


def _list_files(novel_dir: Path, d: Path | None) -> list[dict]:
    if not d or not d.is_dir():
        return []
    out = []
    for f in sorted(d.iterdir()):
        if not f.is_file() or f.name.startswith("."):
            continue
        if f.suffix.lower() not in _TEXT_EXT and f.suffix.lower() != ".mp3":
            continue
        out.append(_file_entry(novel_dir, f))
    return out


def _validate_rel_path(novel_dir: Path, rel_path: str, *, require_text: bool = True) -> Path:
    """校验一个「可能尚不存在」的相对路径可以安全访问；返回绝对路径。

    require_text=True（写入/回填目标/回填来源）：扩展名限文本类；
    require_text=False（读取/删除）：不查扩展名，按扩展名分类交给调用方。
    """
    if not rel_path or rel_path.startswith("/") or rel_path.startswith("~"):
        raise ValueError("非法路径")
    parts = Path(rel_path).parts
    if not parts or ".." in parts:
        raise ValueError("非法路径")
    if parts[0] not in _ALLOWED_ROOTS:
        raise ValueError("路径必须在 05_工作区/03_规划/10_正文 之内")
    if require_text and Path(rel_path).suffix.lower() not in _TEXT_EXT:
        raise ValueError("不支持写入该扩展名")
    nd = novel_dir.resolve()
    target = (nd / rel_path).resolve()
    if nd != target and nd not in target.parents:
        raise ValueError("越界路径")
    # 目标（或其最近存在的祖先）必须仍在小说目录内 —— 防符号链接逃逸
    check = target if target.exists() else target.parent
    while not check.exists():
        check = check.parent
    check = check.resolve()
    if check != nd and nd not in check.parents:
        raise ValueError("越界路径")
    return target


def _safe_resolve(novel_dir: Path, rel_path: str, *, require_text: bool = False) -> Path:
    """校验一个必须已存在的相对路径；返回绝对路径。默认不查扩展名（读/删）。"""
    target = _validate_rel_path(novel_dir, rel_path, require_text=require_text)
    if not target.is_file():
        raise FileNotFoundError(rel_path)
    return target


# ================================================================ 各级文件分组

def _ws_groups(novel_dir: Path, d: Path | None) -> list[dict]:
    if not d:
        return []
    groups = []
    for name, icon in _WS_SUBDIRS:
        files = _list_files(novel_dir, d / name)
        if files:
            groups.append({"name": name, "icon": icon, "files": files})
    return groups


def _level_file_groups(novel_dir: Path, sec: str, level: str, part, vol, ch, entries) -> list[dict]:
    if sec == "work":
        if level == "root":
            d = novel_dir / "05_工作区"
        elif level == "part":
            d = model._ws_dir_for(novel_dir, part)
        elif level == "vol":
            d = model._ws_dir_for(novel_dir, part, vol)
        else:
            d = model._ws_dir_for(novel_dir, part, vol, ch)
        return _ws_groups(novel_dir, d)

    if sec == "plan":
        if level == "root":
            files = _list_files(novel_dir, novel_dir / "03_规划")
            return [{"name": "顶层规划", "icon": "ph-files", "files": files}] if files else []
        if level == "part":
            files = _list_files(novel_dir, model._plan_dir_for(novel_dir, part))
            return [{"name": "本部规划", "icon": "ph-files", "files": files}] if files else []
        if level == "vol":
            pd = model._plan_dir_for(novel_dir, part, vol)
            files = [f for f in _list_files(novel_dir, pd)
                     if not model._OUTLINE_MD_RE.match(Path(f["path"]).name)]
            return [{"name": "本卷规划", "icon": "ph-files", "files": files}] if files else []
        # ch
        e = model.find(entries, part, vol, ch)
        files = [_file_entry(novel_dir, e.outline)] if e and e.outline else []
        return [{"name": "本章规划", "icon": "ph-list-dashes", "files": files}] if files else []

    if sec == "text":
        if level != "ch":
            return []
        e = model.find(entries, part, vol, ch)
        files = []
        if e and e.manuscript:
            files.append(_file_entry(novel_dir, e.manuscript))
        if e:
            for _scene, p in e.audio_units():
                files.append(_file_entry(novel_dir, p))
        return [{"name": "本章", "icon": "ph-files", "files": files}] if files else []

    return []
