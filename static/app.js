const $ = (id) => document.getElementById(id);
const usdFmt = (n) => "$" + Number(n).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const pkrFmt = (n) => "PKR " + Math.round(n).toLocaleString("en-IN");

let mode = null;
let findings = [];
let labels = {};
let selected = new Set();
let scannedRegions = [];
let scanRegions = null;   // null in live mode means "every enabled region"

function show(id, visible) { $(id).classList.toggle("hidden", !visible); }

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function showError(message) {
  $("error").textContent = message;
  show("error", true);
}

function lakh(pkr) {
  return pkr >= 100000 ? `PKR ${(pkr / 100000).toFixed(1)} lakh` : pkrFmt(pkr);
}

function params() {
  return { budget: +$("budget").value || 0, pkr_rate: +$("rate").value || 280 };
}

function renderSummary(s) {
  $("usd").textContent = usdFmt(s.monthly_usd);
  $("pkr").textContent = `${pkrFmt(s.monthly_pkr)} · ${usdFmt(s.daily_usd)}/day`;
  $("wasted").textContent = usdFmt(s.wasted_usd);
  $("wasted").className = `value ${s.wasted_usd > 0 ? "warn" : "ok"}`;
  $("wasted-pkr").textContent = pkrFmt(s.wasted_pkr) + " per month";
  $("idle-count").textContent = `${s.zombies} of ${s.resources}`;
  $("idle-sub").textContent = s.regions.length ? `in ${s.regions.length} region${s.regions.length > 1 ? "s" : ""}` : "";
  $("yearly").textContent = `${lakh(s.yearly_pkr)} per year at this rate`;

  const cd = $("countdown");
  if (s.monthly_usd < 1) {
    cd.textContent = "Nothing billable running";
    cd.className = "value small ok";
  } else if (s.budget_usd <= 0) {
    cd.textContent = "No budget set";
    cd.className = "value small";
  } else if (s.monthly_usd <= s.budget_usd) {
    cd.textContent = `Within ${usdFmt(s.budget_usd)}/month`;
    cd.className = "value small ok";
  } else {
    const d = s.days_to_budget;
    cd.textContent = `${usdFmt(s.budget_usd)} used up in ${d < 1 ? Math.round(d * 24) + " hours" : d + " days"}`;
    cd.className = "value small warn";
  }
}

function renderRegions() {
  const idle = {};
  findings.forEach((f) => { if (f.zombie) idle[f.region] = (idle[f.region] || 0) + 1; });
  $("regions").innerHTML = scannedRegions.map((r) =>
    `<span class="region ${idle[r] ? "hot" : ""}">${r}${idle[r] ? ` (${idle[r]})` : ""}</span>`).join("");
}

function ageText(days) {
  if (days == null) return "–";
  if (days < 1) return "today";
  const d = Math.round(days);
  return `${d} day${d === 1 ? "" : "s"}`;
}

function renderFindings() {
  const rate = params().pkr_rate;
  if (!findings.length) {
    $("findings").innerHTML = `<tr><td colspan="7" class="muted">No billable resources found in the scanned regions.</td></tr>`;
    return;
  }
  $("findings").innerHTML = findings.map((f) => {
    const sel = selected.has(f.id);
    const status = f.zombie
      ? `<span class="tag idle">Idle</span><div class="reason">${f.reasons.map(escapeHtml).join("<br>")}</div>
         <div class="action">Suggested: ${escapeHtml(labels[f.action] || "")}</div>`
      : `<span class="tag used">In use</span>`;
    return `<tr class="${sel ? "selected" : ""}" data-id="${escapeHtml(f.id)}">
      <td class="c"><input type="checkbox" ${sel ? "checked" : ""} aria-label="Select ${escapeHtml(f.name || f.id)}"></td>
      <td><div class="res-name">${escapeHtml(f.name || f.id)}</div>
          <div class="res-meta">${escapeHtml(f.kind)} · ${escapeHtml(f.detail)}</div>
          <div class="res-meta">${escapeHtml(f.id)}</div></td>
      <td class="mono">${escapeHtml(f.region)}</td>
      <td>${ageText(f.age_days)}</td>
      <td class="mono">${escapeHtml(f.usage || "–")}</td>
      <td class="r"><div class="cost">${usdFmt(f.monthly_cost)}</div><div class="cost-pkr">${pkrFmt(f.monthly_cost * rate)}</div></td>
      <td>${status}</td>
    </tr>`;
  }).join("");
  document.querySelectorAll("#findings tr[data-id]").forEach((row) => {
    row.querySelector("input").addEventListener("change", (e) => {
      e.target.checked ? selected.add(row.dataset.id) : selected.delete(row.dataset.id);
      row.classList.toggle("selected", e.target.checked);
      renderDock();
    });
  });
}

