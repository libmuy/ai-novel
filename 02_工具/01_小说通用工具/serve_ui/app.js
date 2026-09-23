// 审查台前端（原生 JS，无构建步骤）。
// 结构与内联样式取自设计稿「审查界面 v3」，数据全部换成后端 /api/* 的真实结果。
"use strict";

// ---------------------------------------------------------------- 小工具

function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  attrs = attrs || {};
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === "style" && typeof v === "object") Object.assign(el.style, v);
    else if (k === "style") el.setAttribute("style", v);
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2).toLowerCase(), v);
    else if (k === "class") el.className = v;
    else if (k === "disabled") { if (v) el.setAttribute("disabled", ""); }
    else el.setAttribute(k, v);
  }
  for (const c of children.flat(Infinity)) {
    if (c == null || c === false || c === "") continue;
    el.appendChild(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}
const icon = (name, size) => h("i", { class: `ph ${name}`, style: `font-size:${size || 15}px` });

async function fetchJSON(method, url, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  if (method !== "GET") opts.headers["X-Review-UI"] = "1";
  const res = await fetch(url, opts);
  let data = {};
  try { data = await res.json(); } catch (e) { /* 空响应 */ }
  if (!res.ok) {
    const err = new Error(data.error || res.statusText || `HTTP ${res.status}`);
    err.status = res.status; err.data = data;
    throw err;
  }
  return data;
}
const q = (obj) => {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(obj)) if (v != null) p.set(k, v);
  return p.toString();
};
const API = {
  book: () => fetchJSON("GET", "/api/book"),
  config: () => fetchJSON("GET", "/api/config"),
  level: (sec, part, vol, ch) => fetchJSON("GET", "/api/level?" + q({ sec, part, vol, ch })),
  file: (path) => fetchJSON("GET", "/api/file?" + q({ path })),
  putFile: (path, text, orig, overwrite) => fetchJSON("PUT", "/api/file", { path, text, orig, overwrite }),
  deleteFile: (path) => fetchJSON("DELETE", "/api/file?" + q({ path })),
  backfillTargets: (part, vol, ch) => fetchJSON("GET", "/api/backfill-targets?" + q({ part, vol, ch })),
  backfill: (body) => fetchJSON("POST", "/api/backfill", body),
  createJob: (body) => fetchJSON("POST", "/api/jobs", body),
  getJob: (id) => fetchJSON("GET", "/api/jobs/" + id),
};

const ARCHIVE_NAME = { outline_ch: "00_单章细纲.md", draft: "01_正文生成.md" };
const SEC_ICON = { work: "ph-folder-open", plan: "ph-compass", text: "ph-book-open" };
const SEC_ORDER = ["work", "plan", "text"];
const LEVEL_CMDS = {
  root: ["outline_book", "backfill"],
  part: ["outline_part", "backfill"],
  vol: ["outline_vol", "check", "backfill"],
  ch: ["outline_ch", "draft", "check", "backfill", "cold", "audio"],
};
const CMD_META = {
  outline_book: { label: "全书大纲提示词", desc: "总纲 + 世界设定 + 人物档案", icon: "ph-tree-structure" },
  outline_part: { label: "部大纲提示词", desc: "总纲 + 伏笔总纲 + 上一部收尾", icon: "ph-tree-structure" },
  outline_vol: { label: "卷大纲提示词", desc: "部大纲 + 本卷伏笔 + 上一卷摘要", icon: "ph-map-trifold" },
  outline_ch: { label: "章细纲提示词", desc: "卷大纲 + 相关伏笔 + 前三章摘要", icon: "ph-list-dashes" },
  draft: { label: "单章正文提示词", desc: "以采纳的细纲为输入", icon: "ph-pen-nib" },
  check: { label: "一致性校验提示词", desc: "单章或连续多章", icon: "ph-check-circle" },
  backfill: { label: "回填到规划/正文", desc: "把当前打开的文件写回正式位置", icon: "ph-arrow-square-in" },
  cold: { label: "冷读", desc: "本地模型或 OpenCode 审读云端产出", icon: "ph-eyeglasses" },
  audio: { label: "生成音频", desc: "本机 TTS，整章或按场", icon: "ph-waveform" },
};
const CR_KEY = "novel-review:coldread";
const CR_DEFAULT = { engine: "local", ocModel: "" };
function loadCR() {
  try { return { ...CR_DEFAULT, ...JSON.parse(localStorage.getItem(CR_KEY) || "{}") }; }
  catch (e) { return { ...CR_DEFAULT }; }
}
function saveCR(cr) {
  try { localStorage.setItem(CR_KEY, JSON.stringify(cr)); } catch (e) { /* 隐私模式等，忽略 */ }
}
function setColdEngine(engine) { S.cr = { ...S.cr, engine }; saveCR(S.cr); render(); }

// ---------------------------------------------------------------- 状态

const S = {
  sec: "work", part: null, vol: null, ch: null, hist: [],
  book: null, level: null, config: null, loading: true,
  file: null, fileData: null, fedit: null,
  editingAddr: false, addr: "",
  flash: "", confirmDel: false,
  cmd: null, note: "", forceRegen: false,
  backfillTargets: null, fillIdx: "0",
  coldMode: "manuscript", cr: loadCR(),
  audioPerScene: false, voiceIdx: "0",
  job: null, jobPoll: null,
  vw: window.innerWidth, pane: "list",
};

function setState(patch) { Object.assign(S, patch); render(); }

function flash(msg) { setState({ flash: msg }); }
function fail(err) { flash("⚠ " + (err && err.message ? err.message : String(err))); }

// ---------------------------------------------------------------- 导航

function curKey() { return { sec: S.sec, part: S.part, vol: S.vol, ch: S.ch }; }

