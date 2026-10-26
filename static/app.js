const $ = (id) => document.getElementById(id);
const usdFmt = (n) => "$" + Number(n).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const pkrFmt = (n) => "PKR " + Math.round(n).toLocaleString("en-IN");

let mode = null;
let findings = [];
let labels = {};
let selected = new Set();
let shownUsd = 0;
let scannedRegions = [];

function show(id, visible) { $(id).classList.toggle("hidden", !visible); }

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function rollTo(target) {
  const start = shownUsd, t0 = performance.now(), dur = 900;
  function step(t) {
    const p = Math.min(1, (t - t0) / dur);
    shownUsd = start + (target - start) * (1 - Math.pow(1 - p, 3));
    $("usd").textContent = usdFmt(shownUsd);
    if (p < 1) requestAnimationFrame(step);
  }
  requestAnimationFrame(step);
}

function lakh(pkr) {
  return pkr >= 100000 ? `PKR ${(pkr / 100000).toFixed(1)} lakh` : pkrFmt(pkr);
}

function renderSummary(s) {
  rollTo(s.monthly_usd);
  $("pkr").textContent = pkrFmt(s.monthly_pkr);
  $("wasted").textContent = `${usdFmt(s.wasted_usd)}`;
  $("yearly").textContent = lakh(s.yearly_pkr);
  const cd = $("countdown");
  if (s.monthly_usd < 1) {
    cd.className = "countdown safe";
    cd.textContent = "Bomb defused. Nothing is burning money.";
  } else if (s.zombies === 0) {
    cd.className = "countdown safe";
    cd.textContent = `No zombies left. The remaining ${usdFmt(s.monthly_usd)}/mo is stuff you actually use.`;
  } else if (s.days_to_budget === 0) {
    cd.className = "countdown armed";
    cd.textContent = `Burning ${usdFmt(s.daily_usd)}/day (${pkrFmt(s.daily_usd * s.pkr_rate)}). Budget already blown.`;
  } else {
    cd.className = "countdown armed";
    const d = s.days_to_budget;
    const when = d < 1 ? `${Math.round(d * 24)} hours` : `${d} days`;
    cd.textContent = `${usdFmt(s.budget_usd)} budget blows up in ${when}`;
  }
}

function renderRegions(regionList) {
  const counts = {};
  const used = {};
  findings.forEach((f) => {
    if (f.zombie) counts[f.region] = (counts[f.region] || 0) + 1;
    else used[f.region] = true;
  });
  $("regions").innerHTML = regionList.map((r) => {
    const cls = counts[r] ? "hot" : used[r] ? "used" : "";
    const label = counts[r] ? `${r} · ${counts[r]} zombie${counts[r] > 1 ? "s" : ""}` : r;
    return `<span class="region ${cls}">${label}</span>`;
  }).join("");
}

function ageText(days) {
  if (days == null) return "";
  if (days < 1) return "created today";
  return `running ${Math.round(days)} day${Math.round(days) === 1 ? "" : "s"}`;
}

function renderFindings(rate) {
  if (!findings.length) {
    $("findings").innerHTML = `<div class="card"><span></span><div><div class="title">Nothing running</div><div class="ok-note">No billable resources found in the scanned regions.</div></div></div>`;
    return;
  }
  $("findings").innerHTML = findings.map((f) => {
    const sel = selected.has(f.id);
    const reasons = f.zombie
      ? `<ul class="reasons">${f.reasons.map((r) => `<li>${escapeHtml(r)}</li>`).join("")}</ul>`
      : `<div class="ok-note">In use. Leave it alone.</div>`;
    return `<div class="card ${f.zombie ? "zombie" : ""} ${sel ? "selected" : ""}" data-id="${escapeHtml(f.id)}">
      <input type="checkbox" ${sel ? "checked" : ""} aria-label="Select ${escapeHtml(f.name || f.id)}">
      <div>
        <div><span class="kind">${escapeHtml(f.kind)}</span><span class="title">${escapeHtml(f.name || f.id)}</span></div>
        <div class="sub">${escapeHtml(f.id)} · ${escapeHtml(f.region)} · ${escapeHtml(f.detail)}${f.age_days != null ? " · " + ageText(f.age_days) : ""}</div>
        ${reasons}
      </div>
      <div class="cost">
        <div class="usd">${usdFmt(f.monthly_cost)}</div>
        <div class="pk">${pkrFmt(f.monthly_cost * rate)}/mo</div>
        <div class="act">${escapeHtml(labels[f.action] || "")}</div>
      </div>
    </div>`;
  }).join("");
  document.querySelectorAll(".card[data-id]").forEach((card) => {
    card.querySelector("input").addEventListener("change", (e) => {
      e.target.checked ? selected.add(card.dataset.id) : selected.delete(card.dataset.id);
      card.classList.toggle("selected", e.target.checked);
      renderDock();
    });
  });
}

