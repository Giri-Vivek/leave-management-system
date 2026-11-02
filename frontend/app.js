/* Leave frontend: plain JS, no build step. Config comes from config.js. */
const CFG = window.APP_CONFIG;
if (!CFG) {
  document.body.innerHTML = "<p style='padding:24px;font-family:sans-serif'>config.js is missing. Create frontend/config.js (see README) and reload.</p>";
  throw new Error("config.js missing");
}
const $ = (sel, root = document) => root.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const REDUCED = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

const STATUS = {
  PENDING_MANAGER: ["With manager", "b-wait"], PENDING_HR: ["With HR", "b-wait"],
  ESCALATED: ["Escalated to HR", "b-info"], APPROVED: ["Approved", "b-ok"],
  REJECTED: ["Rejected", "b-bad"], AUTO_REJECTED: ["Auto-rejected", "b-bad"],
  EXPIRED: ["Expired", "b-flat"], FAILED: ["Failed", "b-flat"],
};
const TYPE_COLOR = { SICK: "#B91C1C", CASUAL: "#1D4ED8", EARNED: "#15803D", UNPAID: "#475569" };
const TIPS = [
  "Weekends are never counted as leave days.",
  "Sick leave can be back-dated. Other types cannot.",
  "Leave longer than 5 working days also needs HR approval.",
  "Your manager decides from a link in their email.",
  "Days stay on hold as pending until the final approval.",
];

let token = sessionStorage.getItem("idToken");
let groups = [];
let calendar = null;

