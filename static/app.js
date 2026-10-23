"use strict";
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = v => v == null ? "–" : typeof v === "number" ? (Math.abs(v) >= 100 ? v.toFixed(0) : v.toFixed(2)) : v;

async function api(url, body, method) {
  const opt = { method: method || (body !== undefined ? "POST" : "GET") };
  if (body !== undefined) { opt.headers = { "Content-Type": "application/json" }; opt.body = JSON.stringify(body); }
  let r;
  try {
    r = await fetch(url, opt);
  } catch (e) {
    // "Failed to fetch" = trình duyệt không kết nối được tới server -> server đã crash/tắt (process chết,
    // không phải lỗi logic app). Xem cửa sổ terminal đã chạy `python edgetool.py` rồi mở lại.
    throw new Error("Mất kết nối tới server (có thể server vừa bị crash/tắt) - kiểm tra terminal chạy edgetool.py rồi mở lại trang.");
  }
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch (e) { }
    throw new Error(msg);
  }
  return r.json();
}
function toast(msg, err) {
  const d = document.createElement("div");
  d.textContent = msg; if (err) d.className = "err";
  $("#toast").appendChild(d); setTimeout(() => d.remove(), err ? 7000 : 2500);
}
const guard = fn => async (...a) => { try { return await fn(...a); } catch (e) { toast(e.message, true); console.error(e); } };
const classColor = i => `hsl(${(i * 137.508) % 360} 85% 55%)`;

const ST = { state: null, rows: [], view: "home", focus: 0 };

// ======================================================================= views
function show(view, noAuto) {
  if (view !== "home" && !ST.state?.project) { toast("Hãy mở 1 folder trước", true); view = "home"; }
  ST.view = view;
  $$(".view").forEach(v => v.classList.toggle("active", v.id === "v-" + view));
  $$("nav button").forEach(b => b.classList.toggle("on", b.dataset.view === view));
  if (view === "grid") loadReport();
  if (view === "home") guard(loadProjects)();
  if (view === "editor" && !noAuto) {
    if (!ED.file && ST.rows.length) guard(openEditor)(ST.rows[0].file);
    else if (!ED.file) api("/api/report").then(r => { ST.rows = r.rows; if (r.rows.length) guard(openEditor)(r.rows[0].file); });
    else resizeCanvas();
  }
}
$$("nav button").forEach(b => b.onclick = () => show(b.dataset.view));

async function loadState() {
  ST.state = await api("/api/state");
  const p = ST.state.project;
  $("#folder").textContent = p ? p.folder : "";
  if (p) $("#oiClasses").value = (p.settings.oi_classes || []).join(", ");
  $("#homeAutoCard").hidden = !p;
  $("#hFolderPath").value = p ? p.folder : "";
  $("#hDest").textContent = p ? p.checked : "";
  $("#hFolderWarn").hidden = true;
  $("#hDestRow").style.opacity = 1;
  $("#eLayer").innerHTML = ST.state.layers.map(([k, t]) => `<option value="${k}">${esc(t)}</option>`).join("");
  $("#eLayer").value = ED.layer;
  fillClasses();
  pollJob(ST.state.job);
}

