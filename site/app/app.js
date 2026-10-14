/* MMP Studio — browser client.
   Everything goes through this site's own API (/api/*) with a session cookie:
   no third-party SDK, no keys in the browser. The server authenticates every
   request and scopes each query to the signed-in account. */

const CFG = window.MMP_CONFIG || {};
export const configured = true;      // the API is part of this site

export const $ = (s, r = document) => r.querySelector(s);
export const $$ = (s, r = document) => [...r.querySelectorAll(s)];
export const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
export const fmt = (v, d = 0) => v == null ? "–" : Number(v).toLocaleString(undefined, { maximumFractionDigits: d });
export function fmtT(t){ if(!t) return "0 t"; return t>=1e9?(t/1e9).toFixed(2)+" Bt":t>=1e6?(t/1e6).toFixed(2)+" Mt":t>=1e3?(t/1e3).toFixed(0)+" kt":Math.round(t)+" t"; }
export function fmtC(v,u){ if(v==null) return "–";
  if(u==="oz") return v>=1e6?(v/1e6).toFixed(2)+" Moz":v>=1e3?(v/1e3).toFixed(0)+" koz":Math.round(v)+" oz";
  if(u==="lb") return v>=1e9?(v/1e9).toFixed(2)+" Blb":v>=1e6?(v/1e6).toFixed(1)+" Mlb":Math.round(v)+" lb";
  return fmtT(v); }
export const slugify = s => (s||"").toLowerCase().replace(/[^a-z0-9]+/g,"-").replace(/^-|-$/g,"").slice(0,48) || "project";

export function banner(msg, kind = "err", where = "#msg") {
  const el = $(where); if (!el) return msg && alert(msg);
  el.innerHTML = msg ? `<div class="${kind === "err" ? "err" : "ok"}">${esc(msg)}</div>` : "";
}

/* -------------------------------------------------------------------- api */
const API = (CFG.API_BASE || "/api").replace(/\/$/, "");

export async function api(path, { method = "GET", body, form } = {}) {
  const opt = { method, credentials: "same-origin", headers: {} };
  if (form) opt.body = form;
  else if (body !== undefined) { opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(body); }
  const r = await fetch(API + path, opt);
  let data = null;
  try { data = await r.json(); } catch (e) { /* no body */ }
  if (!r.ok) {
    const msg = (data && (data.detail || data.message)) || `${r.status} ${r.statusText}`;
    const err = new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
    err.status = r.status;
    throw err;
  }
  return data;
}

/* ---------------------------------------------------------------- session */
let _me = null;
export async function session(refresh = false) {
  if (_me && !refresh) return _me;
  try { const r = await api("/me"); _me = r.user ? { user: r.user, used_bytes: r.used_bytes } : null; }
  catch (e) { _me = null; }
  return _me;
}
export async function requireUser(redirect = "index.html") {
  const s = await session();
  if (!s) { location.href = redirect + "?next=" + encodeURIComponent(location.pathname.split("/").pop() + location.search); return null; }
  return s.user;
}
export async function signIn(email, password) { const r = await api("/auth/login", { method: "POST", body: { email, password } }); _me = { user: r.user }; return r.user; }
export async function signUp({ email, password, name, invite }) {
  const r = await api("/auth/signup", { method: "POST", body: { email, password, name, invite } });
  _me = { user: r.user }; return r.user;
}
export async function signOut() { try { await api("/auth/logout", { method: "POST" }); } catch (e) {} _me = null; location.href = "index.html"; }
export async function changePassword(current, password) { return api("/auth/password", { method: "POST", body: { current, password } }); }

export async function chrome(active) {
  const user = (await session())?.user;
  const bar = $("#topbar");
  if (bar) bar.innerHTML = `<div class="topwrap">
    <a class="brand" href="../index.html"><span class="name">Mine<b>Modeling</b>Pro</span><span class="sub">Studio</span></a>
    <nav class="menu">
      <a href="dashboard.html" class="${active === "dashboard" ? "active" : ""}">My projects</a>
      <a href="../models.html">Model library</a>
      ${user ? `${user.role === "admin" ? `<a href="admin.html" class="${active === "admin" ? "active" : ""}">Accounts</a>` : ""}<a href="account.html" class="${active === "account" ? "active" : ""}">${esc(user.email)}</a><a href="#" id="signout">Sign out</a>`
             : `<a href="index.html">Sign in</a>`}
    </nav></div>`;
  const so = $("#signout"); if (so) so.onclick = e => { e.preventDefault(); signOut(); };
  return user;
}

