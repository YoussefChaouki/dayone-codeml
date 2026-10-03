// DayOne phone simulator: renders the chat, sends events, polls the background state.
const $ = (s) => document.querySelector(s);
let lastId = 0;
let lang = "fr";

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
// WhatsApp-like formatting: *bold*, _italic_
function format(text) {
  return escapeHtml(text || "")
    .replace(/\*([^*\n]+)\*/g, "<strong>$1</strong>")
    .replace(/(^|\s)_([^_\n]+)_/g, "$1<em>$2</em>");
}

function render(msg) {
  const box = $("#messages");
  const el = document.createElement("div");
  el.className = "msg " + (msg.role === "user" ? "user" : "agent");
  el.innerHTML = format(msg.text);
  if (msg.image) {
    const img = document.createElement("img");
    img.src = msg.image;
    img.alt = "image";
    img.onclick = () => window.open(msg.image, "_blank");
    el.appendChild(img);
  }
  box.appendChild(el);
  if (msg.buttons && msg.buttons.length) {
    const bar = document.createElement("div");
    bar.className = "buttons";
    for (const b of msg.buttons) {
      const btn = document.createElement("button");
      btn.textContent = b.title;
      btn.onclick = () => { bar.classList.add("used"); send({ type: "button", id: b.id, title: b.title }); };
      bar.appendChild(btn);
    }
    box.appendChild(bar);
  }
  box.scrollTop = box.scrollHeight;
}

async function poll() {
  try {
    const r = await fetch("/api/messages?after=" + lastId);
    const msgs = await r.json();
    for (const m of msgs) { render(m); lastId = Math.max(lastId, m.id); }
  } catch (e) {
    $("#presence").textContent = "connexion à l'application perdue… (" + e.message + ")";
  }
}

async function send(event) {
  const r = await fetch("/api/send", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(event) });
  if (!r.ok) { render({ role: "agent", text: "⚠️ erreur " + r.status }); return; }
  await poll();
}

async function sendText() {
  const v = $("#text").value.trim();
  if (!v) return;
  $("#text").value = "";
  await send({ type: "text", text: v });
}

async function sendSample(name) {
  $("#gallery").close();
  render({ role: "user", text: "📷 envoi de la photo…" });
  await fetch("/api/sample/" + encodeURIComponent(name), { method: "POST" });
  $("#messages").lastChild.remove();
  await poll();
}

async function sendFile(file) {
  $("#gallery").close();
  const fd = new FormData();
  fd.append("file", file);
  await fetch("/api/photo", { method: "POST", body: fd });
  await poll();
}

async function openGallery() {
  const list = await (await fetch("/api/samples")).json();
  const groups = {};
  for (const s of list) (groups[s.group] = groups[s.group] || []).push(s);
  $("#samples").innerHTML = "";
  for (const [g, items] of Object.entries(groups)) {
    const h = document.createElement("h3"); h.textContent = g; $("#samples").appendChild(h);
    const grid = document.createElement("div"); grid.className = "grid";
    for (const s of items) {
      const b = document.createElement("button");
      b.innerHTML = `<img loading="lazy" src="/api/samples/${encodeURIComponent(s.name)}" alt=""><span>${escapeHtml(s.name)}</span>`;
      b.onclick = () => sendSample(s.name);
      grid.appendChild(b);
    }
    $("#samples").appendChild(grid);
  }
  $("#gallery").showModal();
}