// ======================================================================= home
async function openFolder(f, stay, noScan) {
  if (!f || !f.trim()) { toast("Chưa nhập đường dẫn folder", true); return; }
  await api("/api/open", { folder: f.trim().replace(/^"|"$/g, "") });
  ED.file = null;
  await loadState();
  toast("Đã mở folder");
  if (!noScan) { await api("/api/scan", {}); pollJob(); }
  show(stay ? "home" : "grid");
}
const samePath = (a, b) => (a || "").trim().replace(/^"|"$/g, "").replace(/\//g, "\\").replace(/\\+$/, "").toLowerCase()
  === (b || "").trim().replace(/\//g, "\\").replace(/\\+$/, "").toLowerCase();
$("#btnPick").onclick = guard(async () => { const r = await api("/api/pick_folder", {}); if (r.path) { $("#folderIn").value = r.path; await openFolder(r.path); } });
$("#btnOpen").onclick = guard(() => openFolder($("#folderIn").value));
$("#folderIn").onkeydown = e => { if (e.key === "Enter") $("#btnOpen").click(); };
$("#btnOiSave").onclick = guard(async () => {
  await api("/api/settings", { oi_classes: $("#oiClasses").value.split(",") });
  toast("Đã lưu. Bấm Scan để nhập lại nhãn cho ảnh chưa có nhãn.");
});
$("#btnScan").onclick = guard(async () => { await api("/api/scan", {}); pollJob(); });
$("#jobstop").onclick = guard(() => api("/api/stop", {}));

// --- danh sách "thư mục đã check"
async function loadProjects() {
  const d = await api("/api/projects");
  renderProjects(d.projects || []);
}
const baseName = p => (p || "").replace(/[\\/]+$/, "").split(/[\\/]/).pop();
function projRow(r) {
  const btnRemove = `<button class="small" data-remove="${esc(r.folder)}" title="Xoá khỏi danh sách này (không xoá dữ liệu đã nhãn trong data-labeled\\)">🗑</button>`;
  if (!r.exists) return `<tr class="missing"><td>${esc(r.folder)}</td><td colspan="8" class="muted">(không tìm thấy thư mục trên máy)</td><td>${btnRemove}</td></tr>`;
  const dest = r.checked_dir
    ? `<b>${esc(baseName(r.checked_dir))}</b> <button class="small" data-reveal="${esc(r.checked_dir)}" title="${esc(r.checked_dir)}">📂</button>`
    : `<span class="muted small" title="${esc(r.planned_dir || "")}">(chưa tạo)</span>`;
  return `<tr class="${r.is_current ? "cur" : ""}">
    <td><a class="pname" data-open="${esc(r.folder)}" title="Mở để sửa nhãn">${esc(r.folder)}</a>${r.is_current ? ' <span class="small muted">(đang mở)</span>' : ""}</td>
    <td>${dest}</td>
    <td class="num">${r.n_images}</td><td class="num">${r.n_auto || "–"}</td><td class="num">${r.n_manual || "–"}</td>
    <td class="num">${r.n_imported || "–"}</td><td class="num">${r.n_nolabel}</td>
    <td class="num"><input type="checkbox" data-done="${esc(r.folder)}" ${r.done ? "checked" : ""} title="Tích khi bạn đã xong việc check thư mục này"></td>
    <td class="num">${r.n_good}/${r.n_bad}</td>
    <td><button data-open="${esc(r.folder)}">Mở để sửa</button> <button data-auto="${esc(r.folder)}" title="Chạy auto-label trên thư mục này (chọn model/class ở panel dưới)">⚙ Auto-label</button> ${btnRemove}</td></tr>`;
}
function renderProjects(rows) {
  $("#projBody").innerHTML = rows.length ? rows.map(projRow).join("") : "<tr><td colspan='10' class='muted'>(chưa mở folder nào)</td></tr>";
  $$("#projBody [data-open]").forEach(el => el.onclick = guard(() => openFolder(el.dataset.open)));
  $$("#projBody [data-reveal]").forEach(el => el.onclick = guard(() => api("/api/reveal", { path: el.dataset.reveal })));
  $$("#projBody [data-auto]").forEach(el => el.onclick = guard(() => useForAutoLabel(el.dataset.auto)));
  $$("#projBody [data-done]").forEach(el => el.onclick = guard(() => api("/api/folder_done", { folder: el.dataset.done, done: el.checked })));
  $$("#projBody [data-remove]").forEach(el => el.onclick = guard(async () => {
    if (!confirm(`Xoá "${el.dataset.remove}" khỏi danh sách "Thư mục đã check"?\n\nDữ liệu đã nhãn trong data-labeled\\ vẫn được GIỮ LẠI, không bị xoá - mở lại folder gốc sẽ thấy nhãn cũ như bình thường.`)) return;
    await api("/api/project_remove", { folder: el.dataset.remove });
    await loadState();
    await loadProjects();
    toast("Đã xoá khỏi danh sách");
  }));
}
function useForAutoLabel(folder) {
  $("#homeAutoCard").hidden = false;
  $("#hFolderPath").value = folder;
  hFolderChanged();
  $("#homeAutoCard").scrollIntoView({ behavior: "smooth", block: "start" });
  toast("Đã chọn thư mục — chọn model/class rồi bấm \"Chạy auto-label cả thư mục\" ở dưới.");
}

// --- panel auto-label cả thư mục: chọn/đổi thư mục mục tiêu ngay tại đây
function hFolderChanged() {
  // folder trong ô khác folder đang mở -> báo rõ, bấm "Chạy" sẽ tự mở folder đó trước rồi mới chạy
  const diff = !samePath($("#hFolderPath").value, ST.state?.project?.folder);
  $("#hFolderWarn").hidden = !diff;
  $("#hDestRow").style.opacity = diff ? 0.45 : 1;
}
$("#hFolderBrowse").onclick = guard(async () => {
  const r = await api("/api/pick_folder", {});
  if (r.path) { $("#hFolderPath").value = r.path; hFolderChanged(); }
});
$("#hFolderOpen").onclick = guard(() => openFolder($("#hFolderPath").value, true));
$("#hFolderPath").oninput = hFolderChanged;
$("#hFolderPath").onkeydown = e => { if (e.key === "Enter") $("#hFolderOpen").click(); };
$("#hDestOpen").onclick = guard(() => api("/api/reveal", { path: $("#hDest").textContent }));

// ======================================================================= job
let jobTimer = null;
function pollJob(j) {
  const upd = j => {
    $("#jobbox").hidden = !j.running;
    $("#jobbar").max = Math.max(1, j.total); $("#jobbar").value = j.done;
    const eta = j.eta_s ? ` · còn ~${Math.ceil(j.eta_s / 60)} phút` : "";
    $("#jobtxt").textContent = `${j.kind === "scan" ? "Scan" : "Auto-label"} ${j.done}/${j.total}${eta}`;
  };
  if (j) upd(j);
  if (jobTimer) return;
  jobTimer = setInterval(async () => {
    try {
      const j = await api("/api/job");
      upd(j);
      if (ST.view === "grid" && j.running && j.done % 5 === 0) loadReport(true);
      if (!j.running) {
        clearInterval(jobTimer); jobTimer = null;
        if (j.total) toast(`${j.kind === "scan" ? "Scan" : "Auto-label"} xong (${j.done}/${j.total})${j.errors.length ? ", " + j.errors.length + " lỗi" : ""}`, j.errors.length > 0);
        if (j.kind === "autolabel" && j.total) autoDone(j);
        if (ST.view === "grid") loadReport();
        if (ST.view === "editor" && ED.file) openEditor(ED.file, true);
      }
    } catch (e) { clearInterval(jobTimer); jobTimer = null; }
  }, 1000);
}

// ======================================================================= grid
async function loadReport(keepScroll) {
  const r = await api("/api/report");
  ST.rows = r.rows;
  renderGrid(keepScroll);
}
const rowMinSide = r => Math.min(...(r.labels || []).map(l => l.metrics.min_side_model_px), 1e9);
function filteredRows() {
  const f = $("#gFilter").value, s = $("#gSort").value;
  let rows = ST.rows.filter(r => {
    if (!f) return true;
    if (f === "unrated") return !r.rating && !r.pending;
    if (["good", "bad"].includes(f)) return r.rating === f;
    if (f === "nolabel") return !r.pending && !(r.labels || []).length;
    if (f === "errors") return (r.errors || []).length > 0;
    if (f === "props") return r.n_props > 0;
    if (f === "autounreviewed") return !!(r.src && r.src.startsWith("auto:"));
    if (f === "pending") return r.pending;
  });
  const sc = r => r.score ?? -1;
  if (s === "score_asc") rows.sort((a, b) => sc(a) - sc(b));
  if (s === "score_desc") rows.sort((a, b) => sc(b) - sc(a));
  if (s === "name") rows.sort((a, b) => a.file.localeCompare(b.file));
  if (s === "minside") rows.sort((a, b) => rowMinSide(a) - rowMinSide(b));
  return rows;
}
function cardHtml(r, i) {
  const rb = ["good", "bad"].map(x => `<button data-r="${x}" class="${r.rating === x ? "on" : ""}">${x}</button>`).join("") + `<button data-r="">×</button>`;
  const errs = (r.errors || []).map(e => `<div class="err">⚠ ${esc(e)}</div>`).join("");
  const labs = (r.labels || []).map(l => `<div class="lab">
      ${["gray", "sobel", "color_sobel"].map(k => l.thumbs?.[k] ? `<img loading="lazy" src="/thumbs/${encodeURIComponent(l.thumbs[k])}?t=${r.t}" title="${k}">` : "").join("")}
      <div class="meta"><span class="dot" style="display:inline-block;background:${classColor(l.cls)}"></span> <b>${esc(l.name)}</b>
        <span class="score">${fmt(l.metrics.score)}</span><br>
        @model ${fmt(l.metrics.min_side_model_px)}px · tương phản ${fmt(l.metrics.dyn_range)} · trong/nền ${fmt(l.metrics.sobel_ratio)}
        ${(l.hints || []).map(h => `<div class="hint">• ${esc(h)}</div>`).join("")}</div></div>`).join("");
  const thumb = r.pending ? `<div class="thumb" style="display:grid;place-items:center;color:#aaa">chưa scan</div>`
    : `<img class="thumb" loading="lazy" src="/thumbs/${encodeURIComponent(r.thumb || "")}?t=${r.t}">`;
  return `<div class="gc ${r.rating || ""}" data-i="${i}" data-file="${esc(r.file)}">
    ${thumb}
    <div class="hd"><span class="name" title="${esc(r.file)}">${esc(r.file)}</span>
      ${r.n_props ? `<span class="small" title="đề xuất auto-label chờ duyệt">🤖${r.n_props}</span>` : ""}
      ${r.src && r.src.startsWith("auto:") ? `<span class="badge-auto" title="Nhãn do auto-label ghi thẳng (${esc(r.src.slice(5))}) - hãy kiểm tra lại">AUTO</span>` : ""}
      ${r.score != null ? `<span class="score" title="điểm thấp nhất các nhãn">${fmt(r.score)}</span>` : ""}
      <span class="seg rb">${rb}</span></div>
    ${errs}${labs}</div>`;
}
function renderGrid(keepScroll) {
  const rows = filteredRows();
  ST.gridRows = rows;
  const g = $("#grid"), y = g.scrollTop;
  g.innerHTML = rows.map(cardHtml).join("") || "<p class='muted'>Không có ảnh nào khớp bộ lọc.</p>";
  if (keepScroll) g.scrollTop = y;
  const all = ST.rows, done = all.filter(r => !r.pending);
  const nolab = done.filter(r => !(r.labels || []).length).length;
  const cnt = k => all.filter(r => r.rating === k).length;
  $("#gCount").textContent = `${rows.length}/${all.length} ảnh · good ${cnt("good")} · bad ${cnt("bad")} · chưa scan ${all.length - done.length}`;
  $("#gBanner").innerHTML = nolab ? `<div class="banner">⚠ ${nolab} ảnh KHÔNG CÓ NHÃN — <a data-f="nolabel">xem các ảnh này</a></div>` : "";
  $$("#gBanner a").forEach(a => a.onclick = () => { $("#gFilter").value = a.dataset.f; renderGrid(); });
  ST.focus = Math.min(ST.focus, rows.length - 1);
  focusCard(ST.focus, false);
}
$("#gFilter").onchange = () => renderGrid();
$("#gSort").onchange = () => renderGrid();
$("#grid").onclick = guard(async e => {
  const card = e.target.closest(".gc"); if (!card) return;
  ST.focus = +card.dataset.i; focusCard(ST.focus, false);
  const b = e.target.closest("button[data-r]");
  if (b) return rate(card.dataset.file, b.dataset.r || null);
  if (e.target.classList.contains("thumb")) openEditor(card.dataset.file);
});
async function rate(file, r) {
  await api("/api/rate", { file, rating: r });
  const row = ST.rows.find(x => x.file === file); if (row) row.rating = r;
  if (ST.view === "grid") {
    const card = $(`.gc[data-file="${CSS.escape(file)}"]`);
    if (card) { card.className = `gc ${r || ""} focus`; $$("button[data-r]", card).forEach(b => b.classList.toggle("on", b.dataset.r === (r || "-"))); }
    $("#gCount").textContent = $("#gCount").textContent.replace(/good \d+ · bad \d+/, ["good", "bad"].map(k => `${k} ${ST.rows.filter(x => x.rating === k).length}`).join(" · "));
  }
  if (ED.file === file) { ED.rating = r; renderRate(); }
}
function focusCard(i, scroll = true) {
  $$(".gc.focus").forEach(c => c.classList.remove("focus"));
  const c = $(`.gc[data-i="${i}"]`);
  if (c) { c.classList.add("focus"); if (scroll) c.scrollIntoView({ block: "nearest" }); }
}

// ======================================================================= editor state
const ED = {
  file: null, W: 0, H: 0, labels: [], props: [], errors: [], sel: null, mode: "select", cls: 0, layer: "original",
  img: null, s: 1, ox: 0, oy: 0, draft: [], mouse: null, undo: [], redo: [], dirty: false, rating: null, showLabels: true,
  drag: null, pxFreeze: false, imgCache: new Map(),
};
const cv = $("#cv"), cx = cv.getContext("2d");

async function openEditor(file, keepView) {
  if (ED.dirty && ED.file && ED.file !== file) await saveLabels(true);
  show("editor", true);
  const d = await api("/api/labels?file=" + encodeURIComponent(file));
  const same = ED.file === file;
  Object.assign(ED, { file, W: d.W, H: d.H, labels: d.labels.map(l => ({ cls: l.cls, pts: l.pts })), props: d.proposals || [],
    errors: d.errors, sel: null, draft: [], undo: [], redo: [], dirty: false, rating: d.rating });
  ST.state.project.names = d.names;
  $("#eFile").textContent = ` ${file} (${d.W}×${d.H}) `;
  renderBanner(); renderRate(); fillClasses(); renderLabelList();
  await setLayer(ED.layer);
  if (!(keepView && same)) fit();
  draw();
  const i = ST.rows.findIndex(r => r.file === file); if (i >= 0) ST.focus = (ST.gridRows || ST.rows).findIndex(r => r.file === file);
}
function renderBanner() {
  $("#eBanner").innerHTML = ED.errors.length ? `<div class="banner">${ED.errors.map(e => "⚠ " + esc(e)).join("<br>")}</div>` : "";
}
function renderRate() { $$("#eRate button").forEach(b => b.classList.toggle("on", b.dataset.r === ED.rating)); }
$$("#eRate button").forEach(b => b.onclick = guard(() => rate(ED.file, ED.rating === b.dataset.r ? null : b.dataset.r)));

function loadImg(url) {
  if (ED.imgCache.has(url)) return ED.imgCache.get(url);
  const p = new Promise((res, rej) => { const im = new Image(); im.onload = () => res(im); im.onerror = () => rej(new Error("Không tải được lớp ảnh")); im.src = url; });
  ED.imgCache.set(url, p);
  if (ED.imgCache.size > 40) ED.imgCache.delete(ED.imgCache.keys().next().value);
  p.catch(() => ED.imgCache.delete(url));
  return p;
}
const layerUrl = (f, l) => `/api/layer?file=${encodeURIComponent(f)}&name=${l}`;
async function setLayer(l) {
  ED.layer = l; $("#eLayer").value = l;
  try { ED.img = await loadImg(layerUrl(ED.file, l)); }
  catch (e) { toast(`Lớp "${l}" không có: ${e.message}`, true); ED.img = await loadImg(layerUrl(ED.file, "original")); }
  draw();
}
$("#eLayer").onchange = e => setLayer(e.target.value);
function cycleLayer(d) {
  const ks = ST.state.layers.map(x => x[0]);
  setLayer(ks[(ks.indexOf(ED.layer) + d + ks.length) % ks.length]);
}

function setMode(m) { ED.mode = m; ED.draft = []; $$("#eModes button").forEach(b => b.classList.toggle("on", b.dataset.mode === m)); draw(); }
$$("#eModes button").forEach(b => b.onclick = () => setMode(b.dataset.mode));
$("#eShow").onchange = e => { ED.showLabels = e.target.checked; draw(); };

function fillClasses() {
  const names = ST.state?.project?.names || [];
  $("#eClass").innerHTML = names.map((n, i) => `<option value="${i}">${i < 9 ? i + 1 + ". " : ""}${esc(n)}</option>`).join("") || "<option>(chưa có class)</option>";
  ED.cls = Math.min(ED.cls, Math.max(0, names.length - 1)); $("#eClass").value = ED.cls;
  renderClassTab(); renderAutoClasses();
}
$("#eClass").onchange = e => { ED.cls = +e.target.value; if (ED.sel?.type === "label") { pushUndo(); ED.labels[ED.sel.i].cls = ED.cls; changed(); } };

// ======================================================================= geometry
const sub = (a, b) => [a[0] - b[0], a[1] - b[1]], add = (a, b) => [a[0] + b[0], a[1] + b[1]], mul = (a, k) => [a[0] * k, a[1] * k];
const dot = (a, b) => a[0] * b[0] + a[1] * b[1], norm = a => Math.hypot(a[0], a[1]);
const unit = a => { const n = norm(a) || 1; return [a[0] / n, a[1] / n]; };
const centroid = p => [p.reduce((s, q) => s + q[0], 0) / p.length, p.reduce((s, q) => s + q[1], 0) / p.length];
function hull(pts) {
  const p = [...pts].sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  const cr = (o, a, b) => (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
  const lo = [], up = [];
  for (const q of p) { while (lo.length >= 2 && cr(lo[lo.length - 2], lo[lo.length - 1], q) <= 0) lo.pop(); lo.push(q); }
  for (const q of p.reverse()) { while (up.length >= 2 && cr(up[up.length - 2], up[up.length - 1], q) <= 0) up.pop(); up.push(q); }
  return lo.slice(0, -1).concat(up.slice(0, -1));
}
function minAreaRect(pts) {
  const h = hull(pts); if (h.length < 3) return pts.map(p => [...p]);
  let best = null;
  for (let i = 0; i < h.length; i++) {
    const u = unit(sub(h[(i + 1) % h.length], h[i])), v = [-u[1], u[0]];
    let a = Infinity, b = -Infinity, c = Infinity, d = -Infinity;
    for (const q of h) { const x = dot(q, u), y = dot(q, v); a = Math.min(a, x); b = Math.max(b, x); c = Math.min(c, y); d = Math.max(d, y); }
    const area = (b - a) * (d - c);
    if (!best || area < best.area) best = { area, u, v, a, b, c, d };
  }
  const P = (x, y) => add(mul(best.u, x), mul(best.v, y));
  return [P(best.a, best.c), P(best.b, best.c), P(best.b, best.d), P(best.a, best.d)];
}
function isRect(p) {
  if (p.length !== 4) return false;
  for (let i = 0; i < 4; i++) {
    const a = unit(sub(p[(i + 1) % 4], p[i])), b = unit(sub(p[(i + 3) % 4], p[i]));
    if (Math.abs(dot(a, b)) > 0.03) return false;
  }
  return true;
}
function rectFrom3(p1, p2, m) {
  const u = unit(sub(p2, p1)), n = [-u[1], u[0]], d = dot(sub(m, p1), n);
  return [p1, p2, add(p2, mul(n, d)), add(p1, mul(n, d))];
}
function inPoly(pt, p) {
  let c = false;
  for (let i = 0, j = p.length - 1; i < p.length; j = i++)
    if ((p[i][1] > pt[1]) !== (p[j][1] > pt[1]) && pt[0] < (p[j][0] - p[i][0]) * (pt[1] - p[i][1]) / (p[j][1] - p[i][1]) + p[i][0]) c = !c;
  return c;
}
function rotate(p, deg) {
  const c = centroid(p), t = deg * Math.PI / 180, cs = Math.cos(t), sn = Math.sin(t);
  return p.map(q => { const d = sub(q, c); return [c[0] + d[0] * cs - d[1] * sn, c[1] + d[0] * sn + d[1] * cs]; });
}
function dragRectCorner(p, i, q) {
  // giữ hình chữ nhật & góc xoay: góc đối diện cố định, chiếu điểm kéo lên 2 trục
  const o = p[(i + 2) % 4], a = unit(sub(p[(i + 1) % 4], p[(i + 2) % 4])), b = unit(sub(p[(i + 3) % 4], p[(i + 2) % 4]));
  const d = sub(q, o), da = dot(d, a), db = dot(d, b);
  const n = [...p];
  n[i] = add(o, add(mul(a, da), mul(b, db))); n[(i + 1) % 4] = add(o, mul(a, da)); n[(i + 3) % 4] = add(o, mul(b, db));
  return n;
}

// ======================================================================= canvas
function resizeCanvas() {
  const r = $("#cwrap").getBoundingClientRect(), dpr = devicePixelRatio || 1;
  cv.width = Math.max(1, r.width * dpr); cv.height = Math.max(1, r.height * dpr); draw();
}
new ResizeObserver(resizeCanvas).observe($("#cwrap"));
function fit() {
  const r = $("#cwrap").getBoundingClientRect();
  if (!ED.W || !r.width) return;
  ED.s = Math.min(r.width / ED.W, (r.height - 24) / ED.H) * 0.97;
  ED.ox = (r.width - ED.W * ED.s) / 2; ED.oy = (r.height - 24 - ED.H * ED.s) / 2;
}
const toImg = e => { const r = cv.getBoundingClientRect(); return [(e.clientX - r.left - ED.ox) / ED.s, (e.clientY - r.top - ED.oy) / ED.s]; };

function poly(p, close = true) { cx.beginPath(); p.forEach((q, i) => i ? cx.lineTo(q[0], q[1]) : cx.moveTo(q[0], q[1])); if (close) cx.closePath(); }
function draw() {
  const dpr = devicePixelRatio || 1;
  cx.setTransform(1, 0, 0, 1, 0, 0); cx.clearRect(0, 0, cv.width, cv.height);
  if (!ED.img) return;
  cx.setTransform(dpr * ED.s, 0, 0, dpr * ED.s, dpr * ED.ox, dpr * ED.oy);
  cx.imageSmoothingEnabled = ED.s < 2;           // phóng to thì hiện pixel thật
  cx.drawImage(ED.img, 0, 0, ED.W, ED.H);
  const lw = 2 / ED.s;
  if (ED.showLabels) {
    ED.props.forEach((p, i) => {
      const sel = ED.sel?.type === "prop" && ED.sel.i === i;
      cx.setLineDash([6 / ED.s, 4 / ED.s]); cx.lineWidth = (sel ? 3 : 1.5) * lw / 2 * 1.4; cx.strokeStyle = classColor(p.cls);
      poly(p.pts); cx.stroke(); cx.setLineDash([]);
      label(p.pts, `${names()[p.cls] ?? p.cls} ${p.score.toFixed(2)}?`, classColor(p.cls));
    });
    ED.labels.forEach((L, i) => {
      const sel = ED.sel?.type === "label" && ED.sel.i === i, col = classColor(L.cls);
      cx.lineWidth = sel ? lw * 1.6 : lw; cx.strokeStyle = col; poly(L.pts); cx.stroke();
      if (sel) { cx.fillStyle = col.replace("55%)", "55% / .12)"); cx.fill(); }
      // đánh dấu cạnh đầu (điểm 1 -> 2) để biết hướng
      cx.beginPath(); cx.arc(L.pts[0][0], L.pts[0][1], 3 / ED.s, 0, 7); cx.fillStyle = col; cx.fill();
      if (sel) L.pts.forEach(q => { cx.fillStyle = "#fff"; cx.strokeStyle = col; cx.lineWidth = lw * .75; cx.beginPath(); cx.rect(q[0] - 4 / ED.s, q[1] - 4 / ED.s, 8 / ED.s, 8 / ED.s); cx.fill(); cx.stroke(); });
      label(L.pts, `${i + 1}. ${names()[L.cls] ?? "#" + L.cls}`, col);
    });
  }
  // bản nháp
  const m = ED.mouse, col = classColor(ED.cls);
  if (ED.draft.length && m) {
    cx.strokeStyle = col; cx.lineWidth = lw; cx.setLineDash([4 / ED.s, 3 / ED.s]);
    let p = ED.draft.concat([m]);
    if (ED.mode === "tri" && ED.draft.length === 2) p = rectFrom3(ED.draft[0], ED.draft[1], m);
    poly(p, ED.mode === "tri" && ED.draft.length === 2 || p.length === 4); cx.stroke(); cx.setLineDash([]);
    ED.draft.forEach(q => { cx.fillStyle = col; cx.beginPath(); cx.arc(q[0], q[1], 3.5 / ED.s, 0, 7); cx.fill(); });
  }
  if (m && ED.mode !== "select") {   // thước ngắm
    cx.strokeStyle = "rgba(255,255,255,.35)"; cx.lineWidth = 1 / ED.s;
    cx.beginPath(); cx.moveTo(m[0], 0); cx.lineTo(m[0], ED.H); cx.moveTo(0, m[1]); cx.lineTo(ED.W, m[1]); cx.stroke();
  }
}
function label(p, text, col) {
  const top = p.reduce((a, b) => (b[1] < a[1] ? b : a));
  const fs = 12 / ED.s;
  cx.font = `${fs}px system-ui`; const w = cx.measureText(text).width;
  cx.fillStyle = col; cx.fillRect(top[0], top[1] - fs * 1.35, w + 6 / ED.s, fs * 1.35);
  cx.fillStyle = "#000"; cx.fillText(text, top[0] + 3 / ED.s, top[1] - fs * .35);
}
const names = () => ST.state?.project?.names || [];

// --- chuột
function hitVertex(pt) {
  if (ED.sel?.type !== "label") return -1;
  const p = ED.labels[ED.sel.i].pts;
  return p.findIndex(q => norm(sub(q, pt)) * ED.s < 8);
}
function hitShape(pt) {
  for (let i = ED.labels.length - 1; i >= 0; i--) if (inPoly(pt, ED.labels[i].pts)) return { type: "label", i };
  for (let i = ED.props.length - 1; i >= 0; i--) if (inPoly(pt, ED.props[i].pts)) return { type: "prop", i };
  return null;
}
let spaceDown = false;
cv.addEventListener("contextmenu", e => e.preventDefault());
cv.addEventListener("mousedown", e => {
  const pt = toImg(e);
  if (e.button === 1 || e.button === 2 || spaceDown) { ED.drag = { type: "pan", x: e.clientX, y: e.clientY, ox: ED.ox, oy: ED.oy }; return; }
  if (e.button !== 0) return;
  if (ED.mode === "select") {
    const v = hitVertex(pt);
    if (v >= 0) { pushUndo(); const L = ED.labels[ED.sel.i]; ED.drag = { type: "vertex", v, rect: isRect(L.pts) }; return; }
    const h = hitShape(pt);
    select(h);
    if (h?.type === "label") { pushUndo(); ED.drag = { type: "move", last: pt, moved: false }; }
    return;
  }
  if (!names().length) { toast("Chưa có class: vào tab Class để thêm", true); return; }
  ED.draft.push(pt);
  if (ED.mode === "quad" && ED.draft.length === 4) {
    finishShape($("#eRect").checked ? minAreaRect(ED.draft) : ED.draft);
  } else if (ED.mode === "tri" && ED.draft.length === 3) {
    finishShape(rectFrom3(...ED.draft));
  }
  draw();
});
window.addEventListener("mousemove", e => {
  if (ST.view !== "editor") return;
  const pt = toImg(e);
  const over = e.target === cv;
  ED.mouse = over || ED.drag ? pt : null;
  const d = ED.drag;
  if (d?.type === "pan") { ED.ox = d.ox + e.clientX - d.x; ED.oy = d.oy + e.clientY - d.y; }
  else if (d?.type === "vertex") {
    const L = ED.labels[ED.sel.i];
    L.pts = d.rect ? dragRectCorner(L.pts, d.v, pt) : L.pts.map((q, k) => k === d.v ? pt : q); ED.dirty = true;
  } else if (d?.type === "move") {
    const dl = sub(pt, d.last); d.last = pt; d.moved = true;
    const L = ED.labels[ED.sel.i]; L.pts = L.pts.map(q => add(q, dl)); ED.dirty = true;
  }
  draw();
  if (over) hoverPixel(pt);
});
window.addEventListener("mouseup", () => {
  const d = ED.drag; ED.drag = null;
  if (d && (d.type === "vertex" || (d.type === "move" && d.moved))) changed();
  else if (d?.type === "move" && !d.moved) ED.undo.pop();
});
cv.addEventListener("wheel", e => {
  e.preventDefault();
  const r = cv.getBoundingClientRect(), mx = e.clientX - r.left, my = e.clientY - r.top;
  const k = Math.exp(-e.deltaY * 0.0015), ns = Math.min(64, Math.max(0.02, ED.s * k));
  ED.ox = mx - (mx - ED.ox) * ns / ED.s; ED.oy = my - (my - ED.oy) * ns / ED.s; ED.s = ns; draw();
}, { passive: false });

function finishShape(pts) {
  pushUndo();
  ED.labels.push({ cls: ED.cls, pts: pts.map(q => [+q[0].toFixed(2), +q[1].toFixed(2)]) });
  ED.draft = [];
  select({ type: "label", i: ED.labels.length - 1 });
  changed();
}
function select(s) {
  ED.sel = s;
  if (s?.type === "label") { ED.cls = ED.labels[s.i].cls; $("#eClass").value = ED.cls; }
  renderLabelList(); draw(); scheduleInspect();
}

// --- undo / thay đổi
function pushUndo() { ED.undo.push(JSON.stringify({ l: ED.labels, p: ED.props })); if (ED.undo.length > 200) ED.undo.shift(); ED.redo = []; }
function restore(s) { const o = JSON.parse(s); ED.labels = o.l; ED.props = o.p; ED.sel = null; ED.dirty = true; renderLabelList(); draw(); }
function undo() { if (!ED.undo.length) return; ED.redo.push(JSON.stringify({ l: ED.labels, p: ED.props })); restore(ED.undo.pop()); }
function redo() { if (!ED.redo.length) return; ED.undo.push(JSON.stringify({ l: ED.labels, p: ED.props })); restore(ED.redo.pop()); }
function changed() { ED.dirty = true; $("#eSave").textContent = "Lưu* (Ctrl+S)"; renderLabelList(); draw(); scheduleInspect(); }

async function saveLabels(silent) {
  const r = await api("/api/labels", { file: ED.file, labels: ED.labels, proposals: ED.props });
  ED.dirty = false; $("#eSave").textContent = "Lưu (Ctrl+S)";
  const row = ST.rows.find(x => x.file === ED.file); if (row) Object.assign(row, r.entry, { pending: false });
  ED.errors = (r.entry.errors || []).filter(e => !e.startsWith("KHÔNG CÓ NHÃN") || !ED.labels.length);
  renderBanner();
  if (!silent) toast("Đã lưu nhãn");
}
$("#eSave").onclick = guard(() => saveLabels());

function nav(d) {
  const rows = ST.gridRows?.length ? ST.gridRows : ST.rows;
  const i = rows.findIndex(r => r.file === ED.file);
  const n = rows[i + d]; if (n) guard(openEditor)(n.file); else toast(d > 0 ? "Hết ảnh" : "Ảnh đầu tiên");
}
$("#ePrev").onclick = () => nav(-1); $("#eNext").onclick = () => nav(1);

// ======================================================================= panel: nhãn
function renderLabelList() {
  const nm = names();
  const opts = c => nm.map((n, i) => `<option value="${i}" ${i === c ? "selected" : ""}>${esc(n)}</option>`).join("");
  const L = ED.labels.map((l, i) => `<div class="li ${ED.sel?.type === "label" && ED.sel.i === i ? "sel" : ""}" data-t="label" data-i="${i}">
      <span class="dot" style="background:${classColor(l.cls)}"></span>${i + 1}.
      <select data-cls="${i}">${opts(l.cls)}</select><span class="grow small muted">${isRect(l.pts) ? "chữ nhật" : "tứ giác"}</span>
      <button data-del="${i}" title="Xoá (Delete)">✕</button></div>`).join("") || "<p class='muted'>Chưa có nhãn. Chọn chế độ 4 điểm (F) hoặc 3 điểm (R) để vẽ.</p>";
  const P = ED.props.map((p, i) => `<div class="li ${ED.sel?.type === "prop" && ED.sel.i === i ? "sel" : ""}" data-t="prop" data-i="${i}">
      <span class="dot" style="background:${classColor(p.cls)}"></span>${esc(nm[p.cls] ?? p.cls)} <span class="muted small">"${esc(p.text || "")}" ${p.score.toFixed(2)} · ${esc(p.model || "")}</span>
      <span class="grow"></span><button data-acc="${i}">Nhận</button><button data-rej="${i}">Bỏ</button></div>`).join("");
  $("#t-labels").innerHTML = `<h3>Nhãn (${ED.labels.length})</h3>${L}
    ${ED.props.length ? `<h3>Đề xuất auto-label (${ED.props.length})</h3><div class="row"><button id="accAll">Nhận tất cả</button><button id="rejAll">Bỏ tất cả</button></div>${P}` : ""}
    <p class="muted small">V chọn/sửa: kéo đỉnh (chữ nhật giữ nguyên dạng), kéo trong khung để di chuyển, [ ] xoay 1° (Shift: 5°).
    Chuột phải/giữa hoặc Space+kéo: pan · lăn chuột: zoom · Z: vừa khung · L/Shift+L: đổi lớp ảnh · O: gốc ↔ Sobel ·
    1-9 chọn class · Shift+1/2: good/bad · A/D: ảnh trước/sau (tự lưu) · Ctrl+Z/Y.</p>`;
  $$("#t-labels .li").forEach(el => el.onclick = e => { if (e.target.closest("button,select")) return; select({ type: el.dataset.t, i: +el.dataset.i }); });
  $$("#t-labels select[data-cls]").forEach(s => s.onchange = () => { pushUndo(); ED.labels[+s.dataset.cls].cls = +s.value; changed(); });
  $$("#t-labels [data-del]").forEach(b => b.onclick = () => delLabel(+b.dataset.del));
  $$("#t-labels [data-acc]").forEach(b => b.onclick = () => acceptProp(+b.dataset.acc));
  $$("#t-labels [data-rej]").forEach(b => b.onclick = () => rejectProp(+b.dataset.rej));
  if ($("#accAll")) $("#accAll").onclick = () => { pushUndo(); ED.props.forEach(p => ED.labels.push({ cls: p.cls, pts: p.pts })); ED.props = []; ED.sel = null; changed(); };
  if ($("#rejAll")) $("#rejAll").onclick = () => { pushUndo(); ED.props = []; ED.sel = null; changed(); };
}
function delLabel(i) { pushUndo(); ED.labels.splice(i, 1); ED.sel = null; changed(); }
function acceptProp(i) {
  pushUndo(); const p = ED.props.splice(i, 1)[0];
  ED.labels.push({ cls: p.cls, pts: p.pts }); ED.sel = { type: "label", i: ED.labels.length - 1 }; changed();
}
function rejectProp(i) { pushUndo(); ED.props.splice(i, 1); ED.sel = null; changed(); }

// ======================================================================= panel: kiểm tra cạnh
let inspTimer = null;
function scheduleInspect() { clearTimeout(inspTimer); inspTimer = setTimeout(guard(inspect), 350); }
async function inspect() {
  if (!$("#t-inspect").classList.contains("on")) return;
  const s = ED.sel; if (!s) { $("#t-inspect").innerHTML = "<p class='muted'>Chọn 1 nhãn (hoặc đề xuất) để xem.</p>"; return; }
  const pts = (s.type === "label" ? ED.labels : ED.props)[s.i]?.pts; if (!pts) return;
  $("#t-inspect").innerHTML = "<p class='muted'>Đang tính...</p>";
  const d = await api("/api/inspect", { file: ED.file, pts });
  const t = Object.fromEntries(ST.state.layers);
  const m = d.metrics;
  const rows = [["Điểm cạnh (heuristic)", m.score], ["Kích thước (px)", `${fmt(m.w_px)} × ${fmt(m.h_px)}`], ["Góc xoay", m.angle + "°"],
    ["Cạnh ngắn @ model " + ST.state.cfg.model_size, m.min_side_model_px], ["% diện tích ảnh", m.area_pct],
    ["Gray mean / std", `${fmt(m.gray_mean)} / ${fmt(m.gray_std)}`], ["Gray min / max", `${m.gray_min} / ${m.gray_max}`],
    ["Dải xám p1–p99", m.dyn_range], ["Sobel trong / nền", `${fmt(m.sobel_in)} / ${fmt(m.sobel_ring)}`], ["Tỉ lệ trong/nền", m.sobel_ratio],
    ["Sobel @ model", m.sobel_in_model], ["% cạnh mạnh (>Otsu)", m.strong_pct], ["% Canny", m.canny_pct], ["Laplacian var (độ nét)", m.lap_var],
    ["Sobel màu / gray", m.color_gray_ratio], ["% cạnh chỉ thấy ở màu", m.iso_pct], ["Chroma Sobel", m.chroma_in],
    ["YOLO L0 trong/nền", m.f0_ratio], ["YOLO L2 trong/nền", m.f2_ratio], ["YOLO L4 trong/nền", m.f4_ratio]];
  $("#t-inspect").innerHTML = `
    ${d.hints.length ? `<div class="banner" style="font-weight:500">${d.hints.map(h => "• " + esc(h)).join("<br>")}</div>` : ""}
    <h3>Số liệu</h3><table class="m">${rows.map(([k, v]) => `<tr><td>${k}</td><td>${typeof v === "string" ? v : fmt(v)}</td></tr>`).join("")}</table>
    <h3>Histogram 0–255 (xanh: trong nhãn, xám: cả ảnh)</h3><canvas id="hist" width="400" height="110" style="width:100%"></canvas>
    <h3>Model nhìn thấy ở từng imgsz</h3>
    <div class="sweep">${d.sweep.map(s => `<figure><img src="${s.gray}"><img src="${s.sobel}">
      <div>${s.imgsz}: ${fmt(s.w)}×${fmt(s.h)}px</div><div class="muted">mạnh ${fmt(s.strong_pct)}%</div></figure>`).join("")}</div>
    <h3>Các phép xử lí (crop xoay thẳng theo nhãn)</h3>
    <div class="crops">${Object.entries(d.crops).map(([k, u]) => `<figure><img src="${u}" data-layer="${k}" title="click: dùng làm lớp nền"><figcaption>${esc(t[k] || k)}</figcaption></figure>`).join("")}</div>`;
  $$("#t-inspect img[data-layer]").forEach(im => im.onclick = () => setLayer(im.dataset.layer));
  drawHist($("#hist"), d.hist_in, d.hist_all);
}
function drawHist(c, a, b) {
  const g = c.getContext("2d"), W = c.width, H = c.height;
  const na = Math.max(...a), nb = Math.max(...b);
  g.clearRect(0, 0, W, H);
  const bar = (h, n, col) => { g.fillStyle = col; h.forEach((v, i) => { const y = v / (n || 1) * (H - 14); g.fillRect(i / 256 * W, H - 14 - y, W / 256 + .5, y); }); };
  bar(b, nb, "rgba(140,140,140,.45)"); bar(a, na, "rgba(37,99,235,.75)");
  g.fillStyle = "#888"; g.font = "10px system-ui";[0, 64, 128, 192, 255].forEach(v => g.fillText(v, v / 256 * W, H - 2));
}

// ======================================================================= panel: pixel
let pxTimer = null, pxLast = null;
function hoverPixel(pt) {
  const x = Math.floor(pt[0]), y = Math.floor(pt[1]);
  if (x < 0 || y < 0 || x >= ED.W || y >= ED.H) { $("#status").textContent = ""; return; }
  if (pxLast && pxLast[0] === x && pxLast[1] === y) return;
  pxLast = [x, y];
  clearTimeout(pxTimer);
  pxTimer = setTimeout(async () => {
    try {
      const d = await api(`/api/pixels?file=${encodeURIComponent(ED.file)}&x=${x}&y=${y}&n=11`);
      const gx = x - d.x0, gy = y - d.y0;
      $("#status").textContent = `x=${x} y=${y} · gray=${d.gray[gy][gx]} · sobel=${d.sobel[gy][gx]} · RGB(${d.rgb.join(",")}) · zoom ${(ED.s * 100).toFixed(0)}% · lớp: ${ED.layer}`;
      if (!ED.pxFreeze && $("#t-pixel").classList.contains("on")) renderPx(d, gx, gy);
    } catch (e) { }
  }, 60);
}
function renderPx(d, gx, gy) {
  const tbl = (arr, colorFn) => `<table class="px">${arr.map((r, i) => `<tr>${r.map((v, j) => {
    const [bg, fg] = colorFn(v); const me = i === gy && j === gx ? "outline:2px solid red" : "";
    return `<td style="background:${bg};color:${fg};${me}">${v}</td>`;
  }).join("")}</tr>`).join("")}</table>`;
  const ref = d.ref;
  $("#pxTables").innerHTML = `<div class="small">Gray (0–255), tâm (${d.x0 + gx}, ${d.y0 + gy})${ED.pxFreeze ? " · ĐANG GIỮ" : ""}</div>` +
    tbl(d.gray, v => [`rgb(${v},${v},${v})`, v < 128 ? "#fff" : "#000"]) +
    `<div class="small">Sobel magnitude (sáng = cạnh mạnh, thang p99.5 = ${ref.toFixed(0)})</div>` +
    tbl(d.sobel, v => { const k = Math.min(1, v / ref); return [`hsl(25 90% ${8 + k * 55}%)`, k > .55 ? "#000" : "#fff"]; });
}

// ======================================================================= panel: auto-label
let MODELS = [];
async function loadModels() {
  const d = await api("/api/models"); MODELS = d.models;
  const opts = MODELS.map(m => `<option value="${m.id}">${esc(m.name)}</option>`).join("");
  $("#aModel").innerHTML = opts; $("#hModel").innerHTML = opts;
  if (MODELS.some(m => m.id === "sam3")) $("#hModel").value = "sam3";   // mặc định chính xác nhất cho chạy cả thư mục
  $("#mType").innerHTML = Object.entries(d.types).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("");
  noteModel("aModel", "aNote"); noteModel("hModel", "hNote");
}
function noteModel(selId, noteId) {
  const m = MODELS.find(x => x.id === $("#" + selId).value);
  $("#" + noteId).textContent = m ? `${m.type} · ${m.weights}${m.sam ? " + " + m.sam : ""} · lần chạy đầu sẽ tải weights về edgetool/models/ (dùng local về sau)` : "";
}
$("#aModel").onchange = () => noteModel("aModel", "aNote");
$("#hModel").onchange = () => noteModel("hModel", "hNote");
$("#aConf").oninput = e => $("#aConfV").textContent = (+e.target.value).toFixed(2);
$("#hConf").oninput = e => $("#hConfV").textContent = (+e.target.value).toFixed(2);
function renderClassChips(containerId) {
  const p = ST.state?.project; if (!p) return;
  const el = $("#" + containerId); if (!el) return;
  const prev = new Set($$("input", el).filter(i => i.checked).map(i => +i.value));
  el.innerHTML = p.names.map((n, i) => `<label><input type="checkbox" value="${i}" ${!prev.size || prev.has(i) ? "checked" : ""}> ${esc(n)}
     <span class="muted small">${esc(p.prompts?.[n] ? "→ " + p.prompts[n] : "")}</span></label>`).join("") || "<span class='muted'>Chưa có class</span>";
}
function renderAutoClasses() { renderClassChips("aClasses"); renderClassChips("hClasses"); }
const autoBody = () => ({ model: $("#aModel").value, conf: +$("#aConf").value, classes: $$("#aClasses input:checked").map(i => +i.value) });
const homeAutoBody = () => ({ model: $("#hModel").value, conf: +$("#hConf").value, classes: $$("#hClasses input:checked").map(i => +i.value),
  commit: true, mode: $('input[name="hMode"]:checked').value, only_missing: $('input[name="hScope"]:checked').value === "missing" });
$("#hRun").onclick = guard(async () => {
  const target = $("#hFolderPath").value.trim().replace(/^"|"$/g, "");
  if (!target) { toast("Chọn thư mục ảnh cần auto-label", true); return; }
  // Folder trong ô khác folder đang mở -> mở folder đó trước (không scan: auto-label tự xử lý từng ảnh),
  // để KHÔNG BAO GIỜ chạy nhầm lên folder đang mở từ trước.
  const p0 = ST.state?.project;
  let names = p0 ? $$("#hClasses input:checked").map(i => p0.names[+i.value]) : [];
  if (!p0 || !samePath(target, p0.folder)) {
    await openFolder(target, true, true);
    const p1 = ST.state.project;
    // giữ lại lựa chọn class theo TÊN; class chưa có ở folder mới thì thêm vào data.yaml của nó (kèm prompt cũ)
    for (const n of names) if (!p1.names.includes(n)) await addClass(n, p0?.prompts?.[n] || "");
    $$("#hClasses input").forEach(cb => cb.checked = names.includes(ST.state.project.names[+cb.value]));
  }
  const body = homeAutoBody();
  if (!body.classes.length) { toast("Chọn ít nhất 1 class để nhận diện", true); return; }
  const p = ST.state.project;
  const cls = body.classes.map(i => p.names[i]).join(", ");
  const warn = (body.only_missing
    ? "Chỉ chạy trên ảnh CHƯA có nhãn (bỏ qua ảnh đã có nhãn)."
    : body.mode === "overwrite"
      ? "GHI ĐÈ TOÀN BỘ: xoá nhãn hiện có (kể cả nhãn nhập từ Open Images/YOLO) rồi ghi nhãn mới, trên MỌI ảnh."
      : "Thêm vào nhãn hiện có (giữ nhãn cũ, bỏ qua box trùng), trên MỌI ảnh.")
    + `\n\nThư mục ảnh: ${p.folder}  (${p.n_images} ảnh)\nClass: ${cls}\nLưu vào: ${p.checked}\n\nTiếp tục?`;
  if (!confirm(warn)) return;
  $("#hMsg").textContent = "Đang chạy..."; $("#hRun").disabled = true;
  try {
    await api("/api/autolabel_batch", body);
    pollJob();
    $("#hMsg").innerHTML = `Đang chạy trên <b>${esc(p.folder)}</b> — tiến độ ở thanh trên cùng.<br>Kết quả lưu ở <code>${esc(p.checked)}</code>`;
  } finally { $("#hRun").disabled = false; }
});
function autoDone(j) {
  const p = ST.state?.project; if (!p) return;
  $("#hMsg").innerHTML = `<b>Xong ${j.done}/${j.total} ảnh</b>${j.errors.length ? ` · ${j.errors.length} lỗi` : ""}. Dataset đã label nằm ở
    <code>${esc(p.checked)}</code> (images\\ + labels\\ + data.yaml).<br>
    <button id="hGoCheck" class="primary">Xem & sửa trong Check (lọc AUTO)</button> <button id="hGoReveal">📂 Mở thư mục</button>`;
  $("#hGoCheck").onclick = () => { show("grid"); $("#gFilter").value = "autounreviewed"; renderGrid(); };
  $("#hGoReveal").onclick = guard(() => api("/api/reveal", { path: p.checked }));
  guard(loadProjects)();
}
$("#aRun").onclick = guard(async () => {
  if (ED.dirty) await saveLabels(true);
  $("#aMsg").textContent = "Đang chạy (lần đầu có thể phải tải weights, vài phút)..."; $("#aRun").disabled = true;
  try {
    const t = performance.now();
    const d = await api("/api/autolabel", { file: ED.file, ...autoBody() });
    pushUndo(); ED.props = d.proposals; ED.sel = null; changed();
    $("#aMsg").textContent = `${d.proposals.length} đề xuất trong ${((performance.now() - t) / 1000).toFixed(1)}s` +
      (d.skipped_classes?.length ? ` · bỏ qua class không có trong data.yaml: ${d.skipped_classes.join(", ")}` : "");
  } catch (e) { $("#aMsg").textContent = "Lỗi: " + e.message; throw e; }
  finally { $("#aRun").disabled = false; }
});
$("#aBatch").onclick = guard(async () => {
  if (!confirm("Chạy auto-label cho mọi ảnh CHƯA có nhãn? Kết quả lưu thành đề xuất, bạn duyệt lại từng ảnh.")) return;
  await api("/api/autolabel_batch", { ...autoBody(), only_missing: true }); pollJob();
});
$("#aUnload").onclick = guard(async () => { await api("/api/unload_model", {}); toast("Đã giải phóng model"); });
$("#aDel").onclick = guard(async () => {
  const m = MODELS.find(x => x.id === $("#aModel").value); if (!m || !confirm(`Xoá "${m.name}" khỏi danh sách? (không xoá file weights)`)) return;
  await api("/api/models/" + m.id, undefined, "DELETE"); await loadModels();
});
const dlg = $("#dlgModel");
$("#aAdd").onclick = () => { $("#mName").value = ""; $("#mW").value = ""; $("#mType").value = "yolo"; $("#mSamRow").hidden = true; dlg.showModal(); };
$("#mType").onchange = e => $("#mSamRow").hidden = e.target.value !== "gdino_sam2";
$("#mBrowse").onclick = guard(async () => { const r = await api("/api/pick_file", {}); if (r.path) $("#mW").value = r.path; });
$("#mCancel").onclick = () => dlg.close();
$("#mOk").onclick = guard(async () => {
  const d = await api("/api/models", { name: $("#mName").value, type: $("#mType").value, weights: $("#mW").value.trim().replace(/^"|"$/g, ""), sam: $("#mSam").value });
  dlg.close(); await loadModels(); $("#aModel").value = d.model.id; noteModel(); toast("Đã thêm model");
});

// ======================================================================= panel: class
function renderClassTab() {
  const p = ST.state?.project; if (!p) return;
  $("#cList").innerHTML = p.names.map((n, i) => `<div class="li"><span class="dot" style="background:${classColor(i)}"></span>${i}
     <input value="${esc(n)}" data-rn="${i}" style="width:130px" title="Đổi tên (Enter)">
     <input value="${esc(p.prompts?.[n] || "")}" data-pr="${i}" class="grow" placeholder="prompt auto-label (mặc định = tên)"></div>`).join("");
  $$("#cList [data-rn]").forEach(inp => inp.onchange = guard(async () => { const d = await api("/api/classes", { action: "rename", idx: +inp.dataset.rn, name: inp.value }); p.names = d.names; fillClasses(); draw(); }));
  $$("#cList [data-pr]").forEach(inp => inp.onchange = guard(async () => { const d = await api("/api/classes", { action: "prompt", idx: +inp.dataset.pr, prompt: inp.value }); p.prompts = d.prompts; renderAutoClasses(); toast("Đã lưu prompt"); }));
}
$("#cAdd").onclick = guard(async () => {
  const name = $("#cNew").value.trim(); if (!name) return;
  const d = await api("/api/classes", { action: "add", name }); ST.state.project.names = d.names; $("#cNew").value = "";
  ED.cls = d.names.indexOf(name); fillClasses(); toast(`Đã thêm class "${name}" vào data.yaml`);
});
$("#cNew").onkeydown = e => { if (e.key === "Enter") $("#cAdd").click(); };
const PPE_PRESET = [["Hat", "hat, cap, helmet"], ["Mask", "face mask, surgical mask"], ["Backpack", "backpack, rucksack"],
  ["Gloves", "gloves"], ["Goggles", "goggles, safety glasses"]];
async function addClass(name, prompt) {
  name = name.trim();
  if (!name) throw new Error("Nhập tên class");
  const p = ST.state.project;
  let d = await api("/api/classes", { action: "add", name }); p.names = d.names;
  const idx = p.names.indexOf(name);
  if (prompt && prompt.trim()) { d = await api("/api/classes", { action: "prompt", idx, prompt: prompt.trim() }); p.prompts = d.prompts; }
  fillClasses(); renderClassTab();
  return idx;
}
async function addPpePreset() {
  const p = ST.state.project;
  let added = 0;
  for (const [name, prompt] of PPE_PRESET) {
    if (p.names.includes(name)) continue;
    await addClass(name, prompt);
    added++;
  }
  return added;
}
$("#cPreset").onclick = guard(async () => {
  const added = await addPpePreset();
  toast(added ? `Đã thêm ${added} class gợi ý` : "Các class gợi ý đã có sẵn cả rồi");
});

// --- panel "Auto-label cả thư mục": thêm class ngay tại đây, khỏi phải sang tab Label → Class
$("#hClassAdd").onclick = guard(async () => {
  const name = $("#hClassNew").value;
  const idx = await addClass(name, $("#hClassPrompt").value);
  $("#hClassNew").value = ""; $("#hClassPrompt").value = "";
  const cb = $(`#hClasses input[value="${idx}"]`); if (cb) cb.checked = true;   // tick sẵn để dùng luôn
  toast(`Đã thêm class "${name.trim()}"`);
});
$("#hClassNew").onkeydown = e => { if (e.key === "Enter") { e.preventDefault(); $("#hClassAdd").click(); } };
$("#hClassPrompt").onkeydown = e => { if (e.key === "Enter") { e.preventDefault(); $("#hClassAdd").click(); } };
$("#hClassPreset").onclick = guard(async () => {
  const added = await addPpePreset();
  if (added) $$("#hClasses input").forEach(cb => { if (PPE_PRESET.some(([n]) => ST.state.project.names[+cb.value] === n)) cb.checked = true; });
  toast(added ? `Đã thêm ${added} class gợi ý - đã tick sẵn ở trên` : "Các class gợi ý đã có sẵn cả rồi");
});

// ======================================================================= tabs
$$("#sTabs button").forEach(b => b.onclick = () => {
  $$("#sTabs button").forEach(x => x.classList.toggle("on", x === b));
  $$(".side .tab").forEach(t => t.classList.toggle("on", t.id === "t-" + b.dataset.tab));
  if (b.dataset.tab === "inspect") scheduleInspect();
  if (b.dataset.tab === "classes") renderClassTab();
});

// ======================================================================= phím tắt
window.addEventListener("keydown", e => {
  const typing = /INPUT|SELECT|TEXTAREA/.test(document.activeElement?.tagName) && document.activeElement.type !== "checkbox" && document.activeElement.type !== "range";
  if (typing || dlg.open) return;
  if (ST.view === "grid") {
    const rows = ST.gridRows || [];
    if (["ArrowRight", "ArrowDown", "j"].includes(e.key)) { ST.focus = Math.min(rows.length - 1, ST.focus + 1); focusCard(ST.focus); e.preventDefault(); }
    else if (["ArrowLeft", "ArrowUp", "k"].includes(e.key)) { ST.focus = Math.max(0, ST.focus - 1); focusCard(ST.focus); e.preventDefault(); }
    else if (["1", "2", "0"].includes(e.key) && rows[ST.focus]) {
      guard(rate)(rows[ST.focus].file, { 1: "good", 2: "bad", 0: null }[e.key]);
      if (e.key !== "0") { ST.focus = Math.min(rows.length - 1, ST.focus + 1); focusCard(ST.focus); }
    }
    else if (e.key === "Enter" && rows[ST.focus]) guard(openEditor)(rows[ST.focus].file);
    return;
  }
  if (ST.view !== "editor") return;
  const k = e.key.toLowerCase();
  if (e.ctrlKey && k === "s") { e.preventDefault(); guard(saveLabels)(); return; }
  if (e.ctrlKey && k === "z") { e.preventDefault(); undo(); return; }
  if (e.ctrlKey && k === "y") { e.preventDefault(); redo(); return; }
  if (e.ctrlKey) return;
  if (e.shiftKey && ["Digit1", "Digit2"].includes(e.code)) { const r = { Digit1: "good", Digit2: "bad" }[e.code]; guard(rate)(ED.file, ED.rating === r ? null : r); return; }
  if (e.code === "Space") { spaceDown = true; e.preventDefault(); return; }
  if (/^[1-9]$/.test(e.key) && +e.key <= names().length) { ED.cls = +e.key - 1; $("#eClass").value = ED.cls; $("#eClass").onchange({ target: $("#eClass") }); return; }
  if (k === "v") setMode("select");
  else if (k === "f") setMode("quad");
  else if (k === "r") setMode("tri");
  else if (k === "escape") { ED.draft = []; ED.sel = null; renderLabelList(); draw(); }
  else if (k === "delete" || k === "backspace") {
    if (ED.sel?.type === "label") delLabel(ED.sel.i); else if (ED.sel?.type === "prop") rejectProp(ED.sel.i);
    else if (ED.draft.length) { ED.draft.pop(); draw(); }
  }
  else if (k === "enter" && ED.sel?.type === "prop") acceptProp(ED.sel.i);
  else if ((k === "[" || k === "]" || k === "{" || k === "}") && ED.sel?.type === "label") {
    pushUndo(); const L = ED.labels[ED.sel.i]; L.pts = rotate(L.pts, (k === "[" || k === "{" ? -1 : 1) * (e.shiftKey ? 5 : 1)); changed();
  }
  else if (k === "z") { fit(); draw(); }
  else if (k === "h") { ED.showLabels = !ED.showLabels; $("#eShow").checked = ED.showLabels; draw(); }
  else if (k === "l") cycleLayer(e.shiftKey ? -1 : 1);
  else if (k === "o") setLayer(ED.layer === "original" ? "sobel" : "original");
  else if (k === "p") { ED.pxFreeze = !ED.pxFreeze; toast(ED.pxFreeze ? "Giữ bảng pixel" : "Thả bảng pixel"); }
  else if (k === "a") nav(-1);
  else if (k === "d") nav(1);
});
window.addEventListener("keyup", e => { if (e.code === "Space") spaceDown = false; });
window.addEventListener("beforeunload", e => { if (ED.dirty) { e.preventDefault(); e.returnValue = ""; } });

// ======================================================================= khởi động
guard(async () => {
  $("#sTabs button").click();
  setMode("select");
  await loadState();
  await loadModels();
  show(ST.state.project ? "grid" : "home");
})();