/* ---------------------------------------------------------------- projects */
export async function listProjects() { return (await api("/projects")).projects || []; }
export async function getProject(id) { return (await api(`/projects/${id}`)).project; }
export async function projectPage(id) { return api(`/projects/${id}`); }
export async function createProject({ name, company, attach_to, commodity, params }) {
  return (await api("/projects", { method: "POST", body: { name, company, attach_to, commodity, params } })).project;
}
export async function saveParams(id, params) {
  return api(`/projects/${id}`, { method: "PATCH", body: { params, rebuild: true } });
}
export async function patchProject(id, patch) { return api(`/projects/${id}`, { method: "PATCH", body: patch }); }
export async function build(id) { return api(`/projects/${id}/build`, { method: "POST", body: {} }); }
export async function setPublic(id, isPublic) {
  return api(`/projects/${id}/publish`, { method: "POST", body: { public: !!isPublic } });
}
export async function deleteProject(id) { return api(`/projects/${id}`, { method: "DELETE" }); }

/* -------------------------------------------------------------------- jobs */
export async function latestJob(project_id) {
  const r = await api(`/projects/${project_id}`);
  return (r.jobs || [])[0] || null;
}
/** Poll a project until its build finishes; calls back with (project, job). */
export function watchProject(id, onTick, ms = 3000) {
  let stop = false;
  (async function loop() {
    while (!stop) {
      try {
        const r = await api(`/projects/${id}`);
        const p = r.project, j = (r.jobs || [])[0] || null;
        onTick(p, j);
        if (!["queued", "parsing", "building", "deleting"].includes(p.status) &&
            (!j || ["done", "error"].includes(j.status))) return;
      } catch (e) { /* keep polling */ }
      await new Promise(r => setTimeout(r, ms));
    }
  })();
  return () => { stop = true; };
}

/* ---------------------------------------------------------------- datasets */
export async function listDatasets(project_id) { return (await api(`/projects/${project_id}`)).datasets || []; }
export async function deleteDataset(id) { return api(`/datasets/${id}`, { method: "DELETE" }); }

/** Upload files for a project and queue the import. onProgress(done, total, name). */
export async function uploadDataset(project, files, options = {}, onProgress) {
  const maxB = (CFG.MAX_FILE_MB || 200) * 1024 * 1024;
  for (const f of files) if (f.size > maxB) throw new Error(`${f.name} is larger than ${CFG.MAX_FILE_MB} MB`);
  const form = new FormData();
  [...files].forEach(f => form.append("files", f, f.name));
  form.append("name", [...files].map(f => f.name).join(", ").slice(0, 160));
  for (const k of ["epsg", "utm_zone", "hemisphere", "datum"]) if (options[k]) form.append(k, options[k]);
  onProgress && onProgress(0, files.length, "uploading");
  const r = await api(`/projects/${project.id}/uploads`, { method: "POST", form });
  onProgress && onProgress(files.length, files.length, "queued");
  return r.dataset;
}

/* ------------------------------------------------------------------ models */
export async function modelUrl(project) {
  if (!project?.model_key) return null;
  return `${API}/projects/${project.id}/model.json`;
}
export function publicModelUrl(project) {
  const key = project.public_key || `${project.slug}-${String(project.id).slice(0, 8)}.json`;
  return `${API}/public/model/${encodeURIComponent(key)}`;
}
export async function listPublic() { return (await api("/public/projects")).projects || []; }

/* -------------------------------------------------------------- defaults */
/** Where a new project's parameters start: the most recent technical report for
    the project it builds on, else what is typical for the region + commodity. */
export async function defaults({ region, commodity, attach_to } = {}) {
  const q = new URLSearchParams();
  if (region) q.set("region", region);
  if (commodity) q.set("commodity", commodity);
  if (attach_to) q.set("attach_to", attach_to);
  try { return await api("/defaults?" + q.toString()); } catch (e) { return { params: {}, sources: {} }; }
}

/* ------------------------------------------------------------------ admin */
export async function listInvites() { return (await api("/invites")).invites || []; }
export async function createInvite(body) { return (await api("/invites", { method: "POST", body })).invite; }
export async function listUsers() { return (await api("/users")).users || []; }

/* --------------------------------------------------------------- parameters
   Mirrors minemodelingpro/params.py — the same names the worker understands. */