async function loadLevel(patch, pushHist) {
  if (pushHist) S.hist = [...S.hist, curKey()].slice(-30);
  Object.assign(S, patch, {
    file: null, fileData: null, fedit: null, cmd: null, note: "",
    flash: "", editingAddr: false, confirmDel: false, pane: "list",
    backfillTargets: null, loading: true,
  });
  render();
  try {
    const [level, book] = await Promise.all([
      API.level(S.sec, S.part, S.vol, S.ch),
      API.book(),
    ]);
    Object.assign(S, { level, book, loading: false });
  } catch (e) {
    Object.assign(S, { loading: false });
    fail(e);
  }
  render();
}

function goto(sec, part, vol, ch) { loadLevel({ sec, part, vol, ch }, true); }
function goUp() {
  if (S.ch != null) return goto(S.sec, S.part, S.vol, null);
  if (S.vol != null) return goto(S.sec, S.part, null, null);
  if (S.part != null) return goto(S.sec, null, null, null);
}
function goBack() {
  if (!S.hist.length) return;
  const prev = S.hist[S.hist.length - 1];
  S.hist = S.hist.slice(0, -1);
  loadLevel(prev, false);
}

function findChapterLoc(book, n) {
  for (const p of book.parts) for (const v of p.vols) if (v.chapters.some((c) => c.n === n)) return { part: p.n, vol: v.n };
  return null;
}
function parseAddr(str) {
  const s = str.trim();
  if (!s || !S.book) return;
  let sec = S.sec;
  if (s.includes("工作区")) sec = "work";
  else if (s.includes("规划")) sec = "plan";
  else if (s.includes("正文")) sec = "text";
  const pm = s.match(/第?0*(\d+)部/);
  const vm = s.match(/卷0*(\d+)/);
  const cm = (!/部$/.test(s) && !/卷0*\d+$/.test(s)) ? s.match(/(?:^|[/\s])0*(\d{1,4})\s*$/) : null;
  let part = null, vol = null, ch = null;
  if (cm) {
    const n = Number(cm[1]);
    const hit = findChapterLoc(S.book, n);
    if (hit) { part = hit.part; vol = hit.vol; ch = n; }
  } else if (vm) {
    for (const p of S.book.parts) for (const v of p.vols) if (v.n === Number(vm[1])) { part = p.n; vol = v.n; }
  } else if (pm) {
    const p = S.book.parts.find((p) => p.n === Number(pm[1]));
    if (p) part = p.n;
  }
  goto(sec, part, vol, ch);
}

// ---------------------------------------------------------------- 文件

function dirname(p) { const i = p.lastIndexOf("/"); return i < 0 ? "" : p.slice(0, i); }
function basename(p) { const i = p.lastIndexOf("/"); return i < 0 ? p : p.slice(i + 1); }

async function refreshAll(keepFile) {
  try {
    const [level, book] = await Promise.all([
      API.level(S.sec, S.part, S.vol, S.ch),
      API.book(),
    ]);
    S.level = level; S.book = book;
    if (keepFile && S.file) {
      try { S.fileData = await API.file(S.file); }
      catch (e) { S.file = null; S.fileData = null; }
    }
  } catch (e) { fail(e); }
  render();
}

async function selectFile(path) {
  setState({ pane: S.vw < 720 ? "preview" : S.pane });
  try {
    const data = await API.file(path);
    setState({ file: path, fileData: data, fedit: null, pane: S.vw < 720 ? "preview" : S.pane });
  } catch (e) { fail(e); }
}

function startFileEdit() {
  if (!S.fileData || S.fileData.kind === "audio" || S.fileData.kind === "binary") return;
  const text = S.fileData.text || "";
  setState({ fedit: { orig: S.file, name: basename(S.file), text, origText: text } });
}
function cancelFileEdit() { setState({ fedit: null }); }

async function saveFileEdit() {
  const fe = S.fedit;
  if (!fe) return;
  const name = fe.name.trim() || basename(fe.orig);
  const dir = dirname(fe.orig);
  const newPath = dir ? `${dir}/${name}` : name;
  try {
    await API.putFile(newPath, fe.text, fe.orig, false);
  } catch (e) {
    if (e.status === 409) {
      if (!confirm(`${newPath} 已存在，覆盖它？`)) return;
      try { await API.putFile(newPath, fe.text, fe.orig, true); }
      catch (e2) { return fail(e2); }
    } else return fail(e);
  }
  setState({ fedit: null, flash: `已保存 ${newPath}` });
  await refreshAll(false);
  await selectFile(newPath);
}

async function copyFile() {
  try { await navigator.clipboard.writeText(S.fileData.text || ""); flash(`已复制 ${basename(S.file)}`); }
  catch (e) { flash("复制失败：浏览器拒绝了剪贴板权限"); }
}

async function copyPath() {
  const path = rawPath();
  try { await navigator.clipboard.writeText(path); flash(`已复制路径：${path}`); }
  catch (e) { flash("复制失败：浏览器拒绝了剪贴板权限"); }
}

async function doDelete() {
  try {
    const r = await API.deleteFile(S.file);
    setState({ confirmDel: false, file: null, fileData: null, flash: `已删除，移到 ${r.trashed_to}` });
    await refreshAll(false);
  } catch (e) { setState({ confirmDel: false }); fail(e); }
}

// ---------------------------------------------------------------- 任务

function stopJobPoll() { if (S.jobPoll) { clearInterval(S.jobPoll); S.jobPoll = null; } }

async function runJob(kind, extra) {
  stopJobPoll();
  setState({ job: { kind, status: "running", log: "" }, flash: "" });
  try {
    const r = await API.createJob({ kind, part: S.part, vol: S.vol, ch: S.ch, ...extra });
    const poll = setInterval(async () => {
      try {
        const j = await API.getJob(r.job_id);
        S.job = j;
        if (j.status !== "running") {
          stopJobPoll();
          await onJobDone(kind, j);
        }
        render();
      } catch (e) { stopJobPoll(); fail(e); }
    }, 800);
    S.jobPoll = poll;
  } catch (e) {
    setState({ job: null });
    fail(e);
  }
}

