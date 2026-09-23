// Phase 3 UI render check — drives the real admin UI in headless Chromium and asserts the
// Critic rubric objectively (dark mode, 375px responsiveness, no console errors, design
// tokens in use, drawer behaviour) plus the polish regressions found during Phase 3.
//
// This is a dev-time verification tool. It is NOT part of the app and is never copied into
// the container image (the Dockerfile only copies app/ and config/).
//
// Usage:
//   # terminal 1
//   python3 tools/ui-render-check/stub_server.py
//   # terminal 2 (needs `npm i playwright` once, from this directory)
//   node tools/ui-render-check/verify.mjs
//
// Env overrides:
//   AR_BASE_URL                 default http://127.0.0.1:8765/
//   AR_SHOTS_DIR                default ./shots next to this file
//   AGENT_BROWSER_EXECUTABLE_PATH  explicit Chromium/chrome-headless-shell binary
//
// Exit code is non-zero if any check fails, so it can gate CI.
import { chromium } from "playwright";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const BASE = process.env.AR_BASE_URL || "http://127.0.0.1:8765/";
const OUT = process.env.AR_SHOTS_DIR || path.join(HERE, "shots");
fs.mkdirSync(OUT, { recursive: true });

const report = { base: BASE, checks: [], consoleErrors: [], pageErrors: [], requestFailures: [] };
const ok = (name, pass, detail = "") => {
  report.checks.push({ name, pass: !!pass, detail });
  console.log(`${pass ? "PASS" : "FAIL"}  ${name}${detail ? "  — " + detail : ""}`);
};

const browser = await chromium.launch(
  process.env.AGENT_BROWSER_EXECUTABLE_PATH ? { executablePath: process.env.AGENT_BROWSER_EXECUTABLE_PATH } : {},
);

function wire(page) {
  page.on("console", (m) => { if (m.type() === "error") report.consoleErrors.push(m.text()); });
  page.on("pageerror", (e) => report.pageErrors.push(String(e.message || e)));
  page.on("requestfailed", (r) => report.requestFailures.push(r.url() + " :: " + (r.failure()?.errorText || "")));
}

// ---------- desktop ----------
const desktop = await browser.newContext({ viewport: { width: 1280, height: 900 }, colorScheme: "light" });
const page = await desktop.newPage();
wire(page);
await page.goto(BASE, { waitUntil: "networkidle" });

const themeBoot = await page.evaluate(() => document.documentElement.dataset.theme);
ok("theme initialises (boot script)", themeBoot === "light" || themeBoot === "dark", `data-theme=${themeBoot}`);

const font = await page.evaluate(() => getComputedStyle(document.body).fontFamily);
ok("Inter applied from tokens", /Inter/i.test(font), font);

const primaryBtn = await page.evaluate(() => {
  const b = document.querySelector(".btn-primary");
  return b ? getComputedStyle(b).backgroundColor : null;
});
ok("brand primary token in use on primary button", primaryBtn === "rgb(1, 97, 239)", String(primaryBtn));

ok("health indicator rendered", (await page.locator("#decision-health").innerText()).includes("Decision layer"));
ok("logo asset loaded", await page.evaluate(() => {
  const img = document.querySelector(".brand-logo");
  return !!img && img.complete && img.naturalWidth > 0;
}));

await page.screenshot({ path: `${OUT}/desktop-light.png`, fullPage: true });

// dark mode
await page.click("#theme-toggle");
await page.waitForTimeout(400); // let the 160ms background transition settle
const darkTheme = await page.evaluate(() => document.documentElement.dataset.theme);
const darkToken = await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue("--bg-page").trim());
const darkBg = await page.evaluate(() => getComputedStyle(document.body).backgroundColor);
const persisted = await page.evaluate(() => localStorage.getItem("ar-theme"));
ok("dark mode toggles", darkTheme === "dark", `data-theme=${darkTheme}`);
ok("dark mode swaps the --bg-page token", darkToken === "#030620", darkToken);
ok("dark mode actually repaints background", darkBg === "rgb(3, 6, 32)", darkBg);
ok("theme choice persists", persisted === "dark", String(persisted));
await page.screenshot({ path: `${OUT}/desktop-dark.png`, fullPage: true });
await page.click("#theme-toggle"); // back to light
ok("toggles back to light", (await page.evaluate(() => document.documentElement.dataset.theme)) === "light");

