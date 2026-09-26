# -*- coding: utf-8 -*-
"""`/api/book` `/api/level` `/api/backfill*` `/api/withdraw` 的响应体拼装。"""
import datetime
from pathlib import Path

import progress_store
from prompt_build import layout as L  # noqa: E402

from . import files, jobs, model


class WithdrawConflict(Exception):
    """撤下请求被拒绝（不是最新章 / 细纲被正文挡住 / 有任务在跑）——映射 409。"""

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
        e = model.find(entries, part, vol, ch)
        return 200, {
            "level": "ch", "kicker": f"{sec_label} · 章", "title": title_bits,
            "meta": f"第 {part} 部 卷 {vol:02d} · {cnode['state']}",
            "children": None,
            "file_groups": files._level_file_groups(novel_dir, sec, "ch", part, vol, ch, entries),
            "chapter": {"n": ch, "prev": prev_n, "next": next_n,
                        "withdraw": _withdraw_info(entries, e, part, vol, ch)},
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


def _withdraw_info(entries, e, part: int, vol: int, ch: int) -> dict:
    """`chapter.withdraw`：正文/细纲各自算「这章是不是全书当前最新一章」，
    供审查台决定是显示「撤下重新生成」按钮还是只显示提醒文字。"""
    key = (part, vol, ch)

    def info(attr: str) -> dict:
        exists = bool(e and getattr(e, attr, None))
        lk = model.latest_key(entries, attr)
        latest_label = None
        if lk:
            lp, lv, lc = lk
            latest_label = f"第 {lp} 部 卷 {lv:02d} 第 {lc} 章"
        return {"exists": exists, "latest": exists and lk == key, "latest_label": latest_label}

    manuscript = info("manuscript")
    outline = info("outline")
    outline["blocked_by_manuscript"] = outline["exists"] and manuscript["exists"]
    return {"manuscript": manuscript, "outline": outline}


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
    src = files._safe_resolve(novel_dir, src_rel, require_text=True)
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


# ================================================================ 撤下重新生成

_ARCHIVE_STEM = {"manuscript": "01_正文生成", "outline": "00_单章细纲"}


def _unique_archive_path(output_dir: Path, stem: str, ts: str) -> Path:
    base = f"{stem}_旧稿_{ts}"
    p = output_dir / f"{base}.md"
    n = 2
    while p.exists():
        p = output_dir / f"{base}_{n}.md"
        n += 1
    return p


def _do_withdraw(novel_dir: Path, part: int, vol: int, ch: int, kind: str) -> dict:
    if kind not in ("manuscript", "outline"):
        raise ValueError(f"非法 kind：{kind}")

    lay = L.resolve(novel_dir, part, vol, ch)
    canon = lay.manuscript if kind == "manuscript" else lay.outline
    if not canon.is_file():
        raise FileNotFoundError(f"{L.rel(novel_dir, canon)} 不存在，没有可撤的内容")

    # 不信任前端传来的「是不是最新」判断，按当前磁盘状态重新算一次。
    entries = model.scan(novel_dir)
    e = model.find(entries, part, vol, ch)
    lk = model.latest_key(entries, kind)
    if lk != (part, vol, ch):
        label = None
        if lk:
            lp, lv, lc = lk
            label = f"第 {lp} 部 卷 {lv:02d} 第 {lc} 章"
        raise WithdrawConflict(
            f"本章之后已有更晚的定稿章节（最新：{label}）；改早期已定稿章节请走技能 "
            f"`00_通用模板/03_任务技能/02_小说级/06_章节回溯修改.md`"
            f"（dry-run 确认后再重折状态，这里不做自动化）")

    if kind == "outline" and e and e.manuscript:
        raise WithdrawConflict("本章正文还在——细纲是正文的输入，先撤正文，再撤细纲")

    id_key = f"{part}/{vol}/{ch}"
    with jobs._RUNNING_LOCK:
        busy = any(k.endswith(f":{id_key}") for k in jobs._RUNNING_KEYS)
    if busy:
        raise WithdrawConflict("本章有任务在跑，等它结束再撤")

    # 先探路：JSON 非法就整个中止，不碰任何文件。
    progress_store.load(novel_dir)

    ts = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    stem = _ARCHIVE_STEM[kind]
    dest = _unique_archive_path(lay.output_dir, stem, ts)
    dest.parent.mkdir(parents=True, exist_ok=True)
    canon.rename(dest)

    rel_key = L.rel(novel_dir, canon)
    try:
        progress_cleared = progress_store.remove(novel_dir, rel_key)
    except Exception:
        dest.rename(canon)  # 回滚：canonical 位置和进度表登记必须一致
        raise

    audio_archived: list[str] = []
    warnings: list[str] = []
    if kind == "manuscript":
        audio_dir = e.audio_dir if e else None
        if audio_dir and audio_dir.is_dir():
            cid = f"章{ch:04d}"
            for p in sorted(audio_dir.glob(f"{cid}*")):
                if p.suffix not in (".mp3", ".json"):
                    continue
                new_p = p.with_name(f"{p.stem}_旧稿_{ts}{p.suffix}")
                p.rename(new_p)
                audio_archived.append(L.rel(novel_dir, new_p))
        state_path = lay.state_dir / "01_状态履历.md"
        if state_path.exists():
            warnings.append(
                f"{L.rel(novel_dir, state_path)} 已经写过——新稿定稿后要按技能 "
                f"`03_章节状态对账.md` 重写重折")
        landing = lay.state_dir / "03_细纲落地核对.md"
        if landing.exists():
            warnings.append(f"{L.rel(novel_dir, landing)} 是针对旧稿的落地核对表，新稿出来后要重做")
        review = lay.state_dir / "02_正文校验记录.md"
        if review.exists():
            warnings.append(f"{L.rel(novel_dir, review)} 里的冷读记录是针对旧稿的，新稿出来后要重新冷读")

    return {
        "ok": True, "kind": kind,
        "archived_from": rel_key, "archived_to": L.rel(novel_dir, dest),
        "progress_cleared": progress_cleared,
        "audio_archived": audio_archived, "warnings": warnings,
    }
