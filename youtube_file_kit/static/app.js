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
    return { ok: res.ok, data };
  }

  function finish() {
    current = null;
    go.disabled = false;
    cancelBtn.hidden = true;
  }

  async function poll(id) {
    while (current === id) {
      let data;
      try {
        const res = await fetch("/api/jobs/" + id, { credentials: "same-origin" });
        data = await res.json();
        if (!res.ok) { show(data.message || "Download not found.", "bad"); return finish(); }
      } catch (e) {
        show("Lost contact with the app. Is it still running?", "bad");
        return finish();
      }
      if (data.state === "queued") show("Waiting for a free slot...");
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
    if (current) return;
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
    cancelBtn.hidden = false;
    poll(current);
  });

  cancelBtn.addEventListener("click", async () => {
    if (!current) return;
    const id = current;
    cancelBtn.hidden = true;
    show("Cancelling...");
    try { await post("/api/jobs/" + id + "/cancel"); } catch (e) { /* poll reports state */ }
  });
})();
