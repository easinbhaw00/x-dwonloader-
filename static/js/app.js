const $ = (sel) => document.querySelector(sel);
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// Where the API lives. Empty means "the server that delivered this page".
// When the page is hosted elsewhere (GitHub Pages), static/js/config.js sets it.
const API_BASE = String(window.XENO_API_BASE || "").replace(/\/+$/, "");
const BACKEND_MISSING = !API_BASE && /github\.io$/i.test(location.hostname);
const BACKEND_HINT =
  "Backend is not set up for this page. Set XENO_API_BASE in static/js/config.js to your XENO API URL.";

// Media indexes are only valid for the URL they were extracted from.
let extractedUrl = "";

function api(path) {
  return API_BASE + path;
}

async function request(path, options = {}) {
  if (BACKEND_MISSING) throw new Error(BACKEND_HINT);
  let response;
  try {
    response = await fetch(api(path), options);
  } catch {
    throw new Error(
      "Cannot reach the XENO API. Check that the server is running and that its XENO_CORS_ORIGINS includes this site."
    );
  }
  const data = await response.json().catch(() => ({}));
  return { ok: response.ok, data };
}

function postJSON(path, body) {
  return request(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

function errorText(data, fallback) {
  return typeof data.detail === "string" && data.detail ? data.detail : fallback;
}

function formatBytes(bytes) {
  if (!bytes) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  let value = bytes;
  let i = 0;
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024;
    i += 1;
  }
  return `${value.toFixed(i ? 1 : 0)} ${units[i]}`;
}

function message(el, text, error = false) {
  el.replaceChildren(text);
  el.style.display = "block";
  el.classList.toggle("error", error);
}

function renderQualities(data) {
  const el = $("#xresult");
  el.replaceChildren();
  el.style.display = "block";
  el.classList.remove("error");

  const heading = document.createElement("strong");
  heading.textContent = `Found ${data.media.length} downloadable video file(s)`;
  const note = document.createElement("p");
  note.className = "result-note";
  note.textContent = "Choose an MP4 quality. The highest available bitrate is listed first.";
  el.append(heading, note);

  data.media.forEach((media, mi) => {
    const card = document.createElement("div");
    card.className = "quality-card";

    const selectId = `quality-${mi}`;
    const label = document.createElement("label");
    label.htmlFor = selectId;
    label.textContent = `Video ${mi + 1} · ${media.type}`;

    const select = document.createElement("select");
    select.id = selectId;
    select.className = "quality-select";
    media.variants.forEach((variant) => {
      const option = document.createElement("option");
      option.value = variant.id;
      option.textContent = variant.quality;
      select.append(option);
    });

    const btn = document.createElement("button");
    btn.className = "action";
    btn.type = "button";
    btn.textContent = "Download MP4 ↓";
    btn.addEventListener("click", () => startXDownload(mi, Number(select.value), btn));

    card.append(label, select, btn);
    el.append(card);
  });
}

function renderProgress(el, job) {
  el.replaceChildren();
  el.style.display = "block";
  el.classList.remove("error");

  const title = document.createElement("strong");
  title.textContent = job.status === "queued" ? "Queued…" : "Downloading…";

  const known = typeof job.progress === "number";
  const track = document.createElement("div");
  track.className = known ? "progress" : "progress indeterminate";
  track.setAttribute("role", "progressbar");
  track.setAttribute("aria-label", "Download progress");
  track.setAttribute("aria-valuemin", "0");
  track.setAttribute("aria-valuemax", "100");
  const fill = document.createElement("span");
  if (known) {
    const pct = Math.min(100, Math.max(0, job.progress));
    fill.style.width = `${pct}%`;
    track.setAttribute("aria-valuenow", String(Math.round(pct)));
  }
  track.append(fill);

  const meta = document.createElement("p");
  meta.className = "result-note";
  const parts = [];
  if (known) parts.push(`${job.progress}%`);
  parts.push(formatBytes(job.downloaded_bytes || 0));
  meta.textContent = parts.join(" · ");

  el.append(title, track, meta);
}

function renderComplete(el, job) {
  el.replaceChildren();
  el.style.display = "block";
  el.classList.remove("error");

  const title = document.createElement("strong");
  title.textContent = "Download complete";
  const meta = document.createElement("p");
  meta.className = "result-note";
  meta.textContent = formatBytes(job.size_bytes || 0);

  const link = document.createElement("a");
  link.className = "save-link";
  link.href = api(job.file_url);
  link.setAttribute("download", "");
  link.textContent = "Save MP4 ↓";

  el.append(title, meta, link);
}

async function pollJob(id, el) {
  for (let i = 0; i < 240; i += 1) {
    await sleep(1500);
    const { ok, data: job } = await request(`/api/v1/download/${encodeURIComponent(id)}`);
    if (!ok) throw new Error(errorText(job, "Status check failed"));
    if (job.status === "completed") return renderComplete(el, job);
    if (job.status === "failed") throw new Error(job.error || "Download failed");
    renderProgress(el, job);
  }
  throw new Error("Timed out waiting for download.");
}

async function startXDownload(mediaIndex, variantIndex, btn) {
  const el = $("#xresult");
  const original = btn.textContent;
  btn.disabled = true;
  btn.textContent = "Starting…";
  try {
    const { ok, data } = await postJSON("/api/v1/download-x", {
      url: extractedUrl,
      media_index: mediaIndex,
      variant_index: variantIndex,
    });
    if (!ok) throw new Error(errorText(data, "Could not start download"));
    await pollJob(data.job_id, el);
  } catch (err) {
    message(el, err.message, true);
  } finally {
    btn.disabled = false;
    btn.textContent = original;
  }
}

async function withButton(btn, busyText, task) {
  const original = btn.innerHTML;
  btn.disabled = true;
  btn.textContent = busyText;
  try {
    await task();
  } finally {
    btn.disabled = false;
    btn.innerHTML = original;
  }
}

// Theme: dark by default, choice remembered for the next visit when storage is available.
function applyTheme(light) {
  document.body.classList.toggle("light", light);
  $("#theme").setAttribute("aria-pressed", String(light));
  try { localStorage.setItem("xeno-theme", light ? "light" : "dark"); } catch { /* storage blocked */ }
}
try { applyTheme(localStorage.getItem("xeno-theme") === "light"); } catch { applyTheme(false); }
$("#theme").addEventListener("click", () => applyTheme(!document.body.classList.contains("light")));

$("#paste").addEventListener("click", async () => {
  try {
    $("#xurl").value = await navigator.clipboard.readText();
  } catch {
    message($("#xresult"), "Clipboard unavailable. Paste the URL manually.", true);
  }
});

$("#xurl").addEventListener("input", () => {
  if ($("#xurl").value.trim() !== extractedUrl) {
    extractedUrl = "";
    $("#xresult").replaceChildren();
    $("#xresult").style.display = "none";
  }
});

$("#xform").addEventListener("submit", async (e) => {
  e.preventDefault();
  const url = $("#xurl").value.trim();
  await withButton(e.submitter, "Checking…", async () => {
    try {
      const { ok, data } = await postJSON("/api/v1/validate", { url });
      if (!ok) throw new Error(errorText(data, "Link check failed"));
      message($("#xresult"), "Valid X post URL. Tap ‘Get video & qualities’ to check downloadable MP4 variants.");
    } catch (err) {
      message($("#xresult"), err.message, true);
    }
  });
});

$("#extract").addEventListener("click", async (e) => {
  const url = $("#xurl").value.trim();
  if (!url) {
    message($("#xresult"), "Paste an X post URL first.", true);
    return;
  }
  await withButton(e.currentTarget, "Finding video…", async () => {
    try {
      const { ok, data } = await postJSON("/api/v1/extract", { url });
      if (!ok) throw new Error(errorText(data, "Video extraction failed"));
      extractedUrl = url;
      renderQualities(data);
    } catch (err) {
      extractedUrl = "";
      message($("#xresult"), err.message, true);
    }
  });
});

$("#dform").addEventListener("submit", async (e) => {
  e.preventDefault();
  await withButton(e.submitter, "Starting…", async () => {
    const el = $("#dresult");
    try {
      const { ok, data } = await postJSON("/api/v1/download-direct", {
        media_url: $("#mediaurl").value.trim(),
        filename: $("#filename").value.trim() || "video.mp4",
      });
      if (!ok) throw new Error(errorText(data, "Could not start download"));
      message(el, `Download queued: ${data.job_id}`);
      await pollJob(data.job_id, el);
    } catch (err) {
      message(el, err.message, true);
    }
  });
});

function loadHealth() {
  const out = $("#health");
  if (BACKEND_MISSING) {
    out.textContent = BACKEND_HINT;
    return;
  }
  request("/api/v1/health")
    .then(({ ok, data }) => {
      out.textContent = ok ? JSON.stringify(data, null, 2) : "API unavailable";
    })
    .catch((err) => {
      out.textContent = err.message;
    });
}
loadHealth();
