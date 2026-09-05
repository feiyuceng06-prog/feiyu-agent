/* 网页端的视觉自检：起一个 headless Chrome，走 CDP 把画面拍下来。
 *
 * 跟 `dev/ui_drive.py` 是同一个路子 —— 终端那半边靠 pty + pyte 回放，
 * 这半边靠 Chrome。**改完前端不要只看 CSS 觉得对**，一定要真渲染一遍再看。
 * 不引 puppeteer：Node 自带 WebSocket，CDP 本来就是 JSON over WS。
 *
 * 用法：node dev/shoot.mjs <页面地址> <输出目录>
 *
 * 拍两组：
 *   1_flow  —— 灌一份编好的事件流（工具调用、diff、markdown、报错），
 *              覆盖到真跑一次未必碰得上的分支
 *   2_ask   —— 停在「要你点头」那张卡上，这是全站唯一用红的结构
 *   3_narrow—— 390px 宽，看 meta-rail 塌成单列之后还成不成立
 */

import { spawn } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import { setTimeout as sleep } from "node:timers/promises";

const url = process.argv[2] || "http://127.0.0.1:7791/";
const outDir = process.argv[3] || "/tmp/feiyu-web-shots";
const PORT = 9333;

mkdirSync(outDir, { recursive: true });

const chrome = spawn("google-chrome-stable", [
  "--headless=new",
  `--remote-debugging-port=${PORT}`,
  "--remote-allow-origins=*",
  "--no-first-run",
  "--no-default-browser-check",
  "--disable-gpu",
  "--hide-scrollbars",
  "--user-data-dir=" + outDir + "/chrome-profile",
  "--window-size=1280,1600",
  "about:blank",
], { stdio: ["ignore", "ignore", "pipe"] });

let stderr = "";
chrome.stderr.on("data", (d) => (stderr += d));

async function targets() {
  for (let i = 0; i < 60; i++) {
    try {
      const reply = await fetch(`http://127.0.0.1:${PORT}/json/list`);
      const list = await reply.json();
      const page = list.find((t) => t.type === "page");
      if (page) return page;
    } catch { /* 还没起来 */ }
    await sleep(250);
  }
  throw new Error("Chrome 起不来：\n" + stderr);
}

class Cdp {
  constructor(ws) {
    this.ws = ws;
    this.id = 0;
    this.pending = new Map();
    ws.addEventListener("message", (e) => {
      const msg = JSON.parse(e.data);
      const box = this.pending.get(msg.id);
      if (box) {
        this.pending.delete(msg.id);
        msg.error ? box.reject(new Error(JSON.stringify(msg.error))) : box.resolve(msg.result);
      }
    });
  }
  call(method, params = {}) {
    const id = ++this.id;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.ws.send(JSON.stringify({ id, method, params }));
    });
  }
  async eval(expression) {
    const out = await this.call("Runtime.evaluate", {
      expression, awaitPromise: true, returnByValue: true,
    });
    if (out.exceptionDetails) {
      throw new Error("页面里报错：" + JSON.stringify(out.exceptionDetails.exception?.description
        || out.exceptionDetails.text));
    }
    return out.result?.value;
  }
  async shot(name, { width = 1280, height = 1600, beyond = true } = {}) {
    await this.call("Emulation.setDeviceMetricsOverride", {
      width, height, deviceScaleFactor: 2, mobile: false,
    });
    await sleep(700);                       // 等浮现那 500ms 走完
    // **滚到某处再拍要用 beyond: false。** 页面本身不滚（头尾是钉住的，
    // 滚的是 .sheet 那个容器），captureBeyondViewport 会重新从文档顶部铺开，
    // 把刚滚到的位置丢掉——第一版就是这样拍不到确认卡的。
    const { data } = await this.call("Page.captureScreenshot", {
      format: "png", captureBeyondViewport: beyond,
    });
    writeFileSync(`${outDir}/${name}.png`, Buffer.from(data, "base64"));
    return `${outDir}/${name}.png`;
  }
}

