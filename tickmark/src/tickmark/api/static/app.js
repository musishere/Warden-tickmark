// Tickmark's page: upload -> column meanings -> run -> live log -> results. Plain JS, no build.
const $ = (s) => document.querySelector(s);
const MARK = { matched: "✅", difference: "⚠️", needs_review: "👀", no_evidence: "❓" };
const state = { ws: null, view: null, job: null, logs: [], tenants: [] };

const esc = (v) => String(v ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

async function api(path, opts = {}) {
  const r = await fetch(path, opts);
  const body = r.headers.get("content-type")?.includes("json") ? await r.json() : await r.text();
  if (!r.ok) throw new Error(body.detail ? (typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail)) : r.statusText);
  return body;
}
const json = (method, data) => ({ method, headers: { "content-type": "application/json" }, body: JSON.stringify(data) });

// --- status, firms and engagements ------------------------------------------------------------------

async function loadMeta() {
  const m = await api("/api/meta");
  $("#status").innerHTML = [
    `<span class="chip">Azure: ${m.azure}${m.azure_configured ? "" : " (not configured)"}</span>`,
    `<span class="chip">LLM mapping: ${m.llm_mapping ? "on" : "off"}</span>`,
  ].join("");
  $("#azure").value = m.azure;
  state.decisions = m.decisions;
}

async function loadTenants() {
  try {
    state.tenants = await api("/api/tenants");
    $("#db-note").textContent = state.tenants.length ? "" : "No firm yet: add one below.";
  } catch (e) {
    state.tenants = [];
    $("#db-note").textContent = `Audit trail unavailable: ${e.message}. Runs still work without saving.`;
  }
  const t = $("#tenant"), keep = t.value;
  t.innerHTML = `<option value="">— none —</option>` + state.tenants.map((f) => `<option value="${f.id}">${esc(f.name)}</option>`).join("");
  t.value = keep;
  fillEngagements();
}

function fillEngagements() {
  const firm = state.tenants.find((f) => f.id === $("#tenant").value);
  $("#engagement").innerHTML = `<option value="">— none —</option>` + (firm?.engagements || []).map((e) =>
    `<option value="${e.engagement_id}">${esc(e.client_name)}${e.currency ? " · " + e.currency : ""}${e.period_start ? " · " + e.period_start + "…" + e.period_end : ""}</option>`).join("");
}

$("#tenant").onchange = fillEngagements;
$("#add-firm").onclick = async () => {
  const name = $("#new-firm").value.trim();
  if (!name) return;
  const { id } = await api("/api/tenants", json("POST", { name })).catch(alertErr);
  await loadTenants();
  $("#tenant").value = id; fillEngagements();
};
$("#add-engagement").onclick = async () => {
  const tenant = $("#tenant").value;
  if (!tenant) return alert("Choose a firm first.");
  const body = { client_name: $("#new-client").value.trim(), period: $("#new-period").value.trim() || null,
                 currency: $("#new-currency").value.trim() || null, line_items: $("#new-lines").checked };
  const r = await api(`/api/tenants/${tenant}/engagements`, json("POST", body)).catch(alertErr);
  if (!r) return;
  await loadTenants();
  $("#tenant").value = tenant; fillEngagements(); $("#engagement").value = r.id;
};

function alertErr(e) { alert(e.message); }

// --- 1. upload --------------------------------------------------------------------------------------

$("#upload-form").onsubmit = async (ev) => {
  ev.preventDefault();
  const fd = new FormData();
  fd.append("sample", ev.target.sample.files[0]);
  for (const f of ev.target.invoices.files) fd.append("invoices", f);
  if ($("#engagement").value) {
    fd.append("tenant", $("#tenant").value); fd.append("engagement", $("#engagement").value);
  }
  fd.append("user", $("#user").value);
  try {
    showView(await api("/api/workspaces", { method: "POST", body: fd }));
    $("#s-columns").hidden = $("#s-run").hidden = false;
    $("#save").disabled = !$("#engagement").value;
    $("#save").checked = !!$("#engagement").value;
    loadPast();
  } catch (e) { alertErr(e); }
};

// --- 2. column meanings -----------------------------------------------------------------------------