function time(ts) { return new Date(ts * 1000).toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit", second: "2-digit" }); }

async function refreshState() {
  let s;
  try { s = await (await fetch("/api/state")).json(); } catch (e) {
    $("#presence").textContent = "connexion à l'application perdue… (" + e.message + ")";
    return;
  }
  lang = s.lang; $("#lang").textContent = lang.toUpperCase();
  const net = $("#net");
  net.setAttribute("aria-pressed", s.online ? "true" : "false");
  net.textContent = s.online ? "📶 En ligne" : "📴 Hors ligne";
  $("#presence").textContent = s.online ? "en ligne · traitement IA disponible" : "hors ligne · tout est gardé sur le téléphone";
  $("#netlog").innerHTML = s.network_log.slice().reverse().map((l) => `<li>${escapeHtml(l)}</li>`).join("") +
    (s.faults.length ? `<li>⚡ panne programmée : ${s.faults.join(", ")}</li>` : "");
  $("#outbox").innerHTML = s.outbox.length ? s.outbox.map((j) => `<li>#${j.id} ${j.kind} ${j.ref} · essais ${j.attempts}${j.last_error ? " · " + escapeHtml(j.last_error) : ""}</li>`).join("") : "<li>vide</li>";
  $("#raw").textContent = Object.entries(s.encrypted_sample).map(([k, v]) => `${k}: ${v}`).join("\n") || "(vide)";
  $("#patients").textContent = s.patients;
  $("#records").innerHTML = s.records.length ? s.records.map((r) => `
    <div class="rec">
      <div class="rec-head"><span>Fiche <code>${r.short}</code> · v${r.version} · ${r.n_fields} champs${r.to_review ? " · " + r.to_review + " ❓" : ""}${r.patient ? " · patiente <code>" + r.patient + "</code>" : ""}</span>
      <span class="state ${r.state}">${r.label}</span></div>
      <div class="pages">${r.pages.map((p) => `${p.type || "?"} (${p.status}${p.pii_masked.length ? ", 🔒 " + p.pii_masked.length + " zone(s) masquée(s)" : ""})`).join(" · ")}</div>
      <div class="timeline">${r.history.map((h) => `<span title="${escapeHtml(h.reason || "")} — ${h.actor}">${time(h.ts)} ${h.to}</span>`).join("")}</div>
    </div>`).join("") : '<p class="small">Aucune fiche.</p>';
}

$("#send").onclick = sendText;
$("#text").addEventListener("keydown", (e) => { if (e.key === "Enter") sendText(); });
$("#camera").onclick = openGallery;
$("#file").onchange = (e) => e.target.files[0] && sendFile(e.target.files[0]);
$("#lang").onclick = () => send({ type: "text", text: lang === "fr" ? "language en" : "langue fr" });
$("#net").onclick = async () => {
  const online = $("#net").getAttribute("aria-pressed") !== "true";
  await fetch("/api/network", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ online }) });
  refreshState();
};
document.querySelectorAll("[data-fault]").forEach((b) => b.onclick = async () => {
  await fetch("/api/fault", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ kind: b.dataset.fault }) });
  refreshState();
});
$("#restart").onclick = async () => {
  const r = await (await fetch("/api/restart", { method: "POST" })).json();
  render({ role: "agent", text: `💥 Application tuée puis relancée. ${r.pending_jobs} tâche(s) en attente retrouvée(s) dans le stockage chiffré.` });
  refreshState();
};
$("#access").onclick = async () => {
  const s = await (await fetch("/api/state")).json();
  const synced = s.records.flatMap((r) => r.pages.filter((p) => p.status === "processed").map((p) => p.id));
  if (!synced.length) { $("#access-result").textContent = "Aucune page encore envoyée au serveur."; return; }
  const r = await fetch("http://127.0.0.1:8100/v1/images/" + synced[0], { headers: { Authorization: "Bearer " + $("#role").value } });
  if (r.ok) {
    const url = URL.createObjectURL(await r.blob());
    $("#access-result").innerHTML = `✅ Accès autorisé (journalisé). <a href="${url}" target="_blank">Voir l'image masquée</a>`;
  } else {
    $("#access-result").textContent = `⛔ Refusé (${r.status}) — accès journalisé.`;
  }
};

poll(); refreshState();
setInterval(poll, 1000);
setInterval(refreshState, 1500);
(async () => { if (lastId === 0) { await new Promise((r) => setTimeout(r, 400)); if (lastId === 0) send({ type: "text", text: "menu" }); } })();