async function onJobDone(kind, job) {
  await refreshAll(true);
  if ((kind === "outline_ch" || kind === "draft") && (job.status === "ok" || job.status === "warn")) {
    const archiveName = ARCHIVE_NAME[kind];
    const group = (S.level.file_groups || []).find((g) => g.name === "00_提示词");
    const f = group && group.files.find((f) => f.name === archiveName);
    if (f) await selectFile(f.path);
  }
  render();
}

// ---------------------------------------------------------------- 回填

async function loadBackfillTargets() {
  try {
    const r = await API.backfillTargets(S.part, S.vol, S.ch);
    setState({ backfillTargets: r.targets, fillIdx: "0" });
  } catch (e) { fail(e); }
}

async function doBackfill(cold) {
  if (!S.file) return flash("请先在左侧打开一个文件作为回填来源");
  const targets = S.backfillTargets || [];
  const t = targets[Number(S.fillIdx)];
  if (!t) return flash("没有可回填的目标");
  try {
    const body = { src: S.file, target_id: t.id, part: S.part, vol: S.vol, ch: S.ch };
    if (cold) { body.cold = true; body.engine = S.cr.engine; if (S.cr.ocModel) body.oc_models = [S.cr.ocModel]; }
    const r = await API.backfill(body);
    let msg = `已把 ${basename(S.file)} 写入 ${r.target}`;
    if (r.job_id) msg += "，冷读已开始";
    if (r.job_error) msg += `（冷读未能启动：${r.job_error}）`;
    setState({ flash: msg });
    await refreshAll(true);
    if (r.job_id) {
      S.job = { kind: "cold", status: "running", log: "" };
      S.jobPoll = setInterval(async () => {
        try {
          const j = await API.getJob(r.job_id);
          S.job = j;
          if (j.status !== "running") { stopJobPoll(); await onJobDone("cold", j); }
          render();
        } catch (e) { stopJobPoll(); }
      }, 800);
    }
  } catch (e) { fail(e); }
}

// ---------------------------------------------------------------- 派生显示

function rawPath() {
  if (!S.book) return "";
  const bits = [S_LABEL()];
  const p = S.book.parts.find((p) => p.n === S.part);
  if (p) bits.push(p.label);
  const v = p && p.vols.find((v) => v.n === S.vol);
  if (v) bits.push(v.label);
  if (S.ch != null) bits.push(String(S.ch).padStart(4, "0"));
  return bits.join("/");
}
function S_LABEL() { return { work: "工作区", plan: "规划", text: "正文" }[S.sec]; }

function crumbs() {
  const list = [{ label: S_LABEL(), go: () => goto(S.sec, null, null, null) }];
  const p = S.book && S.book.parts.find((p) => p.n === S.part);
  if (p) list.push({ label: `${p.label}${p.title ? " " + p.title : ""}`, go: () => goto(S.sec, S.part, null, null) });
  const v = p && p.vols.find((v) => v.n === S.vol);
  if (v) list.push({ label: `${v.label}${v.title ? " " + v.title : ""}`, go: () => goto(S.sec, S.part, S.vol, null) });
  if (S.ch != null) list.push({ label: `${String(S.ch).padStart(4, "0")}`, go: () => {} });
  return list;
}

// ---------------------------------------------------------------- 渲染：小组件

const ROW_BASE = "display:flex;align-items:center;gap:var(--space-3);width:100%;padding:var(--space-2) var(--space-4);border:0;border-radius:var(--radius-md);font:inherit;font-size:12.5px;cursor:pointer;text-align:left;";
function rowBtn(active, onClick, ...children) {
  const style = ROW_BASE + (active
    ? "background:var(--color-accent-900);color:var(--color-text);box-shadow:0 0 0 1px var(--color-accent-700)"
    : "background:var(--color-surface);color:var(--color-neutral-300);box-shadow:var(--shadow-sm)");
  return h("button", { style, onClick }, ...children);
}
const CMD_BASE = "display:flex;align-items:center;gap:var(--space-3);width:100%;padding:var(--space-3);border:0;border-radius:var(--radius-md);font:inherit;cursor:pointer;text-align:left;";
function cmdBtn(active, onClick, ...children) {
  const style = CMD_BASE + (active
    ? "background:var(--color-accent-900);color:var(--color-text);box-shadow:0 0 0 1px var(--color-accent-700)"
    : "background:transparent;color:var(--color-neutral-300)");
  return h("button", { style, onClick }, ...children);
}
function ghostIconBtn(iconName, title, onClick, disabled) {
  return h("button", { class: "btn btn-ghost btn-icon", title, onClick, disabled }, icon(iconName));
}
function tag(label, kind) { return h("span", { class: `tag tag-${kind}` }, label); }

function flashBar() {
  if (!S.flash) return null;
  return h("div", {
    style: "display:flex;align-items:flex-start;gap:var(--space-2);font-size:12.5px;color:var(--color-accent-300);"
      + "background:var(--color-accent-900);border-radius:var(--radius-md);padding:var(--space-3) var(--space-4);word-break:break-all",
  },
    icon("ph-info", 14),
    h("span", { style: "flex:1" }, S.flash),
    h("button", {
      style: "border:0;background:transparent;color:inherit;cursor:pointer;padding:0",
      onClick: () => setState({ flash: "" }),
    }, icon("ph-x", 13)));
}

// ---------------------------------------------------------------- 渲染：侧栏 / 顶栏

