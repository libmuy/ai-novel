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
  withdraw: (body) => fetchJSON("POST", "/api/withdraw", body),
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
  file: null, fileData: null, fedit: null, fileToken: 0,
  editingAddr: false, addr: "",
  flash: "", confirmDel: false,
  cmd: null, note: "", forceRegen: false,
  backfillTargets: null, fillIdx: "0",
  coldMode: "manuscript", cr: loadCR(),
  audioPerScene: false, voiceIdx: "0",
  job: null, jobPoll: null, withdrawing: false,
  vw: window.innerWidth, pane: "list",
};

function setState(patch) { Object.assign(S, patch); render(); }

function flash(msg) { setState({ flash: msg }); }
function fail(err) { flash("⚠ " + (err && err.message ? err.message : String(err))); }

// ---------------------------------------------------------------- 迷你播放器
//
// 音频元素与播放条挂在 index.html 的 #app 之外，render() 永远碰不到它们，
// 所以切章 / 切区 / 切文件 / 任务轮询（每 800ms 一次 render）都不会中断播放。
// 播放状态 P 同样放在 S 之外：loadLevel() 的 Object.assign 会重置 S 的字段。
//
// 播放条默认收起 = 只留一个圆钮（进度环 + 随音量起伏的 5 根竖条），点圆才展开进度行
// 与跳转键——底部导航吸在最下面，浮条再加两行会让正文最后几行被顶起来。
// 收起/展开只存在内存里，刷新即回默认收起。
// 圆里的竖条有两套驱动：非 iOS 接 Web Audio 取真实振幅；iOS 只用 CSS 合成动画
//（iOS 退后台会 suspend AudioContext，元素一旦接进去就撤不回原生输出 → 锁屏会静音）。

const AUDIO = document.getElementById("player-audio");
const PL = document.getElementById("miniplayer");
const P = { url: "", title: "", sub: "", album: "", dur: 0, error: "", rate: 1, collapsed: true };
let PL_NODES = null;

// 移动端底部三格导航的显隐，也放 S 之外（同上：loadLevel 会 Object.assign 掉 S 的字段）。
// 下滑藏、上滑露、页顶永远露；只做 transform 位移，不改文档流，正文不会跟着跳。
const UI = { navHidden: false, lastY: null };

// 倍速锁在档位表内：iOS 对 >2 倍不稳，任意值也会让锁屏进度条的 playbackRate 失真。
const RATES = [0.5, 0.75, 1, 1.25, 1.5, 1.75, 2];
const RATE_KEY = "novel-review:playback-rate"; // 与上面 CR_KEY 同风格，存本机浏览器
const SKIP_SECS = 5; // 后退/前进秒数，锁屏的 seek 动作也用它当默认步长

// ---- 圆钮竖条动画的驱动开关 ------------------------------------------
// iOS（含 Chrome iOS，底层就是 WKWebView）默认走 CSS 合成动画；`?eq=1` / `?eq=0`
// 可以覆盖一次并记住，方便在 iPhone 上实测真音量。开关只决定「要不要尝试接线」，
// 实际显示以 EQ.an 是否接成（fab 的 data-eq）为准——关了开关但图还挂着不算数。
const IS_IOS = /iP(hone|ad|od)/.test(navigator.userAgent || "")
  || (navigator.platform === "MacIntel" && (navigator.maxTouchPoints || 0) > 1);
const EQ_KEY = "novel-review:eq-visualizer"; // "1"=真音量 "0"=CSS，同 RATE_KEY 风格
const EQ = { ctx: null, src: null, an: null, data: null, raf: 0, bars: [] };
let EQ_ON = wantRealEq(); // 本会话一旦接线失败就永久关掉

function wantRealEq() {
  try {
    const q = new URLSearchParams(typeof location === "object" && location ? location.search || "" : "")
      .get("eq");
    if (q === "1" || q === "0") { localStorage.setItem(EQ_KEY, q); return q === "1"; }
    const saved = localStorage.getItem(EQ_KEY);
    if (saved === "1" || saved === "0") return saved === "1";
  } catch (e) { /* 隐私模式等，读写都可能失败 */ }
  return !IS_IOS;
}

function fmtTime(sec) {
  if (!isFinite(sec) || sec < 0) sec = 0;
  const total = Math.floor(sec), hr = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60), s = total % 60;
  return hr ? `${hr}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}` : `${m}:${String(s).padStart(2, "0")}`;
}

function loadRate() {
  try {
    const v = Number(localStorage.getItem(RATE_KEY));
    return RATES.includes(v) ? v : 1;
  } catch (e) { return 1; } // 隐私模式等，读不到就用原速
}

function setRate(v) {
  if (!RATES.includes(v)) v = 1;
  P.rate = v;
  try { AUDIO.playbackRate = v; } catch (e) { /* 忽略 */ }
  try { localStorage.setItem(RATE_KEY, String(v)); } catch (e) { /* 忽略 */ }
  _posSec = -1; // 锁屏进度条要连 playbackRate 一起刷新，绕开整秒节流
  syncPositionState();
  renderPlayer();
}

function cycleRate() {
  const i = Math.max(0, RATES.indexOf(P.rate));
  setRate(RATES[(i + 1) % RATES.length]);
}

