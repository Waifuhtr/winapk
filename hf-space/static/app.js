/* Winlator Tek-Oyun Port Aracı — arayüz mantığı.
 *
 * Tasarım notları:
 *  - Loglar WebSocket ile satır satır gelir. Sunucu geçmişi de tuttuğu için
 *    sayfa yenilense/geç açılsa bile hiçbir satır kaybolmaz.
 *  - Log DOM'una tek tek append etmek uzun build'lerde donmaya yol açıyordu;
 *    gelen olaylar bir kuyrukta biriktirilip requestAnimationFrame ile toplu
 *    basılıyor.
 *  - Ham log ayrıca bellekte dizi olarak tutulur; "Kopyala" ve "İndir"
 *    butonları DOM'dan değil bu diziden çalışır (renk kodları bulaşmasın).
 */

const $ = (id) => document.getElementById(id);

const state = {
  socket: null,
  running: false,
  rawLines: [],
  pending: [],
  flushQueued: false,
  steps: [],
  uploaded: null,
  sourceKind: "upload",
  elapsedTimer: null,
  startedAt: 0,
};

const MAX_DOM_LINES = 5000;

/* ----------------------------------------------------------------- yardımcı */
function toast(message) {
  const el = $("toast");
  el.textContent = message;
  el.classList.add("show");
  clearTimeout(el._timer);
  el._timer = setTimeout(() => el.classList.remove("show"), 2200);
}

function fmtClock(ts) {
  const d = new Date(ts * 1000);
  return d.toTimeString().slice(0, 8);
}

function fmtDuration(seconds) {
  const s = Math.max(0, Math.floor(seconds));
  const m = Math.floor(s / 60);
  return m > 0 ? `${m}dk ${String(s % 60).padStart(2, "0")}sn` : `${s}sn`;
}

/* --------------------------------------------------------------------- log */
function pushEvent(event) {
  state.pending.push(event);
  if (!state.flushQueued) {
    state.flushQueued = true;
    requestAnimationFrame(flushLog);
  }
}

function flushLog() {
  state.flushQueued = false;
  const events = state.pending;
  state.pending = [];
  if (!events.length) return;

  const log = $("log");
  const onlyProblems = $("onlyProblems").checked;
  const frag = document.createDocumentFragment();

  for (const event of events) {
    if (event.type === "log") {
      state.rawLines.push(event.line);
      if (onlyProblems && event.level !== "warn" && event.level !== "error") continue;
      const line = document.createElement("div");
      line.className = `l-${event.level || "info"}`;
      const ts = document.createElement("span");
      ts.className = "ts";
      ts.textContent = fmtClock(event.ts);
      line.appendChild(ts);
      line.appendChild(document.createTextNode(event.line));
      frag.appendChild(line);
    } else if (event.type === "step") {
      applyStep(event);
    } else if (event.type === "progress") {
      $("mainProgress").style.width = `${(event.value * 100).toFixed(1)}%`;
      if (event.label) $("statusText").textContent = event.label;
    } else if (event.type === "result") {
      showResult(event);
    }
  }

  log.appendChild(frag);
  while (log.childElementCount > MAX_DOM_LINES) log.removeChild(log.firstChild);
  if ($("autoscroll").checked) log.scrollTop = log.scrollHeight;
}

function rerenderLog() {
  // "Sadece hata/uyarı" filtresi değişince tüm logu yeniden çiz.
  $("log").innerHTML = "";
  const all = state.rawLines.slice();
  state.rawLines = [];
  // Seviye bilgisi rawLines'ta yok; yeniden çizim için sunucudan geçmişi
  // istemek yerine basit bir sezgi kullanıyoruz (filtre yalnızca görseldir).
  for (const line of all) {
    state.rawLines.push(line);
    const level = /BAŞARISIZ|hata|error|ERR/i.test(line) ? "error"
                : /uyarı|warn|bulunamadı/i.test(line) ? "warn" : "info";
    if ($("onlyProblems").checked && level !== "warn" && level !== "error") continue;
    const el = document.createElement("div");
    el.className = `l-${level}`;
    el.textContent = line;
    $("log").appendChild(el);
  }
}

/* ------------------------------------------------------------------ adımlar */
function renderSteps(steps) {
  state.steps = steps;
  const host = $("steps");
  host.innerHTML = "";
  for (const step of steps) {
    const el = document.createElement("div");
    el.className = "step";
    el.id = `step-${step.key}`;
    el.innerHTML = `<span class="dot"></span><span>${step.label}</span>`;
    host.appendChild(el);
  }
}

