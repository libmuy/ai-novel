# -*- coding: utf-8 -*-
"""`/api/book` `/api/level` `/api/backfill*` 的响应体拼装。"""
from pathlib import Path

from prompt_build import layout as L  # noqa: E402

from . import files, model

_SEC_LABEL = {"work": "工作区", "plan": "规划", "text": "正文"}
_STATE_TAG = {"有音频": "neutral", "有正文": "neutral", "细纲已出": "outline", "待细纲": "accent"}


def _book_payload(novel_dir: Path, title: str, base: str, tree) -> dict:
    parts_out, n_vols, n_ch = [], 0, 0
    for p in tree:
        vols_out = []
        for v in p["vols"]:
            vols_out.append({
                "n": v["n"], "label": f"卷 {v['n']:02d}", "title": v["title"],
                "chapters": [{"n": c["n"], "title": c["title"], "state": c["state"],
                              "tag": _STATE_TAG[c["state"]], "has_manuscript": c["has_manuscript"]}
                             for c in v["chapters"]],
            })
            n_vols += 1
            n_ch += len(v["chapters"])
        parts_out.append({"n": p["n"], "label": f"第 {p['n']} 部", "title": p["title"], "vols": vols_out})
    return {"title": title, "parts": parts_out,
            "meta": {"parts": len(parts_out), "vols": n_vols, "chapters": n_ch},
            "feed_url": f"{base}/feed.xml"}


def _level_payload(novel_dir: Path, sec: str, part, vol, ch, title: str, tree, entries) -> tuple[int, dict]:
    sec_label = _SEC_LABEL.get(sec)
    if sec_label is None:
        return 400, {"error": f"未知分区：{sec}"}

    if ch is not None:
        if part is None or vol is None:
            return 400, {"error": "章级需要 part/vol"}
        p = model._find_part(tree, part)
        v = model._find_vol(p, vol)
        if not v:
            return 404, {"error": "not found"}
        visible = [c for c in v["chapters"] if sec != "text" or c["has_manuscript"]]
        nums = [c["n"] for c in visible]
        idx = nums.index(ch) if ch in nums else -1
        prev_n = nums[idx - 1] if idx > 0 else None
        next_n = nums[idx + 1] if 0 <= idx < len(nums) - 1 else None
        cnode = next((c for c in v["chapters"] if c["n"] == ch), None)
        if cnode is None:
            return 404, {"error": "not found"}
        title_bits = f"第 {ch} 章" + (f" · {cnode['title']}" if cnode["title"] else "")
        return 200, {
            "level": "ch", "kicker": f"{sec_label} · 章", "title": title_bits,
            "meta": f"第 {part} 部 卷 {vol:02d} · {cnode['state']}",
            "children": None,
            "file_groups": files._level_file_groups(novel_dir, sec, "ch", part, vol, ch, entries),
            "chapter": {"n": ch, "prev": prev_n, "next": next_n},
        }

    if vol is not None:
        if part is None:
            return 400, {"error": "卷级需要 part"}
        p = model._find_part(tree, part)
        v = model._find_vol(p, vol)
        if not v:
            return 404, {"error": "not found"}
        visible = [c for c in v["chapters"] if sec != "text" or c["has_manuscript"]]
        children = [{"code": f"{c['n']:04d}", "n": c["n"], "title": c["title"],
                     "state": c["state"], "tag": _STATE_TAG[c["state"]]} for c in visible]
        meta = f"共 {len(visible)} 章" if visible else "尚无章节，卷大纲为草稿"
        title_bits = f"卷 {vol:02d}" + (f" · {v['title']}" if v["title"] else "")
        return 200, {
            "level": "vol", "kicker": f"{sec_label} · 卷", "title": title_bits, "meta": meta,
            "children": children,
            "file_groups": files._level_file_groups(novel_dir, sec, "vol", part, vol, None, entries),
        }

    if part is not None:
        p = model._find_part(tree, part)
        if not p:
            return 404, {"error": "not found"}
        children = [{"code": f"{v['n']:02d}", "n": v["n"], "title": v["title"],
                     "state": f"{len(v['chapters'])} 章", "tag": "neutral"} for v in p["vols"]]
        title_bits = f"第 {part} 部" + (f" · {p['title']}" if p["title"] else "")
        return 200, {
            "level": "part", "kicker": f"{sec_label} · 部", "title": title_bits,
            "meta": f"{len(p['vols'])} 卷 · {sum(len(v['chapters']) for v in p['vols'])} 章",
            "children": children,
            "file_groups": files._level_file_groups(novel_dir, sec, "part", part, None, None, entries),
        }

    children = [{"code": f"{p['n']:02d}", "n": p["n"], "title": p["title"],
                 "state": f"{len(p['vols'])} 卷", "tag": "neutral"} for p in tree]
    return 200, {
        "level": "root", "kicker": f"{sec_label} · 全书", "title": title,
        "meta": (f"{len(tree)} 部 · {sum(len(p['vols']) for p in tree)} 卷 · "
                 f"{sum(len(v['chapters']) for p in tree for v in p['vols'])} 章"),
        "children": children,
        "file_groups": files._level_file_groups(novel_dir, sec, "root", None, None, None, entries),
    }


# ================================================================ 回填

def _backfill_targets(novel_dir: Path, part, vol, ch, tree) -> list[dict]:
    if ch is not None:
        lay = L.resolve(novel_dir, part, vol, ch)
        return [
            {"id": L.rel(novel_dir, lay.outline), "label": f"章细纲 → {L.rel(novel_dir, lay.outline)}"},
            {"id": L.rel(novel_dir, lay.manuscript), "label": f"正文 → {L.rel(novel_dir, lay.manuscript)}"},
        ]
    if vol is not None:
        pd = model._plan_dir_for(novel_dir, part, vol)
        files_ = [f for f in files._list_files(novel_dir, pd) if not model._OUTLINE_MD_RE.match(Path(f["path"]).name)]
    elif part is not None:
        files_ = files._list_files(novel_dir, model._plan_dir_for(novel_dir, part))
    else:
        files_ = files._list_files(novel_dir, novel_dir / "03_规划")
    return [{"id": f["path"], "label": f"{f['name']} → {f['path']}"} for f in files_]


def _do_backfill(novel_dir: Path, src_rel: str, target_id: str, part, vol, ch, tree) -> dict:
    src = files._safe_resolve(novel_dir, src_rel)
    targets = _backfill_targets(novel_dir, part, vol, ch, tree)
    if target_id not in {t["id"] for t in targets}:
        raise ValueError("非法回填目标")
    target = files._validate_rel_path(novel_dir, target_id)
    text = src.read_text(encoding="utf-8", errors="ignore")
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(target)
    result = {"ok": True, "target": L.rel(novel_dir, target)}
    if ch is not None:
        lay = L.resolve(novel_dir, part, vol, ch)
        if target == lay.outline:
            result["mode"] = "outline"
        elif target == lay.manuscript:
            result["mode"] = "manuscript"
    return result