function seekTo(t) {
  if (!P.url) return;
  const dur = isFinite(AUDIO.duration) && AUDIO.duration > 0 ? AUDIO.duration : null;
  let v = Number(t);
  if (!isFinite(v)) return;
  if (v < 0) v = 0;
  if (dur != null && v > dur) v = dur;
  try { AUDIO.currentTime = v; } catch (e) { /* 未就绪时忽略 */ }
  _posSec = -1;
  // seek 后 timeupdate 未必立刻来，进度与时间就地更新
  if (PL_NODES && PL_NODES.range && PL_NODES.cur) {
    PL_NODES.range.value = String(v);
    PL_NODES.cur.textContent = fmtTime(v);
  }
  updateProgressVisual();
}

function seekBy(delta) { seekTo((AUDIO.currentTime || 0) + delta); }

// 收起态圆钮的进度环：--mp-p 是 0~100 的百分数，conic-gradient 用它画弧。
function updateProgressVisual() {
  if (!PL_NODES || !PL_NODES.fab) return;
  const d = isFinite(AUDIO.duration) && AUDIO.duration > 0
    ? AUDIO.duration : (P.dur || 0);
  const p = d > 0 ? Math.min(1, Math.max(0, (AUDIO.currentTime || 0) / d)) : 0;
  PL_NODES.fab.style.setProperty("--mp-p", (p * 100).toFixed(2));
}

// ---- 圆钮竖条：真实音量驱动 -------------------------------------------
// 只在 AudioContext 真的 running 时才接线——接线是单程的（createMediaElementSource
// 撤不回原生输出），接到挂起的上下文上就是静音（WebKit 231105/237878/261554 的老坑）。
// 所以这里宁可不接、退回 CSS 合成动画，也不拿「锁屏/后台不断播」去赌。
const EQ_BANDS = [[1, 6], [6, 14], [14, 30], [30, 60], [60, 110]]; // 频段 bin 下标，低→高

function ensureEqGraph() {
  if (!EQ_ON) return;
  try {
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) { EQ_ON = false; return; }
    // WebKit 给 AudioContext 留的后台豁免口子（仅接线路径里设，不外溢到别处）
    if (navigator.audioSession) navigator.audioSession.type = "playback";
    if (!EQ.ctx) EQ.ctx = new AC();
    const kick = EQ.ctx.state === "running"
      ? Promise.resolve() : EQ.ctx.resume().catch(() => null);
    kick.then(() => {
      if (!EQ.ctx || EQ.ctx.state !== "running") return; // 还没 running：下次手势再试
      if (EQ.src) { setEqMode(); return; }               // 已接线，只同步显示模式
      try {
        EQ.src = EQ.ctx.createMediaElementSource(AUDIO);  // 同一元素接两次会抛
        EQ.an = EQ.ctx.createAnalyser();
        EQ.an.fftSize = 256;
        EQ.an.smoothingTimeConstant = 0.6;
        EQ.src.connect(EQ.an);
        EQ.an.connect(EQ.ctx.destination);
        EQ.data = new Uint8Array(EQ.an.frequencyBinCount);
      } catch (e) {
        EQ_ON = false; EQ.src = null; EQ.an = null; EQ.data = null;
        setEqMode();
        return;
      }
      setEqMode();
    });
  } catch (e) { EQ_ON = false; }
}

// 竖条的显示模式以「是否真接成 + 上下文是否还活着」为准，不看用户偏好
function setEqMode() {
  const fab = PL_NODES && PL_NODES.fab;
  if (!fab) return;
  const real = !!(EQ.an && EQ.ctx && EQ.ctx.state === "running");
  fab.dataset.eq = real ? "real" : "css";
  if (real) startEq(); else stopEq();
}

function eqFrame() {
  EQ.raf = 0;
  if (!EQ.an || !EQ.data || !EQ.bars.length) return;
  if (!EQ.ctx || EQ.ctx.state !== "running" || document.hidden
      || !P.collapsed || !P.url || AUDIO.paused || AUDIO.ended) { stopEq(); return; }
  try { EQ.an.getByteFrequencyData(EQ.data); } catch (e) { stopEq(); return; }
  for (let i = 0; i < EQ.bars.length; i++) {
    const band = EQ_BANDS[i] || EQ_BANDS[EQ_BANDS.length - 1];
    let sum = 0, n = 0;
    for (let k = band[0]; k < band[1] && k < EQ.data.length; k++) { sum += EQ.data[k]; n++; }
    const raw = n ? Math.min(1, (sum / n) / 160) : 0;
    const prev = Number(EQ.bars[i].style.getPropertyValue("--eq")) || 0.12;
    // 快起慢落：涨立刻跟上，落要拖一点，看着才像在"喘"
    EQ.bars[i].style.setProperty("--eq", (raw > prev ? raw : prev * 0.86).toFixed(3));
  }
  EQ.raf = requestAnimationFrame(eqFrame);
}

function startEq() {
  if (EQ.raf || !EQ.an || !P.url || !P.collapsed || document.hidden) return;
  if (AUDIO.paused || AUDIO.ended) return;
  if (EQ.ctx && EQ.ctx.state !== "running") { setEqMode(); return; }
  EQ.raf = requestAnimationFrame(eqFrame);
}

function stopEq() {
  if (EQ.raf) { cancelAnimationFrame(EQ.raf); EQ.raf = 0; }
}