function applyStep(event) {
  const el = $(`step-${event.key}`);
  if (!el) return;
  el.classList.remove("running", "done", "failed");
  el.classList.add(event.status === "running" ? "running" : event.status);
  if (event.detail) {
    el.lastElementChild.textContent =
      `${state.steps.find((s) => s.key === event.key)?.label || event.key} · ${event.detail}`;
  }
}

function resetSteps() {
  for (const step of state.steps) {
    const el = $(`step-${step.key}`);
    if (el) {
      el.classList.remove("running", "done", "failed");
      el.lastElementChild.textContent = step.label;
    }
  }
  $("mainProgress").style.width = "0%";
}

/* ------------------------------------------------------------------- sonuç */
function showResult(result) {
  const box = $("summary");
  box.classList.remove("hidden", "ok", "bad");
  box.classList.add(result.ok ? "ok" : "bad");

  if (!result.ok) {
    box.innerHTML = `<h3>${result.aborted ? "İptal edildi" : "Build başarısız"}</h3>
      <p class="muted">${escapeHtml(result.error || "")}</p>
      <p class="muted">Log'u kopyalayıp hatanın tamamına bakabilirsin.</p>`;
    const activeStep = document.querySelector(".step.running");
    if (activeStep) { activeStep.classList.remove("running"); activeStep.classList.add("failed"); }
    return;
  }

  const missing = result.missingDlls || [];
  const smoke = result.smokeTest || {};
  box.innerHTML = `
    <h3>Build tamamlandı</h3>
    <dl>
      <dt>Oyun</dt><dd>${escapeHtml(result.gameName || "-")}</dd>
      <dt>Exe</dt><dd>${escapeHtml(result.execPathDos || "-")}</dd>
      <dt>Süre</dt><dd>${fmtDuration(result.durationSeconds || 0)}</dd>
      <dt>Payload</dt><dd>${(result.payloadBytes / 1048576).toFixed(1)} MB</dd>
      <dt>rootfs</dt><dd>${result.rootfsRepacked ? "yamalandı + paketlendi" : "değişmedi"}</dd>
      <dt>Import kontrolü</dt><dd>${result.importsOk
        ? "tüm bağımlılıklar çözüldü"
        : `${missing.length} eksik DLL: ${escapeHtml(missing.slice(0, 6).join(", "))}`}</dd>
      <dt>Smoke test</dt><dd>${smoke.ran
        ? (smoke.exited_early
            ? `erken çıktı (exit ${smoke.exit_code})`
            : `${smoke.survived_seconds}sn ayakta kaldı`)
        : "çalıştırılmadı"}</dd>
    </dl>`;
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = String(text);
  return div.innerHTML;
}

/* --------------------------------------------------------------- WebSocket */
function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const socket = new WebSocket(`${proto}://${location.host}/ws`);
  state.socket = socket;

  socket.onmessage = (message) => {
    const data = JSON.parse(message.data);
    if (data.type === "batch") { data.events.forEach(pushEvent); return; }
    if (data.type === "synced") { setRunning(data.running); return; }
    pushEvent(data);
    if (data.type === "result") setRunning(false);
  };
  socket.onclose = () => setTimeout(connect, 1500);
  socket.onerror = () => socket.close();
}

/* ------------------------------------------------------------------ durum */
function setRunning(running) {
  state.running = running;
  $("buildBtn").disabled = running;
  $("cancelBtn").disabled = !running;
  $("buildBtn").textContent = running ? "Build çalışıyor…" : "Build başlat";

  clearInterval(state.elapsedTimer);
  if (running) {
    if (!state.startedAt) state.startedAt = Date.now();
    state.elapsedTimer = setInterval(() => {
      $("elapsed").textContent = fmtDuration((Date.now() - state.startedAt) / 1000);
    }, 1000);
  } else {
    state.startedAt = 0;
    refreshArtifacts();
  }
}