function sidebar(mobile) {
  const navItems = SEC_ORDER.map((id) => h("button", {
    style: ROW_BASE.replace("font-size:12.5px", "font-size:13.5px")
      + (S.sec === id ? "background:var(--color-accent-900);color:var(--color-text)" : "background:transparent;color:var(--color-neutral-300)"),
    onClick: () => goto(id, S.part, S.vol, S.ch),
  },
    h("span", { style: `width:3px;height:16px;border-radius:var(--radius-sm);background:${S.sec === id ? "var(--color-accent)" : "transparent"};flex:none` }),
    icon(SEC_ICON[id], 16),
    h("span", { style: "flex:1;text-align:left" }, { work: "工作区", plan: "规划", text: "正文" }[id]),
  ));
  if (mobile) {
    return h("nav", {
      style: "position:sticky;bottom:0;z-index:20;display:flex;gap:var(--space-1);background:var(--color-neutral-900);"
        + "box-shadow:inset 0 1px 0 var(--color-neutral-800);padding:var(--space-1) var(--space-2) calc(var(--space-1) + env(safe-area-inset-bottom))",
    }, SEC_ORDER.map((id) => h("button", {
      style: "flex:1;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:2px;min-height:52px;"
        + "border:0;border-radius:var(--radius-md);background:transparent;font:inherit;cursor:pointer;color:"
        + (S.sec === id ? "var(--color-accent-300)" : "var(--color-neutral-400)"),
      onClick: () => goto(id, S.part, S.vol, S.ch),
    }, icon(SEC_ICON[id], 20), h("span", { style: "font-size:11px" }, { work: "工作区", plan: "规划", text: "正文" }[id]))));
  }
  const meta = S.book ? S.book.meta : { parts: 0, vols: 0, chapters: 0 };
  return h("aside", {
    style: "flex:0 1 216px;min-width:190px;background:var(--color-neutral-900);box-shadow:inset -1px 0 0 var(--color-neutral-800);"
      + "padding:var(--space-8) var(--space-6);display:flex;flex-direction:column;gap:var(--space-8)",
  },
    h("div", {},
      h("div", { style: "display:flex;align-items:center;gap:var(--space-2);font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:var(--color-neutral-500)" },
        icon("ph-eye", 14), "审查台"),
      h("div", { style: "font-family:var(--font-heading);font-weight:var(--font-heading-weight);font-size:19px;margin-top:var(--space-2);line-height:1.3" },
        S.book ? S.book.title : "…"),
      h("div", { style: "font-size:12px;color:var(--color-neutral-500);margin-top:var(--space-1)" },
        `${meta.parts} 部 · ${meta.vols} 卷 · ${meta.chapters} 章`)),
    h("nav", { style: "display:flex;flex-direction:column;gap:var(--space-1)" }, navItems),
    h("div", { style: "margin-top:auto;display:flex;flex-direction:column;gap:var(--space-3)" },
      h("div", { style: "display:flex;align-items:center;gap:var(--space-2);font-size:11px;color:var(--color-neutral-500);letter-spacing:.08em" },
        icon("ph-rss-simple", 13), "播客订阅"),
      h("code", { class: "mono", style: "font-size:11px;color:var(--color-accent-300);background:var(--color-surface);border-radius:var(--radius-sm);padding:var(--space-2) var(--space-3);word-break:break-all" },
        S.book ? S.book.feed_url : "")));
}

function topbarMobile() {
  return h("header", {
    style: "position:sticky;top:0;z-index:20;display:flex;align-items:center;gap:var(--space-3);padding:var(--space-4) var(--space-6);"
      + "background:var(--color-neutral-900);box-shadow:inset 0 -1px 0 var(--color-neutral-800)",
  }, icon("ph-eye", 16),
    h("span", { style: "flex:1;min-width:0;font-family:var(--font-heading);font-weight:var(--font-heading-weight);font-size:16px" },
      S.book ? S.book.title : "…"),
    tag(S_LABEL(), "accent"));
}

// ---------------------------------------------------------------- 渲染：地址栏

function addressBar(mobile) {
  const cr = crumbs();
  const addrInner = S.editingAddr
    ? h("input", {
        class: "input mono", value: S.addr, autofocus: true,
        style: "flex:1 1 auto;min-width:0;font-size:12.5px;padding:var(--space-2) var(--space-3)",
        placeholder: "如 03_规划/01_第01部/01_卷01，或直接输入章号 7",
        onInput: (e) => { S.addr = e.target.value; },
        onKeyDown: (e) => { if (e.key === "Enter") parseAddr(S.addr); if (e.key === "Escape") setState({ editingAddr: false }); },
        onBlur: () => setState({ editingAddr: false }),
      })
    : h("div", {
        onClick: () => setState({ editingAddr: true, addr: rawPath() }), title: "点击输入路径",
        style: "flex:1 1 auto;min-width:0;display:flex;flex-wrap:wrap;align-items:center;gap:var(--space-1);cursor:text;padding:var(--space-1) 0",
      }, cr.map((c, i) => h("span", { style: "display:flex;align-items:center;gap:var(--space-1)" },
          i > 0 ? icon("ph-caret-right", 11) : null,
          h("button", {
            style: "border:0;border-radius:var(--radius-sm);font:inherit;font-size:12.5px;cursor:pointer;padding:var(--space-1) var(--space-2);background:transparent;"
              + (i === cr.length - 1 ? "color:var(--color-text);font-weight:500" : "color:var(--color-neutral-400)"),
            onClick: (e) => { e.stopPropagation(); if (i < cr.length - 1) c.go(); },
          }, c.label))));
  return h("div", {
    style: "display:flex;align-items:center;gap:var(--space-2);background:var(--color-neutral-900);box-shadow:0 0 0 1px var(--color-neutral-800);"
      + "border-radius:var(--radius-md);padding:var(--space-1) var(--space-2);min-height:40px",
  },
    ghostIconBtn("ph-arrow-left", "后退", goBack, !S.hist.length),
    ghostIconBtn("ph-arrow-up", "上一级", goUp, S.part == null),
    addrInner,
    mobile ? null : h("span", { class: "mono", style: "flex:0 1 auto;min-width:0;font-size:11px;color:var(--color-neutral-500);overflow:hidden;text-overflow:ellipsis;white-space:nowrap" }, rawPath()),
    ghostIconBtn("ph-copy", "复制路径", copyPath));
}

// ---------------------------------------------------------------- 渲染：标题块