function renderDock() {
  const save = findings.filter((f) => selected.has(f.id)).reduce((a, f) => a + f.monthly_cost, 0);
  $("sel-count").textContent = selected.size;
  $("sel-save").textContent = `${usdFmt(save)} (${pkrFmt(save * +$("rate").value)})`;
  show("dock", selected.size > 0);
}

function params() {
  return { budget: +$("budget").value || 0, pkr_rate: +$("rate").value || 280 };
}

async function post(path, body) {
  const res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || "Request failed");
  return data;
}

async function scan() {
  $("scan").disabled = true;
  show("results", false);
  show("radar", true);
  const demoRegions = ["us-east-1", "us-east-2", "us-west-1", "us-west-2", "ap-south-1", "ap-southeast-1",
    "ap-northeast-1", "eu-west-1", "eu-central-1", "eu-north-1", "me-south-1", "sa-east-1"];
  $("radar-regions").innerHTML = demoRegions.map((r) => `<span>${r}</span>`).join("");
  const spans = [...$("radar-regions").children];
  let i = 0;
  const tick = setInterval(() => {
    if (i < spans.length) { spans[i].classList.add("on"); $("radar-text").textContent = `Sweeping ${spans[i].textContent}…`; i++; }
  }, 900);
  try {
    const data = await post("/api/scan", params());
    findings = data.findings;
    labels = data.labels;
    selected = new Set();
    scannedRegions = data.regions;
    renderSummary(data.summary);
    renderRegions(scannedRegions);
    renderFindings(params().pkr_rate);
    renderDock();
    if (data.errors.length) {
      $("errors").textContent = `Skipped: ${data.errors.slice(0, 4).join(" · ")}`;
      show("errors", true);
    } else show("errors", false);
    show("results", true);
  } catch (e) {
    alert(e.message);
  } finally {
    clearInterval(tick);
    show("radar", false);
    $("scan").disabled = false;
  }
}

function openModal() {
  const chosen = findings.filter((f) => selected.has(f.id));
  $("modal-list").innerHTML = chosen.map((f) =>
    `<li>${escapeHtml(labels[f.action])}: ${escapeHtml(f.name || f.id)} (${escapeHtml(f.region)}), ${usdFmt(f.monthly_cost)}/mo</li>`).join("");
  $("modal-mode").textContent = mode === "live"
    ? "These actions run on your REAL AWS account. Deleted disks, IPs and snapshots cannot be recovered."
    : "Demo account: these actions run against the simulated AWS account.";
  show("confirm-wrap", mode === "live");
  $("confirm").value = "";
  show("modal", true);
}

async function doDefuse() {
  if (mode === "live" && $("confirm").value.trim() !== "DEFUSE") {
    $("confirm").focus();
    return;
  }
  show("modal", false);
  $("defuse").disabled = true;
  const ids = [...selected];
  try {
    const data = await post("/api/defuse", { ids, ...params() });
    const saved = data.results.reduce((a, r) => a + r.saved, 0);
    const failed = data.results.filter((r) => !r.ok);
    findings = data.findings;
    selected = new Set();
    renderSummary(data.summary);
    renderRegions(scannedRegions);
    renderFindings(params().pkr_rate);
    renderDock();
    $("boom").innerHTML = `<b>Bomb defused</b><span>You just saved ${usdFmt(saved)}/month · ${pkrFmt(saved * params().pkr_rate)}</span>`;
    show("boom", true);
    setTimeout(() => show("boom", false), 3300);
    if (failed.length) alert(failed.map((r) => r.message).join("\n"));
  } catch (e) {
    alert(e.message);
  } finally {
    $("defuse").disabled = false;
  }
}

async function boot() {
  const s = await (await fetch("/api/status")).json();
  mode = s.mode;
  if (!s.ready) {
    $("mode").textContent = "connecting…";
    setTimeout(boot, 1500);
    return;
  }
  $("mode").textContent = s.mode === "demo" ? `Demo · ${s.account}` : `Live · ${s.account}`;
  $("mode").className = `mode ${s.mode}`;
  show("demo-tools", s.mode === "demo");
  $("scan").disabled = false;
}

$("scan").addEventListener("click", scan);
$("defuse").addEventListener("click", openModal);
$("cancel").addEventListener("click", () => show("modal", false));
$("go").addEventListener("click", doDefuse);
$("select-zombies").addEventListener("click", () => {
  findings.filter((f) => f.zombie).forEach((f) => selected.add(f.id));
  renderFindings(params().pkr_rate);
  renderDock();
});
$("reset").addEventListener("click", async () => {
  await post("/api/reset");
  findings = [];
  selected = new Set();
  show("results", false);
  renderDock();
  $("countdown").className = "countdown";
  $("countdown").textContent = "Demo account reset. Scan again.";
});
["budget", "rate"].forEach((id) => $(id).addEventListener("change", async () => {
  if (!findings.length && !shownUsd) return;
  const data = await post("/api/summary", params());
  renderSummary(data.summary);
  renderFindings(params().pkr_rate);
  renderDock();
}));

boot();