async function refreshArtifacts() {
  try {
    const data = await (await fetch("/api/artifacts")).json();
    const host = $("artifactList");
    if (!data.artifacts.length) {
      host.className = "artifactlist muted";
      host.textContent = "Henüz çıktı yok.";
      return;
    }
    host.className = "artifactlist";
    host.innerHTML = "";
    for (const item of data.artifacts) {
      const row = document.createElement("div");
      row.className = "artifact" + (item.name.endsWith(".zip") ? " primary-artifact" : "");
      row.innerHTML = `<span class="name">${escapeHtml(item.name)}</span>
        <span class="size">${item.human}</span>
        <a href="/api/download/${encodeURIComponent(item.name)}" download>İndir</a>`;
      host.appendChild(row);
    }
  } catch (err) { /* sessiz: ağ hatası build'i etkilemez */ }
}

/* ------------------------------------------------------------ applicationId */
function validateAppId() {
  const input = $("app_id");
  const hint = $("appIdHint");
  const value = input.value.trim();
  const max = window.__appIdMax || 12;

  if (!/^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$/.test(value)) {
    hint.className = "hint err";
    hint.textContent = "Geçersiz format. Örnek: com.gameport";
    return false;
  }
  if (value.length > max) {
    hint.className = "hint err";
    hint.textContent = `${value.length}/${max} karakter — ÇOK UZUN. Wine binary'sinde `
      + `'/data/data/${window.__bakedAppId}/files/rootfs' gömülü olduğu için `
      + `daha uzun isim byte-patch'i bozar.`;
    return false;
  }
  if (value.length === max) {
    hint.className = "hint ok";
    hint.textContent = `${value.length}/${max} karakter — ideal. Aynı uzunlukta `
      + "byte-patch yapılacak, offset kayması olmaz.";
    return true;
  }
  hint.className = "hint warn";
  hint.textContent = `${value.length}/${max} karakter — çalışır (NUL padding) ama `
    + `tam ${max} karakter en güvenlisidir.`;
  return true;
}

/* ------------------------------------------------------------------ upload */
async function uploadFile(file) {
  const bar = $("uploadBar");
  const fill = bar.firstElementChild;
  bar.classList.remove("hidden");
  fill.style.width = "0%";
  $("uploadState").textContent = `Yükleniyor: ${file.name}`;

  const form = new FormData();
  form.append("file", file);

  await new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/upload");
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) fill.style.width = `${(e.loaded / e.total * 100).toFixed(1)}%`;
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        state.uploaded = JSON.parse(xhr.responseText);
        $("uploadState").textContent = `Hazır: ${state.uploaded.name} (${state.uploaded.human})`;
        toast("Yükleme tamam");
        resolve();
      } else {
        $("uploadState").textContent = `Yükleme hatası: ${xhr.status}`;
        reject(new Error(xhr.responseText));
      }
    };
    xhr.onerror = () => reject(new Error("ağ hatası"));
    xhr.send(form);
  }).catch((err) => toast(`Yükleme başarısız: ${err.message}`));

  setTimeout(() => bar.classList.add("hidden"), 800);
}

/* -------------------------------------------------------------------- init */
async function init() {
  const data = await (await fetch("/api/state")).json();
  window.__appIdMax = data.appIdMaxLength;
  window.__bakedAppId = data.bakedAppId;

  renderSteps(data.steps);
  $("hostinfo").innerHTML =
    `${data.host.cpus} vCPU · HF_TOKEN: ${data.host.hasDatasetToken ? "var" : "yok"}`;
  $("tokenHint").className = data.host.hasDatasetToken ? "hint ok" : "hint warn";
  $("tokenHint").textContent = data.host.hasDatasetToken
    ? "HF_TOKEN secret'ı tanımlı; private dataset okunabilir."
    : "HF_TOKEN secret'ı yok. Private dataset için Space Settings → "
      + "Variables and secrets altından HF_TOKEN ekle.";

  const presets = $("box64_preset");
  for (const preset of data.box64Presets) {
    const opt = document.createElement("option");
    opt.value = preset;
    opt.textContent = preset === "STABILITY" ? "STABILITY (Unity için önerilen)" : preset;
    presets.appendChild(opt);
  }
  presets.value = data.defaults.box64_preset;
  $("graphics_driver").value = data.defaults.graphics_driver;

  if (data.uploaded) {
    state.uploaded = data.uploaded;
    $("uploadState").textContent = `Hazır: ${data.uploaded.name} (${data.uploaded.human})`;
  }
  if (data.lastResult) showResult(data.lastResult);

  validateAppId();
  setRunning(data.running);
  if (data.running && data.elapsed) state.startedAt = Date.now() - data.elapsed * 1000;
  refreshArtifacts();
  connect();
}