function titleBlock() {
  if (!S.level) return h("div", {}, "加载中…");
  const l = S.level;
  const head = h("div", { style: "flex:1 1 260px;min-width:0" },
    h("div", { style: "font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:var(--color-neutral-500)" }, l.kicker),
    h("h1", { style: "font-family:var(--font-heading);font-weight:var(--font-heading-weight);font-size:23px;margin:var(--space-2) 0 var(--space-1)" }, l.title),
    h("div", { style: "font-size:12.5px;color:var(--color-neutral-400)" }, l.meta));
  let nav = null;
  if (l.level === "ch") {
    nav = h("div", { style: "display:flex;gap:var(--space-2)" },
      h("button", {
        class: "btn btn-ghost", style: "font-size:12px;padding:var(--space-2) var(--space-4)",
        disabled: l.chapter.prev == null, onClick: () => goto(S.sec, S.part, S.vol, l.chapter.prev),
      }, icon("ph-arrow-left", 14), "上一章"),
      h("button", {
        class: "btn btn-ghost", style: "font-size:12px;padding:var(--space-2) var(--space-4)",
        disabled: l.chapter.next == null, onClick: () => goto(S.sec, S.part, S.vol, l.chapter.next),
      }, "下一章", icon("ph-arrow-right", 14)));
  }
  return h("div", { style: "display:flex;flex-wrap:wrap;align-items:flex-end;gap:var(--space-6)" }, head, nav);
}

// ---------------------------------------------------------------- 渲染：文件列表 + 下级导航

function fileList() {
  const l = S.level;
  const groups = (l.file_groups || []).map((g) => h("div", {},
    h("div", { style: "display:flex;align-items:center;gap:var(--space-2);font-size:11px;letter-spacing:.1em;color:var(--color-neutral-500);padding:0 var(--space-1) var(--space-3)" },
      icon(g.icon, 13), g.name),
    h("div", { style: "display:flex;flex-direction:column;gap:var(--space-3)" },
      g.files.map((f) => rowBtn(S.file === f.path, () => selectFile(f.path),
        icon(f.icon, 14),
        h("span", { style: "flex:1;min-width:0;text-align:left;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" }, f.name),
        h("span", { style: "font-size:11px;color:var(--color-neutral-500);flex:none" }, f.size))))));

  let childrenBlock = null;
  if (l.children) {
    const labelMap = { root: "部", part: "卷", vol: "章节" };
    childrenBlock = h("div", {},
      h("div", { style: "display:flex;align-items:center;gap:var(--space-2);font-size:11px;letter-spacing:.1em;color:var(--color-neutral-500);padding:0 var(--space-1) var(--space-3)" },
        icon("ph-list-bullets", 13), labelMap[l.level] || ""),
      h("div", { style: "display:flex;flex-direction:column;gap:var(--space-2)" },
        l.children.map((c) => h("button", {
          style: "display:flex;flex-wrap:nowrap;align-items:center;gap:var(--space-3);width:100%;padding:var(--space-3) var(--space-4);"
            + "min-height:44px;border:0;border-radius:var(--radius-md);font:inherit;cursor:pointer;background:var(--color-surface);"
            + "color:var(--color-text);box-shadow:var(--shadow-sm);text-align:left",
          onClick: () => {
            if (l.level === "root") goto(S.sec, c.n, null, null);
            else if (l.level === "part") goto(S.sec, S.part, c.n, null);
            else goto(S.sec, S.part, S.vol, c.n);
          },
        },
          h("span", { class: "mono", style: "font-size:11.5px;color:var(--color-neutral-500);flex:none;min-width:44px" }, c.code),
          h("span", { style: "flex:1 1 0;min-width:0;font-size:13.5px;font-weight:500;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" },
            c.title || "（无标题）"),
          h("span", { class: `tag tag-${c.tag}`, style: "flex:none" }, c.state),
          icon("ph-caret-right", 13)))));
  }

  const isEmpty = groups.length === 0 && !childrenBlock;
  return h("div", { style: "flex:1 1 240px;min-width:220px;display:flex;flex-direction:column;gap:var(--space-8)" },
    groups, childrenBlock,
    isEmpty ? h("div", { style: "font-size:13px;color:var(--color-neutral-500);padding:var(--space-6);background:var(--color-surface);border-radius:var(--radius-md)" },
      S.sec === "text" ? "本级尚无定稿正文。" : "本级暂无内容。") : null);
}

// ---------------------------------------------------------------- 渲染：预览面板