export const PARAM_FIELDS = [
  { k: "block", label: "Block size (x,y,z m)", ph: "e.g. 5,5,5", help: "Starts from the most recent technical report for this project; with no report, from what is typical for the region and commodity." },
  { k: "composite_m", label: "Composite length (m)", type: "number", step: "0.5", help: "Down-hole sample length used for estimation. Defaults to the report's, else the block height (max 5 m)." },
  { k: "radius_m", label: "Search radius (m)", type: "number", step: "1", help: "How far a block may look for composites. Defaults to the report's search range, else about 1.6 × drill spacing." },
  { k: "max_gap_m", label: "Max distance to data (m)", type: "number", step: "1", help: "A block is only estimated this close to real drilling. Defaults to about 0.75 × drill spacing." },
  { k: "spacing_m", label: "Drill spacing override (m)", type: "number", step: "1", help: "Normally measured from the holes themselves." },
  { k: "min_holes", label: "Minimum holes", type: "number", step: "1", help: "Distinct holes needed before a block is estimated (default 2)." },
  { k: "min_samples", label: "Minimum composites", type: "number", step: "1", help: "Composites needed inside the search radius (default 3)." },
  { k: "method", label: "Estimator", type: "select", options: [["", "Inverse distance (default)"], ["idw", "Inverse distance"], ["nn", "Nearest neighbour"], ["mean", "Equal-weight average"]] },
  { k: "power", label: "Inverse-distance power", type: "number", step: "0.5", help: "2 by default (ID²); the report's own power when it states one." },
  { k: "top_cut", label: "Top-cut grade", type: "number", step: "0.1", help: "Hard cap on composite grades. Defaults to the report's capping grade, else the 99.5th percentile." },
  { k: "top_cut_pct", label: "Top-cut percentile", type: "number", step: "0.1", help: "Used when no hard top-cut is set (default 99.5)." },
  { k: "density", label: "Density (t/m³)", type: "number", step: "0.05", help: "Defaults to the report's bulk density, else what is typical for the region and commodity." },
  { k: "default_cutoff", label: "Opening cut-off grade", type: "number", step: "0.05", help: "Where the model page's cut-off slider starts; defaults to the report's reporting cut-off." },
  { k: "elements", label: "Elements to model", ph: "e.g. Au,Ag", help: "Blank = pick automatically, most-drilled first." },
  { k: "max_elements", label: "How many elements", type: "number", step: "1", help: "Default 3." },
  { k: "gap_fill", label: "Unsampled hole length", type: "select", options: [["", "Composite as zero grade (default)"], ["false", "Leave out of the estimate"]],
    help: "Unreported intervals are below the reporting threshold, so treating them as zero keeps grade from smearing through waste." },
];

export function paramForm(el, values = {}, sources = {}) {
  el.innerHTML = PARAM_FIELDS.map(f => {
    const v = values[f.k] == null ? "" : (Array.isArray(values[f.k]) ? values[f.k].join(",") : values[f.k]);
    const src = sources[f.k];
    const input = f.type === "select"
      ? `<select name="${f.k}">${f.options.map(([ov, ol]) => `<option value="${ov}" ${String(v) === ov ? "selected" : ""}>${esc(ol)}</option>`).join("")}</select>`
      : `<input type="${f.type || "text"}" name="${f.k}" value="${esc(v)}" ${f.step ? `step="${f.step}"` : ""} placeholder="${esc(f.ph || "")}">`;
    return `<div><label title="${esc(f.help || "")}">${esc(f.label)}</label>${input}
      ${src ? `<div class="note src">from ${esc(src)}</div>` : ""}
      ${f.help ? `<div class="note">${esc(f.help)}</div>` : ""}</div>`;
  }).join("");
}

export function readParams(el) {
  const out = {};
  $$("input,select", el).forEach(i => {
    const v = (i.value || "").trim();
    if (v === "") return;
    if (i.name === "block" || i.name === "elements") out[i.name] = v.split(/[,;\s]+/).filter(Boolean);
    else if (i.name === "gap_fill") out[i.name] = v !== "false";
    else if (i.type === "number") out[i.name] = Number(v);
    else out[i.name] = v;
  });
  if (out.block) out.block = out.block.map(Number);
  return out;
}

/* ------------------------------------------------------------ status chips */
export function statusChip(p) {
  const s = p.status || "new";
  const cls = { ready: "ok", error: "err", building: "info", parsing: "info", queued: "info", new: "" }[s] || "";
  const label = { ready: "Ready", error: "Failed", building: "Building…", parsing: "Reading files…",
                  queued: "Queued", deleting: "Deleting…", new: "No data yet" }[s] || s;
  return `<span class="chip ${cls}">${label}</span>` +
    (p.is_public ? ` <span class="chip info">Shared</span>` : ` <span class="chip">Private</span>`);
}