/* ---------------- themes ---------------- */
const THEMES = ["daylight", "midnight", "ocean", "sunset"];
function applyTheme(name) {
  if (!THEMES.includes(name)) name = "daylight";
  document.documentElement.dataset.theme = name;
  try { localStorage.setItem("leave-theme", name); } catch (e) { /* private mode */ }
  document.querySelectorAll("[data-theme-pick]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.themePick === name)));
}
document.querySelectorAll("[data-theme-pick]").forEach((b) => b.addEventListener("click", () => applyTheme(b.dataset.themePick)));
applyTheme(document.documentElement.dataset.theme);

/* ---------------- animated text ---------------- */
function typewriter(el, phrases) {
  if (REDUCED) { el.textContent = phrases[0]; return; }
  let p = 0, i = 0, deleting = false;
  (function tick() {
    if ($("#login-view").hidden) return;                       // stop once signed in
    const full = phrases[p];
    el.textContent = full.slice(0, i);
    if (!deleting && i === full.length) { deleting = true; return setTimeout(tick, 1700); }
    if (deleting && i === 0) { deleting = false; p = (p + 1) % phrases.length; return setTimeout(tick, 350); }
    i += deleting ? -1 : 1;
    setTimeout(tick, deleting ? 28 : 55);
  })();
}

function buildGreeting(text) {
  const el = $("#greeting");
  el.setAttribute("aria-label", text);
  el.textContent = "";
  let i = 0;
  text.split(" ").forEach((w, wi, all) => {
    const word = document.createElement("span");
    word.className = "word";
    word.setAttribute("aria-hidden", "true");
    [...w].forEach((c) => {
      const s = document.createElement("span");
      s.className = "ch"; s.style.setProperty("--i", i++); s.textContent = c;
      word.append(s);
    });
    el.append(word);
    if (wi < all.length - 1) el.append(" ");
  });
}

function countUp(el, to, ms = 900) {
  if (REDUCED || to === 0) { el.textContent = to; return; }
  const t0 = performance.now();
  (function step(t) {
    const k = Math.min((t - t0) / ms, 1);
    el.textContent = Math.round(to * (1 - Math.pow(1 - k, 3)));
    if (k < 1) requestAnimationFrame(step);
  })(t0);
}

let tipTimer = null;
function rotateTips() {
  const el = $("#tip");
  let n = 0;
  el.textContent = TIPS[0];
  clearInterval(tipTimer);
  if (REDUCED) return;
  tipTimer = setInterval(() => {
    el.classList.add("swap");
    setTimeout(() => { n = (n + 1) % TIPS.length; el.textContent = TIPS[n]; el.classList.remove("swap"); }, 260);
  }, 4500);
}

const greetingWord = () => { const h = new Date().getHours(); return h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening"; };
const initials = (name) => name.split(/\s+/).map((w) => w[0]).slice(0, 2).join("").toUpperCase();

/* ---------------- auth (Cognito USER_PASSWORD_AUTH over plain fetch) ---------------- */
function jwt(t) { return JSON.parse(atob(t.split(".")[1].replace(/-/g, "+").replace(/_/g, "/"))); }
async function signIn(email, password) {
  const r = await fetch(`https://cognito-idp.${CFG.region}.amazonaws.com/`, {
    method: "POST",
    headers: { "Content-Type": "application/x-amz-json-1.1", "X-Amz-Target": "AWSCognitoIdentityProviderService.InitiateAuth" },
    body: JSON.stringify({ AuthFlow: "USER_PASSWORD_AUTH", ClientId: CFG.clientId, AuthParameters: { USERNAME: email, PASSWORD: password } }),
  });
  const d = await r.json();
  if (!r.ok) throw new Error(d.message || "Sign-in failed");
  if (d.ChallengeName) throw new Error("This account needs a new password. Ask an admin to reset it.");
  return d.AuthenticationResult.IdToken;
}
function signOut() { sessionStorage.removeItem("idToken"); location.reload(); }

/* ---------------- API helper ---------------- */
async function api(path, options = {}) {
  const res = await fetch(CFG.apiBase + path, {
    ...options,
    headers: { Authorization: token, "Content-Type": "application/json", ...(options.headers || {}) },
  });
  if (res.status === 401) { signOut(); throw new Error("Session expired"); }
  const isJson = (res.headers.get("content-type") || "").includes("json");
  const data = isJson ? await res.json() : await res.blob();
  if (!res.ok) throw new Error((isJson && data.error) || res.statusText);
  return data;
}

/* ---------------- helpers ---------------- */
const badge = (s) => { const [label, cls] = STATUS[s] || [s, "b-flat"]; return `<span class="badge ${cls}">${esc(label)}</span>`; };
const dates = (r) => (r.start_date === r.end_date ? esc(r.start_date) : `${esc(r.start_date)} to ${esc(r.end_date)}`);
function workingDays(a, b) {
  let n = 0;
  for (let d = new Date(a + "T00:00"); d <= new Date(b + "T00:00"); d.setDate(d.getDate() + 1)) if (d.getDay() % 6) n++;
  return n;
}
const iso = (d) => new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
const plusDay = (s) => { const d = new Date(s + "T00:00"); d.setDate(d.getDate() + 1); return iso(d); };
function say(el, text, kind) { el.textContent = text; el.className = kind || ""; el.hidden = !text; }

/* ---------------- My leave ---------------- */
function meter(b) {
  if (b.unlimited) {
    return `<article class="meter"><div class="meter-head"><span class="meter-type">${esc(b.leave_type)}</span></div>
      <p class="meter-legend">${esc(b.description)}. No quota. ${b.used} day(s) taken this year.</p></article>`;
  }
  const free = Math.max(b.total - b.used - b.pending, 0);
  let i = 0;
  const cell = (cls) => `<span class="cell ${cls}" style="--i:${i++}"></span>`;
  const cells = [...Array(b.used)].map(() => cell("used")).join("")
    + [...Array(b.pending)].map(() => cell("pending")).join("")
    + [...Array(free)].map(() => cell("free")).join("");
  return `<article class="meter"><div class="meter-head"><span class="meter-type">${esc(b.leave_type)}</span>
    <span class="meter-left"><b data-to="${b.available}">0</b><small>days left</small></span></div>
    <div class="cells" role="img" aria-label="${b.used} used, ${b.pending} pending, ${free} free">${cells}</div>
    <p class="meter-legend">${b.used} used, ${b.pending} pending of ${b.total}${b.carried_forward ? ` (incl. ${b.carried_forward} carried forward)` : ""}</p></article>`;
}

async function loadMine() {
  const [bal, hist] = await Promise.all([api("/balances"), api("/requests")]);
  $("#balances").innerHTML = bal.balances.map(meter).join("");
  document.querySelectorAll("[data-to]").forEach((el) => countUp(el, +el.dataset.to));

  const counted = bal.balances.filter((b) => !b.unlimited);
  countUp($("#st-avail"), counted.reduce((s, b) => s + b.available, 0));
  countUp($("#st-pending"), counted.reduce((s, b) => s + b.pending, 0));
  countUp($("#st-used"), bal.balances.reduce((s, b) => s + b.used, 0));

  $("#f-type").innerHTML = bal.balances.map((b) =>
    `<option value="${esc(b.leave_type)}">${esc(b.leave_type)} (${b.unlimited ? "no quota" : b.available + " left"})</option>`).join("");
  const rows = hist.requests.map((r) => `<tr>
      <td>${esc(r.leave_type)}</td><td>${dates(r)}</td><td>${esc(r.days)}</td>
      <td>${badge(r.status)}${r.reject_reason ? `<span class="reason">${esc(r.reject_reason)}</span>` : ""}</td></tr>`);
  $("#history tbody").innerHTML = rows.join("") || `<tr><td colspan="4" class="empty">No requests yet. Use the form to submit your first one.</td></tr>`;
}

function updateHint() {
  const a = $("#f-start").value, b = $("#f-end").value;
  $("#f-hint").textContent = a && b && b >= a ? `${workingDays(a, b)} working day(s), weekends excluded.` : "Pick your dates to see the working days.";
}

async function submitLeave(e) {
  e.preventDefault();
  const btn = e.submitter, msg = $("#apply-msg");
  btn.disabled = true; say(msg, "");
  try {
    const out = await api("/leave", { method: "POST", body: JSON.stringify({
      leave_type: $("#f-type").value, start_date: $("#f-start").value, end_date: $("#f-end").value, reason: $("#f-reason").value }) });
    say(msg, out.message, out.request.status === "AUTO_REJECTED" ? "bad" : "ok");
    await loadMine();
  } catch (err) { say(msg, err.message, "bad"); }
  btn.disabled = false;
}

/* ---------------- Team ---------------- */
async function loadPending() {
  const { pending } = await api("/pending");
  $("#pending tbody").innerHTML = pending.map((r) => `<tr><td>${esc(r.employee_name)}</td><td>${esc(r.leave_type)}</td>
    <td>${dates(r)}</td><td>${esc(r.days)}</td><td>${badge(r.status)}</td></tr>`).join("")
    || `<tr><td colspan="5" class="empty">Nothing is waiting for you.</td></tr>`;
}

function startCalendar() {
  if (calendar) return;
  calendar = new FullCalendar.Calendar($("#calendar"), {
    initialView: "dayGridMonth", height: "auto", firstDay: 1, dayMaxEvents: 3,
    headerToolbar: { left: "prev,next today", center: "title", right: "" },
    events: async (info, ok, fail) => {
      try {
        const months = new Set();
        for (let d = new Date(info.start); d < info.end; d.setDate(d.getDate() + 1)) months.add(iso(d).slice(0, 7));
        const seen = new Map();
        for (const m of await Promise.all([...months].map((m) => api(`/calendar?month=${m}`))))
          m.events.forEach((e) => seen.set(e.employee_id + e.start_date, e));
        ok([...seen.values()].map((e) => ({
          title: `${e.name} (${e.leave_type})`, start: e.start_date, end: plusDay(e.end_date), allDay: true,
          color: TYPE_COLOR[e.leave_type] || "#475569" })));
      } catch (err) { fail(err); }
    },
  });
  calendar.render();
}

async function downloadReport(e) {
  e.preventDefault();
  const msg = $("#report-msg"); say(msg, "");
  try {
    const from = $("#r-from").value, to = $("#r-to").value;
    const blob = await api(`/report?from=${from}&to=${to}`);
    const a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(blob), download: `leave-report-${from}-to-${to}.csv` });
    document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  } catch (err) { say(msg, err.message); }
}