function previewPanel(readOnly) {
  if (!S.file || !S.fileData) return null;
  const fe = S.fedit;
  const header = fe
    ? h("span", { style: "flex:1 1 auto;min-width:0;display:flex;flex-wrap:wrap;align-items:center;gap:var(--space-3)" },
        h("input", {
          class: "input mono", value: fe.name, title: "改文件名即另存为新文件",
          style: "flex:1 1 180px;min-width:0;font-size:12.5px;padding:var(--space-1) var(--space-3)",
          onInput: (e) => { S.fedit.name = e.target.value; },
        }),
        (fe.text !== fe.origText || fe.name !== basename(fe.orig)) ? tag("未保存", "accent") : null,
        h("span", { style: "display:flex;gap:var(--space-2)" },
          h("button", { class: "btn btn-ghost", style: "font-size:12px;padding:var(--space-1) var(--space-3)", onClick: cancelFileEdit }, "取消"),
          h("button", { class: "btn btn-primary", style: "font-size:12px;padding:var(--space-1) var(--space-3)", onClick: saveFileEdit },
            icon("ph-floppy-disk", 14), fe.name.trim() && fe.name.trim() !== basename(fe.orig) ? "另存为" : "保存")))
    : h("span", { style: "flex:1 1 auto;min-width:0;display:flex;flex-wrap:wrap;align-items:center;gap:var(--space-3)" },
        h("span", { style: "flex:1 1 160px;min-width:0;font-size:13px;font-weight:500;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" }, basename(S.file)),
        h("span", { style: "font-size:11px;color:var(--color-neutral-500)" }, S.fileData.desc || (S.fileData.truncated ? "（只显示前 512 KiB）" : "")),
        h("span", { style: "display:flex;gap:var(--space-2)" },
          (!readOnly && S.fileData.kind !== "audio" && S.fileData.kind !== "binary")
            ? h("button", { class: "btn btn-secondary", style: "font-size:12px;padding:var(--space-1) var(--space-3)", onClick: startFileEdit },
                icon("ph-pencil-simple", 14), "编辑") : null,
          S.fileData.kind !== "audio" ? h("button", { class: "btn btn-ghost", style: "font-size:12px;padding:var(--space-1) var(--space-3)", onClick: copyFile },
            icon("ph-copy", 14), "复制原文") : null,
          !readOnly ? h("button", { class: "btn btn-ghost btn-icon", title: "删除文件", onClick: () => setState({ confirmDel: true }) },
            icon("ph-trash", 14)) : null));

  let body;
  if (fe) {
    body = h("div", { style: "padding:var(--space-4) var(--space-6) var(--space-6);display:flex;flex-direction:column;gap:var(--space-3)" },
      h("textarea", {
        class: "input mono", placeholder: "可直接粘贴云端输出",
        style: "min-height:480px;font-size:13px;line-height:1.7",
        onInput: (e) => { S.fedit.text = e.target.value; },
        onKeyDown: (e) => { if ((e.metaKey || e.ctrlKey) && e.key === "s") { e.preventDefault(); saveFileEdit(); } },
      }, fe.text),
      h("div", { style: "font-size:11.5px;color:var(--color-neutral-500)" },
        `${S.file} · Ctrl/⌘+S 保存 · 改文件名即另存为新文件，原文件保留`));
  } else if (S.fileData.kind === "audio") {
    body = h("div", { style: "padding:var(--space-8);display:flex;align-items:center;gap:var(--space-4)" },
      S.fileData.audio_url
        ? h("audio", { controls: true, preload: "none", src: S.fileData.audio_url, style: "flex:1" })
        : h("span", { style: "color:var(--color-neutral-500)" }, "找不到音频文件"));
  } else if (S.fileData.kind === "binary") {
    body = h("div", { style: "padding:var(--space-8);color:var(--color-neutral-500)" }, "该文件类型不支持预览。");
  } else if (S.fileData.kind === "prose") {
    body = h("div", {
      style: "padding:var(--space-8) var(--space-8);font-size:15.5px;line-height:1.95;color:var(--color-neutral-200);"
        + "white-space:pre-wrap;max-width:40em;max-height:620px;overflow:auto",
    }, S.fileData.text);
  } else {
    body = h("div", {
      class: "mono",
      style: "padding:var(--space-6) var(--space-8);font-size:13px;line-height:1.75;color:var(--color-neutral-200);"
        + "white-space:pre-wrap;max-height:620px;overflow:auto",
    }, S.fileData.text);
  }

  const panel = h("div", { style: "flex:3 1 400px;min-width:280px;background:var(--color-surface);box-shadow:var(--shadow-sm);border-radius:var(--radius-lg);overflow:hidden" },
    h("div", { style: "display:flex;flex-wrap:wrap;align-items:center;gap:var(--space-3);padding:var(--space-4) var(--space-6);box-shadow:inset 0 -1px 0 var(--color-neutral-800)" }, header),
    body);

  if (!S.confirmDel) return panel;
  return [panel, deleteDialog()];
}

function deleteDialog() {
  return h("div", {
    class: "dialog-backdrop", style: "position:fixed;inset:0;z-index:50;display:flex;align-items:center;justify-content:center;padding:var(--space-6)",
    onClick: () => setState({ confirmDel: false }),
  }, h("div", { class: "dialog", style: "max-width:420px;width:100%", onClick: (e) => e.stopPropagation() },
    h("div", { class: "dialog-title", style: "display:flex;align-items:center;gap:var(--space-3)" }, icon("ph-trash", 18), "删除文件"),
    h("div", { class: "dialog-body", style: "display:flex;flex-direction:column;gap:var(--space-3)" },
      h("div", {}, "确定删除 ", h("strong", { style: "font-weight:500" }, basename(S.file)), "？"),
      h("div", { class: "mono", style: "font-size:11.5px;color:var(--color-neutral-500);word-break:break-all" }, S.file),
      h("div", { style: "font-size:12px;color:var(--color-neutral-400)" }, "文件会移到 .trash/，可在服务端手动恢复。")),
    h("div", { class: "dialog-actions", style: "display:flex;justify-content:flex-end;gap:var(--space-3)" },
      h("button", { class: "btn btn-ghost", style: "font-size:13px", onClick: () => setState({ confirmDel: false }) }, "取消"),
      h("button", { class: "btn btn-primary", style: "font-size:13px", onClick: doDelete }, icon("ph-trash", 15), "删除"))));
}

// ---------------------------------------------------------------- 渲染：指令面板

function jobLogBox() {
  if (!S.job) return null;
  const statusLabel = { running: "运行中…", ok: "完成", warn: "完成（有提示，见日志）", fail: "失败" }[S.job.status] || S.job.status;
  return h("div", { style: "display:flex;flex-direction:column;gap:var(--space-2)" },
    h("div", { style: "font-size:12px;color:var(--color-neutral-400)" }, `任务状态：${statusLabel}`),
    S.job.log ? h("div", {
      class: "mono",
      style: "font-size:11.5px;line-height:1.6;color:var(--color-neutral-200);white-space:pre-wrap;background:var(--color-neutral-900);"
        + "border-radius:var(--radius-md);padding:var(--space-3);max-height:220px;overflow:auto",
    }, S.job.log) : null);
}