/* ------------------------------------------------------------------ events */
document.addEventListener("DOMContentLoaded", () => {
  init();

  $("app_id").addEventListener("input", validateAppId);
  $("onlyProblems").addEventListener("change", rerenderLog);

  // kaynak sekmeleri
  for (const tab of document.querySelectorAll("#sourceTabs .tab")) {
    tab.addEventListener("click", () => {
      document.querySelectorAll("#sourceTabs .tab").forEach((t) => t.classList.remove("active"));
      tab.classList.add("active");
      state.sourceKind = tab.dataset.source;
      document.querySelectorAll(".tabpane").forEach((pane) =>
        pane.classList.toggle("hidden", pane.dataset.pane !== state.sourceKind));
    });
  }

  // dosya yükleme
  const dropzone = $("dropzone");
  dropzone.addEventListener("click", () => $("fileInput").click());
  $("fileInput").addEventListener("change", (e) => {
    if (e.target.files[0]) uploadFile(e.target.files[0]);
  });
  ["dragenter", "dragover"].forEach((type) =>
    dropzone.addEventListener(type, (e) => { e.preventDefault(); dropzone.classList.add("drag"); }));
  ["dragleave", "drop"].forEach((type) =>
    dropzone.addEventListener(type, (e) => { e.preventDefault(); dropzone.classList.remove("drag"); }));
  dropzone.addEventListener("drop", (e) => {
    const file = e.dataTransfer.files[0];
    if (file) uploadFile(file);
  });

  // log araçları
  $("copyBtn").addEventListener("click", async () => {
    const text = state.rawLines.join("\n");
    try {
      await navigator.clipboard.writeText(text);
      toast(`${state.rawLines.length} satır kopyalandı`);
    } catch {
      // clipboard API HTTPS dışında bloklanabilir — textarea fallback
      const area = document.createElement("textarea");
      area.value = text;
      document.body.appendChild(area);
      area.select();
      document.execCommand("copy");
      area.remove();
      toast(`${state.rawLines.length} satır kopyalandı`);
    }
  });

  $("downloadLogBtn").addEventListener("click", () => {
    const blob = new Blob([state.rawLines.join("\n")], { type: "text/plain" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `winlator-port-build-${Date.now()}.log`;
    link.click();
    URL.revokeObjectURL(url);
  });

  $("clearBtn").addEventListener("click", () => {
    state.rawLines = [];
    $("log").innerHTML = "";
    toast("Log temizlendi");
  });

  $("cancelBtn").addEventListener("click", () => fetch("/api/cancel", { method: "POST" }));

  $("buildBtn").addEventListener("click", async () => {
    if (!validateAppId()) { toast("applicationId geçersiz"); return; }
    if (state.sourceKind === "upload" && !state.uploaded) {
      toast("Önce oyun arşivini yükle"); return;
    }

    const payload = {
      app_id: $("app_id").value.trim(),
      game_name: $("game_name").value.trim() || "Game",
      app_label: $("app_label").value.trim(),
      version_name: $("version_name").value.trim() || "1.0",
      version_code: parseInt($("version_code").value, 10) || 1,
      source_kind: state.sourceKind,
      dataset_repo: $("dataset_repo").value.trim(),
      dataset_file: $("dataset_file").value.trim(),
      exec_path_override: $("exec_path_override").value.trim(),
      box64_preset: $("box64_preset").value,
      exec_args: $("exec_args").value.trim(),
      screen_size: $("screen_size").value.trim() || "1280x720",
      graphics_driver: $("graphics_driver").value,
      dxwrapper: $("dxwrapper").value,
      force_fullscreen: $("force_fullscreen").checked,
      auto_redist: $("auto_redist").checked,
      winetricks_verbs: $("winetricks_verbs").value,
      prefix_cleanup: $("prefix_cleanup").checked,
      run_smoke_test: $("run_smoke_test").checked,
      smoke_test_seconds: parseInt($("smoke_test_seconds").value, 10) || 25,
      zstd_level: parseInt($("zstd_level").value, 10) || 19,
    };

    $("summary").classList.add("hidden");
    resetSteps();
    state.rawLines = [];
    $("log").innerHTML = "";
    state.startedAt = Date.now();

    const response = await fetch("/api/build", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: response.statusText }));
      toast(`Başlatılamadı: ${error.detail}`);
      return;
    }
    setRunning(true);
  });
});
