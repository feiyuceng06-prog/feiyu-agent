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
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
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

/* 出图那一屏。图是真读磁盘的（devserver 的 /file），所以夹具要在仓库根上跑 ——
   feiyu/logo.png 是这棵树里现成的一张图。
   最后那段正文里**故意**又 ![]() 了同一个路径：模型生成完常会把路径重述一遍，
   前端该认出来不画第二遍。这一条只能在这儿验，单元测试碰不到 DOM。 */
/* 出图那一屏要一张真图，而**工作区是什么目录夹具事先不知道**。
   走 /upload 把 logo 传进去，拿回来的相对路径正好就是 agent 出图时给的那种。
   顺带也验了一次上传那条路。 */
async function putImage() {
  const raw = readFileSync(new URL("../../feiyu/logo.png", import.meta.url));
  const reply = await fetch(new URL("/upload" + new URL(url).search, url), {
    method: "POST",
    headers: { "x-feiyu-filename": encodeURIComponent("shot.png"),
               "content-type": "application/octet-stream", "origin": new URL(url).origin },
    body: raw,
  });
  if (!reply.ok) throw new Error("上传失败 HTTP " + reply.status);
  return (await reply.json()).path;
}

const SHOT = [
  { kind: "turn_start", text: "画一只狗当这个项目的记号" },
  { kind: "tool_start", name: "image_generate", label: "Image",
    args: { prompt: "一只侧脸的狗，纯黑剪影，白底，极简" }, target: "一只侧脸的狗，纯黑剪影，白底，极简" },
  { kind: "tool_result", summary: "出了一张图（12 KB）", is_error: false,
    detail: [], shows: "@@SHOT@@" },
  { kind: "text", delta: "画好了，剪影版本，白底黑狗：\n\n" },
  { kind: "text", delta: "![一只狗](@@SHOT@@)\n\n要换个角度或者加点线条的话直接说。" },
  { kind: "turn_end" },
];

/* 并行那一批：四条 tool_start 全来完，四条 tool_result 才开始来。
   没有调用 id 的话前端只能按到达顺序猜，后三条结果会全挂到最后那条标题下面。 */