// 锁屏/通知栏封面：画一张 256×256 的波形图，失败就当作没有封面。
let _playerArt = null;
function playerArtwork() {
  if (_playerArt !== null) return _playerArt;
  _playerArt = "";
  try {
    const c = document.createElement("canvas");
    c.width = 256; c.height = 256;
    const g = c.getContext("2d");
    g.fillStyle = "#232532"; g.fillRect(0, 0, 256, 256);
    g.fillStyle = "#9184d9";
    const bars = [36, 74, 118, 156, 128, 92, 148, 104, 66, 124, 86, 142, 58];
    const w = 10, gap = (256 - 24 - bars.length * w) / (bars.length - 1);
    bars.forEach((hh, i) => {
      const x = 12 + i * (w + gap), y = (256 - hh) / 2;
      const r = Math.min(w / 2, 4);
      g.beginPath();
      g.moveTo(x + r, y);
      g.arcTo(x + w, y, x + w, y + hh, r);
      g.arcTo(x + w, y + hh, x, y + hh, r);
      g.arcTo(x, y + hh, x, y, r);
      g.arcTo(x, y, x + w, y, r);
      g.closePath(); g.fill();
    });
    _playerArt = c.toDataURL("image/png");
  } catch (e) { _playerArt = ""; }
  return _playerArt;
}

function mediaSession() { return "mediaSession" in navigator ? navigator.mediaSession : null; }

function updateMediaSession() {
  const ms = mediaSession();
  if (!ms) return;
  try {
    if (!P.url || typeof MediaMetadata === "undefined") { ms.metadata = null; return; }
    const art = playerArtwork();
    ms.metadata = new MediaMetadata({
      title: P.title || "",
      artist: (S.book && S.book.title) || "",
      album: P.album || P.sub || "",
      artwork: art ? [{ src: art, sizes: "256x256", type: "image/png" }] : [],
    });
  } catch (e) { /* 老浏览器忽略 */ }
}

function updatePlaybackState() {
  const ms = mediaSession();
  if (!ms) return;
  try { ms.playbackState = P.url ? (AUDIO.paused || AUDIO.ended ? "paused" : "playing") : "none"; } catch (e) { /* 忽略 */ }
}

let _posSec = -1;
function syncPositionState() {
  const ms = mediaSession();
  if (!ms || typeof ms.setPositionState !== "function") return;
  const dur = isFinite(AUDIO.duration) && AUDIO.duration > 0 ? AUDIO.duration : 0;
  if (!P.url || !dur) return;
  const s = Math.floor(AUDIO.currentTime);
  if (s === _posSec) return;
  _posSec = s;
  try { ms.setPositionState({ duration: dur, playbackRate: AUDIO.playbackRate || 1, position: Math.min(AUDIO.currentTime, dur) }); }
  catch (e) { /* 参数不合法时忽略 */ }
}

function loadTrack(url, title, sub) {
  if (P.url === url) return false;
  P.url = url; P.title = title; P.sub = sub || ""; P.album = rawPath(); P.dur = 0; P.error = "";
  _posSec = -1;
  AUDIO.src = url;
  try { AUDIO.playbackRate = P.rate; } catch (e) { /* 个别浏览器换源会复位，重设一次 */ }
  updateMediaSession();
  return true;
}

function playTrack(url, title, sub) {
  ensureEqGraph(); // 趁这次用户手势把 AudioContext 起来（iOS 默认 EQ_ON=false，直接返回）
  loadTrack(url, title, sub);
  const pr = AUDIO.play();
  if (pr && pr.catch) pr.catch((e) => { P.error = "无法播放：" + (e && e.message ? e.message : String(e)); renderPlayer(); });
  renderPlayer();
}

function togglePlayer() {
  if (!P.url) return;
  if (AUDIO.paused || AUDIO.ended) {
    ensureEqGraph(); // 恢复播放也是一次手势，上下文被挂起过就趁这次重试
    const pr = AUDIO.play();
    if (pr && pr.catch) pr.catch((e) => { P.error = "无法播放：" + (e && e.message ? e.message : String(e)); renderPlayer(); });
  } else AUDIO.pause();
}

function closePlayer() {
  AUDIO.pause();
  AUDIO.removeAttribute("src");
  try { AUDIO.load(); } catch (e) { /* 释放媒体资源，失败无所谓 */ }
  P.url = ""; P.title = ""; P.sub = ""; P.album = ""; P.dur = 0; P.error = ""; _posSec = -1;
  stopEq();
  EQ.bars = [];
  const ms = mediaSession();
  if (ms) { try { ms.metadata = null; ms.playbackState = "none"; } catch (e) { /* 忽略 */ } }
  renderPlayer();
  render();
}

function refreshPlayerButton() {
  const playing = !AUDIO.paused && !AUDIO.ended;
  if (PL_NODES && PL_NODES.fab) {
    // 收起态只有一个圆：点它展开，不直接控播放；竖条起伏交给 rAF（真音量）或 CSS 关键帧
    const b = PL_NODES.fab;
    if (b.dataset.playing !== String(playing)) {
      b.dataset.playing = String(playing);
      b.title = playing ? "播放中 · 点开看控制条" : "已暂停 · 点开看控制条";
      b.setAttribute("aria-label", b.title);
    }
    if (playing) startEq(); else stopEq();
    return;
  }
  if (!PL_NODES || !PL_NODES.playBtn) { renderPlayer(); return; }
  const b = PL_NODES.playBtn;
  if (b.dataset.playing === String(playing)) return;
  b.dataset.playing = String(playing);
  b.innerHTML = "";
  b.appendChild(icon(playing ? "ph-pause" : "ph-play", 18));
  b.title = playing ? "暂停" : "播放";
  b.setAttribute("aria-label", b.title);
}

