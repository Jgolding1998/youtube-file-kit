// Headless browser check of the real UI against the fake yt-dlp transport.
// Env: PYTHON (interpreter with deps), PLAYWRIGHT_MODULE (path to playwright), CHROME_PATH, OUT_DIR (screenshots).
"use strict";
const { spawn } = require("child_process");
const path = require("path");
const fs = require("fs");
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");

const PORT = Number(process.env.YFK_BROWSER_PORT || 18765);
if (!Number.isInteger(PORT) || PORT < 1024 || PORT > 65535) throw new Error("Invalid local test port");
const BASE = `http://127.0.0.1:${PORT}`;
const OUT = process.env.OUT_DIR || path.join(require("os").tmpdir(), "yfk-browser");
fs.mkdirSync(OUT, { recursive: true });

function check(cond, msg) {
  if (!cond) throw new Error("FAILED: " + msg);
  console.log("ok -", msg);
}

async function waitForServer(server) {
  for (let i = 0; i < 100; i++) {
    if (server.exitCode !== null) throw new Error("local test server exited before startup");
    try { if ((await fetch(BASE + "/")).ok) return; } catch (e) { /* not up yet */ }
    await new Promise((r) => setTimeout(r, 200));
  }
  throw new Error("server did not start");
}

(async () => {
  const server = spawn(process.env.PYTHON || "python", [path.join(__dirname, "fake_server.py"), String(PORT), path.join(OUT, "runtime")],
    { stdio: ["pipe", "inherit", "inherit"], windowsHide: true });
  let browser;
  try {
    await waitForServer(server);
    browser = await chromium.launch({ executablePath: process.env.CHROME_PATH, headless: true,
      args: ["--mute-audio", "--no-first-run", "--disable-background-networking", "--disable-sync"] });
    const context = await browser.newContext({ acceptDownloads: true, serviceWorkers: "block" });
    const blocked = [];
    await context.route("**/*", (route) => {
      const url = route.request().url();
      if (url.startsWith(BASE + "/")) return route.continue();
      blocked.push(url);
      return route.abort();
    });
    const page = await context.newPage();
    const errors = [];
    // Expected 4xx replies make Chrome log "Failed to load resource"; only script errors count.
  page.on("console", (m) => { if (m.type() === "error" && !m.text().startsWith("Failed to load resource")) errors.push(m.text()); });
    page.on("pageerror", (e) => errors.push(String(e)));

    await page.goto(BASE + "/");
    check(await page.title() === "YouTube File Kit", "page loads");
    check(await page.locator("#go").textContent() === "Download", "single Download button");
    check(await page.locator("input").count() === 1, "only one input field");
    check(await page.locator("img, iframe, video").count() === 0, "no remote images or embeds");
    await page.screenshot({ path: path.join(OUT, "youtube-desktop.png"), fullPage: true });

    await page.fill("#url", "https://www.youtube.com/playlist?list=PL123");
    await page.click("#go");
    await page.waitForSelector(".status.bad");
    check((await page.textContent("#status-text")).includes("Playlists"), "playlist link rejected with plain message");

    await page.fill("#url", "https://youtu.be/okokokokok1?si=share");
    const [download] = await Promise.all([page.waitForEvent("download"), page.click("#go")]);
    check(download.suggestedFilename() === "Test video.mp4", "download saved with safe title name");
    const saved = path.join(OUT, "download.mp4");
    await download.saveAs(saved);
    check(fs.statSync(saved).size === 4012, "downloaded bytes match");
    await page.waitForSelector(".status.ok");
    check(await page.isVisible("#again"), "Save again link shown");

    await page.fill("#url", "https://www.youtube.com/watch?v=privatevid1");
    await page.click("#go");
    await page.waitForSelector(".status.bad");
    const msg = await page.textContent("#status-text");
    check(msg === "This video is private.", "private video reported plainly");

    await page.fill("#url", "https://www.youtube.com/watch?v=agelimited1");
    await page.click("#go");
    await page.waitForSelector(".status.bad");
    check((await page.textContent("#status-text")).includes("age-restricted"), "age-restricted reported, no bypass");

    await page.fill("#url", "https://youtu.be/sleepy00001");
    await page.click("#go");
    await page.waitForSelector("#cancel:not([hidden])");
    await page.click("#cancel");
    await page.waitForFunction(() => document.getElementById("status-text").textContent.includes("cancelled"));
    check(true, "cancel works");

    await page.setViewportSize({ width: 390, height: 760 });
    check(!(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)), "mobile layout fits the viewport");
    await page.screenshot({ path: path.join(OUT, "youtube-mobile.png"), fullPage: true });
    check(errors.length === 0, "no console errors " + JSON.stringify(errors));
    check(blocked.length === 0, "no external requests " + JSON.stringify(blocked));
    console.log("ALL BROWSER CHECKS PASSED");
  } finally {
    if (browser) await browser.close();
    await new Promise((resolve, reject) => {
      if (server.exitCode !== null) return resolve();
      const timer = setTimeout(() => {server.kill(); reject(new Error("local test server did not shut down"));}, 10000);
      server.once("exit", () => {clearTimeout(timer); resolve();});
      server.stdin.on("error", () => {});
      server.stdin.end("stop\n");
    });
    check(!fs.existsSync(path.join(OUT, "runtime")), "test server and runtime shut down cleanly");
  }
})().catch((e) => { console.error(e); process.exit(1); });