function cmdPanel() {
  const l = S.level;
  const ids = LEVEL_CMDS[l.level] || [];
  const cmdId = ids.includes(S.cmd) ? S.cmd : ids[0];
  if (S.cmd !== cmdId) S.cmd = cmdId;

  const levelTag = { root: "全书", part: "部", vol: "卷", ch: "章" }[l.level];
  const list = h("div", { style: "display:flex;flex-direction:column;gap:var(--space-2);padding:var(--space-4)" },
    ids.map((id) => cmdBtn(id === cmdId, () => setState({ cmd: id, job: null, flash: "" }),
      icon(CMD_META[id].icon, 17),
      h("span", { style: "flex:1;min-width:0;text-align:left" },
        h("span", { style: "display:block;font-size:13px;font-weight:500" }, CMD_META[id].label),
        h("span", { style: "display:block;font-size:11.5px;color:var(--color-neutral-500);margin-top:2px" }, CMD_META[id].desc)))));

  let body;
  if (["outline_book", "outline_part", "outline_vol", "check"].includes(cmdId)) {
    body = h("div", { style: "display:flex;flex-direction:column;gap:var(--space-4)" },
      h("div", { style: "font-size:12.5px;color:var(--color-neutral-400)" }, "这一项还没实现，点「生成」会看到服务端的占位错误。"),
      h("button", { class: "btn btn-primary", style: "font-size:13px", onClick: () => runJob(cmdId, {}) }, icon("ph-play", 15), "生成"),
      jobLogBox());
  } else if (cmdId === "outline_ch" || cmdId === "draft") {
    body = h("div", { style: "display:flex;flex-direction:column;gap:var(--space-4)" },
      h("div", { class: "field" }, h("label", {}, "附加要求（复制时才拼进去，不写进存档）"),
        h("textarea", {
          class: "input", placeholder: "例：本章收束伏笔 B，节奏偏紧", style: "font-size:13px;min-height:60px",
          onInput: (e) => { S.note = e.target.value; },
        }, S.note)),
      h("label", { class: "radio", style: "font-size:12.5px" },
        h("input", { type: "checkbox", checked: S.forceRegen || null, onChange: (e) => setState({ forceRegen: e.target.checked }) }),
        h("span", { class: "dot" }), "强制覆盖已存在的提示词存档"),
      h("div", {}, h("button", {
        class: "btn btn-primary", style: "font-size:13px",
        onClick: () => runJob(cmdId, { force: S.forceRegen }),
      }, icon("ph-play", 15), "生成并存档")),
      jobLogBox(),
      (S.file && S.fileData && S.fileData.text != null) ? h("div", { style: "display:flex;flex-direction:column;gap:var(--space-3)" },
        h("div", { class: "mono", style: "font-size:12px;line-height:1.7;color:var(--color-neutral-200);white-space:pre-wrap;background:var(--color-neutral-900);border-radius:var(--radius-md);padding:var(--space-4);max-height:260px;overflow:auto" },
          S.fileData.text),
        h("button", {
          class: "btn btn-primary", style: "font-size:13px",
          onClick: async () => {
            const extra = S.note.trim() ? `\n\n【附加要求】${S.note.trim()}` : "";
            try { await navigator.clipboard.writeText(S.fileData.text + extra); flash("提示词已复制，可直接粘贴到云端模型"); }
            catch (e) { flash("复制失败：浏览器拒绝了剪贴板权限"); }
          },
        }, icon("ph-copy", 15), "复制提示词")) : null);
  } else if (cmdId === "backfill") {
    if (!S.backfillTargets) { loadBackfillTargets(); }
    const targets = S.backfillTargets || [];
    body = h("div", { style: "display:flex;flex-direction:column;gap:var(--space-4)" },
      h("div", { class: "field" }, h("label", {}, "回填到"),
        h("select", {
          class: "input", style: "font-size:13px",
          onChange: (e) => setState({ fillIdx: e.target.value }),
        }, targets.map((t, i) => h("option", { value: String(i), selected: String(i) === S.fillIdx || null }, t.label)))),
      h("div", { class: "field" }, h("label", {}, "来源（左侧当前打开的文件）"),
        h("div", { class: "mono", style: "font-size:12.5px;color:var(--color-neutral-200);background:var(--color-neutral-900);border-radius:var(--radius-md);padding:var(--space-2) var(--space-3);word-break:break-all" },
          S.file || "（未打开文件）")),
      h("div", { style: "display:flex;flex-wrap:wrap;gap:var(--space-3)" },
        h("button", { class: "btn btn-primary", style: "font-size:13px", onClick: () => doBackfill(false) }, icon("ph-arrow-square-in", 15), "回填"),
        l.level === "ch" ? h("button", { class: "btn btn-ghost", style: "font-size:13px", onClick: () => doBackfill(true) }, icon("ph-eyeglasses", 15), "回填后冷读") : null));
  } else if (cmdId === "cold") {
    const cr = S.cr;
    body = h("div", { style: "display:flex;flex-direction:column;gap:var(--space-4)" },
      h("div", { class: "field" }, h("label", {}, "冷读对象"),
        h("select", { class: "input", style: "font-size:13px", onChange: (e) => setState({ coldMode: e.target.value }) },
          h("option", { value: "manuscript", selected: S.coldMode === "manuscript" || null }, "正文"),
          h("option", { value: "outline", selected: S.coldMode === "outline" || null }, "细纲"))),
      h("div", { class: "field" }, h("label", {}, "冷读模型"),
        h("div", { class: "seg" },
          h("label", { class: "seg-opt" }, h("input", { type: "radio", name: "cold-engine", checked: cr.engine === "local" || null, onChange: () => setColdEngine("local") }), "本地模型"),
          h("label", { class: "seg-opt" }, h("input", { type: "radio", name: "cold-engine", checked: cr.engine === "opencode" || null, onChange: () => setColdEngine("opencode") }), "OpenCode"))),
      cr.engine === "local"
        ? h("div", { style: "font-size:12px;color:var(--color-neutral-400)" },
            `本地端点：${S.config ? S.config.llm.base_url : "…"} · 模型：${S.config ? S.config.llm.model : "…"}（见 llm.config.toml，此处只读）`)
        : h("div", { class: "field" }, h("label", {}, "OpenCode 模型（留空用配置默认）"),
            h("input", {
              class: "input mono", style: "font-size:12.5px", value: cr.ocModel,
              onInput: (e) => { S.cr.ocModel = e.target.value; saveCR(S.cr); },
            })),
      h("div", { style: "font-size:11.5px;color:var(--color-neutral-500)" }, "设置保存在本机浏览器，下次沿用。结果追加进本章「校验记录」。"),
      h("div", {}, h("button", { class: "btn btn-primary", style: "font-size:13px", onClick: () => runJob("cold", { mode: S.coldMode, engine: cr.engine, oc_models: cr.ocModel ? [cr.ocModel] : undefined }) }, icon("ph-play", 15), "开始冷读")),
      jobLogBox());
  } else if (cmdId === "audio") {
    body = h("div", { style: "display:flex;flex-direction:column;gap:var(--space-4)" },
      h("div", { class: "field" }, h("label", {}, "切分"),
        h("div", { class: "seg" },
          h("label", { class: "seg-opt" }, h("input", { type: "radio", name: "audio-mode", checked: !S.audioPerScene || null, onChange: () => setState({ audioPerScene: false }) }), "整章"),
          h("label", { class: "seg-opt" }, h("input", { type: "radio", name: "audio-mode", checked: S.audioPerScene || null, onChange: () => setState({ audioPerScene: true }) }), "按场"))),
      h("div", { class: "field" }, h("label", {}, "音色"),
        h("select", { class: "input", style: "font-size:13px", onChange: (e) => setState({ voiceIdx: e.target.value }) },
          (S.config ? S.config.voices : []).map((v, i) => h("option", { value: String(i), selected: String(i) === S.voiceIdx || null }, v.label)))),
      h("div", {}, h("button", {
        class: "btn btn-primary", style: "font-size:13px",
        onClick: () => {
          const voice = S.config.voices[Number(S.voiceIdx)].id || undefined;
          runJob("audio", { per_scene: S.audioPerScene, voice });
        },
      }, icon("ph-play", 15), "生成音频")),
      jobLogBox());
  }

  return h("div", { style: "flex:1 1 300px;min-width:280px;max-width:420px;background:var(--color-surface);box-shadow:var(--shadow-sm);border-radius:var(--radius-lg);overflow:hidden" },
    h("div", { style: "display:flex;align-items:center;gap:var(--space-3);padding:var(--space-4) var(--space-6);box-shadow:inset 0 -1px 0 var(--color-neutral-800)" },
      icon("ph-terminal-window", 15), h("span", { style: "flex:1;font-size:13px;font-weight:500" }, "指令"), tag(levelTag, "accent")),
    list,
    h("div", { style: "display:flex;flex-direction:column;gap:var(--space-4);padding:var(--space-4) var(--space-6) var(--space-6);box-shadow:inset 0 1px 0 var(--color-neutral-800)" }, body));
}