/* ---------------- wiring ---------------- */
function switchTab(name) {
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("is-active", t.dataset.tab === name));
  $("#tab-mine").hidden = name !== "mine";
  $("#tab-team").hidden = name !== "team";
  if (name === "team") { startCalendar(); calendar.refetchEvents(); loadPending().catch(() => {}); }
}

async function showApp() {
  $("#login-view").hidden = true; $("#app-view").hidden = false;
  const me = await api("/me");
  groups = me.groups;
  const name = me.employee.name;
  $("#who-name").textContent = name;
  $("#avatar").textContent = initials(name);
  $("#team-tab").hidden = !(groups.includes("Manager") || groups.includes("HRAdmin"));

  $("#hero-date").textContent = new Date().toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" });
  buildGreeting(`${greetingWord()}, ${name.split(" ")[0]}`);
  rotateTips();

  const today = new Date(), first = new Date(today.getFullYear(), today.getMonth(), 1);
  $("#r-from").value = iso(first); $("#r-to").value = iso(today);
  await loadMine();
}

$("#login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const err = $("#login-error"); say(err, "");
  e.submitter.disabled = true;
  try {
    token = await signIn($("#email").value.trim().toLowerCase(), $("#password").value);
    sessionStorage.setItem("idToken", token);
    await showApp();
  } catch (ex) { say(err, ex.message); }
  e.submitter.disabled = false;
});
$("#apply-form").addEventListener("submit", submitLeave);
$("#report-form").addEventListener("submit", downloadReport);
$("#f-start").addEventListener("change", () => { if (!$("#f-end").value || $("#f-end").value < $("#f-start").value) $("#f-end").value = $("#f-start").value; updateHint(); });
$("#f-end").addEventListener("change", updateHint);
$("#signout").addEventListener("click", signOut);
document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => switchTab(t.dataset.tab)));

typewriter($("#typed"), ["approved in one click.", "tracked to the day.", "planned with your team.", "balanced automatically."]);
if (token && jwt(token).exp * 1000 > Date.now()) showApp().catch(signOut);
