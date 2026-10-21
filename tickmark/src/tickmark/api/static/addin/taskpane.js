// Tickmark in Excel: the selected sample range + invoice PDFs -> the same API as the web page -> verdicts
// written next to the data. Plain JS, no build.
const $ = (s) => document.querySelector(s);
const MARK = { matched: "✅", difference: "⚠️", needs_review: "👀", no_evidence: "❓" };
const LABEL = { matched: "matched", difference: "difference", needs_review: "needs review", no_evidence: "no evidence" };
const FILL = { matched: "#C6EFCE", difference: "#FFC7CE", needs_review: "#FFEB9C", no_evidence: "#E7E6E6" };
const state = { range: null, view: null, result: null };

const esc = (v) => String(v ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
const status = (msg, error = false) => { $("#status").innerHTML = `<span class="${error ? "error" : ""}">${esc(msg)}</span>`; };

async function api(path, opts = {}) {
  const r = await fetch(path, opts);
  const body = r.headers.get("content-type")?.includes("json") ? await r.json() : await r.text();
  if (!r.ok) throw new Error(body.detail ? (typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail)) : r.statusText);
  return body;
}
const json = (method, data) => ({ method, headers: { "content-type": "application/json" }, body: JSON.stringify(data) });
const ready = () => { $("#run").disabled = !state.range; };

// --- 1. the sample: the selected range, or the sheet's used range when one cell is selected ------------

$("#use-range").onclick = () => Excel.run(async (ctx) => {
  let range = ctx.workbook.getSelectedRange();
  range.load("cellCount");
  await ctx.sync();
  if (range.cellCount === 1) range = ctx.workbook.worksheets.getActiveWorksheet().getUsedRange();
  range.load("values,numberFormat,address,rowIndex,columnIndex,rowCount,columnCount,worksheet/name");
  await ctx.sync();
  state.range = { values: range.values, formats: range.numberFormat, address: range.address, sheet: range.worksheet.name,
                  rowIndex: range.rowIndex, columnIndex: range.columnIndex, rowCount: range.rowCount,
                  columnCount: range.columnCount };
  $("#range").textContent = `${range.address} · ${range.rowCount - 1} rows below the header`;
  ready();
}).catch((e) => status(e.message, true));

$("#pdfs").onchange = () => { $("#pdf-count").textContent = `${$("#pdfs").files.length} PDF(s) chosen`; };

// --- 2. upload, settle the columns, run --------------------------------------------------------------

$("#run").onclick = async () => {
  $("#run").disabled = true;
  $("#results").hidden = true;
  $("#log").textContent = "";
  try {
    if (!state.view || $("#mapping").hidden) state.view = await upload();
    if (!(await settleColumns())) return;  // the person picks the missing columns, then presses again
    const r = await run();
    state.result = r;
    await writeBack(r);
    showResults(r);
    status("Done.");
  } catch (e) {
    status(e.message, true);
  } finally {
    ready();
  }
};

async function upload() {
  status("Uploading the sample and invoices…");
  const fd = new FormData();
  fd.append("rows", JSON.stringify({ values: state.range.values, formats: state.range.formats, sheet: state.range.sheet }));
  for (const f of $("#pdfs").files) fd.append("invoices", f, f.name);
  return api("/api/workspaces", { method: "POST", body: fd });
}

async function settleColumns() {
  const v = state.view;
  let columns = v.mapping.columns;
  if (!$("#mapping").hidden) {  // the person's choices
    columns = {};
    for (const s of document.querySelectorAll("#mapping-fields select")) if (s.value) columns[s.dataset.field] = s.value;
  }
  if (v.problems.length && $("#mapping").hidden) return showMapping(v);
  const c = await api(`/api/workspaces/${v.id}/mapping`, json("PUT", { columns, confirmed_by: "excel" }));
  state.view = c;
  if (!c.confirmed) return showMapping(c);
  $("#mapping").hidden = true;
  return true;
}

function showMapping(v) {
  const fields = v.catalogue.filter((f) => f.role !== "label" || f.name === "row_id");
  const opts = `<option value="">— not in the sample —</option>` +
    v.columns.map((c) => `<option value="${c.col}">${esc(c.col)}: ${esc(c.header)}</option>`).join("");
  $("#mapping-fields").innerHTML = fields.map((f) =>
    `<label>${esc(f.name.replace("_", " "))} <span class="muted">· ${esc(f.description)}</span></label>
     <select data-field="${f.name}">${opts}</select>`).join("");
  for (const s of document.querySelectorAll("#mapping-fields select")) s.value = v.mapping.columns[s.dataset.field] || "";
  $("#mapping").hidden = false;
  const noHeader = !Object.keys(v.mapping.columns).length;  // not one column recognised by its name
  status((noHeader ? "No column names recognised: did the selection include the header row? Select it too and " +
          "press Use selected range again, or say below which column holds what. " : "") +
         (v.problems.join(" · ") || "Check the columns, then press Check sample again."), true);
  return false;
}

