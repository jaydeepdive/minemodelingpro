/* MMP Studio — browser client.
   Supabase does the accounts, storage and database; this file is the glue:
   sign-in, uploads, project parameters, job polling and the private model viewer.
   Everything it can see is bounded by the row-level-security policies, so a user
   only ever reaches their own rows and their own storage folder. */
import { createClient } from "https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2/+esm";

const CFG = window.MMP_CONFIG || {};
export const configured = !!(CFG.SUPABASE_URL && CFG.SUPABASE_ANON_KEY);
export const sb = configured ? createClient(CFG.SUPABASE_URL, CFG.SUPABASE_ANON_KEY,
  { auth: { persistSession: true, autoRefreshToken: true } }) : null;

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
  const el = $(where); if (!el) return alert(msg);
  el.innerHTML = msg ? `<div class="${kind === "err" ? "err" : "ok"}">${esc(msg)}</div>` : "";
}

/* ----------------------------------------------------------------- session */
export async function session() { if (!sb) return null; const { data } = await sb.auth.getSession(); return data.session; }
export async function requireUser(redirect = "index.html") {
  const s = await session();
  if (!s) { location.href = redirect + "?next=" + encodeURIComponent(location.pathname.split("/").pop() + location.search); return null; }
  return s.user;
}
export async function signOut() { await sb.auth.signOut(); location.href = "index.html"; }

export async function chrome(active) {
  const user = (await session())?.user;
  const bar = $("#topbar");
  if (bar) bar.innerHTML = `<div class="topwrap">
    <a class="brand" href="../index.html"><span class="name">Mine<b>Modeling</b>Pro</span><span class="sub">Studio</span></a>
    <nav class="menu">
      <a href="dashboard.html" class="${active === "dashboard" ? "active" : ""}">My projects</a>
      <a href="public.html" class="${active === "public" ? "active" : ""}">Shared models</a>
      <a href="../models.html">Public gallery</a>
      ${user ? `<span class="who">${esc(user.email)}</span><a href="#" id="signout">Sign out</a>`
             : `<a href="index.html">Sign in</a>`}
    </nav></div>`;
  const so = $("#signout"); if (so) so.onclick = e => { e.preventDefault(); signOut(); };
  return user;
}

/* ---------------------------------------------------------------- projects */
export async function listProjects() {
  const { data, error } = await sb.from("projects").select("*").order("updated_at", { ascending: false });
  if (error) throw error; return data || [];
}
export async function getProject(id) {
  const { data, error } = await sb.from("projects").select("*").eq("id", id).single();
  if (error) throw error; return data;
}
export async function createProject({ name, company, attach_to }) {
  const user = (await session()).user;
  let slug = slugify(name), n = 1;
  const { data: taken } = await sb.from("projects").select("slug").eq("owner", user.id);
  const used = new Set((taken || []).map(t => t.slug));
  while (used.has(slug)) slug = slugify(name) + "-" + (++n);
  const { data, error } = await sb.from("projects")
    .insert({ owner: user.id, slug, name, company: company || null, attach_to: attach_to || null })
    .select().single();
  if (error) throw error; return data;
}
export async function saveParams(id, params) {
  const { error } = await sb.from("projects").update({ params }).eq("id", id);
  if (error) throw error;
  return enqueue("build", { project_id: id });
}
export async function setPublic(id, isPublic) {
  return enqueue("publish", { project_id: id, payload: { public: !!isPublic } });
}
export async function deleteProject(id) { return enqueue("delete", { project_id: id }); }

/* ------------------------------------------------------------------- jobs */
export async function enqueue(kind, { project_id = null, dataset_id = null, payload = {} } = {}) {
  const user = (await session()).user;
  const { data, error } = await sb.from("jobs")
    .insert({ owner: user.id, kind, project_id, dataset_id, payload }).select().single();
  if (error) throw error; return data;
}
export async function latestJob(project_id) {
  const { data } = await sb.from("jobs").select("*").eq("project_id", project_id)
    .order("created_at", { ascending: false }).limit(1);
  return (data || [])[0] || null;
}
/** Poll a project until its build finishes; calls back with (project, job). */
export function watchProject(id, onTick, ms = 3000) {
  let stop = false;
  (async function loop() {
    while (!stop) {
      try {
        const [p, j] = await Promise.all([getProject(id), latestJob(id)]);
        onTick(p, j);
        if (!["queued", "building"].includes(p.status) && (!j || ["done", "error"].includes(j.status))) return;
      } catch (e) { /* keep polling */ }
      await new Promise(r => setTimeout(r, ms));
    }
  })();
  return () => { stop = true; };
}

/* ---------------------------------------------------------------- datasets */
export async function listDatasets(project_id) {
  const { data, error } = await sb.from("datasets").select("*").eq("project_id", project_id)
    .order("created_at", { ascending: false });
  if (error) throw error; return data || [];
}