/* 编一份事件流，覆盖每一种 kind。真跑一次未必碰得上报错和 diff。 */
const FLOW = [
  { kind: "status", ready: true, mode: "default", busy: false, workspace: "/home/you/blog",
    model: "deepseek-v4-pro", name: "deepseek-v4-pro", tokens: 18240, cost: 0.0412,
    context: 21400, context_limit: 128000 },
  { kind: "turn_start", text: "把 build.mjs 里读 frontmatter 那段抽成一个函数，然后跑一遍构建" },
  { kind: "tool_start", name: "search", label: "Search", args: { regex: "frontmatter" },
    target: "frontmatter" },
  { kind: "tool_result", summary: "7 处匹配", is_error: false, detail: [
    { kind: "", text: "scripts/build.mjs:142:  // frontmatter 用 --- 包起来" },
    { kind: "", text: "scripts/build.mjs:151:function splitFrontmatter(raw) {" },
  ] },
  { kind: "text", delta: "找到了，`build.mjs` 里这段解析散在两个地方。我打算：\n\n" },
  { kind: "text", delta: "- 抽成 `readFrontmatter(raw)`，放在文件顶部的工具函数区\n" },
  { kind: "text", delta: "- 两处调用点都换过去\n- 跑 `npm run build` 确认产物没变\n\n先改：\n" },
  { kind: "tool_start", name: "edit_file", label: "Update",
    args: { path: "scripts/build.mjs" }, target: "scripts/build.mjs" },
  { kind: "tool_result", summary: "已修改 2 处", is_error: false, detail: [
    { kind: "-", text: "142  -  const body = raw.replace(/^---[\\s\\S]*?---/, \"\");" },
    { kind: "+", text: "142  +  const { meta, body } = readFrontmatter(raw);" },
  ] },
  { kind: "hook", ok: true, text: "钩子通过：npm run build" },
  { kind: "tool_start", name: "bash", label: "Bash", args: { command: "npm run build" },
    target: "npm run build" },
  { kind: "tool_result", summary: "退出码 1", is_error: true, detail: [
    { kind: "", text: "ReferenceError: readFrontmatter is not defined" },
    { kind: "", text: "    at renderPost (scripts/build.mjs:142:23)" },
  ] },
  { kind: "note", level: "warn", text: "接口出错（429），2 秒后重试第 1 次" },
  { kind: "text", delta: "\n函数定义漏在下面了，提到调用点之前。\n\n" },
  { kind: "text", delta: "| 文件 | 改了什么 |\n|---|---|\n| `scripts/build.mjs` | 抽出 `readFrontmatter`，两处调用点换掉 |\n\n" },
  { kind: "text", delta: "```js\nfunction readFrontmatter(raw) {\n  const m = /^---\\n([\\s\\S]*?)\\n---\\n?/.exec(raw);\n  return m ? { meta: parse(m[1]), body: raw.slice(m[0].length) } : { meta: {}, body: raw };\n}\n```\n" },
  { kind: "turn_end" },
  { kind: "status", ready: true, mode: "default", busy: false, workspace: "/home/you/blog",
    model: "deepseek-v4-pro", name: "deepseek-v4-pro", tokens: 31480, cost: 0.0684,
    context: 29100, context_limit: 128000 },
];

/* 纯对话：没有工具调用、没有 diff，只有一问一答。
   这是最常见的一屏，也是最容易做丑的一屏 —— 排版全靠字号和留白撑着。 */
const TALK = [
  { kind: "turn_start", text: "你好" },
  { kind: "text", delta: "你好！我是 Feiyu，很高兴见到你 👋\n\n" },
  { kind: "text", delta: "我可以帮你在这个工作区里写代码、调试、重构、跑测试、查文档，" },
  { kind: "text", delta: "也能帮你看看 fin 那边接入的持仓、行情和回测数据。\n\n有什么想做的，直接说就行。" },
  { kind: "turn_end" },
  { kind: "turn_start", text: "你是哪个模型" },
  { kind: "text", delta: "我当前使用的模型是 **qwen/qwen3.8-flash**（配置名 qwen3.8-flash）。" },
  { kind: "turn_end" },
  { kind: "turn_start", text: "把 scripts/build.mjs 里读 frontmatter 那段抽成一个函数，两处调用点都换过去，然后跑一遍构建确认产物没变" },
  { kind: "text", delta: "好，我先看一下那两处怎么写的。" },
  { kind: "turn_end" },
];

const ASK = [
  { kind: "preview", rows: [
    { kind: "-", text: "  47  -  rx = re.compile(args[\"regex\"])" },
    { kind: "+", text: "  47  +  if shutil.which(\"rg\"):" },
    { kind: "+", text: "  48  +      return _ripgrep(args[\"regex\"], self.root)" },
  ] },
  { kind: "ask", id: 9001, type: "approve", tool: "edit_file", label: "Update",
    target: "feiyu/tools.py", args: { path: "feiyu/tools.py" }, rule: "Update(feiyu/tools.py)" },
];

const page = await targets();
const ws = new WebSocket(page.webSocketDebuggerUrl);
await new Promise((ok, bad) => { ws.onopen = ok; ws.onerror = bad; });
const cdp = new Cdp(ws);