function showView(v) {
  state.view = v; state.ws = v.id;
  $("#sheet-info").textContent = `Sheet "${v.sheet}": header on row ${v.header_row}` +
    (v.above_header ? ` (${v.above_header} title row(s) above it kept)` : "") + `, ${v.rows} rows, ${v.pdfs.length} PDFs.`;
  $("#own-type").innerHTML = v.types.map((t) => `<option>${t}</option>`).join("");
  const byCol = Object.fromEntries(Object.entries(v.mapping.columns).map(([f, c]) => [c, f]));
  const options = (selected) => [`<option value="">— not checked —</option>`]
    .concat(v.catalogue.filter((m) => m.role !== "label" || true).map((m) =>
      `<option value="${m.name}" ${m.name === selected ? "selected" : ""}>${m.name} (${m.type})</option>`))
    .concat(Object.entries(v.mapping.meanings).map(([n, m]) =>
      `<option value="${n}" ${n === selected ? "selected" : ""}>${n} (own, ${m.type})</option>`)).join("");
  const ambiguous = Object.entries(v.mapping.notes).filter(([f, n]) => n.startsWith("ambiguous") && !v.mapping.columns[f]);
  $("#columns tbody").innerHTML = v.columns.map((c) => {
    const f = byCol[c.col] || "";
    const amb = ambiguous.find(([, n]) => n.includes(`${c.col} '`));
    const note = f && v.mapping.notes[f] ? `<div class="small">${esc(v.mapping.notes[f])}</div>` : "";
    return `<tr class="${amb ? "ambiguous" : ""}"><td>${c.col}</td><td>${esc(c.header)}${amb ? `<div class="small">${esc(amb[0])} is ${esc(amb[1])}</div>` : ""}</td>
      <td class="small">${esc(c.pattern.join("; "))}</td>
      <td><select data-col="${c.col}">${options(f)}</select>${note}</td>
      <td>${f ? `<span class="badge chip">${esc(v.origin[f] || "")}</span>` : ""}</td></tr>`;
  }).join("");
  $("#problems").innerHTML = v.problems.map((p) => `<li>${esc(p)}</li>`).join("");
  $("#confirmed").textContent = v.mapping.confirmed_by ? `Confirmed by ${v.mapping.confirmed_by}.` : "";
}

function currentColumns() {
  const columns = {};
  for (const s of document.querySelectorAll("#columns select")) if (s.value) {
    if (columns[s.value]) throw new Error(`${s.value} is chosen for two columns`);
    columns[s.value] = s.dataset.col;
  }
  return columns;
}

$("#own-add").onclick = () => {
  const name = $("#own-name").value.trim(), labels = $("#own-labels").value.split("|").map((x) => x.trim()).filter(Boolean);
  if (!name || !labels.length) return alert("Give a name and at least one label.");
  state.view.mapping.meanings[name] = { type: $("#own-type").value, labels, description: "" };
  const cols = (() => { try { return currentColumns(); } catch { return {}; } })();
  state.view.mapping.columns = cols;
  showView(state.view);
};

$("#suggest").onclick = async () => {
  $("#suggest").disabled = true;
  try { showView(await api(`/api/workspaces/${state.ws}/suggest`, { method: "POST" })); }
  catch (e) { alertErr(e); } finally { $("#suggest").disabled = false; }
};

$("#confirm").onclick = async () => {
  try {
    const columns = currentColumns();
    const meanings = Object.fromEntries(Object.entries(state.view.mapping.meanings).filter(([n]) => n in columns));
    const v = await api(`/api/workspaces/${state.ws}/mapping`, json("PUT", { columns, meanings, confirmed_by: $("#user").value }));
    showView(v);
    $("#confirmed").textContent = v.confirmed ? `Confirmed by ${$("#user").value}${v.saved_mapping ? ", saved for this engagement" : ""}.` : "Not usable yet.";
  } catch (e) { alertErr(e); }
};

// --- 3. run, 4. live log ----------------------------------------------------------------------------