function renderPlayer() {
  PL_NODES = null;
  document.body.classList.toggle("has-player", !!P.url);
  document.body.classList.toggle("player-collapsed", !!P.url && P.collapsed);
  if (!P.url) { PL.hidden = true; PL.innerHTML = ""; return; }
  PL.hidden = false;
  PL.innerHTML = "";
  const playing = !AUDIO.paused && !AUDIO.ended;

  // 收起态：整个播放条只画一个圆——进度环 + 5 根随音量起伏的竖条，点圆展开控制条
  if (P.collapsed) {
    const tip = P.error ? "音频加载失败 · 点开看详情"
      : (playing ? "播放中 · 点开看控制条" : "已暂停 · 点开看控制条");
    const eq = h("span", { class: "mp-eq", "aria-hidden": "true" },
      [0, 1, 2, 3, 4].map(() => h("i", { style: "--eq:.12" })));
    const fab = h("button", {
      class: "mp-fab", type: "button", "aria-expanded": "false",
      title: tip, "aria-label": tip,
      onClick: () => { P.collapsed = false; renderPlayer(); },
    },
      h("span", { class: "mp-fab-ring", "aria-hidden": "true" }),
      h("span", { class: "mp-fab-core", "aria-hidden": "true" }),
      P.error ? icon("ph-warning-octagon", 22) : eq);
    fab.dataset.playing = String(playing);
    PL.appendChild(fab);
    EQ.bars = P.error ? [] : eq.children;
    PL_NODES = { fab, range: null, cur: null, playBtn: null, rateBtn: null, dragging: false };
    updateProgressVisual();
    setEqMode(); // 真音量已接成 → data-eq=real 并起 rAF；否则 data-eq=css 走关键帧
    if (!EQ.an && EQ_ON) ensureEqGraph(); // 没接过线就趁这次手势接（iOS 默认不会走到这）
    return;
  }
  stopEq(); // 展开态没有竖条可画

  const range = h("input", {
    class: "mp-range", type: "range", min: "0",
    max: String(P.dur > 0 ? P.dur : 100), step: "any",
    value: String(Math.min(AUDIO.currentTime || 0, P.dur || Infinity)),
    "aria-label": "播放进度",
    // 拖动期间不要让 timeupdate 把滑块拽回去（pointer/touch 两套都挂，iOS 兼容）
    onPointerDown: () => { if (PL_NODES) PL_NODES.dragging = true; },
    onTouchStart: () => { if (PL_NODES) PL_NODES.dragging = true; },
    onPointerUp: () => { if (PL_NODES) PL_NODES.dragging = false; },
    onTouchEnd: () => { if (PL_NODES) PL_NODES.dragging = false; },
    onInput: (e) => {
      const v = Number(e.target.value);
      try { AUDIO.currentTime = v; } catch (err) { /* 未就绪时忽略 */ }
      if (PL_NODES && PL_NODES.cur) PL_NODES.cur.textContent = fmtTime(v);
    },
    onChange: () => { if (PL_NODES) PL_NODES.dragging = false; },
    onBlur: () => { if (PL_NODES) PL_NODES.dragging = false; },
  });
  const playBtn = h("button", {
    class: "mp-btn", type: "button", title: playing ? "暂停" : "播放",
    "aria-label": playing ? "暂停" : "播放", onClick: togglePlayer,
  }, icon(playing ? "ph-pause" : "ph-play", 18));
  playBtn.dataset.playing = String(playing);
  const skipBtn = (dir) => h("button", {
    class: "mp-btn mp-skip", type: "button",
    title: dir < 0 ? `后退 ${SKIP_SECS} 秒` : `前进 ${SKIP_SECS} 秒`,
    "aria-label": dir < 0 ? `后退 ${SKIP_SECS} 秒` : `前进 ${SKIP_SECS} 秒`,
    onClick: () => seekBy(dir * SKIP_SECS),
  }, icon(dir < 0 ? "ph-arrow-counter-clockwise" : "ph-arrow-clockwise", 15),
     h("span", { class: "mp-skip-n mono" }, String(SKIP_SECS)));
  const rateBtn = h("button", {
    class: "mp-rate", type: "button", title: "播放倍速（点击切换）",
    "aria-label": `播放倍速 ${P.rate} 倍，点击切换`, onClick: cycleRate,
  }, `${P.rate}×`);
  const closeBtn = h("button", {
    class: "mp-btn mp-close", type: "button", title: "关闭播放器", "aria-label": "关闭播放器", onClick: closePlayer,
  }, icon("ph-x", 16));
  // 展开态：标题块仍是开合按钮，点它收起回一个圆
  const meta = h("button", {
    class: "mp-meta", type: "button",
    title: "收起播放控制", "aria-expanded": "true",
    onClick: () => { P.collapsed = true; renderPlayer(); },
  },
    h("div", { class: "mp-meta-text" },
      h("div", { class: "mp-title" }, P.title || "正在播放"),
      h("div", { class: "mp-sub" }, P.sub || "")),
    icon("ph-caret-up", 14));
  const cur = h("span", { class: "mp-time mono" }, fmtTime(AUDIO.currentTime));
  const dur = h("span", { class: "mp-time mono" }, fmtTime(P.dur));

  PL.appendChild(h("div", { class: "mp-top" },
    skipBtn(-1), playBtn, skipBtn(1), meta, rateBtn, closeBtn));
  if (P.error) PL.appendChild(h("div", { class: "mp-error" }, P.error));
  PL.appendChild(h("div", { class: "mp-seek" }, cur, range, dur));
  PL_NODES = { range, cur, playBtn, rateBtn, dragging: false };
}

