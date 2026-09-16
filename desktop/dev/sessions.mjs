/* 侧边栏和上传的**真实**回放：真模型、真存盘、真文件。
 *
 * `input.mjs` 灌的是事件，验的是按键和 DOM；这个验的是「Python 那边真的存了两份、
 * 切回去真的能把历史捞回来、上传的文件 agent 真的读得到」。要花钱。
 *
 * 用法：node dev/sessions.mjs <页面地址> <输出目录>
 *
 * **devserver 必须起在一份干净的会话库上**，否则前三条断言必失败：
 * 它们数的是侧边栏里有几条对话（要 1、要 2），而 `session.DIR` 认
 * `XDG_STATE_HOME`，直接跑就会数到这个工作目录攒下的几十条历史会话。
 *
 *   XDG_STATE_HOME=$(mktemp -d) desktop/target/release/feiyu-devserver -p 7791
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

/* 冒出确认卡就点「同意一次」。
   **这条不能省**：default 模式下 agent 随时可能调 remember / write_file 这类要点头的
   工具，卡在那儿就永远不「闲下来」，后面所有断言都会因为 turn 没结束而挂 ——
   第一版就是这样，看着像会话存盘坏了，其实是它在等我点头。 */
const approve = () => ev(`(() => {
  const card = [...document.querySelectorAll(".ask")].find(c => !c.classList.contains("ask--done"));
  if (!card) return 0;
  const yes = [...card.querySelectorAll("button")].find(b => b.textContent.includes("同意"));
  if (yes) { yes.click(); return 1; }
  return 0;
})()`);

async function ask(text) {
  await ev(`input.value = ${JSON.stringify(text)};
            input.dispatchEvent(new Event("input"));
            document.getElementById("send").click(); true`);
  // **先等它真的忙起来，再等它闲下来。**
  // 只等「不忙」是错的：刚点下去的那一刻本来就是不忙的，第一次轮询就返回了，
  // 后面的断言全跑在对话开始之前 —— 第一版就是这么把自己骗了一轮。
  for (let i = 0; i < 40 && !(await busy()); i++) await sleep(500);
  for (let i = 0; i < 150 && (await busy()); i++) {
    await approve();
    await sleep(2000);
  }
  await sleep(1000);        // 收尾那几条事件（turn_end / status / sessions）画完
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
/* 把**收到**的事件也记下来。只看 DOM 的话，「列表为什么少一条」永远只能靠猜；
   记下时间线就能一眼看出是没发、还是发了没画。 */
await ev(`window.__in = [];
  (function () {
    const real = WebSocket.prototype.addEventListener;
    const sock = socket;
    sock.addEventListener("message", (e) => {
      const d = JSON.parse(e.data);
      window.__in.push([Date.now() % 100000, d.kind,
        d.kind === "sessions" ? d.rows.length + "行/current=" + d.current : ""]);
    });
  })(); true`);
await ev(`document.body.dataset.side = "on"; true`);

// 第一段对话
await ask("我说一个水果：菠萝。只回一个「好」，别用任何工具。");
await sleep(1200);
let list = await talks();
check("第一段对话进了侧边栏", list.length === 1 && list[0].current, list);

// 开第二段
await ev(`document.getElementById("new-talk").click(); true`);
await sleep(1500);
check("新对话把正文清空了",
      (await ev(`document.querySelectorAll(".block").length`)) === 0, null);
await ask("我说一个水果：西瓜。只回一个「好」，别用任何工具。");
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

// agent 那边的上下文也真的换过去了（不是只换了画面）。
// **只看最后一块「它说」**：正文里本来就有「菠萝」（是上面捞回来的历史），
// 拿整段正文去 includes 等于白测。
await ask("我上面说的那个水果是什么？只回那两个字，别用工具。");
const answer = await ev(`(() => {
  const said = [...document.querySelectorAll(".block")]
    .filter(b => b.querySelector(".block__label").textContent === "它说");
  return said.length ? said[said.length - 1].textContent : "";
})()`);
check("agent 的上下文也切过去了（答菠萝不是西瓜）",
      answer.includes("菠萝") && !answer.includes("西瓜"), answer.slice(0, 80));

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
const read = await ev(`(() => {
  const said = [...document.querySelectorAll(".block")]
    .filter(b => b.querySelector(".block__label").textContent === "它说");
  return said.length ? said[said.length - 1].textContent : "";
})()`);
check("agent 真的读到了上传的文件", read.includes("蓝鲸七号"), read.slice(0, 120));

await sleep(600);
const { data } = await call("Page.captureScreenshot", { format: "png" });
writeFileSync(`${outDir}/sidebar.png`, Buffer.from(data, "base64"));
console.log("拍了", `${outDir}/sidebar.png`);
console.log("花费:", await ev(`document.getElementById("m-cost").textContent`));
const bad = cases.filter((x) => !x).length;
if (bad) {
  const timeline = JSON.parse(await ev(`JSON.stringify(window.__in || [])`));
  console.log("\n收到的事件时间线（只列会话相关的）:");
  timeline.filter((r) => ["sessions", "cleared", "opened", "turn_start", "turn_end"].includes(r[1]))
    .forEach((r) => console.log("  ", r[0], r[1], r[2]));
}
console.log(`${cases.length - bad}/${cases.length} 通过`);
ws.close();
chrome.kill();
process.exitCode = bad ? 1 : 0;