$("#run").onclick = async () => {
  const start = $("#p-start").value, end = $("#p-end").value;
  const body = { period: start && end ? `${start}:${end}` : null, currency: $("#currency").value.trim() || null,
                 azure: $("#azure").value, line_items: $("#line-items").checked || null, save: $("#save").checked };
  try {
    const { job } = await api(`/api/workspaces/${state.ws}/runs`, json("POST", body));
    state.job = job; state.logs = [];
    $("#s-log").hidden = false; $("#s-results").hidden = true; $("#run").disabled = true;
    poll(0);
  } catch (e) { alertErr(e); }
};

async function poll(since) {
  const j = await api(`/api/jobs/${state.job}?since=${since}`);
  state.logs.push(...j.logs);
  renderLog();
  $("#job-status").textContent = j.status === "running" ? "running…" : j.status === "failed" ? `failed: ${j.error}` : "done";
  if (j.status === "running") return setTimeout(() => poll(j.next), 800);
  $("#run").disabled = false;
  if (j.result) { renderResult(j.result); loadPast(); }
}

function renderLog() {
  const steps = [...new Set(state.logs.map((l) => l.step))];
  const sel = $("#log-step"), keep = sel.value;
  sel.innerHTML = `<option value="">all</option>` + steps.map((s) => `<option>${s}</option>`).join("");
  sel.value = keep;
  const shown = state.logs.filter((l) => (!sel.value || l.step === sel.value) && ($("#log-debug").checked || l.level !== "DEBUG"));
  const pre = $("#log");
  pre.innerHTML = shown.map((l) => `<span class="lvl-${l.level}">${l.t} <span class="step">${l.step.padEnd(8)}</span> ${esc(l.msg)}</span>`).join("\n");
  pre.scrollTop = pre.scrollHeight;
}
$("#log-step").onchange = $("#log-debug").onchange = renderLog;

// --- 5. results -------------------------------------------------------------------------------------

function renderResult(r, wsId = state.ws) {
  $("#s-results").hidden = false;
  $("#counts").innerHTML = Object.entries(r.counts).map(([k, n]) => `<span class="chip ${k}">${MARK[k]} ${k.replace("_", " ")} ${n}</span>`).join("")
    + (r.run_id ? `<span class="chip">saved as run ${r.run_id}</span>` : "");
  const idents = Object.fromEntries((r.identifications || []).map((i) => [i.excel_row, i]));
  $("#verdicts tbody").innerHTML = r.verdicts.map((v, i) => {
    const link = v.invoice_file && wsId ? `<a href="/api/workspaces/${wsId}/pdf/${encodeURIComponent(v.invoice_file)}" target="_blank">${esc(v.invoice_file)}</a>` : esc(v.invoice_file || "");
    return `<tr class="click" data-i="${i}"><td>${MARK[v.verdict]}</td><td>${esc(v.row_id)}</td><td><span class="chip ${v.verdict}">${v.verdict.replace("_", " ")}</span></td>
      <td>${link}</td><td class="small">${esc(v.reasons.join("; "))}</td></tr>
      <tr class="detail" data-for="${i}" hidden><td colspan="5">${detail(v, idents[v.excel_row])}</td></tr>`;
  }).join("");
  for (const tr of document.querySelectorAll("#verdicts tr.click"))
    tr.onclick = () => { const d = document.querySelector(`tr[data-for="${tr.dataset.i}"]`); d.hidden = !d.hidden; };
  const pdfs = Object.fromEntries((r.pdfs || []).map((p) => [p.file, p]));
  $("#routes tbody").innerHTML = (r.routes || []).map((x) => {
    const p = pdfs[x.file] || { fields: {}, unread: {}, needs_ocr: [] };
    return `<tr><td>${esc(x.file)}</td><td><span class="chip ${x.read_by}">${x.read_by}</span></td><td class="small">${esc(x.why)}</td>
      <td class="small">${esc(Object.entries(p.fields).map(([k, v]) => `${k}=${v}`).join(", "))}</td>
      <td class="small">${esc([...p.needs_ocr, ...Object.entries(p.unread).map(([k, v]) => `${k}: ${v}`)].join("; "))}</td></tr>`;
  }).join("") || `<tr><td colspan="5" class="muted">Read from labels (test mode).</td></tr>`;
  $("#issues").innerHTML = [...(r.errors || []).map((e) => `<li>Excel row ${e.row_number}: ${esc(e.message)}</li>`),
    ...(r.skipped || []).map((s) => `<li class="muted">Excel row ${s.row_number}: ${esc(s.reason)}</li>`)].join("") || `<li class="muted">none</li>`;
}