// every tab renders
for (const [tab, marker] of [
  ["log", "#log-table tbody tr"],
  ["spend", "#spend-periods tbody tr"],
  ["models", "#models-table tbody tr"],
  ["tiers", "#tiers-editor .tier-model"],
  ["keys", "#keys-table tbody tr"],
  ["settings", "#settings-preset"],
]) {
  await page.click(`.nav-link[data-tab="${tab}"]`);
  const visible = await page.locator(`#tab-${tab}`).isVisible();
  // content is fetched async — wait for it rather than counting immediately
  try { await page.locator(marker).first().waitFor({ state: "visible", timeout: 5000 }); } catch { /* counted below */ }
  const count = await page.locator(marker).count();
  ok(`tab "${tab}" renders content`, visible && count > 0, `items=${count}`);
}
await page.click('.nav-link[data-tab="log"]');

// transparency: badges + expandable detail
const badges = (await page.locator("#log-table .badge").allInnerTexts()).map((b) => b.toUpperCase());
ok("routing transparency badges present", badges.includes("FALLBACK") && badges.includes("ESCALATED") && badges.includes("NORMAL"), badges.join(","));
await page.locator("#log-table button:has-text('details')").first().click();
ok("per-request detail expands", await page.locator("#log-table tr.row-detail").first().isVisible());
await page.screenshot({ path: `${OUT}/desktop-log-detail.png`, fullPage: true });

// polish regressions flagged by the visual review of the first render
ok("badges render in one consistent case", await page.evaluate(
  () => getComputedStyle(document.querySelector("#log-table .badge")).textTransform) === "uppercase");
ok("API key placeholder is not truncated", await page.evaluate(() => {
  const inp = document.querySelector("#router-key");
  const cs = getComputedStyle(inp);
  const ctx = document.createElement("canvas").getContext("2d");
  ctx.font = `${cs.fontWeight} ${cs.fontSize} ${cs.fontFamily}`;
  const textW = ctx.measureText(inp.placeholder).width;
  const avail = inp.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
  return textW <= avail;
}));
ok("theme toggle label states the action", await page.evaluate(
  () => document.querySelector("#theme-toggle").getAttribute("aria-label")) === "Switch to dark mode");

// ---------- mobile 375px ----------
const mobile = await browser.newContext({ viewport: { width: 375, height: 812 }, colorScheme: "light" });
const mp = await mobile.newPage();
wire(mp);
await mp.goto(BASE, { waitUntil: "networkidle" });

const overflow = await mp.evaluate(() => ({
  scrollW: document.documentElement.scrollWidth,
  innerW: window.innerWidth,
  bodyScrollW: document.body.scrollWidth,
}));
ok("no horizontal scroll at 375px", overflow.scrollW <= overflow.innerW + 1 && overflow.bodyScrollW <= overflow.innerW + 1,
   JSON.stringify(overflow));

const strayWide = await mp.evaluate(() => {
  const bad = [];
  for (const el of document.querySelectorAll("body *")) {
    const r = el.getBoundingClientRect();
    if (r.width > window.innerWidth + 1 && getComputedStyle(el).position !== "fixed") {
      bad.push(`${el.tagName.toLowerCase()}.${el.className || "?"}=${Math.round(r.width)}`);
    }
  }
  return bad.slice(0, 5);
});
ok("no element wider than the viewport", strayWide.length === 0, strayWide.join(", "));

