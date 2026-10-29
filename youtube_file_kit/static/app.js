"use strict";
(() => {
  const token = document.querySelector('meta[name="csrf-token"]').content;
  const form = document.getElementById("form");
  const input = document.getElementById("url");
  const go = document.getElementById("go");
  const box = document.getElementById("status");
  const text = document.getElementById("status-text");
  const cancelBtn = document.getElementById("cancel");
  const again = document.getElementById("again");
  let current = null;
  let cancelling = null;

  function show(message, kind) {
    box.hidden = false;
    box.className = "status" + (kind ? " " + kind : "");
    text.textContent = message;
  }

  function size(bytes) {
    if (bytes < 1024 * 1024) return Math.max(1, Math.round(bytes / 1024)) + " KB";
    return (bytes / (1024 * 1024)).toFixed(1) + " MB";
  }

  async function post(path, body) {
    const res = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": token },
      body: JSON.stringify(body || {}),
      credentials: "same-origin",
    });
    let data = {};
    try { data = await res.json(); } catch (e) { /* non-JSON error */ }
    return { ok: res.ok, status: res.status, data };
  }

  function finish() {
    current = null;
    cancelling = null;
    go.disabled = false;
    cancelBtn.disabled = false;
    cancelBtn.hidden = true;
  }

  async function poll(id) {
    let failures = 0;
    while (current === id) {
      let data;
      try {
        const res = await fetch("/api/jobs/" + id, { credentials: "same-origin" });
        try { data = await res.json(); } catch (e) { if (res.ok) throw e; data = {}; }
        if (current !== id) return;
        if (res.status >= 500) throw new Error("Temporary status failure");
        if (!res.ok) { show(data.message || "Download not found.", "bad"); return finish(); }
        failures = 0;
      } catch (e) {
        if (current !== id) return;
        if (++failures >= 3) {
          show("Lost contact with the app. Is it still running?", "bad");
          return finish();
        }
        show("Contact interrupted. Retrying...");
        await new Promise((r) => setTimeout(r, 1000));
        continue;
      }
      if (cancelling === id && (data.state === "queued" || data.state === "running")) show("Cancelling...");
      else if (data.state === "queued") show("Waiting for a free slot...");
      else if (data.state === "running") show(data.bytes ? "Downloading... " + size(data.bytes) : "Contacting YouTube...");
      else if (data.state === "done") {
        const href = "/api/jobs/" + id + "/file";
        again.href = href;
        again.hidden = false;
        show("Done: " + data.filename + " (" + size(data.bytes) + "). Your browser is saving it.", "ok");
        const a = document.createElement("a");
        a.href = href;
        a.download = "";
        document.body.appendChild(a);
        a.click();
        a.remove();
        return finish();
      } else {
        show(data.message || "The download failed.", "bad");
        return finish();
      }
      await new Promise((r) => setTimeout(r, 1000));
    }
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (current || go.disabled) return;
    const url = input.value.trim();
    if (!url) return;
    go.disabled = true;
    again.hidden = true;
    show("Checking link...");
    let result;
    try {
      result = await post("/api/jobs", { url });
    } catch (e) {
      show("Lost contact with the app. Is it still running?", "bad");
      return finish();
    }
    if (!result.ok) {
      show(result.data.message || "Something went wrong.", "bad");
      return finish();
    }
    current = result.data.id;
    cancelBtn.disabled = false;
    cancelBtn.hidden = false;
    poll(current);
  });

  cancelBtn.addEventListener("click", async () => {
    if (!current || cancelBtn.disabled) return;
    const id = current;
    cancelBtn.disabled = true;
    cancelling = id;
    show("Cancelling...");
    try {
      const result = await post("/api/jobs/" + id + "/cancel");
      if (current !== id) return;
      if (result.ok || result.status === 404) cancelBtn.hidden = true;
      else {
        cancelling = null;
        cancelBtn.disabled = false;
        show(result.data.message || "Could not cancel. Press Cancel to retry.", "bad");
      }
    } catch (e) {
      if (current !== id) return;
      cancelling = null;
      cancelBtn.disabled = false;
      show("Could not cancel. Press Cancel to retry.", "bad");
    }
  });
})();