function detail(v, ident) {
  const fields = v.agreements.length ? `<h3>Row vs PDF</h3><table><tr><th>Field</th><th>Row</th><th>PDF</th><th>Result</th><th></th></tr>` +
    v.agreements.map((a) => `<tr><td>${esc(a.field)}</td><td>${esc(a.row_value)}</td><td>${esc(a.pdf_value)}</td><td><span class="chip ${a.result}">${a.result}</span></td><td class="small">${esc(a.delta || a.detail)}</td></tr>`).join("") + `</table>` : "";
  const checks = v.checks.length ? `<h3>Invoice checks</h3><table>` +
    v.checks.map((c) => `<tr><td>${esc(c.name)}</td><td><span class="chip ${c.status}">${c.status}</span></td><td class="small">${esc(c.detail)}</td></tr>`).join("") + `</table>` : "";
  const cands = ident?.candidates?.length ? `<h3>Candidate PDFs</h3><ul>` + ident.candidates.map((c) => `<li>${esc(c.invoice_file)}: agrees on ${esc(c.agree.join(", "))}${c.note ? " — " + esc(c.note) : ""}</li>`).join("") + `</ul>` : "";
  const steps = [...(ident?.steps || []), ...v.steps];
  return fields + checks + cands + `<h3>Steps</h3><ol class="small">${steps.map((s) => `<li>${esc(s)}</li>`).join("")}</ol>` + reviewForm(v);
}

function reviewForm(v) {
  const run = state.openRun;
  if (!run) return "";
  return `<div class="row"><select data-review="${esc(v.row_id)}">${state.decisions.map((d) => `<option>${d}</option>`).join("")}</select>
    <input data-note="${esc(v.row_id)}" placeholder="note"><button type="button" onclick="review('${esc(v.row_id)}')">Record review</button></div>`;
}

async function review(rowId) {
  const r = state.openRun;
  await api(`/api/runs/${r.run}/reviews`, json("POST", { tenant: r.tenant, engagement: r.engagement, user: $("#user").value,
    row_id: rowId, decision: document.querySelector(`[data-review="${rowId}"]`).value,
    note: document.querySelector(`[data-note="${rowId}"]`).value })).then(() => alert("Review recorded."), alertErr);
}

// --- 6. saved runs ----------------------------------------------------------------------------------

async function loadPast() {
  const tenant = $("#tenant").value, engagement = $("#engagement").value;
  $("#s-past").hidden = !engagement;
  if (!engagement) return;
  const runs = await api(`/api/engagements/${engagement}/runs?tenant=${tenant}`).catch(() => []);
  $("#past").innerHTML = runs.map((r) => `<li><a href="#" data-run="${r.id}">${esc(r.started_at)}</a> · ${esc(r.status)} · ${esc(r.run_by)}</li>`).join("") || `<li class="muted">none yet</li>`;
  for (const a of document.querySelectorAll("#past a")) a.onclick = (e) => { e.preventDefault(); openRun(a.dataset.run, tenant, engagement); };
}

async function openRun(run, tenant, engagement) {
  const r = await api(`/api/runs/${run}?tenant=${tenant}&engagement=${engagement}`).catch(alertErr);
  if (!r) return;
  state.openRun = { run, tenant, engagement };
  const counts = Object.fromEntries(Object.keys(MARK).map((k) => [k, r.verdicts.filter((v) => v.verdict === k).length]));
  renderResult({ run_id: run, counts, verdicts: r.verdicts, identifications: r.identifications,
                 routes: r.files.map((f) => ({ file: f.file_name, read_by: f.read_by || "?", why: f.read_reason || "" })),
                 pdfs: [], errors: [], skipped: r.issues.map((i) => ({ row_number: i.excel_row, reason: `${i.kind}: ${i.reason}` })) }, null);
}

loadMeta().catch(alertErr);
loadTenants();
