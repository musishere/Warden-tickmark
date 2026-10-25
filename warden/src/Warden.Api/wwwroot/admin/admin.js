/*
 * admin.js — the admin page's logic (step 22g). Plain browser JavaScript, no libraries, no build.
 *
 * Sign-in: OAuth 2.0 authorization code + PKCE, straight with Microsoft (like MSAL does, by hand):
 *   1. GET /admin/sign-in -> Warden's client id, tenant, scope
 *   2. redirect to Microsoft's sign-in page; it comes back to /admin/?code=...
 *   3. POST the code to Microsoft's token endpoint (allowed from a browser because Warden's app
 *      registration lists this page as a "Single-page application" redirect URI)
 *   4. call /admin/... with "Authorization: Bearer <token>"
 * The token is issued to Warden's own app (azp = Warden), which is what /admin requires; an agent's
 * token never is. It lives in sessionStorage (gone when the tab closes); no refresh token is kept:
 * after an hour, Warden says 401 and the page signs in again.
 *
 * Safety: text from the server (names, reasons, tool names, which can come from agents and tool
 * servers) is only ever set as textContent, never as HTML, so it can't run as code (no XSS).
 */
"use strict";

const $ = (sel) => document.querySelector(sel);
const view = $("#view");
let token = sessionStorage.getItem("warden.token");
let firm = null;  // GET /admin/firm: name, roles, scopes

// ---- DOM helper: el("td", {class: "no"}, "text", childEl) — text is always textContent ----
function el(tag, props = {}, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (v !== false && v != null) e.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) if (kid != null) e.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  return e;
}
const say = (msg) => { $("#status").textContent = msg || ""; };
const short = (id) => id ? String(id).slice(0, 8) : "";

// ---- sign-in ----
const b64url = (bytes) => btoa(String.fromCharCode(...new Uint8Array(bytes))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
const random = () => b64url(crypto.getRandomValues(new Uint8Array(32)));
const redirectUri = () => location.origin + "/admin/";

async function signIn() {
  const s = await (await fetch("/admin/sign-in")).json();
  const verifier = random(), state = random();
  sessionStorage.setItem("warden.pkce", JSON.stringify({ verifier, state, ...s }));
  const challenge = b64url(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier)));
  location.assign(`https://login.microsoftonline.com/${s.tenantId}/oauth2/v2.0/authorize?` + new URLSearchParams({
    client_id: s.clientId, response_type: "code", redirect_uri: redirectUri(), response_mode: "query",
    scope: `${s.scope} openid`, state, code_challenge: challenge, code_challenge_method: "S256", prompt: "select_account",
  }));
}

async function finishSignIn(params) {
  const saved = JSON.parse(sessionStorage.getItem("warden.pkce") || "null");
  sessionStorage.removeItem("warden.pkce");
  history.replaceState(null, "", "/admin/");
  if (!saved || params.get("state") !== saved.state) throw new Error("sign-in didn't match this tab; try again");
  const res = await fetch(`https://login.microsoftonline.com/${saved.tenantId}/oauth2/v2.0/token`, {
    method: "POST",
    body: new URLSearchParams({ client_id: saved.clientId, grant_type: "authorization_code", code: params.get("code"),
      redirect_uri: redirectUri(), code_verifier: saved.verifier, scope: `${saved.scope} openid` }),
  });
  const body = await res.json();
  if (!res.ok) throw new Error(body.error_description || body.error);
  token = body.access_token;
  sessionStorage.setItem("warden.token", token);
}

function signOut() {
  sessionStorage.clear();
  token = null;
  view.replaceChildren(el("p", {}, "Signed out. ", el("button", { onclick: signIn }, "Sign in again")));
  $("#tabs").hidden = $("#signout").hidden = true;
  $("#who").textContent = "";
}

