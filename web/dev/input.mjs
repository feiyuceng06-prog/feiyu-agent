/* 输入区和命令补全的按键回放。
 *
 * 跟 shoot.mjs 一样走 CDP，但敲的是**真按键**（Input.dispatchKeyEvent），
 * 不是直接改 input.value —— 补全的毛病全在 keydown 那一层，
 * 绕过按键就等于没测。
 *
 * 用法：node dev/input.mjs <页面地址>
 */

import { spawn } from "node:child_process";
import { setTimeout as sleep } from "node:timers/promises";

const url = process.argv[2];
const PORT = 9337;
const chrome = spawn("google-chrome-stable", [
  "--headless=new", `--remote-debugging-port=${PORT}`, "--remote-allow-origins=*",
  "--no-first-run", "--disable-gpu", "--user-data-dir=/tmp/feiyu-input-profile",
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
  (await call("Runtime.evaluate", { expression: x, returnByValue: true })).result?.value;

const KEYS = {
  Enter: { key: "Enter", code: "Enter", windowsVirtualKeyCode: 13, text: "\r" },
  Tab: { key: "Tab", code: "Tab", windowsVirtualKeyCode: 9 },
  Escape: { key: "Escape", code: "Escape", windowsVirtualKeyCode: 27 },
  ArrowDown: { key: "ArrowDown", code: "ArrowDown", windowsVirtualKeyCode: 40 },
  ArrowUp: { key: "ArrowUp", code: "ArrowUp", windowsVirtualKeyCode: 38 },
  Backspace: { key: "Backspace", code: "Backspace", windowsVirtualKeyCode: 8 },
};

async function key(name) {
  const spec = KEYS[name];
  await call("Input.dispatchKeyEvent", { type: "keyDown", ...spec });
  await call("Input.dispatchKeyEvent", { type: "keyUp", ...spec });
  await sleep(120);
}

async function type(text) {
  for (const ch of text) {
    await call("Input.dispatchKeyEvent", { type: "keyDown", text: ch, key: ch });
    await call("Input.dispatchKeyEvent", { type: "keyUp", key: ch });
  }
  await sleep(200);
}

async function reset() {
  await ev(`input.value = ""; input.dispatchEvent(new Event("input"));
            window.__sent = []; true`);
  await ev(`input.focus(); true`);
  await sleep(120);
}

async function state() {
  return JSON.parse(await ev(`JSON.stringify({
    value: input.value,
    palette: !palette.hidden,
    items: [...palette.querySelectorAll(".palette__name")].map(e => e.textContent),
    selected: [...palette.querySelectorAll('[aria-selected="true"] .palette__name')]
                .map(e => e.textContent)[0] || null,
    sent: window.__sent || [],
  })`));
}

await call("Page.enable");
await call("Runtime.enable");
await call("Page.navigate", { url });
await sleep(2200);

// 记下真正发出去的消息，用来判断「回车到底是提交了还是被补全吃掉了」
await ev(`window.__sent = [];
  (function () {
    const real = WebSocket.prototype.send;
    WebSocket.prototype.send = function (data) { window.__sent.push(data); return real.call(this, data); };
  })();
  window.__errs = [];
  addEventListener("error", (e) => __errs.push(String(e.message)));
  true`);
// 假装正在跑一轮，用来验 esc
await ev(`handle({kind:"status", ready:true, mode:"default", busy:true,
                  workspace:"/w", model:"m", name:"m", tokens:0, cost:0,
                  context:0, context_limit:1000}); true`);

const cases = [];
const check = (name, ok, detail) => {
  cases.push({ name, ok, detail });
  console.log((ok ? "  OK " : "  !! ") + name + (ok ? "" : "  → " + JSON.stringify(detail)));
};

// 1. 敲 / 应当弹出补全
await reset();
await type("/");
let s = await state();
check("敲 / 弹出补全", s.palette && s.items.length > 3, s);

// 2. 继续敲字母应当筛选（/mo 同时匹配 /model 和 /mode，两条都该在）
await type("mo");
s = await state();
check("/mo 筛出 /model 和 /mode",
      s.palette && s.items.includes("/mode") && s.items.includes("/model"), s);

// 3. Tab 补上高亮那条
await key("Tab");
s = await state();
check("Tab 补全并关掉菜单", s.value.startsWith("/mod") && s.value.endsWith(" ") && !s.palette, s);

// 3b. **打全了再按 Tab，不该被补成别的命令**（/mode 不能变成 /model）
await reset();
await type("/mode");
s = await state();
check("完全相同的那条排最前", s.selected === "/mode", s);
await key("Tab");
s = await state();
check("/mode + Tab 还是 /mode", s.value === "/mode ", s);

// 4. **打完参数再回车，应当提交，而不是被补全吃掉**
await reset();
await type("/mode auto");
s = await state();
check("打了参数之后菜单应当收起", !s.palette, s);
await key("Enter");
s = await state();
const sent = s.sent.map((x) => { try { return JSON.parse(x); } catch { return {}; } });
check("回车真的提交了 /mode auto",
      sent.some((m) => m.op === "command" && m.text === "/mode auto"), s.sent);
check("提交后输入框清空", s.value === "", s);

// 5. 敲完整命令直接回车（没有参数）
await reset();
await type("/undo");
await key("Enter");
s = await state();
const sent2 = s.sent.map((x) => { try { return JSON.parse(x); } catch { return {}; } });
check("完整命令一次回车就提交",
      sent2.some((m) => m.op === "command" && m.text === "/undo"), s.sent);

// 6. esc 关菜单**不应当**顺手把这一轮打断
// 先把 busy 重新按成真：上面那几条命令跑完，服务端回过 busy:false 的状态，
// 不补回来的话 esc 本来就不会中断，等于这两条什么都没验到。
const pretendBusy = `handle({kind:"status", ready:true, mode:"default", busy:true,
  workspace:"/w", model:"m", name:"m", tokens:0, cost:0, context:0, context_limit:1000}); true`;
await reset();
await ev(pretendBusy);
await type("/mo");
await key("Escape");
s = await state();
const sent3 = s.sent.map((x) => { try { return JSON.parse(x); } catch { return {}; } });
check("esc 关掉菜单", !s.palette, s);
check("esc 关菜单时不应当发出中断",
      !sent3.some((m) => m.op === "interrupt"), s.sent);

// 7. 菜单关掉之后 esc 才是中断
await ev(pretendBusy);
await key("Escape");
s = await state();
const sent4 = s.sent.map((x) => { try { return JSON.parse(x); } catch { return {}; } });
check("菜单关着时 esc 是中断",
      sent4.some((m) => m.op === "interrupt"), s.sent);

// 8. 上下键选择
await reset();
await type("/");
await key("ArrowDown");
s = await state();
check("↓ 选到第二条", s.selected === s.items[1], s);
await key("ArrowUp");
s = await state();
check("↑ 回到第一条", s.selected === s.items[0], s);

// 8b. **自己用 ↑↓ 挑过之后，回车才是「用我选的那条」**
await reset();
await type("/mo");
await key("ArrowDown");
const wanted = (await state()).selected;
await key("Enter");
s = await state();
check("挑过之后回车是补全，不是发送", s.value === wanted + " " && !s.palette, { s, wanted });

// 8c. 改了字之后「挑过」要作废，回车重新变回发送
await reset();
await type("/mo");
await key("ArrowDown");
await type("d");                      // 现在是 /mod，之前挑的那条不算数了
await key("Enter");
s = await state();
const sent5 = s.sent.map((x) => { try { return JSON.parse(x); } catch { return {}; } });
check("改了字之后回车是发送",
      sent5.some((m) => m.op === "command" && m.text === "/mod"), s.sent);

// 9. 退格回到只有 / 时菜单要回来
await reset();
await type("/xyzzy");
s = await state();
check("敲了不存在的命令时菜单收起", !s.palette, s);
for (let i = 0; i < 5; i++) await key("Backspace");
s = await state();
check("退格回到 / 时菜单重新出现", s.palette && s.items.length > 3, s);

// 9b. **不该在补全里提供一个点了就报错的东西**
await reset();
await type("/");
s = await state();
check("/key 不在补全里（网页端不给）", !s.items.includes("/key"), s.items);
check("/theme 不在补全里", !s.items.includes("/theme"), s.items);
check("/verify /review /helper 都在",
      ["/verify", "/review", "/helper"].every((n) => s.items.includes(n)), s.items);

// 9c. /help 要真的画出来一块
await reset();
await ev(`stream.replaceChildren(); true`);
await type("/help");
await key("Enter");
await sleep(900);
const helped = await ev(`(() => {
  const b = [...document.querySelectorAll(".block")].find(
    (x) => x.querySelector(".block__label").textContent === "命令");
  return b ? b.querySelectorAll("tbody tr").length : 0;
})()`);
check("/help 画出命令表", helped > 8, { rows: helped });

// 9d. 硬打 /key 要给人话，不是「未知命令」
await reset();
await type("/key");
await key("Enter");
await sleep(900);
const keyNote = await ev(`(() => {
  const n = [...document.querySelectorAll(".note")].map(e => e.textContent).join(" | ");
  return n.slice(-160);
})()`);
check("/key 给的是解释不是「未知命令」",
      keyNote.includes("只在终端") && !keyNote.includes("未知命令"), keyNote);

// 9e. 启动那条 MCP 说明不该占正文流，也不该顶掉空态提示
await ev(`stream.replaceChildren(); empty.hidden = false;
  handle({kind:"boot", what:"mcp", level:"notice", text:"MCP：fin（29 个工具）"}); true`);
const bootState = JSON.parse(await ev(`JSON.stringify({
  blocks: document.querySelectorAll(".block").length,
  bootShown: !document.getElementById("boot").hidden,
  bootText: document.getElementById("boot").textContent,
  emptyShown: !empty.hidden,
})`));
check("启动说明不进正文流", bootState.blocks === 0, bootState);
check("启动说明摆在刊头里", bootState.bootShown && bootState.bootText.includes("MCP"), bootState);
check("空态提示还在", bootState.emptyShown, bootState);

// 10. 普通一句话不该弹菜单
await reset();
await type("把 a.py 改一下");
s = await state();
check("普通输入不弹菜单", !s.palette, s);

const errs = await ev(`JSON.stringify(window.__errs || [])`);
console.log("\n页面 JS 报错:", errs);
const bad = cases.filter((c) => !c.ok).length;
console.log(`${cases.length - bad}/${cases.length} 通过`);
ws.close();
chrome.kill();
process.exitCode = bad ? 1 : 0;