function renderDock() {
  const save = findings.filter((f) => selected.has(f.id)).reduce((a, f) => a + f.monthly_cost, 0);
  $("sel-count").textContent = selected.size;
  $("sel-save").textContent = `${usdFmt(save)} (${pkrFmt(save * params().pkr_rate)})`;
  const idle = findings.filter((f) => f.zombie);
  $("select-zombies").checked = idle.length > 0 && idle.every((f) => selected.has(f.id));
  show("dock", selected.size > 0);
}

async function post(path, body) {
  const res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || "Request failed");
  return data;
}

async function scan() {
  $("scan").disabled = true;
  show("error", false);
  show("toast", false);
  show("radar", true);
  const started = performance.now();
  $("radar-text").textContent = scanRegions
    ? `Scanning ${scanRegions.length} regions in parallel…`
    : "Scanning every enabled region in parallel…";
  const timer = setInterval(() => {
    $("radar-elapsed").textContent = `${Math.round((performance.now() - started) / 1000)}s`;
  }, 1000);
  try {
    const data = await post("/api/scan", params());
    findings = data.findings;
    labels = data.labels;
    scannedRegions = data.regions;
    selected = new Set();
    renderSummary(data.summary);
    renderRegions();
    renderFindings();
    renderDock();
    $("errors").textContent = data.errors.length ? `Skipped: ${data.errors.slice(0, 4).join(" · ")}` : "";
    show("errors", data.errors.length > 0);
    show("results", true);
  } catch (e) {
    showError(e.message);
  } finally {
    clearInterval(timer);
    show("radar", false);
    $("scan").disabled = false;
  }
}

function openModal() {
  const chosen = findings.filter((f) => selected.has(f.id));
  $("modal-list").innerHTML = chosen.map((f) =>
    `<li>${escapeHtml(labels[f.action])}: ${escapeHtml(f.name || f.id)} (${escapeHtml(f.region)}), ${usdFmt(f.monthly_cost)}/month</li>`).join("");
  $("modal-mode").textContent = mode === "live"
    ? "These actions run on your real AWS account. Deleted volumes, IPs and snapshots cannot be recovered."
    : "Demo mode: these actions run against the simulated account.";
  show("confirm-wrap", mode === "live");
  $("confirm").value = "";
  show("modal", true);
}

async function runActions() {
  if (mode === "live" && $("confirm").value.trim() !== "DEFUSE") {
    $("confirm").focus();
    return;
  }
  show("modal", false);
  $("defuse").disabled = true;
  try {
    const data = await post("/api/defuse", { ids: [...selected], ...params() });
    const done = data.results.filter((r) => r.ok);
    const failed = data.results.filter((r) => !r.ok);
    const saved = done.reduce((a, r) => a + r.saved, 0);
    findings = data.findings;
    selected = new Set();
    renderSummary(data.summary);
    renderRegions();
    renderFindings();
    renderDock();
    $("toast").textContent = `Done: ${done.length} resource${done.length === 1 ? "" : "s"} stopped or deleted. ` +
      `Monthly cost down by ${usdFmt(saved)} (${pkrFmt(saved * params().pkr_rate)}).`;
    show("toast", true);
    if (failed.length) showError(failed.map((r) => r.message).join(" · "));
  } catch (e) {
    showError(e.message);
  } finally {
    $("defuse").disabled = false;
  }
}

async function boot() {
  const s = await (await fetch("/api/status")).json();
  mode = s.mode;
  scanRegions = s.regions;
  if (!s.ready) {
    setTimeout(boot, 1500);
    return;
  }
  $("mode").textContent = s.account;
  show("demo-tools", s.mode === "demo");
  $("scan").disabled = false;
}

$("scan").addEventListener("click", scan);
$("defuse").addEventListener("click", openModal);
$("cancel").addEventListener("click", () => show("modal", false));
$("go").addEventListener("click", runActions);
$("select-zombies").addEventListener("change", (e) => {
  findings.filter((f) => f.zombie).forEach((f) => (e.target.checked ? selected.add(f.id) : selected.delete(f.id)));
  renderFindings();
  renderDock();
});
$("reset").addEventListener("click", async () => {
  await post("/api/reset");
  findings = [];
  selected = new Set();
  show("results", false);
  show("toast", false);
  renderDock();
  ["usd", "wasted", "idle-count", "countdown"].forEach((id) => { $(id).textContent = "–"; });
  ["pkr", "wasted-pkr", "idle-sub", "yearly"].forEach((id) => { $(id).textContent = ""; });
});
["budget", "rate"].forEach((id) => $(id).addEventListener("change", async () => {
  if (!scannedRegions.length) return;
  const data = await post("/api/summary", params());
  renderSummary(data.summary);
  renderFindings();
  renderDock();
}));

boot();