const PARALLEL = [
  { kind: "turn_start", text: "抓四个新闻站看看今天有什么" },
  ...["https://apnews.com/", "https://www.reuters.com/world/",
      "https://www.bbc.com/news/world", "https://top.baidu.com/board"]
    .map((u, n) => ({ kind: "tool_start", id: "p" + n, name: "web_fetch", label: "Fetch",
                      args: { url: u }, target: u })),
  ...["https://apnews.com/", "https://www.reuters.com/world/",
      "https://www.bbc.com/news/world", "https://top.baidu.com/board"]
    .map((u, n) => ({ kind: "tool_result", id: "p" + n, is_error: n === 3,
                      summary: n === 3 ? u + " — HTTP 403" : u + " — 读了 2140 字",
                      detail: [] })),
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

// 出图那一屏
await cdp.call("Emulation.setDeviceMetricsOverride",
  { width: 1280, height: 1100, deviceScaleFactor: 1, mobile: false });
await cdp.call("Page.navigate", { url });
await sleep(1200);
const shotPath = await putImage();
await feed(JSON.parse(JSON.stringify(SHOT).replaceAll("@@SHOT@@", shotPath)));
await sleep(900);                     // 图是异步读回来的，等它落地
console.log("拍了", await cdp.shot("5_image", { width: 1280, height: 1100 }));

/* 图必须真的画出来了，而且只画了一次。
   零尺寸的图在截图里跟「没有这块」长得一模一样，只能靠量。 */
/* **只看这一屏喂进去的那张。** 每跑一次夹具就往工作区传一张图，
   上一轮的残留会让"该只有一张"挂掉 —— 是夹具不可重复，不是产品坏了。 */
const shots = await cdp.eval(`JSON.stringify([...document.querySelectorAll("figure.shot")]
  .filter((f) => f.querySelector("figcaption").textContent === ${JSON.stringify(shotPath)})
  .map((f) => {
  const img = f.querySelector("img");
  const r = img ? img.getBoundingClientRect() : { width: 0, height: 0 };
  return { w: Math.round(r.width), h: Math.round(r.height), failed: !!f.dataset.failed,
           cap: f.querySelector("figcaption").textContent };
}))`);
const rows = JSON.parse(shots);
/* 高度那道门要卡得高一点：**读不到的图也有个盒子** —— 浏览器给破图标留 26px 左右，
   卡在 24 上的话「图根本没读回来」会一路绿灯过去（夹具真这么骗过我一轮）。 */
if (rows.length !== 1 || rows[0].failed || rows[0].w < 64 || rows[0].h < 64) {
  console.error("出图那一屏不对（该只有一张、且看得见）：", shots);
  process.exitCode = 1;
} else {
  console.log(`出图 ${rows[0].w}×${rows[0].h}，正文里重述的那次没画第二遍`);
}

/* 并行那批：**每一块的标题必须各是各的**。用一个槽位的话会变成
   四行一样的标题配四个不同的结果 —— 用户拿截图来问过。 */
await cdp.call("Page.navigate", { url });
await sleep(1200);
await feed(PARALLEL);
await sleep(700);
console.log("拍了", await cdp.shot("9_parallel", { width: 1280, height: 900 }));
/* 只看这一批灌进去的那四条。**屏幕上可能还有别的** —— 夹具连的是一个真 agent，
   上一轮 input.mjs 起的活会把自己的工具行推过来（真被这个绊过一次）。 */
const pairs = JSON.parse(await cdp.eval(`JSON.stringify(
  [...document.querySelectorAll(".block--tool .tool")].map((t) => ({
    head: (t.querySelector(".tool__head") || t).textContent.trim().slice(0, 60),
    note: (t.querySelector(".tool__note") || {}).textContent || "",
  })).filter((r) => r.head.startsWith("Fetch(")))`));
const heads = new Set(pairs.map((p) => p.head));
if (pairs.length !== 4 || heads.size !== 4) {
  console.error("并行那批的标题没有各归各（该四块四个标题）：", JSON.stringify(pairs));
  process.exitCode = 1;
} else if (!pairs.every((p) => p.head.includes(p.note.split(" —")[0].trim()))) {
  console.error("结果挂错了块：", JSON.stringify(pairs));
  process.exitCode = 1;
} else {
  console.log("并行四条各归各");
}

/* 窗口缩到最小（桌面端 minWidth 720）时侧栏会浮起来盖住内容。
   **要自动收起**，不然正文被压在下面而且没有任何地方能关掉 ——
   量的是侧栏宽度，不是"看着还行"。 */
for (const [w, want] of [[720, 0], [760, 240]]) {
  await cdp.call("Emulation.setDeviceMetricsOverride",
    { width: w, height: 800, deviceScaleFactor: 1, mobile: false });
  await sleep(400);
  const got = JSON.parse(await cdp.eval(`JSON.stringify({
    side: Math.round(document.getElementById("side").getBoundingClientRect().width),
    body: Math.round(document.querySelector(".block__body").getBoundingClientRect().x),
    scrim: !document.getElementById("scrim").hidden,
  })`));
  if (got.side !== want || (w === 720 && got.body < 8)) {
    console.error(`${w}px 下侧栏该是 ${want} 宽：`, JSON.stringify(got));
    process.exitCode = 1;
  } else {
    console.log(`${w}px 侧栏 ${got.side} 宽，正文起点 ${got.body}`);
  }
}
await cdp.call("Emulation.setDeviceMetricsOverride",
  { width: 1280, height: 900, deviceScaleFactor: 1, mobile: false });
await sleep(300);

/* 一页只该有一条纵线：代码块不许再画自己的左竖线（meta-rail 就在左边 16px）。 */
const lines = JSON.parse(await cdp.eval(`JSON.stringify(
  [...document.querySelectorAll(".prose pre")].map((p) =>
    getComputedStyle(p).borderLeftWidth))`));
if (lines.some((w) => w !== "0px")) {
  console.error("代码块又画了自己的竖线，会跟 meta-rail 并排：", JSON.stringify(lines));
  process.exitCode = 1;
} else {
  console.log("代码块没有多余的竖线");
}

/* 技能与工具那一屏。**不灌合成事件** —— 面板一打开就向服务端要 catalog，
   合成的会被真推送盖掉（CLAUDE.md 里记过这个坑）。所以这里靠夹具跑在一份
   备好技能的 XDG_CONFIG_HOME 上，拍的就是真数据。 */
await cdp.call("Emulation.setDeviceMetricsOverride",
  { width: 1280, height: 900, deviceScaleFactor: 1, mobile: false });
await cdp.call("Page.navigate", { url });
/* **先等真的连上。** 起 MCP 服务器要好几秒，hello 排在那之后；
   连上之前 send() 是空跑的，面板会一直空着 —— 第一版就是这么骗了我一轮。 */
/* 看状态灯，**不要看 `online`** —— 那是个顶层 `let`，在 classic script 里
   落在 script scope 上，CDP 的 eval 按裸名字取不到（会抛 ReferenceError）。 */
for (let n = 0; n < 60; n++) {
  // **要挡住 null**：navigate 刚发出去时文档还是上一页/空页，取不到那个元素就抛
  const lit = await cdp.eval(
    `(document.getElementById("dot") || {}).dataset ` +
    `? document.getElementById("dot").dataset.state : ""`);
  if (lit === "ready") break;
  await sleep(250);
}
await cdp.eval(`showShelf(true); true`);
await sleep(900);
console.log("拍了", await cdp.shot("6_shelf", { width: 1280, height: 900 }));

/* 工作台那一行不能被 bidi 重排。`direction: rtl` 那个"从左边裁"的老套路会把
   开头的 `~/` 挪到视觉末尾，`~/feiyuwork` 显示成 `feiyuwork/~` —— 用户拿截图来问过。
   量的是渲染顺序，不是 textContent（那个是对的，坏的只是显示）。 */
const deskShown = await cdp.eval(`(() => {
  const el = document.getElementById("desk-path");
  el.textContent = "~/feiyuwork";
  return getComputedStyle(el).direction + "|" + el.textContent;
})()`);
if (!deskShown.startsWith("ltr|")) {
  console.error("工作台那一行的书写方向不对（会把 ~/ 甩到末尾）：", deskShown);
  process.exitCode = 1;
} else {
  console.log("工作台路径按 ltr 排");
}

/* 设置那一页（面板打开时就是它）。改动一律走斜杠命令，所以这里除了看画得对不对，
   还要**真按一下**，确认发出去的是那条命令。 */
const conf = JSON.parse(await cdp.eval(`JSON.stringify({
  tabs: [...document.querySelectorAll("#shelf-tabs .tab")].map((t) => t.textContent.replace(/\\d+$/, "")),
  rows: [...document.querySelectorAll("#shelf-list .shelf__name")].map((e) => e.textContent),
  now: [...document.querySelectorAll("#shelf-list .shelf__now")].map((e) => e.textContent),
  pane: (document.querySelector("#shelf-pane h2") || {}).textContent || "",
  opts: [...document.querySelectorAll(".opt__name")].map((e) => e.textContent),
  marked: [...document.querySelectorAll(".opt--now .opt__name")].map((e) => e.textContent),
})`));
console.log("拍了", await cdp.shot("11_settings", { width: 1280, height: 900 }));
if (conf.tabs[0] !== "通用" || conf.rows.length < 8 || conf.pane !== "权限模式"
    || conf.opts.length !== 4 || conf.marked.length !== 1) {
  console.error("设置那一页不对：", JSON.stringify(conf));
  process.exitCode = 1;
} else {
  console.log(`设置 ${conf.rows.length} 项，当前是「${conf.marked[0]}」，右边 ${conf.opts.length} 个可选`);
}
// 每一项右边都得显示当前值，空的说明 meta() 算错了
if (conf.now.some((t) => !t.trim())) {
  console.error("有项目没显示当前值：", JSON.stringify(conf.now));
  process.exitCode = 1;
}
// 真按一下「只读」，看发出去的是不是 /mode readonly
await cdp.eval(`window.__sent = []; const real = WebSocket.prototype.send;
  WebSocket.prototype.send = function (d) { window.__sent.push(d); return real.call(this, d); }; true`);
await cdp.eval(`[...document.querySelectorAll(".opt")]
  .find((b) => b.textContent.includes("只读")).click(); true`);
await sleep(600);
const sent = await cdp.eval(`JSON.stringify(window.__sent)`);
if (!sent.includes("/mode readonly")) {
  console.error("点了「只读」没发出 /mode readonly：", sent);
  process.exitCode = 1;
} else {
  console.log("点一下设置项 = 发一条斜杠命令");
}
await cdp.eval(`[...document.querySelectorAll(".opt")]
  .find((b) => b.textContent.includes("默认")).click(); true`);
await sleep(500);

await cdp.eval(`document.querySelector('[data-tab="skills"]').click(); true`);
await sleep(500);
const shelfRows = await cdp.eval(`JSON.stringify({
  tabs: [...document.querySelectorAll("#shelf-tabs .tab")].map((t) => t.textContent),
  rows: [...document.querySelectorAll("#shelf-list .shelf__row")].map((r) => ({
    name: r.querySelector(".shelf__name").textContent,
    on: r.querySelector(".sw").getAttribute("aria-checked"),
  })),
  pane: (document.querySelector("#shelf-pane h2") || {}).textContent || "",
  body: (document.querySelector(".shelf__body-text") || {}).textContent || "",
  sheetHidden: sheet.hidden, composerHidden: document.getElementById("composer").hidden,
})`);
const shelf = JSON.parse(shelfRows);
const bad = [];
if (!shelf.rows.length) bad.push("技能一份都没列出来");
if (!shelf.rows.some((r) => r.on === "false")) bad.push("关掉的那份没显示成关着");
if (!shelf.pane) bad.push("详情栏是空的");
if (!shelf.body || shelf.body === "读正文…") bad.push("技能正文没取回来");
if (!shelf.sheetHidden || !shelf.composerHidden) bad.push("面板开着时对话那一屏没收起来");
if (bad.length) {
  console.error("技能面板：", bad.join("；"), shelfRows);
  process.exitCode = 1;
} else {
  console.log(`技能面板 ${shelf.rows.length} 份，详情是「${shelf.pane}」，正文取回来了`);
}

// 工具集那一页：开关必须是看得见的方块，不是零尺寸
await cdp.eval(`document.querySelector('[data-tab="tools"]').click(); true`);
await sleep(400);
console.log("拍了", await cdp.shot("7_shelf_tools", { width: 1280, height: 900 }));
const sw = await cdp.eval(`JSON.stringify((() => {
  const s = document.querySelector("#shelf-list .sw");
  if (!s) return { w: 0, h: 0 };
  const r = s.getBoundingClientRect();
  const off = [...document.querySelectorAll("#shelf-list .shelf__row")]
    .filter((x) => x.dataset.on === "0").map((x) => x.querySelector(".shelf__name").textContent);
  const locked = [...document.querySelectorAll("#shelf-list .sw[disabled]")].length;
  return { w: Math.round(r.width), h: Math.round(r.height), off, locked };
})())`);
const swBox = JSON.parse(sw);
if (swBox.w < 20 || swBox.h < 12 || !swBox.locked || !swBox.off.length) {
  console.error("工具集那一页不对（开关要看得见、要有关掉的和锁住的）：", sw);
  process.exitCode = 1;
} else {
  console.log(`开关 ${swBox.w}×${swBox.h}，关掉的 ${swBox.off}，锁住的 ${swBox.locked} 个`);
}

// MCP 那一页
await cdp.eval(`document.querySelector('[data-tab="mcp"]').click(); true`);
await sleep(400);
console.log("拍了", await cdp.shot("8_shelf_mcp", { width: 1280, height: 900 }));

// 档案那一页：清单不给开关（那是单选不是双态），详情栏能改能存
await cdp.eval(`document.querySelector('[data-tab="profiles"]').click(); true`);
await sleep(600);
console.log("拍了", await cdp.shot("10_profiles", { width: 1280, height: 900 }));
const souls = JSON.parse(await cdp.eval(`JSON.stringify({
  rows: [...document.querySelectorAll("#shelf-list .shelf__row")]
          .map((r) => r.querySelector(".shelf__name").textContent),
  switches: document.querySelectorAll("#shelf-list .sw").length,
  soul: (document.querySelector(".soul") || {}).value || "",
  acts: [...document.querySelectorAll(".acts .act")].map((b) => b.textContent),
})`));
if (souls.rows.length < 3 || souls.switches !== 0 || !souls.soul || souls.acts.length !== 3) {
  console.error("档案那一页不对（该有清单+新建行、没有开关、有正文和三个按钮）：",
                JSON.stringify(souls));
  process.exitCode = 1;
} else {
  console.log(`档案 ${souls.rows.length - 1} 份，正文取回来了，按钮 ${souls.acts.join("/")}`);
}

/* 档案的增删改：新建 → 改正文 → 存 → 删。**这条只能在 DOM 里验** ——
   面板那几个动作各自要发对 op、发对名字，而"存到了另一个文件上"这种错
   单测看不出来（清单和详情用的是同一个名字来源才行）。 */
await cdp.eval(`document.querySelector(".shelf__row--new").click(); true`);
await sleep(200);
await cdp.eval(`(() => {
  const box = document.querySelector(".newname");
  box.value = "夹具新建的";
  box.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
})(); true`);
await sleep(700);
const born = JSON.parse(await cdp.eval(`JSON.stringify({
  rows: [...document.querySelectorAll("#shelf-list .shelf__name")].map((e) => e.textContent),
  pane: (document.querySelector("#shelf-pane h2") || {}).textContent || "",
})`));
if (!born.rows.includes("夹具新建的") || born.pane !== "夹具新建的") {
  console.error("新建档案不对：", JSON.stringify(born));
  process.exitCode = 1;
} else {
  console.log("新建档案，详情栏跟着跳过去了");
}

await cdp.eval(`(() => {
  const box = document.querySelector(".soul");
  box.value = "夹具改过的人格";
  box.dispatchEvent(new Event("input", { bubbles: true }));
  [...document.querySelectorAll(".acts .act")].find((b) => b.textContent === "保存").click();
})(); true`);
await sleep(800);
const saved = await cdp.eval(`(document.querySelector(".soul") || {}).value || ""`);
if (saved !== "夹具改过的人格") {
  console.error("存完读回来的不是刚存的：", JSON.stringify(saved));
  process.exitCode = 1;
} else {
  console.log("改正文存得回去、读得回来");
}

// 删：第一下是"真的删掉？"，第二下才删 —— 真数据值得问一句
await cdp.eval(`[...document.querySelectorAll(".acts .act")].find((b) => b.textContent === "删掉").click(); true`);
await sleep(200);
const asking = await cdp.eval(`[...document.querySelectorAll(".acts .act")].some((b) => b.textContent === "真的删掉？")`);
await cdp.eval(`[...document.querySelectorAll(".acts .act")].find((b) => b.textContent === "真的删掉？").click(); true`);
await sleep(800);
const left = JSON.parse(await cdp.eval(
  `JSON.stringify([...document.querySelectorAll("#shelf-list .shelf__name")].map((e) => e.textContent))`));
if (!asking || left.includes("夹具新建的")) {
  console.error("删档案不对（该先问一句、再真删）：", asking, JSON.stringify(left));
  process.exitCode = 1;
} else {
  console.log("删档案：先问一句，再真删掉");
}

// esc 退回对话 —— 面板开着时它不该是"打断这一轮"
await cdp.eval(`document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })); true`);
await sleep(300);
const backed = await cdp.eval(`JSON.stringify({ shelf: shelf0(), sheet: sheet.hidden })`
  .replace("shelf0()", "document.getElementById('shelf').hidden"));
if (JSON.parse(backed).shelf !== true || JSON.parse(backed).sheet !== false) {
  console.error("esc 没退回对话：", backed);
  process.exitCode = 1;
} else {
  console.log("esc 退回对话");
}

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