function onPlayerState() {
  updatePlaybackState();
  syncPositionState();
  refreshPlayerButton();
  render(); // 同步预览面板里的播放/暂停按钮文案
}

function initPlayer() {
  P.rate = loadRate();
  try { AUDIO.playbackRate = P.rate; } catch (e) { /* 忽略 */ }
  AUDIO.addEventListener("play", onPlayerState);
  AUDIO.addEventListener("pause", onPlayerState);
  AUDIO.addEventListener("ended", onPlayerState);
  AUDIO.addEventListener("loadedmetadata", () => {
    P.dur = isFinite(AUDIO.duration) && AUDIO.duration > 0 ? AUDIO.duration : 0;
    updateMediaSession();
    syncPositionState();
    renderPlayer();
  });
  AUDIO.addEventListener("timeupdate", () => {
    if (PL_NODES && !PL_NODES.dragging && PL_NODES.range && PL_NODES.cur) {
      PL_NODES.range.value = String(AUDIO.currentTime);
      PL_NODES.cur.textContent = fmtTime(AUDIO.currentTime);
    }
    updateProgressVisual();
    syncPositionState();
  });
  AUDIO.addEventListener("error", () => {
    if (!AUDIO.getAttribute("src")) return; // 主动关闭时的空 src，不算错误
    P.error = "加载失败（音频可能已被删除）";
    renderPlayer();
  });

  const ms = mediaSession();
  if (ms) {
    const set = (name, fn) => { try { ms.setActionHandler(name, fn); } catch (e) { /* 不支持则跳过 */ } };
    set("play", () => { if (P.url) { const pr = AUDIO.play(); if (pr && pr.catch) pr.catch(() => {}); } });
    set("pause", () => AUDIO.pause());
    // 系统给了 seekOffset 就用系统的，否则与界面按钮一致按 5 秒
    set("seekbackward", (d) => seekBy(-((d && d.seekOffset) || SKIP_SECS)));
    set("seekforward", (d) => seekBy((d && d.seekOffset) || SKIP_SECS));
    set("seekto", (d) => { if (d && typeof d.seekTime === "number") seekTo(d.seekTime); });
  }
  // 切后台/回前台：竖条 rAF 只在前台跑；回前台若上下文被挂起，试着拉回来——
  // 拉不动也不碰播放（视觉退回 CSS 关键帧，声音要彻底恢复只需刷新页面）。
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) { stopEq(); return; }
    if (EQ.ctx && EQ.ctx.state !== "running") {
      Promise.resolve(EQ.ctx.resume().catch(() => null)).then(() => setEqMode());
      return;
    }
    setEqMode();
  });
  renderPlayer();
}

// 当前打开的音频该显示成什么名字：优先用章级标题，按场切分再补场号。
function audioTrackTitle(path) {
  if (S.level && S.level.level === "ch" && S.ch != null) {
    const m = basename(path).match(/_场(\d+)/);
    return m ? `${S.level.title} · 场 ${m[1]}` : S.level.title;
  }
  return basename(path);
}

// ---------------------------------------------------------------- 导航

function curKey() { return { sec: S.sec, part: S.part, vol: S.vol, ch: S.ch }; }