// ---- calls to Warden ----
async function api(method, path, body) {
  const res = await fetch(path, {
    method,
    headers: { Authorization: `Bearer ${token}`, ...(body ? { "Content-Type": "application/json" } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (res.status === 401) { sessionStorage.removeItem("warden.token"); await signIn(); throw new Error("signing in again"); }
  if (res.status === 403) throw new Error("Forbidden: you need the Warden Admin role in Microsoft Entra.");
  const text = await res.text();
  const data = text ? JSON.parse(text) : null;
  if (!res.ok) throw new Error(data?.reason || `${res.status} ${res.statusText}`);
  return data;
}

// Run a change, show the error if refused, then redraw the current tab.
async function act(work) {
  say("");
  try { await work(); await show(current); } catch (e) { say(e.message); }
}

const form = (onsubmit, ...fields) => el("form", {
  onsubmit: (ev) => { ev.preventDefault(); const data = Object.fromEntries(new FormData(ev.target)); act(() => onsubmit(data, ev.target)); },
}, ...fields);
const field = (label, name, props = {}) => el("label", {}, label, el("input", { name, ...props }));
const choose = (label, name, options) => el("label", {}, label,
  el("select", { name, required: true }, options.map(([value, text]) => el("option", { value }, text))));
const table = (heads, rows) => el("table", {}, el("thead", {}, el("tr", {}, heads.map((h) => el("th", {}, h)))), el("tbody", {}, rows));

// ---- tabs ----
const tabs = {
  async people() {
    const [people, engagements] = await Promise.all([api("GET", "/admin/people"), api("GET", "/admin/engagements")]);
    return [
      el("section", {}, el("h2", {}, "People"), table(["Name", "Email", "Microsoft account", "Engagements"], people.map((p) =>
        el("tr", {},
          el("td", {}, p.name, p.active ? "" : el("span", { class: "no" }, " (deactivated)")),
          el("td", {}, p.email),
          el("td", {}, p.entraObjectId ? el("code", {}, p.entraObjectId) : form(
            (d) => api("PUT", `/admin/people/${p.id}/entra`, { objectId: d.objectId }),
            field("", "objectId", { placeholder: "Object ID", required: true, size: 20 }), el("button", {}, "Link"))),
          el("td", {}, p.engagements.map((e) => el("div", {}, `${e.name}: ${e.role} `,
            el("button", { class: "link", title: "Take off", onclick: () => act(() => api("DELETE", `/admin/engagements/${e.id}/members/${p.id}`)) }, "remove")))))))),
      el("section", {}, el("h2", {}, "Add a person"),
        el("p", {}, el("code", {}, "Object ID: Entra admin center → Users → the person → Object ID. Without it they can't sign in yet.")),
        form((d, f) => api("POST", "/admin/people", { name: d.name, email: d.email, entraObjectId: d.entraObjectId || null }).then(() => f.reset()),
          field("Name", "name", { required: true }), field("Email", "email", { type: "email", required: true }),
          field("Microsoft account (Object ID)", "entraObjectId", { size: 36 }), el("button", {}, "Add"))),
      el("section", {}, el("h2", {}, "Put someone on an engagement"),
        form((d) => api("PUT", `/admin/engagements/${d.engagement}/members/${d.person}`, { role: d.role }),
          choose("Person", "person", people.map((p) => [p.id, p.name])),
          choose("Engagement", "engagement", engagements.map((e) => [e.id, e.name])),
          choose("Role", "role", firm.roles.map((r) => [r, r])), el("button", {}, "Save"))),
    ];
  },

  async engagements() {
    const [engagements, people] = await Promise.all([api("GET", "/admin/engagements"), api("GET", "/admin/people")]);
    return [
      el("section", {}, el("h2", {}, "Engagements"), table(["Engagement", "Members", "Ethical walls (blocked, whatever their role)"], engagements.map((e) =>
        el("tr", {}, el("td", {}, e.name, el("br"), el("code", {}, e.id)),
          el("td", {}, e.members.map((m) => el("div", {}, `${m.name}: ${m.role}`))),
          el("td", {}, e.walls.map((w) => el("div", { class: "no" }, `${w.name}: ${w.reason} `,
            el("button", { class: "link", onclick: () => act(() => api("DELETE", `/admin/engagements/${e.id}/walls/${w.userId}`)) }, "lift")))))))),
      el("section", {}, el("h2", {}, "Open an engagement"),
        form((d, f) => api("POST", "/admin/engagements", { name: d.name }).then(() => f.reset()), field("Name", "name", { required: true }), el("button", {}, "Open"))),
      el("section", {}, el("h2", {}, "Wall someone off an engagement"),
        form((d) => api("PUT", `/admin/engagements/${d.engagement}/walls/${d.person}`, { reason: d.reason }),
          choose("Person", "person", people.map((p) => [p.id, p.name])),
          choose("Engagement", "engagement", engagements.map((e) => [e.id, e.name])),
          field("Why (shown in every refusal)", "reason", { required: true, size: 30 }), el("button", {}, "Set wall"))),
    ];
  },

  async agents() {
    const agents = await api("GET", "/admin/agents");
    const scopeBoxes = (have = "") => el("span", { class: "chips" }, firm.scopes.map((s) =>
      el("label", {}, el("input", { type: "checkbox", name: `scope:${s}`, checked: have.split(" ").includes(s) }), s)));
    const scopesOf = (d) => firm.scopes.filter((s) => d[`scope:${s}`]).join(" ");
    return [
      el("section", {}, el("h2", {}, "Agents your firm allows"),
        el("p", {}, "Each agent may be given at most these scopes (its ceiling), and never more than the person it works for. None ticked = switched off."),
        table(["Agent", "Entra app (client id)", "Ceiling"], agents.map((a) => el("tr", {},
          el("td", {}, a.name), el("td", {}, el("code", {}, a.entraClientId)),
          el("td", {}, form((d) => api("PUT", `/admin/agents/${a.entraClientId}`, { name: a.name, allowedScopes: scopesOf(d) }),
            scopeBoxes(a.allowedScopes), el("button", {}, "Save"))))))),
      el("section", {}, el("h2", {}, "Allow an agent"),
        el("p", {}, el("code", {}, "Client id: the agent's app registration in Entra (Application (client) ID).")),
        form((d, f) => api("PUT", `/admin/agents/${d.clientId}`, { name: d.name, allowedScopes: scopesOf(d) }).then(() => f.reset()),
          field("Name", "name", { required: true }), field("Client id", "clientId", { required: true, size: 36 }), scopeBoxes(), el("button", {}, "Allow"))),
    ];
  },

  async servers() {
    const servers = await api("GET", "/admin/servers");
    const toolsBox = el("div");
    const loadTools = async (s) => {
      const tools = await api("GET", `/admin/servers/${s.id}/tools`);
      toolsBox.replaceChildren(el("section", {}, el("h2", {}, `What ${s.name} offers`),
        el("p", {}, "A tool with no scope is hidden from agents and refused. New tools the server adds stay hidden until you assign them."),
        table(["Tool", "What the server says it does", "Scope needed"], tools.map((t) => el("tr", {},
          el("td", {}, t.name), el("td", {}, t.description || ""),
          el("td", {}, t.takenBy ? `name taken by ${t.takenBy}` : form(
            (d) => d.scope ? api("PUT", `/admin/servers/${s.id}/tools/${encodeURIComponent(t.name)}`, { scope: d.scope })
                           : api("DELETE", `/admin/servers/${s.id}/tools/${encodeURIComponent(t.name)}`),
            el("select", { name: "scope" }, el("option", { value: "" }, "hidden"),
              firm.scopes.map((sc) => el("option", { value: sc, selected: sc === t.scope }, sc))),
            el("button", {}, "Save"))))))));
    };
    const showTools = (s) => { openServer = s.id; act(() => Promise.resolve()); };  // redraw, with its tools open
    const open = servers.find((s) => s.id === openServer);
    if (open) await loadTools(open);
    return [
      el("section", {}, el("h2", {}, "MCP servers behind Warden"),
        table(["Server", "Address", "Token audience", "Tools agents may use", ""], servers.map((s) => el("tr", {},
          el("td", {}, s.name), el("td", {}, el("code", {}, s.url)), el("td", {}, el("code", {}, s.audience)),
          el("td", {}, s.tools.map((t) => el("div", {}, `${t.name} (${t.scope})`))),
          el("td", {}, el("button", { onclick: () => showTools(s) }, "Tools…")))))),
      toolsBox,
      el("section", {}, el("h2", {}, "Connect an MCP server"),
        el("p", {}, "The server must accept Warden's signed tokens (JWKS at /.well-known/jwks.json) for this audience, and nothing else."),
        form((d, f) => api("POST", "/admin/servers", d).then(() => f.reset()),
          field("Name", "name", { required: true }), field("MCP address", "url", { type: "url", required: true, size: 32, placeholder: "https://…/mcp" }),
          field("Audience", "audience", { required: true }), el("button", {}, "Connect"))),
    ];
  },

  async audit(before) {
    const [engagements, people, agents] = await Promise.all([api("GET", "/admin/engagements"), api("GET", "/admin/people"), api("GET", "/admin/agents")]);
    const names = Object.fromEntries([...engagements, ...people, ...agents].map((x) => [x.id, x.name]));
    const filter = auditFilter;
    const rows = await api("GET", `/admin/audit?limit=100${filter ? `&engagement=${filter}` : ""}${before ? `&before=${before}` : ""}`);
    return [
      el("section", {}, el("h2", {}, "Audit log (newest first, can't be changed or deleted)"),
        el("label", {}, "Engagement", el("select", { onchange: (ev) => { auditFilter = ev.target.value; show("audit"); } },
          el("option", { value: "" }, "all, and firm-wide changes"),
          engagements.map((e) => el("option", { value: e.id, selected: e.id === filter }, e.name)))),
        table(["#", "When", "Who", "Agent", "Engagement", "What", "Scope", "", "Why"], rows.map((r) => el("tr", {},
          el("td", {}, r.id), el("td", {}, new Date(r.at).toLocaleString()),
          el("td", {}, names[r.userId] || short(r.userId)), el("td", {}, r.agentId ? names[r.agentId] || short(r.agentId) : "—"),
          el("td", {}, r.engagementId ? names[r.engagementId] || short(r.engagementId) : "firm"),
          el("td", {}, el("code", {}, r.action)), el("td", {}, r.scope || ""),
          el("td", { class: r.allowed ? "yes" : "no" }, r.allowed ? "allowed" : "denied"), el("td", {}, r.reason)))),
        rows.length === 100 ? el("button", { onclick: () => act(async () => view.replaceChildren(...await tabs.audit(rows[rows.length - 1].id))) }, "Older") : null),
    ];
  },
};

let current = "people";
let auditFilter = "";
let openServer = null;  // the server whose tool list is open on the Tool servers tab
async function show(tab) {
  current = tab;
  document.querySelectorAll("#tabs button").forEach((b) => b.dataset.tab === tab ? b.setAttribute("aria-current", "page") : b.removeAttribute("aria-current"));
  view.replaceChildren(...await tabs[tab]());
}

// ---- start ----
(async () => {
  try {
    const params = new URLSearchParams(location.search);
    if (params.get("error")) throw new Error(params.get("error_description") || params.get("error"));
    if (params.get("code")) await finishSignIn(params);
    if (!token) return signIn();
    firm = await api("GET", "/admin/firm");
    $("#who").textContent = `${firm.name} · signed in as ${firm.admin}`;
    $("#tabs").hidden = $("#signout").hidden = false;
    $("#signout").onclick = signOut;
    $("#tabs").onclick = (ev) => ev.target.dataset.tab && act(() => show(ev.target.dataset.tab));
    await show("people");
  } catch (e) {
    say(e.message);
    view.replaceChildren(el("button", { onclick: () => { sessionStorage.clear(); signIn(); } }, "Sign in"));
  }
})();