async function run() {
  const period = $("#from").value && $("#to").value ? `${$("#from").value}:${$("#to").value}` : null;
  const { job } = await api(`/api/workspaces/${state.view.id}/runs`, json("POST", {
    period, currency: $("#currency").value.trim().toUpperCase() || null, vat_rates: $("#vat").value.trim() || null,
    azure: $("#azure").value, line_items: false,
  }));
  let since = 0;
  for (;;) {
    const j = await api(`/api/jobs/${job}?since=${since}`);
    since = j.next;
    for (const line of j.logs) $("#log").textContent += `${line.step}  ${line.msg}\n`;
    $("#log").scrollTop = $("#log").scrollHeight;
    if (j.status === "done") return j.result;
    if (j.status === "failed") throw new Error(j.error);
    status("Reading invoices and checking rows…");
    await new Promise((r) => setTimeout(r, 1000));
  }
}

// --- 3. results: three columns right of the range, one colour per verdict ------------------------------

const pdfUrl = (file) => `${location.origin}/api/workspaces/${state.view.id}/pdf/${encodeURIComponent(file)}`;
const sheetRow = (excelRow) => state.range.rowIndex + excelRow - 1;  // the sample's row n is the range's row n

async function writeBack(r) {
  await Excel.run(async (ctx) => {
    const sheet = ctx.workbook.worksheets.getItem(state.range.sheet);
    const col = state.range.columnIndex + state.range.columnCount;
    const head = sheet.getRangeByIndexes(sheetRow(state.view.header_row), col, 1, 3);
    head.values = [["Tickmark", "Why", "Invoice"]];
    head.format.font.bold = true;
    for (const v of r.verdicts) {
      const cells = sheet.getRangeByIndexes(sheetRow(v.excel_row), col, 1, 3);
      cells.values = [[`${MARK[v.verdict]} ${LABEL[v.verdict]}`, v.reasons.join("; ") || "every field agrees", v.invoice_file || ""]];
      cells.getCell(0, 0).format.fill.color = FILL[v.verdict];
      if (v.invoice_file) cells.getCell(0, 2).hyperlink = { address: pdfUrl(v.invoice_file), textToDisplay: v.invoice_file };
    }
    sheet.getRangeByIndexes(sheetRow(state.view.header_row), col, 1, 3).getEntireColumn().format.autofitColumns();
    sheet.getRangeByIndexes(0, col + 1, 1, 1).getEntireColumn().format.columnWidth = 320;
    await ctx.sync();
  });
}

function showResults(r) {
  $("#counts").innerHTML = Object.entries(r.counts).map(([k, n]) => `<span title="${LABEL[k]}">${MARK[k]} ${n}</span>`).join("");
  $("#rows").innerHTML = r.verdicts.map((v, i) =>
    `<div class="row" data-i="${i}"><b>${MARK[v.verdict]} ${esc(v.row_id)}</b> ${esc(v.invoice_file || "")}
       <div class="why">${esc(v.reasons.join("; ") || "every field agrees")}</div></div>`).join("");
  for (const el of document.querySelectorAll("#rows .row")) el.onclick = () => openRow(r.verdicts[el.dataset.i]);
  $("#results").hidden = false;
}

function openRow(v) {
  Excel.run(async (ctx) => {
    ctx.workbook.worksheets.getItem(state.range.sheet)
      .getRangeByIndexes(sheetRow(v.excel_row), state.range.columnIndex, 1, state.range.columnCount + 3).select();
    await ctx.sync();
  }).catch(() => {});
  if (v.invoice_file) showPdf(v.invoice_file);
}

// Excel (on the web above all) will not follow a localhost link from a cell or a pane pop-up; its own
// dialog window will, as the PDF is served from the add-in's own address.
function showPdf(file) {
  Office.context.ui.displayDialogAsync(pdfUrl(file), { height: 85, width: 60, promptBeforeOpen: false }, (res) => {
    if (res.status !== Office.AsyncResultStatus.Succeeded) {
      status(`Could not open ${file} (${res.error.message}). Opening it in a browser tab instead.`, true);
      window.open(pdfUrl(file), "_blank");
    }
  });
}

Office.onReady(async (info) => {
  if (info.host !== Office.HostType.Excel) return status("Open this from Excel.", true);
  try {
    const m = await api("/api/meta");
    $("#azure").value = m.azure;
  } catch (e) {
    status(`Tickmark is not reachable: ${e.message}`, true);
  }
});