ok("viewport really is 375px", (await mp.evaluate(() => window.innerWidth)) === 375, String(await mp.evaluate(() => window.innerWidth)));
ok("mobile nav hidden behind toggle", !(await mp.locator("#primary-nav").isVisible()));
const rowDisplay = await mp.evaluate(() => {
  const tr = document.querySelector("#log-table tbody tr");
  return tr ? getComputedStyle(tr).display : "missing";
});
ok("tables collapsed to stacked cards", rowDisplay === "block", `tbody tr display=${rowDisplay}`);
const labelVisible = await mp.evaluate(() => {
  const td = document.querySelector("#log-table tbody td[data-label]");
  return td ? getComputedStyle(td, "::before").content : "none";
});
ok("card layout shows column labels", labelVisible && labelVisible !== "none", String(labelVisible));

await mp.screenshot({ path: `${OUT}/mobile-light.png`, fullPage: true });

// drawer open / close (mouse)
await mp.click("#nav-toggle");
await mp.waitForTimeout(350); // let the slide-in transition finish
ok("drawer opens", await mp.locator("#primary-nav.is-open").isVisible());
ok("backdrop shown", await mp.locator("#drawer-backdrop.is-open").isVisible());
ok("aria-expanded set", (await mp.getAttribute("#nav-toggle", "aria-expanded")) === "true");
// regression guard: the drawer must be the topmost thing under the cursor (an earlier
// stacking-context bug put the backdrop on top and made every drawer link unclickable)
ok("drawer links are hit-testable while open", await mp.evaluate(() => {
  const link = document.querySelector("#primary-nav .nav-link");
  const r = link.getBoundingClientRect();
  const hit = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
  return link === hit || link.contains(hit);
}));
await mp.screenshot({ path: `${OUT}/mobile-drawer.png` });

await mp.keyboard.press("Escape");
ok("Escape closes drawer", !(await mp.locator("#primary-nav.is-open").isVisible()));

// keyboard path — focus must land inside the drawer. Checked via Enter rather than a mouse
// click: playwright's synthesized click re-focuses the toggle button after dispatch, which
// masks the app's own focus handling (verified separately for click() and keyboard paths).
await mp.evaluate(() => document.querySelector("#nav-toggle").focus());
await mp.keyboard.press("Enter");
await mp.waitForTimeout(150);
ok("keyboard opens drawer", await mp.locator("#primary-nav.is-open").isVisible());
ok("focus moves into drawer", await mp.evaluate(() => document.querySelector("#primary-nav").contains(document.activeElement)));

// picking a tab from the drawer closes it and switches content
await mp.click('.nav-link[data-tab="spend"]');
ok("drawer closes after picking a tab", !(await mp.locator("#primary-nav.is-open").isVisible()));
ok("selected tab switched", await mp.locator("#tab-spend").isVisible());
ok("active nav state tracks tab", (await mp.locator('.nav-link[data-tab="spend"].is-active').count()) === 1);

// mobile dark
await mp.click("#theme-toggle");
ok("dark mode on mobile", (await mp.evaluate(() => document.documentElement.dataset.theme)) === "dark");
await mp.click("#nav-toggle"); // nav lives in the drawer on mobile — open it first
await mp.click('.nav-link[data-tab="log"]');
await mp.screenshot({ path: `${OUT}/mobile-dark.png`, fullPage: true });

// ---------- verdict ----------
ok("no console errors", report.consoleErrors.length === 0, report.consoleErrors.join(" | "));
ok("no uncaught page errors", report.pageErrors.length === 0, report.pageErrors.join(" | "));
ok("no failed requests", report.requestFailures.length === 0, report.requestFailures.join(" | "));

await browser.close();
const failed = report.checks.filter((c) => !c.pass);
fs.writeFileSync(`${OUT}/report.json`, JSON.stringify(report, null, 2));
console.log(`\n${report.checks.length - failed.length}/${report.checks.length} checks passed`);
if (failed.length) { console.log("FAILURES:\n" + failed.map((f) => " - " + f.name + " :: " + f.detail).join("\n")); process.exit(1); }