/** Upload files for a project and queue the import. onProgress(done, total, name). */
export async function uploadDataset(project, files, options, onProgress) {
  const user = (await session()).user;
  const maxB = (CFG.MAX_FILE_MB || 200) * 1024 * 1024;
  for (const f of files) if (f.size > maxB) throw new Error(`${f.name} is larger than ${CFG.MAX_FILE_MB} MB`);
  const { data: ds, error: e1 } = await sb.from("datasets")
    .insert({ owner: user.id, project_id: project.id, name: [...files].map(f => f.name).join(", ").slice(0, 160),
              options, files: [], status: "uploading" }).select().single();
  if (e1) throw e1;
  const stored = [];
  let i = 0;
  for (const f of files) {
    const path = `${user.id}/${project.id}/${ds.id}/${f.name.replace(/[^\w.\-]+/g, "_")}`;
    const { error } = await sb.storage.from("uploads").upload(path, f, { upsert: true });
    if (error) throw error;
    stored.push({ path, filename: f.name, bytes: f.size });
    onProgress && onProgress(++i, files.length, f.name);
  }
  await sb.from("datasets").update({ files: stored, status: "uploaded" }).eq("id", ds.id);
  await enqueue("import", { project_id: project.id, dataset_id: ds.id });
  await sb.from("projects").update({ status: "queued" }).eq("id", project.id);
  return ds;
}

export async function modelUrl(project) {
  if (!project?.model_path) return null;
  const { data, error } = await sb.storage.from("models").createSignedUrl(project.model_path, 3600);
  if (error) throw error; return data.signedUrl;
}
export function publicModelUrl(project) {
  return `${CFG.SUPABASE_URL}/storage/v1/object/public/public-models/${project.slug}-${project.id.slice(0, 8)}.json`;
}

/* --------------------------------------------------------------- parameters
   Mirrors minemodelingpro/params.py — the same names the worker understands. */
export const PARAM_FIELDS = [
  { k: "block", label: "Block size (x,y,z m)", ph: "e.g. 5,5,5", help: "Defaults to the technical report's parent block, else the median 43-101 block for the commodity." },
  { k: "composite_m", label: "Composite length (m)", type: "number", step: "0.5", help: "Down-hole sample length used for estimation. Defaults to the block height (max 5 m)." },
  { k: "radius_m", label: "Search radius (m)", type: "number", step: "1", help: "How far a block may look for composites. Defaults to about 1.6 × drill spacing." },
  { k: "max_gap_m", label: "Max distance to data (m)", type: "number", step: "1", help: "A block is only estimated this close to real drilling. Defaults to about 0.75 × drill spacing." },
  { k: "spacing_m", label: "Drill spacing override (m)", type: "number", step: "1", help: "Normally measured from the holes themselves." },
  { k: "min_holes", label: "Minimum holes", type: "number", step: "1", help: "Distinct holes needed before a block is estimated (default 2)." },
  { k: "min_samples", label: "Minimum composites", type: "number", step: "1", help: "Composites needed inside the search radius (default 3)." },
  { k: "method", label: "Estimator", type: "select", options: [["", "Inverse distance (default)"], ["idw", "Inverse distance"], ["nn", "Nearest neighbour"], ["mean", "Equal-weight average"]] },
  { k: "power", label: "Inverse-distance power", type: "number", step: "0.5", help: "2 by default (ID²)." },
  { k: "top_cut", label: "Top-cut grade", type: "number", step: "0.1", help: "Hard cap on composite grades. Default: the 99.5th percentile." },
  { k: "top_cut_pct", label: "Top-cut percentile", type: "number", step: "0.1", help: "Used when no hard top-cut is set (default 99.5)." },
  { k: "density", label: "Density (t/m³)", type: "number", step: "0.05", help: "Default: the report's, else a value typical of the commodity." },
  { k: "default_cutoff", label: "Opening cut-off grade", type: "number", step: "0.05", help: "Where the model page's cut-off slider starts." },
  { k: "elements", label: "Elements to model", ph: "e.g. Au,Ag", help: "Blank = pick automatically, most-drilled first." },
  { k: "max_elements", label: "How many elements", type: "number", step: "1", help: "Default 3." },
  { k: "gap_fill", label: "Unsampled hole length", type: "select", options: [["", "Composite as zero grade (default)"], ["false", "Leave out of the estimate"]],
    help: "Unreported intervals are below the reporting threshold, so treating them as zero keeps grade from smearing through waste." },
];

export function paramForm(el, values = {}) {
  el.innerHTML = PARAM_FIELDS.map(f => {
    const v = values[f.k] == null ? "" : (Array.isArray(values[f.k]) ? values[f.k].join(",") : values[f.k]);
    const input = f.type === "select"
      ? `<select name="${f.k}">${f.options.map(([ov, ol]) => `<option value="${ov}" ${String(v) === ov ? "selected" : ""}>${esc(ol)}</option>`).join("")}</select>`
      : `<input type="${f.type || "text"}" name="${f.k}" value="${esc(v)}" ${f.step ? `step="${f.step}"` : ""} placeholder="${esc(f.ph || "")}">`;
    return `<div><label title="${esc(f.help || "")}">${esc(f.label)}</label>${input}
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
  const s = p.status || "draft";
  const cls = { ready: "ok", error: "err", building: "info", queued: "info", draft: "" }[s] || "";
  const label = { ready: "Ready", error: "Failed", building: "Building…", queued: "Queued", draft: "No data yet" }[s] || s;
  return `<span class="chip ${cls}">${label}</span>` +
    (p.is_public ? ` <span class="chip info">Shared</span>` : ` <span class="chip">Private</span>`);
}