async function loadLevel(patch, pushHist) {
  if (pushHist) S.hist = [...S.hist, curKey()].slice(-30);
  Object.assign(S, patch, {
    file: null, fileData: null, fedit: null, fileToken: S.fileToken + 1, cmd: null, note: "",
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
  // token：进来时先记一下当前文件请求的版本号，等下面的 await 落地时如果版本号已经
  // 变了（用户在这期间点开了别的文件，或翻页离开），说明这份响应已经过期，不能再往
  // S.fileData 里写——否则会出现地址是新文件、内容却是旧文件那种错位。
  const token = S.fileToken;
  try {
    const [level, book] = await Promise.all([
      API.level(S.sec, S.part, S.vol, S.ch),
      API.book(),
    ]);
    S.level = level; S.book = book;
    if (keepFile && S.file) {
      const path = S.file;
      try {
        const data = await API.file(path);
        if (S.fileToken === token && S.file === path) S.fileData = data;
      } catch (e) {
        if (S.fileToken === token && S.file === path) { S.file = null; S.fileData = null; }
      }
    }
  } catch (e) { fail(e); }
  render();
}

async function selectFile(path) {
  const token = ++S.fileToken;
  setState({ pane: S.vw < 720 ? "preview" : S.pane });
  try {
    const data = await API.file(path);
    if (S.fileToken !== token) return; // 期间又点开了别的文件，这份响应已经过期
    setState({ file: path, fileData: data, fedit: null, pane: S.vw < 720 ? "preview" : S.pane });
  } catch (e) { if (S.fileToken === token) fail(e); }
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

// 复制文本 → 返回 "" 表示成功，否则返回给用户看的失败文案。
// navigator.clipboard 只在安全上下文（https 或 http://localhost）里存在，而审查台默认
// 监听 0.0.0.0、控制台打印的入口又是 http://<局域网IP>:8765：非回环的 http 页面里
// clipboard 根本是 undefined，writeText 一调就抛，旧代码一律报「浏览器拒绝了剪贴板权限」，
// 把「地址不是安全上下文」说成了「权限被拒」，用户没处下手。
// 所以：安全上下文才走标准 API；否则（或标准 API 被拒）回退 <textarea> +
// document.execCommand("copy")——它不要求安全上下文，且仍在点击手势里；两条路都失败
// 才如实报错并给出可操作的下一步。
async function copyText(text) {
  let apiDenied = false;
  if (window.isSecureContext && navigator.clipboard) {
    try { await navigator.clipboard.writeText(text); return ""; }
    catch (e) { apiDenied = true; }
  }
  try {
    const prev = document.activeElement; // 复制完把焦点还给按钮，键盘用户不会掉回 body
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.cssText = "position:fixed;top:0;left:0;width:1px;height:1px;margin:0;padding:0;"
      + "border:0;opacity:0;pointer-events:none";
    document.body.appendChild(ta);
    ta.select();
    try { ta.setSelectionRange(0, text.length); } catch (e) { /* 部分浏览器不认，select() 已够 */ }
    const ok = document.execCommand("copy");
    ta.remove();
    if (prev && prev.focus) prev.focus();
    if (ok) return "";
  } catch (e) { /* 回退也失败 → 走下面的报错 */ }
  if (!window.isSecureContext) {
    return "复制失败：当前地址不是安全上下文（http + 局域网 IP），浏览器禁用剪贴板 API。"
      + "改用 http://localhost:8765 打开，或手动选中文字按 Ctrl/⌘+C。";
  }
  if (apiDenied) return "复制失败：浏览器拒绝了剪贴板权限（点地址栏左侧图标可改权限）。";
  return "复制失败：浏览器不支持自动复制，请手动选中文字按 Ctrl/⌘+C。";
}

async function copyFile() {
  const err = await copyText(S.fileData.text || "");
  flash(err ? "⚠ " + err : `已复制 ${basename(S.file)}`);
}

async function copyPath() {
  const path = rawPath();
  const err = await copyText(path);
  flash(err ? "⚠ " + err : `已复制路径：${path}`);
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

async function doWithdraw(kind) {
  if (S.withdrawing) return;
  S.withdrawing = true; render();
  try {
    const r = await API.withdraw({ part: S.part, vol: S.vol, ch: S.ch, kind });
    const patch = { withdrawing: false, forceRegen: true,
      flash: `已把 ${r.archived_from} 移到 ${r.archived_to}，进度表登记已清除`
        + (r.warnings && r.warnings.length ? "。" + r.warnings.join("；") : "") };
    if (S.file === r.archived_from) { patch.file = null; patch.fileData = null; }
    setState(patch);
    await refreshAll(true);
  } catch (e) {
    setState({ withdrawing: false });
    fail(e);
  }
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
      class: "tabbar" + (UI.navHidden ? " is-hidden" : ""),
      "aria-hidden": UI.navHidden ? "true" : "false",
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
  // 滚动窗口时左栏固定：aside 自身仍按 wrap 的 align-items:stretch 拉到整页高（左边缘
  // 深色底不会随滚动断掉），只有里面这层做 sticky 跟着视口走——书名、分区导航、播客
  // 订阅读多长正文都钉在原位，不用滚回页顶切区。min-height:100vh 让「播客订阅」在内容
  // 不足一屏时仍贴在可见栏的下沿（margin-top:auto 的落点），不加内滚，整页仍只有一个滚动条。
  return h("aside", {
    style: "flex:0 1 216px;min-width:190px;background:var(--color-neutral-900);box-shadow:inset -1px 0 0 var(--color-neutral-800);"
      + "display:flex;flex-direction:column",
  },
    h("div", {
      style: "position:sticky;top:0;min-height:100vh;display:flex;flex-direction:column;gap:var(--space-8);"
        + "padding:var(--space-8) var(--space-6)",
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
          S.book ? S.book.feed_url : ""))));
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

// sticky=true（桌面端）：文件列表钉在页头下面，长正文读到多深都还在手边。
// 不设 max-height / overflow：那样会在页面滚动之外再叠一层列表自己的内滚，两层滚动区域
// 分别对应鼠标落点，用户很难分清「现在滚的是哪一层」，滚到看着卡住的条目够不着（同样的
// 教训见 previewPanel 的正文预览）。列表不设内滚上限，跟着页面走——列表比视口还长的
// 罕见情况下，滚到底部时它会正常跟随页面滚出去，不完美但只有一层，好懂。
function fileList(sticky) {
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
  const listStyle = "flex:1 1 240px;min-width:220px;display:flex;flex-direction:column;gap:var(--space-8)"
    + (sticky ? ";position:sticky;top:var(--head-h,130px);align-self:flex-start" : "");
  return h("div", { class: "file-list", style: listStyle },
    childrenBlock, groups,
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
    const url = S.fileData.audio_url;
    const isCur = !!url && P.url === url;
    const isPlaying = isCur && !AUDIO.paused && !AUDIO.ended;
    body = h("div", { style: "padding:var(--space-8);display:flex;flex-wrap:wrap;align-items:center;gap:var(--space-4)" },
      url
        ? h("button", {
            class: "btn btn-primary", style: "font-size:13px",
            onClick: () => { if (isCur) togglePlayer(); else playTrack(url, audioTrackTitle(S.file), basename(S.file)); },
          }, icon(isPlaying ? "ph-pause" : "ph-play", 15), isPlaying ? "暂停" : isCur ? "继续播放" : "播放")
        : h("span", { style: "color:var(--color-neutral-500)" }, "找不到音频文件"),
      isCur ? tag(isPlaying ? "正在播放" : "已暂停", "accent") : null,
      S.fileData.desc ? h("span", { style: "font-size:12.5px;color:var(--color-neutral-500)" }, S.fileData.desc) : null,
      url ? h("span", { style: "font-size:12.5px;color:var(--color-neutral-500)" },
        "播放条常驻屏幕下方，切章 / 切区 / 切后台都不中断") : null);
  } else if (S.fileData.kind === "binary") {
    body = h("div", { style: "padding:var(--space-8);color:var(--color-neutral-500)" }, "该文件类型不支持预览。");
  } else if (S.fileData.kind === "prose") {
    // 不设 max-height / overflow：正文顺着页面往下长，整页只有 window 一个滚动条。
    // 以前这里自带 620px 内滚，跟页面滚动叠成两层，滚到哪儿全看指针落在哪个框里。
    body = h("div", {
      style: "padding:var(--space-8) var(--space-8);font-size:15.5px;line-height:1.95;color:var(--color-neutral-200);"
        + "white-space:pre-wrap;max-width:40em",
    }, S.fileData.text);
  } else {
    body = h("div", {
      class: "mono",
      style: "padding:var(--space-6) var(--space-8);font-size:13px;line-height:1.75;color:var(--color-neutral-200);"
        + "white-space:pre-wrap",
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
        + "border-radius:var(--radius-md);padding:var(--space-3);max-height:220px;overflow:auto;overscroll-behavior:contain",
    }, S.job.log) : null);
}

// 章级「撤下重新生成」：只在这一版是全书当前最新一章时给按钮，否则只给提醒文字，
// 不做任何文件操作——早期已定稿章节改用 06_章节回溯修改.md，这里不自动化状态重折。
function withdrawBlock(cmdId) {
  const l = S.level;
  const w = l.chapter && l.chapter.withdraw;
  if (!w) return null;
  const kind = cmdId === "draft" ? "manuscript" : "outline";
  const info = w[kind];
  const noteStyle = "font-size:12.5px;color:var(--color-neutral-400);padding:var(--space-3);"
    + "background:var(--color-neutral-900);border-radius:var(--radius-md)";
  if (!info || !info.exists) return null;
  if (kind === "outline" && info.blocked_by_manuscript) {
    return h("div", { style: noteStyle }, "本章正文还在——先到「单章正文提示词」撤下正文，再撤细纲。");
  }
  if (!info.latest) {
    return h("div", { style: noteStyle },
      `本章不是全书最新一章（最新：${info.latest_label || "—"}），不能直接撤下重写。`
      + "改早期已定稿章节请走技能 ", h("code", { class: "mono" }, "06_章节回溯修改.md"),
      "（dry-run 确认后再重折状态，这里不做自动化）。");
  }
  const label = kind === "manuscript" ? "正文" : "细纲";
  return h("div", { style: "display:flex;flex-direction:column;gap:var(--space-2);padding:var(--space-3);"
    + "background:var(--color-neutral-900);border-radius:var(--radius-md)" },
    h("div", { style: "font-size:12px;color:var(--color-neutral-400)" },
      `会把当前${label}移到本章 01_模型输出/ 留档并加时间戳，同时删掉 00_进度.json 里对应的登记。`),
    h("button", {
      class: "btn btn-ghost", style: "font-size:13px;align-self:flex-start",
      disabled: S.withdrawing || null,
      onClick: () => doWithdraw(kind),
    }, icon("ph-arrow-counter-clockwise", 15), S.withdrawing ? "撤下中…" : `撤下本章${label}，重新生成`));
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
      withdrawBlock(cmdId),
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
        h("div", { class: "mono", style: "font-size:12px;line-height:1.7;color:var(--color-neutral-200);white-space:pre-wrap;background:var(--color-neutral-900);border-radius:var(--radius-md);padding:var(--space-4);max-height:260px;overflow:auto;overscroll-behavior:contain" },
          S.fileData.text),
        h("button", {
          class: "btn btn-primary", style: "font-size:13px",
          onClick: async () => {
            const extra = S.note.trim() ? `\n\n【附加要求】${S.note.trim()}` : "";
            const err = await copyText(S.fileData.text + extra);
            flash(err ? "⚠ " + err : "提示词已复制，可直接粘贴到云端模型");
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

// 把 UI.navHidden 同步到 DOM：导航自身的 .is-hidden、无障碍的 aria-hidden、
// body 的 .nav-hidden（CSS 靠它让播放条跟着沉到屏幕最底）。
// 桌面端没有 .tabbar → on 恒为 false，旋转屏幕后 body 上的类会被顺手清掉。
function applyNavHidden() {
  const root = document.getElementById("app");
  const bar = root && root.querySelector(".tabbar");
  const on = !!bar && UI.navHidden;
  document.body.classList.toggle("nav-hidden", on);
  if (!bar) return;
  // 焦点还留在正要藏起来的导航里会把键盘/读屏用户困住（aria-hidden 内不该有焦点）
  if (on && bar.contains(document.activeElement) && document.activeElement.blur) document.activeElement.blur();
  bar.classList.toggle("is-hidden", on);
  bar.setAttribute("aria-hidden", on ? "true" : "false");
}

let HEAD_RO = null; // 常驻观察页头高度，见 render() 里的用法

function render() {
  const root = document.getElementById("app");
  root.innerHTML = "";
  const mobile = S.vw < 720;
  const readOnly = !!(S.config && S.config.read_only);

  if (!S.book || !S.level) {
    root.appendChild(h("div", { style: "padding:var(--space-8);color:var(--color-neutral-500)" }, S.loading ? "加载中…" : "加载失败"));
    applyNavHidden(); // 这条分支没有导航，顺手把 body 上的类清掉
    return;
  }

  const l = S.level;
  const panes = [{ id: "list", label: "浏览", icon: "ph-list-bullets" }];
  if (S.file) panes.push({ id: "preview", label: "预览", icon: "ph-file-text" });
  if (S.sec === "work") panes.push({ id: "cmd", label: "指令", icon: "ph-terminal-window" });
  let pane = S.pane;
  if (pane === "preview" && !S.file) pane = "list";
  if (pane === "cmd" && S.sec !== "work") pane = "list";

  // 页头 = 地址栏 + 标题块。桌面端把它整块钉在窗口顶端：正文跟页面一起滚（单层滚动），
  // 读到多深路径与「上一章/下一章」都还在。手机端不钉——那里有自己的 sticky topbar。
  const head = h("div", {
    class: "page-head",
    // 间距沿用原来的 main gap：手机 11.2 / 桌面 16.8，包一层不能把节奏改掉
    style: "display:flex;flex-direction:column;gap:" + (mobile ? "var(--space-4)" : "var(--space-6)")
      + (mobile ? ""
        : ";position:sticky;top:0;z-index:15;background:var(--color-bg);padding-bottom:var(--space-2);"
          + "box-shadow:0 16px 20px -18px rgba(0,0,0,.95)"),
  }, addressBar(mobile), titleBlock());

  const mainChildren = [
    head,
    mobile ? h("div", { class: "seg", style: "display:flex;width:100%" },
      panes.map((p) => h("label", { class: "seg-opt", style: "flex:1;justify-content:center;min-height:44px;font-size:13.5px" },
        h("input", { type: "radio", name: "pane", checked: pane === p.id || null, onChange: () => setState({ pane: p.id }) }),
        icon(p.icon, 15), p.label))) : null,
    (mobile && S.flash && pane !== "cmd") ? flashBar() : null,
    h("div", { style: "display:flex;flex-wrap:wrap;gap:var(--space-6);align-items:flex-start" },
      (!mobile || pane === "list") ? fileList(!mobile) : null,
      (S.file && (!mobile || pane === "preview")) ? previewPanel(readOnly) : null,
      (!readOnly && S.sec === "work" && (!mobile || pane === "cmd")) ? cmdPanel() : null),
  ];
  // 桌面端 flashBar 插在 head（0 号）之后
  if (!mobile && S.flash) mainChildren.splice(1, 0, flashBar());

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
  // 实测页头高度，供钉住的文件列表贴着页头下沿（top: var(--head-h)），不是量少了就露馅、
  // 量多了顶多贴得不够紧，不影响能不能滚到——用 ResizeObserver 常驻盯着，字体换掉/标题
  // 换行/窗口缩放不管什么原因引起的高度变化都会补一次，比只在 resize 事件时测更可靠。
  if (!mobile && head) {
    if (!HEAD_RO) {
      HEAD_RO = new ResizeObserver((entries) => {
        const hh = entries[0].contentRect.height;
        const m = document.querySelector("main");
        if (hh > 0 && m) m.style.setProperty("--head-h", Math.ceil(hh) + "px");
      });
    }
    HEAD_RO.disconnect();
    HEAD_RO.observe(head);
    const hh = head.getBoundingClientRect().height;
    if (hh > 0) main.style.setProperty("--head-h", Math.ceil(hh) + "px");
  }
  applyNavHidden(); // 重建出来的导航按 UI.navHidden 补上标记（render 每 800ms 轮询一次）
}

// ---------------------------------------------------------------- 启动

window.addEventListener("resize", () => { S.vw = window.innerWidth; render(); });

// 底部导航：往下滚就藏起来，往上滚或回到页顶再露出来。
// 页面滚的是 window（body 下没有 overflow 容器），scroll 事件逐帧来，够快也够省。
// 判定走阈值而不是逐像素比，惯性滚动的细碎抖动不会让导航来回抽搐。
window.addEventListener("scroll", () => {
  const y = window.scrollY || 0;
  if (UI.lastY == null) { UI.lastY = y; return; } // 第一次只记位置，不判断方向
  const dy = y - UI.lastY;
  UI.lastY = y;
  if (Math.abs(dy) < 8) return; // 抖动阈值
  const next = y < 40 ? false : dy > 0; // 页顶永远露着，方便回去点
  if (next === UI.navHidden) return;
  UI.navHidden = next;
  applyNavHidden(); // 不等下一轮 render，滚动中要立刻响应
}, { passive: true });

(async function init() {
  initPlayer();
  try {
    S.config = await API.config();
  } catch (e) { /* 配置读不到也不阻塞浏览 */ }
  await loadLevel({ sec: "work", part: null, vol: null, ch: null }, false);
})();
