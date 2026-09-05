/* 真实端到端：打开真页面、真敲字、真跑一轮模型，中间的确认按钮也真点。
 *
 * 跟 `shoot.mjs` 的分工：那个灌编好的事件流，专门覆盖分支和量网格，不花钱；
 * 这个打真接口，验的是「WebSocket ↔ stdio 协议 ↔ agent」整条链真的通。
 * **改完协议两个都要跑**：灌事件流跑得通不代表真 agent 那半边也通。
 *
 * 用法：node dev/live.mjs <页面地址> <输出目录> <要它做的事>
 */

import { spawn } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import { setTimeout as sleep } from "node:timers/promises";

const url = process.argv[2] || "http://127.0.0.1:7791/";
const outDir = process.argv[3] || "/tmp/feiyu-web-live";
const task = process.argv[4] || "在 a.py 末尾加一个 add(a, b) 函数";
const PORT = 9335;
mkdirSync(outDir, { recursive: true });

const chrome = spawn("google-chrome-stable", [
  "--headless=new", `--remote-debugging-port=${PORT}`, "--remote-allow-origins=*",
  "--no-first-run", "--disable-gpu", "--hide-scrollbars",
  `--user-data-dir=${outDir}/chrome-profile`, "--window-size=1280,1400", "about:blank",
], { stdio: "ignore" });

let page;
for (let i = 0; i < 80 && !page; i++) {
  try {
    page = (await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json())
      .find((t) => t.type === "page");
  } catch {}
  if (!page) await sleep(250);
}
const ws = new WebSocket(page.webSocketDebuggerUrl);
await new Promise((ok) => (ws.onopen = ok));
let id = 0;
const pending = new Map();
ws.addEventListener("message", (e) => {
  const m = JSON.parse(e.data);
  if (pending.has(m.id)) { pending.get(m.id)(m.result); pending.delete(m.id); }
});
const call = (method, params = {}) =>
  new Promise((ok) => { pending.set(++id, ok); ws.send(JSON.stringify({ id, method, params })); });
const ev = async (expr) =>
  (await call("Runtime.evaluate", { expression: expr, returnByValue: true })).result?.value;
async function shot(name) {
  const { data } = await call("Page.captureScreenshot", { format: "png", captureBeyondViewport: true });
  writeFileSync(`${outDir}/${name}.png`, Buffer.from(data, "base64"));
  console.log("拍了", `${outDir}/${name}.png`);
}

await call("Page.enable");
await call("Runtime.enable");
await call("Emulation.setDeviceMetricsOverride",
  { width: 1280, height: 1400, deviceScaleFactor: 2, mobile: false });
await call("Page.navigate", { url });
await sleep(2000);

// 真的往输入框里放字，再走真的提交路径 —— 直接调 send() 就绕过了输入那一段
await ev(`input.value = ${JSON.stringify(task)}; input.dispatchEvent(new Event("input")); true`);
await ev(`document.getElementById("send").click(); true`);
console.log("已提交：", task);

// 等它跑。中途冒出确认卡就点「同意一次」—— 这一步是这个脚本的重点
let approved = 0;
for (let i = 0; i < 150; i++) {
  await sleep(2000);
  const asked = await ev(`(() => {
    const card = [...document.querySelectorAll(".ask")].find((c) => !c.classList.contains("ask--done"));
    if (!card) return 0;
    const yes = [...card.querySelectorAll("button")].find((b) => b.textContent.includes("同意"));
    if (yes) { yes.click(); return 1; }
    return 0;
  })()`);
  if (asked) { approved++; console.log("点了同意（第", approved, "次）"); }
  const done = await ev(`document.getElementById("stop").hidden === true &&
    document.querySelectorAll(".block").length > 1`);
  if (done && i > 2) break;
}

await sleep(1500);
await shot("live");
const summary = await ev(`JSON.stringify({
  blocks: document.querySelectorAll(".block").length,
  tools: document.querySelectorAll(".tool").length,
  asks: document.querySelectorAll(".ask").length,
  state: document.getElementById("state").textContent,
  cost: document.getElementById("m-cost").textContent,
  errs: window.__errs || [],
})`);
console.log("页面上：", summary);
ws.close();
chrome.kill();
