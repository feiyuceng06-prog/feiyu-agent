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
const ev = async (x) => {
  const out = await call("Runtime.evaluate", { expression: x, returnByValue: true });
  if (out.exceptionDetails) {
    // **不能静默**：eval 抛了却返回 undefined 的话，断言会挂在一个根本没执行的
    // 动作上 —— 顶层 const 跨 eval 重复声明就是这么骗过我一轮的
    throw new Error("页面里报错：" +
      (out.exceptionDetails.exception?.description || out.exceptionDetails.text || "").slice(0, 200));
  }
  return out.result?.value;
};

const KEYS = {
  Enter: { key: "Enter", code: "Enter", windowsVirtualKeyCode: 13, text: "\r" },
  Tab: { key: "Tab", code: "Tab", windowsVirtualKeyCode: 9 },
  Escape: { key: "Escape", code: "Escape", windowsVirtualKeyCode: 27 },
  ArrowDown: { key: "ArrowDown", code: "ArrowDown", windowsVirtualKeyCode: 40 },
  ArrowUp: { key: "ArrowUp", code: "ArrowUp", windowsVirtualKeyCode: 38 },
  Backspace: { key: "Backspace", code: "Backspace", windowsVirtualKeyCode: 8 },
};

async function key(name, mods = {}) {
  const spec = { ...KEYS[name] };
  // CDP 的修饰键是个位掩码：1 alt / 2 ctrl / 4 meta / 8 shift
  if (mods.shift) spec.modifiers = (spec.modifiers || 0) | 8;
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

/* **每轮先把 localStorage 清干净。** Chrome 的 profile 目录是跨次保留的，
   上一轮置顶过的会话会留到下一轮，把列表顺序改掉 —— 断言就挂在别人的数据上。
   （侧栏收放和置顶都存在这儿。） */
/* **开头先打断一次。** 附件那条用例会真的发一个 prompt 出去，agent 会真的跑一轮；
   夹具跑完那轮多半还没结束，下一次跑的时候 `/help` `/key` 这些命令就排在它后面，
   看起来像「agent 不回话」。收尾也打断一次，别把这个烂摊子留给下一轮。 */
await ev(`localStorage.clear();
  if (socket && socket.readyState === 1) socket.send(JSON.stringify({op:"interrupt"}));
  true`);
await sleep(600);
await call("Page.navigate", { url });
await sleep(1500);

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

/* **等命令清单到位再开始。** 补全靠的是连上时那条 /help 的回话，而 agent 启动要载
   MCP（29 个工具），几秒才回得来。不等的话第一条断言跑在一个空清单上。 */
for (let i = 0; i < 60; i++) {
  if (await ev(`commands.length > 0`)) break;
  await sleep(500);
}

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
// 补完命令名接着开参数菜单（这条命令有参数的话），省一次回车
check("Tab 补全命令名", s.value.startsWith("/mod") && s.value.endsWith(" "), s);

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
// **打了参数菜单不该收起，该接着补参数** —— 0.56.x 之前一见空格就收，
// 于是 `/mode ` 之后你得自己记住有哪四档（用户拿截图来问过）
check("打了参数还在补参数", s.palette && s.items.every(i => "auto".startsWith(i) || i === "auto"), s);
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
check("挑过之后回车是补全，不是发送", s.value === wanted + " ", { s, wanted });

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
// **要等 agent 回话**，不是等 DOM。它启动时要载 MCP（29 个工具），
// 固定 sleep 撞上冷启动就会挂在一个还没发生的事情上。
for (let i = 0; i < 40; i++) {
  await sleep(400);
  if (await ev(`commands.length > 0 && [...document.querySelectorAll(".block__label")]
      .some(e => e.textContent === "命令")`)) break;
}
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
for (let i = 0; i < 40; i++) {
  await sleep(400);
  if (await ev(`[...document.querySelectorAll(".note")].some(e => e.textContent.includes("只在终端"))`)) break;
}
/* **只看最后一条 note。** 把所有 note 拼起来的话，前面用例留下的
   那句合法的"未知命令 /mod"会把这条断言带挂 —— 它查的是"这条命令有没有
   给出解释"，不是"屏幕上有没有出现过这四个字"。 */
const keyNote = await ev(`(() => {
  const n = [...document.querySelectorAll(".note")];
  return n.length ? n[n.length - 1].textContent : "";
})()`);
check("/key 给的是解释不是「未知命令」",
      keyNote.includes("只在终端") && !keyNote.includes("未知命令"), keyNote);

// 9e. 启动那条 MCP 说明不该占正文流，也不该顶掉空态提示
await ev(`stream.replaceChildren();
  handle({kind:"boot", what:"mcp", level:"notice", text:"MCP：fin（29 个工具）"}); true`);
const bootState = JSON.parse(await ev(`JSON.stringify({
  blocks: document.querySelectorAll(".block").length,
  bootShown: !document.getElementById("s-boot").hidden,
  bootText: document.getElementById("s-boot").textContent,
})`));
check("启动说明不进正文流", bootState.blocks === 0, bootState);
check("启动说明摆在状态栏里", bootState.bootShown && bootState.bootText.includes("MCP"), bootState);

// 10. 普通一句话不该弹菜单
await reset();
await type("把 a.py 改一下");
s = await state();
check("普通输入不弹菜单", !s.palette, s);

// ── @路径 ────────────────────────────────────────────────────
/* 候选是服务端算的（`op:"paths"` → `mentions.complete()`），所以每条都要
   **等回话到了再断言**：打字比一次往返快，固定 sleep 会挂在还没到的那份上。 */
async function waitPaths(want) {
  for (let i = 0; i < 40; i++) {
    const now = await state();
    if (now.palette && now.items.some((x) => x.includes(want))) return now;
    await sleep(150);
  }
  return await state();
}

await reset();
await type("@fei");
s = await waitPaths("feiyu");
check("@ 弹出路径补全", s.palette && s.items.includes("feiyu/"), s);

// Tab 补到目录之后**不加空格**，接着往下一层补
await key("Tab");
s = await waitPaths("feiyu/");
check("补到目录不封口", s.value === "@feiyu/", s);
check("目录补完接着列下一层",
      s.palette && s.items.some((x) => x.startsWith("feiyu/")), s);

// 补到文件要加空格，菜单收起
await reset();
await type("@READ");
s = await waitPaths("README");
await key("Tab");
s = await state();
check("补到文件后面跟一个空格", s.value === "@README.md ", s);
check("补完文件菜单收起", !s.palette, s);

// **句子中间的 @ 也要补，而且只换掉那一段** —— 整框覆盖的话前面打的话就没了
await reset();
await type("照着 @fei");
s = await waitPaths("feiyu");
await key("Tab");
s = await state();
check("句子中间补全只换 @ 那一段", s.value === "照着 @feiyu/", s);

// 邮箱里的 @ 不是 mention
await reset();
await type("发到 a@b");
await sleep(400);
s = await state();
check("邮箱里的 @ 不弹菜单", !s.palette, s);

// 打到一个不存在的路径，菜单要收起来，回车照旧是发送
await reset();
await type("@zzzz");
await sleep(500);
s = await state();
check("没有匹配时菜单收起", !s.palette, s);

// ── 侧边栏 ──────────────────────────────────────────────────
async function side() {
  return JSON.parse(await ev(`JSON.stringify({
    open: document.body.dataset.side !== "off",
    // 0.80.0 起收起是整块滑出视口（定宽 240、滑完 visibility: hidden），
    // 量的是露在屏幕上的那一截，不是它自己的盒子宽
    width: (() => {
      const el = document.getElementById("side");
      const r = el.getBoundingClientRect();
      if (getComputedStyle(el).visibility === "hidden") return 0;
      return Math.round(Math.max(0, Math.min(r.right, innerWidth) - Math.max(r.left, 0)));
    })(),
    talks: [...document.querySelectorAll("#talks .talk__title")].map(e => e.textContent),
    current: [...document.querySelectorAll('#talks .talk[aria-current="true"] .talk__title')]
               .map(e => e.textContent)[0] || null,
  })`));
}

await ev(`setSide(true); true`);
let sd = await side();
check("侧边栏展开时有宽度", sd.width > 100, sd);
await ev(`document.getElementById("side-toggle").click(); true`);
await sleep(450);          // 滑出去要 --side-dur（240ms），滑完才藏
sd = await side();
check("点一下收起来，滑出视口", !sd.open && sd.width === 0, sd);
check("收起状态记进了 localStorage",
      (await ev(`localStorage.getItem("feiyu.side")`)) === "off", null);
await ev(`document.getElementById("side-toggle").click(); true`);
await sleep(450);
sd = await side();
check("再点一下展开", sd.open && sd.width > 100, sd);

// 历史列表：灌两条进去看画得对不对
await ev(`handle({kind:"sessions", current:"B", rows:[
  {id:"B", title:"把 parser 拆一下", model:"m", count:12, saved_at:2, when:"09-04 20:10"},
  {id:"A", title:"改一下构建脚本", model:"m", count:4,  saved_at:1, when:"09-03 11:02"}]}); true`);
await sleep(200);
sd = await side();
check("历史列表画出两条", sd.talks.length === 2 && sd.talks[0].includes("parser"), sd);
check("当前那条标出来了", sd.current && sd.current.includes("parser"), sd);

await ev(`handle({kind:"sessions", current:null, rows:[]}); true`);
await sleep(150);
const emptyText = await ev(`document.getElementById("talks").textContent`);
check("没有历史时说一声", emptyText.includes("还没有"), emptyText);

// 「新对话」按钮发的是 {op:"new"}
await reset();
await ev(`document.getElementById("new-talk").click(); true`);
await sleep(200);
const newSent = (await state()).sent.map(x => { try { return JSON.parse(x); } catch { return {}; } });
check("新对话按钮发 op:new", newSent.some(m => m.op === "new"), newSent);

// 点历史里的一条发 {op:"open", id}
await reset();
await ev(`handle({kind:"sessions", current:"B", rows:[
  {id:"A", title:"旧的那段", model:"m", count:4, saved_at:1, when:"09-03 11:02"}]});
  document.querySelector(".talk").click(); true`);
await sleep(200);
const openSent = (await state()).sent.map(x => { try { return JSON.parse(x); } catch { return {}; } });
check("点历史发 op:open 带 id",
      openSent.some(m => m.op === "open" && m.id === "A"), openSent);

// ── 删对话 ──────────────────────────────────────────────────
const twoTalks = `handle({kind:"sessions", current:"B", rows:[
  {id:"B", title:"新的那段", model:"m", count:9, saved_at:2, when:"09-05 01:10"},
  {id:"A", title:"旧的那段", model:"m", count:4, saved_at:1, when:"09-04 11:02"}]}); true`;

await reset();
await ev(twoTalks);
await sleep(150);
// 点叉**不能**顺手把这段对话打开
await ev(`document.querySelector(".talk .talk__x").click(); true`);
await sleep(200);
let delSent = (await state()).sent.map(x => { try { return JSON.parse(x); } catch { return {}; } });
check("点叉不会顺手打开这段对话", !delSent.some(m => m.op === "open"), delSent);
check("点叉先问一句，不是直接删",
      (await ev(`document.querySelectorAll(".talk--asking").length`)) === 1 &&
      !delSent.some(m => m.op === "delete"), delSent);

// 取消要能回到原样，而且回去之后还能再点
await ev(`[...document.querySelectorAll(".talk--asking button")]
  .find(b => b.textContent === "取消").click(); true`);
await sleep(200);
check("取消回到原样",
      (await ev(`document.querySelectorAll("#talks .talk--asking").length`)) === 0 &&
      (await ev(`document.querySelectorAll("#talks .talk").length`)) === 2, null);
await reset();
await ev(`document.querySelector(".talk .talk__x").click(); true`);
await sleep(200);
check("取消之后那一行还能再点叉",
      (await ev(`document.querySelectorAll(".talk--asking").length`)) === 1, null);

// 确认才真发 delete
await ev(`[...document.querySelectorAll(".talk--asking button")]
  .find(b => b.textContent === "删掉").click(); true`);
await sleep(200);
delSent = (await state()).sent.map(x => { try { return JSON.parse(x); } catch { return {}; } });
check("确认之后才发 op:delete 带 id",
      delSent.some(m => m.op === "delete" && m.id === "B"), delSent);

// 平时不显形，划上去才出现
await reset();
await ev(twoTalks);
await sleep(150);
const vis = JSON.parse(await ev(`JSON.stringify({
  idle: getComputedStyle(document.querySelector(".talk__x")).opacity })`));
check("叉平时不显形", vis.idle === "0", vis);

// ── 附件 ────────────────────────────────────────────────────
await reset();
await ev(`clips = [{name:".feiyu/uploads/a.png", state:"ready", path:".feiyu/uploads/a.png"}];
  drawClips(); input.value = "看看这张图"; true`);
await ev(`document.getElementById("send").click(); true`);
await sleep(200);
const clipSent = (await state()).sent.map(x => { try { return JSON.parse(x); } catch { return {}; } });
const prompt = clipSent.find(m => m.op === "prompt");
check("附件路径跟着这句话一起发出去",
      prompt && prompt.text.includes("看看这张图") && prompt.text.includes(".feiyu/uploads/a.png"),
      prompt);
check("发完附件条清空", (await ev(`clips.length`)) === 0, null);
/* **发完就打断。** 那句话真发给了 agent，它真跑一轮 —— 模型要是去 read / bash 那张图，
   会停下来要人点头，那张真卡就一直等着：后面几条量状态行的，量到的是「等你点头」
   （0.80.0 起它会这么说），下一次跑夹具时 /help、/key 也排在它后面没人回（真踩到过，
   跟模型那一轮怎么走有关，所以时好时坏）。0.80.0 起中断能把等着的确认一起放掉。 */
await sleep(300);
await ev(`if (socket && socket.readyState === 1) socket.send(JSON.stringify({op:"interrupt"})); true`);
await sleep(800);

// 断线时点发送：话要留在框里，状态行要说一声
await reset();
await ev(`input.value = "这句发不出去"; socket.close(); true`);
await sleep(400);
await ev(`document.getElementById("send").click(); true`);
await sleep(200);
const offline = JSON.parse(await ev(`JSON.stringify({
  kept: input.value, note: document.getElementById("s-state").textContent })`));
check("断线时话留在输入框里", offline.kept === "这句发不出去", offline);
check("断线时状态行说一声", offline.note.includes("没发出去"), offline);

// ── 外壳（Hermes 那套信息架构）────────────────────────────
const shell = JSON.parse(await ev(`JSON.stringify({
  bar: !!document.querySelector(".bar"),
  navs: [...document.querySelectorAll(".nav__text")].map(e => e.textContent),
  find: !!document.getElementById("find"),
  groups: [...document.querySelectorAll(".group__head")].map(e => e.textContent),
  status: !!document.querySelector(".status"),
  pick: document.getElementById("model").textContent,
  winBtnsHidden: document.getElementById("win-min").hidden,
})`));
check("标题栏在", shell.bar, shell);

/* 等的时候要看得见等了多久。一轮跑十分钟是真会发生的（实测 622 秒），
   只写「干活中」的话，慢和死长得一模一样 —— 用户拿这个来问过「为什么卡死了」。 */
await ev(`handle({kind:"status", ready:true, mode:"default", busy:true,
  workspace:"/tmp", model:"m", name:"m", tokens:1, cost:0,
  context:1, context_limit:100}); waitFrom = Date.now() - 125000; drawWait(); true`);
await sleep(150);
const waited = JSON.parse(await ev(`JSON.stringify({
  said: document.getElementById("s-state").textContent,
  dot: document.getElementById("dot").dataset.state })`));
check("等久了要显示等了多久", waited.said.includes("2:05"), waited);
check("等太久了要说一声「比平时慢」", waited.said.includes("比平时慢") && waited.dot === "slow",
      waited);

// 在想的时候要说"在想"，还要报想了多少字 —— 那 622 秒全在这儿
await ev(`handle({kind:"thinking", size:3200}); true`);
await sleep(150);
const thought = await ev(`document.getElementById("s-state").textContent`);
check("在想的时候说在想、带字数",
      thought.includes("在想") && thought.includes("3200 字"), thought);

/* **不能靠喂一条 busy:false 来验收尾**：夹具连的是一个真 agent，
   前面那条附件用例起的活还在跑，它推的 status(busy:true) 会把计时重新点起来
   （真被这个绊过一次）。直接调 stopWait 验它自己。 */
await ev(`stopWait(); true`);
const stopped = await ev(`JSON.stringify([waitFrom, waitTick, thoughtSize])`);
check("收尾把计时和想的字数都清掉", stopped === "[0,0,0]", stopped);

/* 命令补全那块。三条都是用户拿截图来问的：
   ↑↓ 走到看不见的地方（菜单最高 16rem，命令十几条）；
   `/命令 空格` 之后没有子选项；shift+tab 在桌面端没反应。 */
await reset();
await ev(`commands = [{name:"/help",about:"a"},{name:"/model",about:"b"},
  {name:"/style",about:"c"},{name:"/mode",about:"d"},{name:"/allow",about:"e"},
  {name:"/verify",about:"f"},{name:"/review",about:"g"},{name:"/cost",about:"h"},
  {name:"/compact",about:"i"},{name:"/clear",about:"j"},{name:"/undo",about:"k"},
  {name:"/jobs",about:"l"},{name:"/memory",about:"m"},{name:"/skills",about:"n"}];
  cmdArgs = {"/mode":["default","auto","plan","readonly"],
             "/model":["kimi-k2.6","deepseek-v4-pro"]};
  input.value = "/"; input.dispatchEvent(new Event("input")); true`);
await sleep(200);
for (let n = 0; n < 12; n++) await key("ArrowDown");
await sleep(200);
const seen = JSON.parse(await ev(`JSON.stringify((() => {
  const box = palette.getBoundingClientRect();
  const hit = palette.querySelector('[aria-selected="true"]').getBoundingClientRect();
  return { top: Math.round(hit.top - box.top), bot: Math.round(box.bottom - hit.bottom) };
})())`));
check("↑↓ 走到哪儿列表就滚到哪儿", seen.top >= -1 && seen.bot >= -1, seen);

await reset();
await ev(`input.value = "/mode "; input.dispatchEvent(new Event("input")); true`);
await sleep(200);
let sub = await state();
check("命令 + 空格给子选项", sub.palette && sub.items.join("/") === "default/auto/plan/readonly",
      sub.items);

await ev(`input.value = "/mode au"; input.dispatchEvent(new Event("input")); true`);
await sleep(200);
sub = await state();
check("子选项跟着往下筛", sub.items.join("/") === "auto", sub.items);
await key("Tab");
await sleep(150);
check("补出去的是整串", (await ev(`input.value`)) === "/mode auto", await ev(`input.value`));

await reset();
await ev(`input.value = "/mode auto"; input.dispatchEvent(new Event("input")); true`);
await sleep(150);
await key("Enter");
await sleep(200);
const modeSent = (await state()).sent.map(x => { try { return JSON.parse(x); } catch { return {}; } });
check("参数打全了回车是发送不是补全",
      modeSent.some(m => m.op === "command" && m.text === "/mode auto"), modeSent);

await reset();
await key("Tab", { shift: true });
await sleep(200);
const cycled = (await state()).sent.map(x => { try { return JSON.parse(x); } catch { return {}; } });
check("shift+tab 发 op:cycle_mode", cycled.some(m => m.op === "cycle_mode"), cycled);

// 技能与工具面板：Ctrl K 开、esc 关，开着的时候对话那一屏和输入区都收起来
await ev(`document.dispatchEvent(new KeyboardEvent("keydown",
  { key: "k", ctrlKey: true, bubbles: true })); true`);
await sleep(250);
const panel = JSON.parse(await ev(`JSON.stringify({
  open: !document.getElementById("shelf").hidden,
  sheet: document.getElementById("sheet").hidden,
  composer: document.getElementById("composer").hidden,
  tabs: [...document.querySelectorAll("#shelf-tabs .tab")].length,
  pressed: document.getElementById("nav-shelf").getAttribute("aria-pressed"),
})`));
check("Ctrl K 打开设置面板", panel.open && panel.tabs === 5, panel);
check("面板开着时对话那一屏收起来", panel.sheet && panel.composer, panel);
check("导航项跟着按下", panel.pressed === "true", panel);

await ev(`document.dispatchEvent(new KeyboardEvent("keydown",
  { key: "Escape", bubbles: true })); true`);
await sleep(250);
const closed = JSON.parse(await ev(`JSON.stringify({
  open: !document.getElementById("shelf").hidden,
  sheet: document.getElementById("sheet").hidden,
})`));
check("esc 退回对话", !closed.open && !closed.sheet, closed);
check("侧栏就这两项", shell.navs.join("/") === "新建会话/设置", shell.navs);
check("有搜索框", shell.find, shell);
check("有「会话」分组", shell.groups.includes("会话"), shell.groups);
check("有状态栏", shell.status, shell);
check("浏览器里不显示窗口按钮（那是桌面端的）", shell.winBtnsHidden, shell);

// 搜索只筛本地那份列表
await ev(`handle({kind:"sessions", current:"B", rows:[
  {id:"B", title:"改构建脚本", model:"m", count:9, saved_at:2, when:"09-05 01:10"},
  {id:"A", title:"看看持仓", model:"m", count:4, saved_at:1, when:"09-04 11:02"}]});
  (() => { const box = document.getElementById("find");
    box.value = "构建"; box.dispatchEvent(new Event("input")); })(); true`);
await sleep(200);
const found = JSON.parse(await ev(`JSON.stringify(
  [...document.querySelectorAll("#talks .talk__title")].map(e => e.textContent))`));
check("搜索筛得动", found.length === 1 && found[0].includes("构建"), found);

// ── 机器人那一栏 ────────────────────────────────────────────
// 搜索框是「会话」那栏的东西，切到机器人要收起来 —— 它筛的是会话，
// 留在那儿会让人以为能筛机器人
await ev(`setSideTab("bots"); true`);
await sleep(200);
check("切到机器人：会话列表让开、搜索框收起",
      (await ev(`document.getElementById("side-talks").hidden`)) === true &&
      (await ev(`document.getElementById("side-find-wrap").hidden`)) === true, null);

// 点一个机器人 = 开一段新对话（op:bot / do:start），不是 op:open
await reset();
await ev(`handle({kind:"bots", current:"", rows:[
  {name:"代码评审", description:"只挑毛病", model:"m", mode:"readonly", style:"", on:false}]});
  document.querySelector("#bots .talk").click(); true`);
await sleep(200);
const botSent = (await state()).sent.map(x => { try { return JSON.parse(x); } catch { return {}; } });
check("点机器人发 op:bot do:start",
      botSent.some(m => m.op === "bot" && m.do === "start" && m.name === "代码评审"), botSent);
check("点机器人不发 op:open", !botSent.some(m => m.op === "open"), botSent);

await ev(`setSideTab("talks"); true`);
await sleep(150);
await ev(`(() => { const b = document.getElementById("find");
  b.value = ""; b.dispatchEvent(new Event("input")); })(); true`);

// shift+点 = 置顶
await sleep(150);
await ev(`localStorage.removeItem("feiyu.pinned");
  handle({kind:"sessions", current:"B", rows:[
    {id:"B", title:"改构建脚本", model:"m", count:9, saved_at:2, when:"09-05 01:10"},
    {id:"A", title:"看看持仓", model:"m", count:4, saved_at:1, when:"09-04 11:02"}]});
  document.querySelectorAll(".talk")[1].dispatchEvent(
    new MouseEvent("click", { shiftKey: true, bubbles: true })); true`);
await sleep(200);
// **点完要再灌一遍再断言。** 服务端自己也会推 sessions（它盘上有别的会话），
// 那一条会在我们灌的合成列表之后到，把 DOM 覆盖掉 —— 断言就跑在别人的数据上了。
await ev(`handle({kind:"sessions", current:"B", rows:[
  {id:"B", title:"改构建脚本", model:"m", count:9, saved_at:2, when:"09-05 01:10"},
  {id:"A", title:"看看持仓", model:"m", count:4, saved_at:1, when:"09-04 11:02"}]}); true`);
await sleep(200);
const pin = JSON.parse(await ev(`JSON.stringify({
  shown: !document.getElementById("group-pinned").hidden,
  top: [...document.querySelectorAll("#pinned .talk__title")].map(e => e.textContent),
})`));
check("shift+点把那条置顶", pin.shown && pin.top.length === 1, pin);

/* 余额：查得到就显，查不到整个不显示（摆个"—"只会让人以为坏了）。
   **放在最后**：最后那次点击会引一次服务端往返，夹在别的用例中间的话，
   那份迟到的 status 会把后面的断言搅了。 */
await ev(`drawCredits({shown:"$9.78", unit:"$", left:9.78, used:0.22, total:10}); true`);
let money = JSON.parse(await ev(`JSON.stringify({
  text: document.getElementById("s-left").textContent,
  hidden: document.getElementById("s-left").hidden,
  low: document.getElementById("s-left").dataset.low,
  title: document.getElementById("s-left").title })`));
check("有余额就显在状态栏", !money.hidden && money.text.includes("$9.78"), money);
check("充了多少用了多少放在 title 里", money.title.includes("充值") && money.title.includes("已用"),
      money.title);
check("余额充足时不染红", money.low === "0", money);

await ev(`drawCredits({shown:"$0.30", unit:"$", left:0.3}); true`);
check("余额见底要染成 alert 色",
      (await ev(`document.getElementById("s-left").dataset.low`)) === "1", null);

await ev(`drawCredits({why:"这个接口没有查额度的地方"}); true`);
money = JSON.parse(await ev(`JSON.stringify({
  hidden: document.getElementById("s-left").hidden,
  sep: document.getElementById("s-left-sep").hidden,
  title: document.getElementById("s-left").title })`));
check("查不到就整个不显示", money.hidden && money.sep, money);
check("查不到的理由留在 title 上", money.title.includes("没有查额度"), money.title);

await reset();
await ev(`document.getElementById("s-left").hidden = false;
  document.getElementById("s-left").click(); true`);
await sleep(300);
const asked = (await state()).sent.map(x => { try { return JSON.parse(x); } catch { return {}; } });
check("点一下发 op:credits 现查一次", asked.some(m => m.op === "credits"), asked);


/* ── 右下角换模型（0.79.1）─────────────────────────────────────
   那个钮从 0.52.0 起就摆在那儿、有 title，却**从来没绑过点击**，用户拿截图来要的。
   清单用编好的一份（跟跑夹具这台机器上配了什么无关）：十条，过了出筛选框的线，
   两个厂商、一条没 key 的。点开之后真 agent 也会回一份 models，**等它落地再灌**，
   不然它会把编好的这份盖掉（跟文件开头那句「要等 agent 回话」同一条）。 */
const FAKE_MODELS = { kind: "models", trusted: true, rows: [
  ...["opus-5.5", "sonnet-5", "haiku-4.5", "fable-5.1", "opus-5"].map((n) => (
    { name: n, model: "anthropic/claude-" + n, vision: true, has_key: true, using: n === "sonnet-5" })),
  ...["gpt-6-luna", "gpt-6-sol", "gpt-6-astra", "gpt-5.6-luna"].map((n) => (
    { name: n, model: "openai/" + n, vision: true, has_key: true, using: false })),
  { name: "没配key的", model: "openai/gpt-x", vision: false, has_key: false, using: false },
] };
const ops = async () => (await state()).sent.map((x) => { try { return JSON.parse(x); } catch { return {}; } });
// 没配模型的时候点模型名是直接去「连接模型」（0.82.0）—— 夹具的 agent 就没配，先量这一条
await ev(`document.body.dataset.needModel = "1"; models = null; document.getElementById("model").click(); true`);
await sleep(300);
check("没模型时点模型名 = 直接摊开「加一条」", await ev(`!document.getElementById("shelf").hidden
  && editing === "" && (document.querySelector("#shelf .shelf__row[aria-current='true'] .shelf__name") || {}).textContent === "模型"`));
await ev(`showShelf(false); editing = null; formDismissed = false; true`);
await sleep(200);

async function openMenu() {
  // 下面量的是有模型时那张清单
  await ev(`delete document.body.dataset.needModel; document.getElementById("model").click(); true`);
  await sleep(700);
  await ev(`handle(${JSON.stringify(FAKE_MODELS)}); true`);
  await sleep(200);
}
async function menuState() {
  return JSON.parse(await ev(`JSON.stringify({
    open: !document.getElementById("models").hidden,
    expanded: document.getElementById("model").getAttribute("aria-expanded"),
    groups: [...document.querySelectorAll("#models .models__group")].map((e) => e.textContent),
    rows: [...document.querySelectorAll("#models .models__row .models__name")].map((e) => e.textContent),
    lit: (document.querySelector('#models [aria-selected="true"] .models__name') || {}).textContent || "",
    now: (document.querySelector('#models [data-now="1"] .models__name') || {}).textContent || "",
    findShown: !!document.querySelector("#models .models__find") && !document.querySelector("#models .models__find").hidden,
    focusInFind: document.activeElement === document.querySelector("#models .models__find"),
    foot: (document.querySelector("#models .models__foot") || {}).textContent || "",
  })`));
}

await reset();
await openMenu();
let mm = await menuState();
check("点右下角的模型名弹出清单", mm.open && mm.expanded === "true", mm);
check("点开时现取一次清单（op:models）", (await ops()).some((m) => m.op === "models"), await ops());
check("不加厂商小标题，按配置里的顺序排（用户看过说不要小标题）",
      mm.groups.length === 0 && mm.rows[0] === "opus-5.5" && mm.rows[5] === "gpt-6-luna", mm);
check("在用的那条标出来、键盘也先停在它上面", mm.now === "sonnet-5" && mm.lit === "sonnet-5", mm);
check("十条过了线就给筛选框，焦点就在它上面", mm.findShown && mm.focusInFind, mm);

await type("luna");
mm = await menuState();
check("打字就筛", mm.rows.join("|") === "gpt-6-luna|gpt-5.6-luna", mm.rows);
await key("ArrowDown");
mm = await menuState();
check("上下键在筛出来的里面走", mm.lit === "gpt-5.6-luna", mm);
await ev(`window.__sent = []; true`);
await key("Enter");
mm = await menuState();
let got = await ops();
check("回车换过去：发的是 /model <名字>", got.some((m) => m.op === "command" && m.text === "/model gpt-5.6-luna"), got);
check("换完清单收起来", !mm.open && mm.expanded === "false", mm);

// esc 只收菜单：一轮正跑着也不能顺手把它打断了（补全菜单 0.50.3 踩过同一个坑）
await ev(`handle({kind:"status", ready:true, busy:true, mode:"default", name:"sonnet-5", model:"x"}); true`);
await openMenu();
mm = await menuState();
check("跑着的时候说一声「跑完才换」", mm.foot.includes("跑完才换"), mm.foot);
await ev(`window.__sent = []; true`);
await key("Escape");
mm = await menuState();
got = await ops();
check("esc 收起清单", !mm.open, mm);
check("esc 收清单时不打断这一轮", !got.some((m) => m.op === "interrupt"), got);
await ev(`handle({kind:"status", ready:true, busy:false, mode:"default", name:"sonnet-5", model:"x"}); true`);

await openMenu();
await ev(`window.__sent = []; true`);
await ev(`[...document.querySelectorAll("#models .models__row")]
  .find((li) => li.textContent.includes("没配key的")).click(); true`);
mm = await menuState();
got = await ops();
check("没 key 的那条点不动（换过去必然 401）", mm.open && !got.some((m) => m.op === "command"), { mm, got });
await ev(`[...document.querySelectorAll("#models .models__row")]
  .find((li) => li.dataset.now === "1").click(); true`);
mm = await menuState();
got = await ops();
check("点在用的那条只收起来，不白发一条命令", !mm.open && !got.some((m) => m.op === "command"), { mm, got });

await openMenu();
await call("Input.dispatchMouseEvent", { type: "mousePressed", x: 640, y: 200, button: "left", clickCount: 1 });
await call("Input.dispatchMouseEvent", { type: "mouseReleased", x: 640, y: 200, button: "left", clickCount: 1 });
await sleep(200);
check("点清单外面就收起来", !(await menuState()).open, null);

await ev(`input.value = "/mo"; input.dispatchEvent(new Event("input")); true`);
await sleep(200);
await openMenu();
check("跟补全菜单一次只开一张",
      (await menuState()).open && (await ev(`document.getElementById("palette").hidden`)), null);
await ev(`input.value = ""; input.dispatchEvent(new Event("input")); true`);
await key("Escape");

/* ── 0.80.0：确认卡用数字键答 ───────────────────────────────
   **真按键**，不直接调函数 —— 这一条的毛病全在「焦点在哪、框里有没有字、到没到点」
   这几个判断上，绕过按键层就等于没测。 */
async function digit(d) {
  const code = 48 + Number(d);
  await call("Input.dispatchKeyEvent",
    { type: "keyDown", key: d, code: "Digit" + d, text: d, windowsVirtualKeyCode: code });
  await call("Input.dispatchKeyEvent",
    { type: "keyUp", key: d, code: "Digit" + d, windowsVirtualKeyCode: code });
  await sleep(250);            // 键盘按下去那一下要先演 110ms 再点
}
const replies = async () => (await ops()).filter((m) => m.op === "reply");
let askId = 9100;
/* **这一段只灌假事件，先把真 agent 的收件口堵上。** 附件那条用例真发过一句话出去，
   那一轮什么时候跑完不一定 —— 跑完推过来的 turn_end / status(busy=false) 会（正确地）
   把假卡上的键撤掉，于是断言随机挂在「按了没反应」上（真踩到过：单独跑全过，
   串在整套里挂一条）。发出去的照旧记在 __sent 里。 */
await ev(`window.__inbound = socket.onmessage; socket.onmessage = () => {}; true`);
async function askNow(extra = {}) {
  askId += 1;
  const card = { kind: "ask", id: askId, type: "approve", tool: "bash", label: "Bash", target: "",
                 args: { command: "npm test" }, rule: "Bash(npm:*)", ...extra };
  await ev(`handle({kind:"status", ready:true, busy:true, mode:"default", name:"m", model:"m"});
    handle(${JSON.stringify(card)}); true`);
  return askId;
}

await reset();
const firstAsk = await askNow();
await digit("1");             // 卡片刚弹出来：这个 1 是手指还在往下落的那一下
let rs = await replies();
let st = await state();
check("卡片刚出现时按数字不算回答（落进输入框当字）", !rs.length && st.value === "1", { rs, st });
check("这时状态行说的是「等你点头」，不是「干活中」",
      (await ev(`document.getElementById("s-state").textContent`)).startsWith("等你点头"), null);
await reset();
await sleep(700);             // 过了 ARM_AFTER
// 卡片找不着（上一条已经红了）也别在这儿抛 —— 夹具该报红，不该崩
check("到点了键帽浮出来",
      (await ev(`(() => { const k = document.querySelector(".ask:not(.ask--done) .btn__key");
                         return k ? getComputedStyle(k).opacity : "没有卡"; })()`)) === "1", null);
await digit("1");
rs = await replies();
check("框里空着、到了点，按 1 = 同意一次",
      rs.length === 1 && rs[0].id === firstAsk && rs[0].value === "yes", rs);
check("答完收成一行回执，前面一个勾",
      await ev(`!!document.querySelector(".ask--done .ask__answer .ask__tick")`), null);

await reset();
await askNow();
await sleep(700);
await type("修");              // 框里有字了 —— 数字就是字
await digit("2");
rs = await replies();
st = await state();
check("框里有字时按数字是打字，不是回答", !rs.length && st.value.endsWith("2"), { rs, st });
await reset();
await digit("2");
rs = await replies();
check("框清空了再按 2 = 拒绝", rs.length === 1 && rs[0].value === "no", rs);

await reset();
await askNow({ args: { command: "rm -rf build/" } });
await sleep(800);
await digit("1");
rs = await replies();
check("回不去的那档 0.8 秒时还不接键", !rs.length, rs);
await reset();
await sleep(700);
await digit("2");
rs = await replies();
check("过了 1.2 秒才接（按 2 拒绝）", rs.length === 1 && rs[0].value === "no", rs);

await reset();
await askNow();
await sleep(700);
await ev(`input.value = "/"; input.dispatchEvent(new Event("input")); input.value = ""; true`);
await ev(`showSoft(palette); true`);     // 补全开着：手在那儿挑命令
await digit("1");
rs = await replies();
check("补全菜单开着时数字不当回答", !rs.length, rs);
await ev(`hidePalette(); true`);
await reset();
await digit("2");            // 把这张收掉，别留给下面

await reset();
await ev(`window.__prompted = 0; window.prompt = () => { __prompted++; return null; }; true`);
askId += 1;
await ev(`handle({kind:"ask", id:${askId}, type:"choice", question:"用哪个？", options:["npm","pnpm"]}); true`);
await sleep(700);
await digit("3");
let own = JSON.parse(await ev(`JSON.stringify({ open: !!document.querySelector(".ask__own"),
  focus: document.activeElement.className, prompted: __prompted })`));
check("「自己写」就地展开一行，不弹 prompt()", own.open && own.focus === "ask__own-box" && !own.prompted, own);
await type("用 bun 12");      // 数字在这儿是字，不是回答
await key("Escape");
own = JSON.parse(await ev(`JSON.stringify({ open: !!document.querySelector(".ask__own"),
  ops: (window.__sent || []).map((x) => { try { return JSON.parse(x).op; } catch { return ""; } }) })`));
check("esc 只收起那一行，不打断这一轮", !own.open && !own.ops.includes("interrupt") && !own.ops.includes("reply"), own);
await digit("3");
await type("用 bun");
await key("Enter");
rs = await replies();
check("写完回车 = 把这句当回答", rs.length === 1 && rs[0].value === "用 bun", rs);
await ev(`handle({kind:"status", ready:true, busy:false, mode:"default", name:"m", model:"m"});
  socket.onmessage = window.__inbound; true`);

// 收尾打断：附件那条把 agent 支使去跑活儿了，别留给下一轮
await ev(`if (socket && socket.readyState === 1)
  socket.send(JSON.stringify({op:"interrupt"})); true`);

const errs = await ev(`JSON.stringify(window.__errs || [])`);
console.log("\n页面 JS 报错:", errs);
const bad = cases.filter((c) => !c.ok).length;
console.log(`${cases.length - bad}/${cases.length} 通过`);
ws.close();
chrome.kill();
process.exitCode = bad ? 1 : 0;