await cdp.call("Page.enable");
await cdp.call("Runtime.enable");
await cdp.call("Page.navigate", { url });
await sleep(1500);

const errors = [];
await cdp.eval(`window.__errs = []; addEventListener("error", (e) => __errs.push(String(e.message)));
  addEventListener("unhandledrejection", (e) => __errs.push(String(e.reason))); true`);

const feed = (events) =>
  cdp.eval(`(${JSON.stringify(events)}).forEach(handle); true`);

await feed(FLOW);
console.log("拍了", await cdp.shot("1_flow"));

await feed(ASK);
// 滚的是 .sheet 那个容器，不是 window —— 页面本身不滚，头和尾是钉住的
await cdp.call("Emulation.setDeviceMetricsOverride", {
  width: 1280, height: 1000, deviceScaleFactor: 2, mobile: false,
});
await sleep(300);
await cdp.eval(`sheet.scrollTop = sheet.scrollHeight; true`);
console.log("拍了", await cdp.shot("2_ask", { height: 1000, beyond: false }));

await cdp.call("Page.navigate", { url });
await sleep(1200);
await feed(FLOW.slice(0, 8));
console.log("拍了", await cdp.shot("3_narrow", { width: 390, height: 1200 }));

// 纯对话那一屏单独拍一张 —— 它跟有工具调用的那屏排版重点完全不同
await cdp.call("Emulation.setDeviceMetricsOverride",
  { width: 1280, height: 900, deviceScaleFactor: 1, mobile: false });
await cdp.call("Page.navigate", { url });
await sleep(1200);
await feed(TALK);
console.log("拍了", await cdp.shot("4_talk", { width: 1280, height: 900 }));

/* 量一遍网格：每块的大写标签必须跟正文第一行顶对齐。
   靠眼睛看差 2px 是看不出来的，但十几块堆起来整条 rail 就是歪的。

   **视口要先还原成宽的。** 上面拍 3_narrow 时改成了 390px，那个宽度下
   meta-rail 是**故意**塌成上下两行的，不还原就会量到 17px 的"偏移"然后
   去修一个根本不存在的问题（第一版就是这么被自己骗了一轮）。 */
await cdp.call("Emulation.setDeviceMetricsOverride", {
  width: 1280, height: 1600, deviceScaleFactor: 1, mobile: false,
});
await cdp.call("Page.navigate", { url });
await sleep(1200);
await feed(FLOW);
await sleep(900);          // 浮现那 500ms 走完再量，动画中途量的是中间态
const drift = await cdp.eval(`JSON.stringify([...document.querySelectorAll(".block")].map((b) => {
  const label = b.querySelector(".block__label").getBoundingClientRect();
  const first = b.querySelector(".block__body").firstElementChild;
  const body = (first || b.querySelector(".block__body")).getBoundingClientRect();
  return { label: b.querySelector(".block__label").textContent,
           drift: Math.round(body.top - label.top) };
}).filter((r) => Math.abs(r.drift) > 2))`);
if (drift !== "[]") {
  console.error("标签和正文没对齐：", drift);
  process.exitCode = 1;
} else {
  console.log("meta-rail 每一块都对齐");
}

/* 宽窄两档下那只狗都得真的看得见。
   **零尺寸的元素在截图里就是「不存在」，眼睛扫过去不会发现少了东西** ——
   窄屏那版真的这么坏过一次：--rail 收成 0，而 .mark 是 width:100%，于是宽 0。 */
for (const width of [1280, 390]) {
  await cdp.call("Emulation.setDeviceMetricsOverride",
    { width, height: 900, deviceScaleFactor: 1, mobile: false });
  await sleep(400);
  const box = await cdp.eval(`JSON.stringify((() => {
    const m = document.querySelector(".bar__mark");
    if (!m) return { w: 0, h: 0, missing: true };
    const r = m.getBoundingClientRect();
    return { w: Math.round(r.width), h: Math.round(r.height) };
  })())`);
  const { w, h } = JSON.parse(box);
  if (w < 14 || h < 14) {
    console.error(`${width}px 下标题栏那只狗没了或太小：`, box);
    process.exitCode = 1;
  } else {
    console.log(`${width}px 下标题栏记号 ${w}×${h}`);
  }
}

const pageErrors = await cdp.eval("JSON.stringify(window.__errs || [])");
if (pageErrors && pageErrors !== "[]") {
  console.error("页面里有报错：", pageErrors);
  process.exitCode = 1;
} else {
  console.log("页面无 JS 报错");
}

ws.close();
chrome.kill();