// ---------------------------------------------------------------- 顶层渲染

function render() {
  const root = document.getElementById("app");
  root.innerHTML = "";
  const mobile = S.vw < 720;
  const readOnly = !!(S.config && S.config.read_only);

  if (!S.book || !S.level) {
    root.appendChild(h("div", { style: "padding:var(--space-8);color:var(--color-neutral-500)" }, S.loading ? "加载中…" : "加载失败"));
    return;
  }

  const l = S.level;
  const panes = [{ id: "list", label: "浏览", icon: "ph-list-bullets" }];
  if (S.file) panes.push({ id: "preview", label: "预览", icon: "ph-file-text" });
  if (S.sec === "work") panes.push({ id: "cmd", label: "指令", icon: "ph-terminal-window" });
  let pane = S.pane;
  if (pane === "preview" && !S.file) pane = "list";
  if (pane === "cmd" && S.sec !== "work") pane = "list";

  const mainChildren = [
    addressBar(mobile), titleBlock(),
    mobile ? h("div", { class: "seg", style: "display:flex;width:100%" },
      panes.map((p) => h("label", { class: "seg-opt", style: "flex:1;justify-content:center;min-height:44px;font-size:13.5px" },
        h("input", { type: "radio", name: "pane", checked: pane === p.id || null, onChange: () => setState({ pane: p.id }) }),
        icon(p.icon, 15), p.label))) : null,
    (mobile && S.flash && pane !== "cmd") ? flashBar() : null,
    h("div", { style: "display:flex;flex-wrap:wrap;gap:var(--space-6);align-items:flex-start" },
      (!mobile || pane === "list") ? fileList() : null,
      (S.file && (!mobile || pane === "preview")) ? previewPanel(readOnly) : null,
      (!readOnly && S.sec === "work" && (!mobile || pane === "cmd")) ? cmdPanel() : null),
  ];
  if (!mobile && S.flash) mainChildren.splice(2, 0, flashBar());

  const main = h("main", {
    style: mobile
      ? "flex:1 1 auto;min-width:0;padding:var(--space-4) var(--space-4) var(--space-8);display:flex;flex-direction:column;gap:var(--space-4)"
      : "flex:1 1 600px;min-width:0;padding:var(--space-6) var(--space-8) calc(var(--space-8) * 2.5);display:flex;flex-direction:column;gap:var(--space-6)",
  }, mainChildren);

  const rootStyle = mobile
    ? "display:flex;flex-direction:column;min-height:100vh"
    : "display:flex;flex-wrap:wrap;align-items:stretch;min-height:100vh";
  const wrap = h("div", { style: rootStyle });
  if (mobile) { wrap.appendChild(topbarMobile()); wrap.appendChild(main); wrap.appendChild(sidebar(true)); }
  else { wrap.appendChild(sidebar(false)); wrap.appendChild(main); }
  root.appendChild(wrap);
}

// ---------------------------------------------------------------- 启动

window.addEventListener("resize", () => { S.vw = window.innerWidth; render(); });

(async function init() {
  try {
    S.config = await API.config();
  } catch (e) { /* 配置读不到也不阻塞浏览 */ }
  await loadLevel({ sec: "work", part: null, vol: null, ch: null }, false);
})();
