/* 侧边栏和上传的**真实**回放：真模型、真存盘、真文件。
 *
 * `input.mjs` 灌的是事件，验的是按键和 DOM；这个验的是「Python 那边真的存了两份、
 * 切回去真的能把历史捞回来、上传的文件 agent 真的读得到」。要花钱。
 *
 * 用法：node dev/sessions.mjs <页面地址> <输出目录>
 */

import { spawn } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import { setTimeout as sleep } from "node:timers/promises";

const url = process.argv[2];
const outDir = process.argv[3] || "/tmp/feiyu-sessions";
const PORT = 9342;
mkdirSync(outDir, { recursive: true });

const chrome = spawn("google-chrome-stable", [
  "--headless=new", `--remote-debugging-port=${PORT}`, "--remote-allow-origins=*",
  "--no-first-run", "--disable-gpu", "--hide-scrollbars",
  `--user-data-dir=${outDir}/profile`, "--window-size=1400,1000", "about:blank",
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
const pend = new Map();
ws.addEventListener("message", (e) => {
  const m = JSON.parse(e.data);
  if (pend.has(m.id)) { pend.get(m.id)(m.result); pend.delete(m.id); }
});
const call = (method, params = {}) =>
  new Promise((ok) => { pend.set(++id, ok); ws.send(JSON.stringify({ id, method, params })); });
const ev = async (x) =>
  (await call("Runtime.evaluate", { expression: x, awaitPromise: true, returnByValue: true }))
    .result?.value;

const cases = [];
const check = (name, ok, detail) => {
  cases.push(ok);
  console.log((ok ? "  OK " : "  !! ") + name + (ok ? "" : "  → " + JSON.stringify(detail)));
};

const busy = () => ev(`document.getElementById("stop").hidden === false`);

async function ask(text) {
  await ev(`input.value = ${JSON.stringify(text)};
            input.dispatchEvent(new Event("input"));
            document.getElementById("send").click(); true`);
  // **先等它真的忙起来，再等它闲下来。**
  // 只等「不忙」是错的：刚点下去的那一刻本来就是不忙的，第一次轮询就返回了，
  // 后面的断言全跑在对话开始之前 —— 第一版就是这么把自己骗了一轮。
  for (let i = 0; i < 40 && !(await busy()); i++) await sleep(500);
  for (let i = 0; i < 120 && (await busy()); i++) await sleep(2000);
  await sleep(800);         // 收尾那几条事件（turn_end / status / sessions）画完
}

const talks = async () => JSON.parse(await ev(`JSON.stringify(
  [...document.querySelectorAll(".talk")].map(t => ({
    title: t.querySelector(".talk__title").textContent,
    current: t.getAttribute("aria-current") === "true",
  })))`));

await call("Page.enable");
await call("Runtime.enable");
await call("Emulation.setDeviceMetricsOverride",
  { width: 1400, height: 1000, deviceScaleFactor: 2, mobile: false });
await call("Page.navigate", { url });
await sleep(2500);
await ev(`document.body.dataset.side = "on"; true`);

// 第一段对话
await ask("记住一个词：菠萝。只回一个「好」。");
await sleep(1200);
let list = await talks();
check("第一段对话进了侧边栏", list.length === 1 && list[0].current, list);

// 开第二段
await ev(`document.getElementById("new-talk").click(); true`);
await sleep(1500);
check("新对话把正文清空了",
      (await ev(`document.querySelectorAll(".block").length`)) === 0, null);
await ask("记住一个词：西瓜。只回一个「好」。");
await sleep(1200);
list = await talks();
check("现在有两段，新的在最前", list.length === 2 && list[0].current, list);

// 切回第一段，历史要真的回来
await ev(`const rows = [...document.querySelectorAll(".talk")];
  if (rows[1]) rows[1].click(); true`);
await sleep(2500);
const restored = await ev(`document.getElementById("stream").textContent`);
check("切回去把上一段的历史捞回来了", restored.includes("菠萝"), restored.slice(0, 120));
list = await talks();
check("当前那条跟着换", list.length === 2 && list[1].current, list);

// agent 那边的上下文也真的换过去了（不是只换了画面）
await ask("我刚让你记的那个词是什么？只回那两个字。");
const answer = await ev(`document.getElementById("stream").textContent`);
check("agent 的上下文也切过去了（答菠萝不是西瓜）",
      answer.trimEnd().endsWith("菠萝") || /菠萝/.test(answer.slice(-40)),
      answer.slice(-80));

// 上传一个文件，让它读
await ev(`(async () => {
  const body = new Blob(["这个文件里的暗号是 蓝鲸七号"], { type: "text/plain" });
  const file = new File([body], "暗号.txt", { type: "text/plain" });
  await upload(file);
})()`);
await sleep(1500);
const clip = await ev(`JSON.stringify(clips)`);
check("上传完了，附件条上有它", clip.includes(".feiyu/uploads/"), clip);
await ask("读一下我上传的那个文件，把里面的暗号原样念出来。");
const read = await ev(`document.getElementById("stream").textContent`);
check("agent 真的读到了上传的文件", read.includes("蓝鲸七号"), read.slice(-160));

await sleep(600);
const { data } = await call("Page.captureScreenshot", { format: "png" });
writeFileSync(`${outDir}/sidebar.png`, Buffer.from(data, "base64"));
console.log("拍了", `${outDir}/sidebar.png`);
console.log("花费:", await ev(`document.getElementById("m-cost").textContent`));
const bad = cases.filter((x) => !x).length;
console.log(`${cases.length - bad}/${cases.length} 通过`);
ws.close();
chrome.kill();
process.exitCode = bad ? 1 : 0;
