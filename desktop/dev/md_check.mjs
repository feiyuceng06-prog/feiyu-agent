/* md() 的判据：喂几种真实会出现的 markdown 片段，看它**会不会转不出来**。
 *
 * 为什么要单独一个夹具：`md()` 是手写的逐行解析器，外层 `while (i < lines.length)`
 * 靠每个分支自己推进 `i`。有一个分支不推进，整个渲染线程就原地转圈往数组里塞
 * 元素 —— 表现不是"画错了"而是**窗口彻底卡死**（实测 70 秒吃掉 16 G 内存，
 * CPU 100%）。这种失败方式 `fuzz_ui.mjs` 抓不到：它灌的是畸形**事件**，
 * 而这里坏的是合法事件里的一段**合法 markdown**。
 *
 * 关键是 `Runtime.evaluate` 的 `timeout` —— 它能打断一个同步死循环（V8 的
 * TerminateExecution），所以死循环在这儿是"超时"而不是"夹具自己也挂住"。
 *
 * 用法：node dev/md_check.mjs <页面地址>
 */

import { spawn } from "node:child_process";
import { setTimeout as sleep } from "node:timers/promises";

const url = process.argv[2];
if (!url) {
  console.error("用法：node dev/md_check.mjs <页面地址>");
  process.exit(2);
}
const PORT = 9347;
const chrome = spawn("google-chrome-stable", [
  "--headless=new", `--remote-debugging-port=${PORT}`, "--remote-allow-origins=*",
  "--no-first-run", "--disable-gpu", "--user-data-dir=/tmp/feiyu-mdcheck-profile2",
  "--window-size=1280,900", "about:blank",
], { stdio: "ignore" });

let page;
for (let i = 0; i < 80 && !page; i++) {
  try {
    page = (await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json())
      .find((t) => t.type === "page");
  } catch {}
  if (!page) await sleep(250);
}
if (!page) { console.error("Chrome 没起来"); chrome.kill(); process.exit(2); }

const ws = new WebSocket(page.webSocketDebuggerUrl);
await new Promise((ok) => (ws.onopen = ok));
let id = 0;
const pend = new Map();
ws.addEventListener("message", (e) => {
  const m = JSON.parse(e.data);
  // **`error` 也要收。** 死循环被 `timeout` 打断时 CDP 回的是 `{error}` 而不是
  // `{result}`，只取 result 的话夹具自己在 `out.exceptionDetails` 上崩掉 ——
  // 那正是它该报"转不出来"的那一刻，判据反而废了（第一版就是这样）。
  if (pend.has(m.id)) { pend.get(m.id)(m); pend.delete(m.id); }
});
const call = (method, params = {}) =>
  new Promise((ok) => { pend.set(++id, ok); ws.send(JSON.stringify({ id, method, params })); });

await call("Page.enable");
await call("Runtime.enable");
await call("Page.navigate", { url });
await sleep(2500);

/* 挑的都是真实会出现的形状，不是随机字符串：
   前四条在修之前**每一条都会让页面转不出来**。 */
const CASES = [
  ["没有分隔行的表格行", "| a | b |"],
  ["表格头落地、分隔行还没到（流式中间态）", "看下表：\n| 编号 | 结论 |"],
  ["四个连字符", "----"],
  ["`---` 后面还有字", "--- 分隔"],
  ["只有一个竖线", "|"],
  ["竖线开头的普通句子", "|这不是表格"],
  ["正常表格", "| a | b |\n|---|---|\n| 1 | 2 |"],
  ["三连字符是分隔线", "上\n---\n下"],
  ["围栏代码块没闭合", "```py\nprint(1)"],
  ["引用里套表格行", "> | a | b |"],
  ["列表项里有竖线", "- a | b"],
  ["普通段落", "就是一句话。"],
];

let bad = 0;
for (const [name, src] of CASES) {
  const m = await call("Runtime.evaluate", {
    expression: `md(${JSON.stringify(src)}).length`,
    returnByValue: true,
    timeout: 2000,
  });
  const stuck = !!m.error || !!m.result?.exceptionDetails;
  if (stuck) {
    console.log(`转不出来  ${name}   ${(m.error?.message || "").slice(0, 60)}`);
    bad++;
    // 被 terminate 过的上下文未必还能用，重新载一次再跑下一条
    await call("Page.navigate", { url });
    await sleep(2500);
  } else {
    console.log(`  ok      ${name}  →  ${m.result?.result?.value} 字 HTML`);
  }
}

ws.close();
chrome.kill();
if (bad) {
  console.error(`\n${bad} 条转不出来 —— md() 里有分支没推进 i`);
  process.exit(1);
}
console.log("\nOK 全部转得出来");
