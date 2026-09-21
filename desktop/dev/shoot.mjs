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
 *   2_ask   —— 停在「要你点头」那张卡上（日常那档，中性灰）
 *   2b_danger—— 危险操作那档（rm -rf），全站唯一该见红的地方
 *   0_hello —— 一条消息都没有时的「开始」那块
 *   13_anthropic—— Anthropic 暖色主题（跟复古并存，底色不能撞）
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

/* 危险那一档。**日常确认和这一档必须一眼分得开** —— 要是两张图看着一样，
   那就是红又变回背景噪音了，`looksDangerous()` 白写。 */
const DANGER_ASK = [
  { kind: "ask", id: 9002, type: "approve", tool: "bash", label: "Bash",
    target: "", args: { command: "rm -rf build/ dist/ node_modules/" },
    rule: "Bash(rm:*)" },
];

const page = await targets();
const ws = new WebSocket(page.webSocketDebuggerUrl);
await new Promise((ok, bad) => { ws.onopen = ok; ws.onerror = bad; });
const cdp = new Cdp(ws);

await cdp.call("Page.enable");
await cdp.call("Runtime.enable");
await cdp.call("Page.navigate", { url });
await sleep(1500);
/* **先把本机偏好清干净。** Chrome 的 profile 目录是跨次保留的，
   上一轮切成复古主题会留到下一轮，于是"换了主题颜色却没变"——
   夹具自己骗了我一轮（input.mjs 早就这么做了，这边漏了）。 */
await cdp.eval(`try { localStorage.clear(); } catch {} setTheme("light"); true`);

const errors = [];
await cdp.eval(`window.__errs = []; addEventListener("error", (e) => __errs.push(String(e.message)));
  addEventListener("unhandledrejection", (e) => __errs.push(String(e.reason))); true`);

const feed = (events) =>
  cdp.eval(`(${JSON.stringify(events)}).forEach(handle); true`);

/* **等服务端那一波推完再灌。** 连上时前端会发 `/help` / `status` / `sessions` /
   `bots`，而 agent 冷启动要载 MCP，几秒才回得来 —— 这几条回话（还有它盘上真有
   会话时跟着来的 `cleared` / `opened`）会落在 `feed()` 之后，把刚灌进去的那一屏
   整个换掉。
   表现很难认：`1_flow.png` 拍到的是**上一轮夹具留下的真实对话**，接着那条
   量 diff 颜色的断言在一个不存在的元素上抛 `getComputedStyle ... not of type
   Element`，看着像"前端坏了"。而且是间歇的，跑五次挂一次。
   `input.mjs` 早就这么等了（「要等 agent 回话，不是等 DOM」），这边漏了。 */
for (let i = 0; i < 60; i++) {
  if (await cdp.eval(`commands.length > 0`)) break;
  await sleep(500);
}
await sleep(800);                       // sessions / bots 那几条也让它落地
await cdp.eval(`stream.replaceChildren(); true`);

await feed(FLOW);
console.log("拍了", await cdp.shot("1_flow"));

/* **行长按内容类型分两档，这一条要量不要看。**
   散文（段落 / 列表 / 「你」那句）吃 --measure，代码块 / diff / 表格照旧吃满
   --content。不分档的话正文一行 51 个汉字 —— 眼睛看"有点宽"说不清，量得清。
   判据：正文 ≤ 40 字（中文可读上限），而代码块必须**明显更宽**，
   否则就是有人把 --content 一起缩了，diff 会折得很碎。 */
const wide = JSON.parse(await cdp.eval(`JSON.stringify((() => {
  const cjk = (el) => { const b = el.getBoundingClientRect();
    return Math.round(b.width / parseFloat(getComputedStyle(el).fontSize)); };
  const w = (el) => Math.round(el.getBoundingClientRect().width);
  const pro = document.querySelector(".block:not(.block--you) .prose > p");
  const you = document.querySelector(".block--you .block__body");
  const pre = document.querySelector(".prose pre");
  return { prose: cjk(pro), you: cjk(you), proseW: w(pro), preW: pre ? w(pre) : 0 };
})())`));
if (wide.prose > 40 || wide.you > 40 || wide.preW <= wide.proseW) {
  console.error("行长那两档不对（正文该 ≤40 字、代码块该更宽）：", JSON.stringify(wide));
  process.exitCode = 1;
} else {
  console.log(`正文 ${wide.prose} 字 / 「你」${wide.you} 字，代码块 ${wide.preW} 宽`);
}

/* **敲进去的字和发出来的字要在同一条竖线上。**
   输入区第一列吃 --rail，＋ 靠右贴着 rail 那条线，textarea 的文字起点
   就落回 .block__body 上。差过 1px 就是有人动了那一列。 */
const lineUp = JSON.parse(await cdp.eval(`JSON.stringify((() => {
  const ta = document.getElementById("input");
  const body = document.querySelector(".block__body");
  return { input: Math.round(ta.getBoundingClientRect().x
                             + parseFloat(getComputedStyle(ta).paddingLeft)),
           body: Math.round(body.getBoundingClientRect().x) };
})())`));
if (Math.abs(lineUp.input - lineUp.body) > 1) {
  console.error("输入框里的字跟正文没对齐：", JSON.stringify(lineUp));
  process.exitCode = 1;
} else {
  console.log(`敲的字和正文都起于 ${lineUp.body}`);
}

/* 一页只该有一条纵线：代码块不许再画自己的左竖线（meta-rail 就在左边 16px）。
   **必须在有代码块的那一屏上量。** 这一条原来跑在并行那屏（全是工具调用、
   一个 `.prose pre` 都没有），`[].some()` 永远是 false —— 绿了很久，
   其实一次都没验过。挪到 1_flow 之后，并加一条"至少得有一个"的守卫。 */
const lines = JSON.parse(await cdp.eval(`JSON.stringify(
  [...document.querySelectorAll(".prose pre")].map((p) =>
    getComputedStyle(p).borderLeftWidth))`));
if (lines.length === 0 || lines.some((w) => w !== "0px")) {
  console.error("代码块的竖线这条没验成（该有代码块、且都不画线）：", JSON.stringify(lines));
  process.exitCode = 1;
} else {
  console.log(`代码块 ${lines.length} 块，没有多余的竖线`);
}


/* **每个主题下 diff 的加号减号都得分得开。** 复古那套整页暖色，
   加号原来直接用 --accent（赭色），跟减号的砖红糊成一片 —— 眼睛看
   "都是红的"说不清差多少，量 RGB 距离说得清。 */
for (const t of ["light", "retro"]) {
  await cdp.eval(`setTheme("${t}"); true`);
  await sleep(250);
  const pair = JSON.parse(await cdp.eval(`JSON.stringify({
    plus: getComputedStyle(document.querySelector('.tool__lines li[data-kind="+"]')).color,
    minus: getComputedStyle(document.querySelector('.tool__lines li[data-kind="-"]')).color,
  })`));
  const rgb = (c) => (c.match(/\d+/g) || [0, 0, 0]).map(Number);
  const [a, b] = [rgb(pair.plus), rgb(pair.minus)];
  const far = Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);
  if (far < 90) {
    console.error(`${t} 主题下 diff 的加减号太像了（距离 ${Math.round(far)}）：`, JSON.stringify(pair));
    process.exitCode = 1;
  } else {
    console.log(`${t} 主题 diff 加减号色距 ${Math.round(far)}`);
  }
}
await cdp.eval(`setTheme("light"); true`);


// 一条消息都没有时那块「开始」——**这是第一次打开这个应用看到的画面**，
// 以前它是纯白一片，没有任何东西说明能干什么。
// **先重新导航**：上面 FLOW 那一串还留在流里，不清掉这块根本不会出现
await cdp.call("Page.navigate", { url });
await sleep(1200);
await cdp.call("Emulation.setDeviceMetricsOverride", {
  width: 1280, height: 900, deviceScaleFactor: 2, mobile: false,
});
/* **光 sleep 不够。** 这一屏的前提是"流里一条都没有"，而重连之后 agent 那侧
   还会把这一轮里我们自己点出来的东西推过来（改模式、点机器人那些 note）——
   落在 sleep 之后的话，这块「开始」就永远不出现，报出来像是它坏了。
   跟文件开头那段「要等 agent 回话，不是等 DOM」是同一条，这儿一直漏着。 */
for (let i = 0; i < 60; i++) {
  if (await cdp.eval(`commands.length > 0`)) break;
  await sleep(500);
}
await sleep(800);
await feed([{ kind: "cleared" }]);       // 清流 + 把「开始」摆回去，走真实那条路
await sleep(300);
console.log("拍了", await cdp.shot("0_hello", { height: 900, beyond: false }));
const 例子数 = await cdp.eval(`document.querySelectorAll(".hello__pick").length`);
if (!例子数) throw new Error("空对话时「开始」那块没出现");
// 点一下要填进输入框、而且不能直接发出去
await cdp.eval(`document.querySelector(".hello__pick").click(); true`);
const 填了 = await cdp.eval(`document.getElementById("input").value`);
if (!填了) throw new Error("点了例子没填进输入框");
console.log(`空状态 ${例子数} 个例子，点一下填进去：「${填了.slice(0, 12)}…」`);
await cdp.eval(`document.getElementById("input").value = ""; true`);

await feed(ASK);
// 滚的是 .sheet 那个容器，不是 window —— 页面本身不滚，头和尾是钉住的
await cdp.call("Emulation.setDeviceMetricsOverride", {
  width: 1280, height: 1000, deviceScaleFactor: 2, mobile: false,
});
await sleep(300);
await cdp.eval(`sheet.scrollTop = sheet.scrollHeight; true`);
console.log("拍了", await cdp.shot("2_ask", { height: 1000, beyond: false }));

// 两档的底色必须不一样，光看图容易放过去，所以直接量
const 日常底 = await cdp.eval(
  `getComputedStyle(document.querySelector(".ask")).backgroundColor`);

await cdp.call("Page.navigate", { url });
await sleep(1200);
await feed(DANGER_ASK);
await sleep(300);
await cdp.eval(`sheet.scrollTop = sheet.scrollHeight; true`);
console.log("拍了", await cdp.shot("2b_danger", { height: 700, beyond: false }));
const 危险底 = await cdp.eval(
  `getComputedStyle(document.querySelector(".ask")).backgroundColor`);
const 有红字 = await cdp.eval(
  `!!document.querySelector(".ask--danger")`);
if (日常底 === 危险底) throw new Error(`两档确认卡底色一样（${日常底}）—— 危险分级没生效`);
if (!有红字) throw new Error("rm -rf 没被判成危险");
console.log(`确认卡两档：日常 ${日常底} / 危险 ${危险底}`);

// 扩大授权的两个按钮要被顶到右边（`:first-of-type` 那个坑就是这么漏掉的）
const 顶开 = await cdp.eval(
  `(() => { const b = document.querySelector(".ask__row .btn--wide");
            return b ? getComputedStyle(b).marginLeft : "没有"; })()`);
if (顶开 === "0px" || 顶开 === "没有") {
  throw new Error(`「本轮都允许」没被顶到右边（margin-left: ${顶开}）`);
}
console.log("扩大授权的按钮顶开了", 顶开);

await cdp.call("Page.navigate", { url });
await sleep(1200);
await feed(FLOW.slice(0, 8));
console.log("拍了", await cdp.shot("3_narrow", { width: 390, height: 1200 }));

/* **这一档没有 rail 那一列，输入区左边那个 ＋ 不许跑出屏幕。**
   宽屏下它靠右贴着 rail 那条线（`justify-self: end`）；窄屏 `--rail` 归 0，
   不把第一列退回 `auto` 的话 ＋ 的右边缘就被钉在 0 上，整个按钮溢到界外
   —— 390px 下只剩半个加号，这一条就是那次逮到的。 */
const clip = JSON.parse(await cdp.eval(`JSON.stringify((() => {
  const b = document.querySelector(".composer__row > .chip--attach").getBoundingClientRect();
  return { x: Math.round(b.x), w: Math.round(b.width) };
})())`));
if (clip.x < 0 || clip.w < 24) {
  console.error("390px 下输入区那个 ＋ 跑出屏幕了：", JSON.stringify(clip));
  process.exitCode = 1;
} else {
  console.log(`390px 下 ＋ 在 ${clip.x}，${clip.w} 宽`);
}

// 纯对话那一屏单独拍一张 —— 它跟有工具调用的那屏排版重点完全不同
await cdp.call("Emulation.setDeviceMetricsOverride",
  { width: 1280, height: 900, deviceScaleFactor: 1, mobile: false });
await cdp.call("Page.navigate", { url });
await sleep(1200);
await feed(TALK);
console.log("拍了", await cdp.shot("4_talk", { width: 1280, height: 900 }));

/* ── 想的过程 / 等模型 / 长输出折起来（0.77.0）──────────────────
   这三样都只在**时间维度**上成立：轨迹是边想边长的、等待块是"还什么都没发生"、
   折叠要点一下才展开。所以这一屏是喂一串事件再点几下，不是摆个静态样子。 */
const THINK = [
  { kind: "status", ready: true, mode: "default", busy: true, workspace: "/home/you/blog",
    model: "deepseek-v4-pro", name: "deepseek-v4-pro",
    context: 21400, context_limit: 128000 },
  { kind: "turn_start", text: "这份 build.mjs 为什么在 CI 上比本机慢十倍？" },
  { kind: "thinking", size: 40, delta: "先想清楚问的是什么。\n\n" },
  { kind: "thinking", size: 140,
    delta: "「CI 上慢十倍」有三种常见成因：缓存没命中、并发被限成单核、" },
  { kind: "thinking", size: 260,
    delta: "或者它在 CI 上走了另一条代码路径。\n\n先看有没有缓存配置，再看 CPU 配额。" },
  { kind: "text", delta: "我先看一眼 CI 的配置和这份构建脚本。\n" },
  { kind: "tool_start", name: "bash", label: "Bash", args: { command: "npm run build" },
    target: "npm run build" },
  { kind: "tool_result", summary: "退出码 0 · 38.4s", is_error: false,
    detail: Array.from({ length: 30 }, (_, n) => ({ kind: "", text: `  building page ${n + 1}/30 …` })) },
];

await cdp.call("Emulation.setDeviceMetricsOverride",
  { width: 1280, height: 1100, deviceScaleFactor: 1, mobile: false });
await cdp.call("Page.navigate", { url });
await sleep(1200);
await feed(THINK);
await sleep(300);

/* 轨迹必须：在想的时候是**展开**的、想完自己**收起来**、原文真的进去了。
   收不起来的话事后那一大段横在对话中间，把真正说的话挤下去。 */
const trace = JSON.parse(await cdp.eval(`JSON.stringify((() => {
  const box = document.querySelector(".think");
  return box ? { open: box.dataset.open, done: box.dataset.done || "",
                 head: box.querySelector(".think__what").textContent,
                 chars: box.querySelector(".think__body .prose").textContent.length } : null;
})())`));
if (!trace || trace.open !== "0" || trace.done !== "1" || trace.chars < 40) {
  console.error("想的轨迹不对（该有、该收起来、原文该进去）：", JSON.stringify(trace));
  process.exitCode = 1;
} else {
  console.log(`想的轨迹：${trace.head}，${trace.chars} 字，想完收起来了`);
}

/* 点开来拍，收着拍等于没验。**顺带把"想完之后还点得动"钉住** ——
   这一下真的抓到过一个 bug：折叠按钮的闭包抓的是模块上那个 `think`，
   而 `closeThinking()` 会把它置成 null，于是想完再点就在 null 上取属性。
   而按钮的 onclick 抛异常是**不会让 click() 这条 eval 失败的**，只能看结果。 */
const 点开 = await cdp.eval(`(() => {
  const head = document.querySelector(".think__head");
  head.click();
  const box = head.closest(".think");
  return box.dataset.open + "/" + head.getAttribute("aria-expanded");
})()`);
if (点开 !== "1/true") {
  console.error("想完之后轨迹点不开了：", 点开);
  process.exitCode = 1;
}
await sleep(500);

/* 长输出默认只露头几行。**折的只能是 detail，摘要那行不许折** ——
   折起来就等于把"发生了什么"藏了。 */
const fold = JSON.parse(await cdp.eval(`JSON.stringify((() => {
  const box = document.querySelector(".fold");
  if (!box) return null;
  const list = box.querySelector(".tool__lines");
  const shut = list.getBoundingClientRect().height;
  box.querySelector(".fold__more").click();
  return { shut: Math.round(shut), rows: list.children.length,
           note: !!document.querySelector(".tool__note").offsetHeight,
           more: box.querySelector(".fold__more").textContent };
})())`));
await sleep(500);
const opened = await cdp.eval(
  `Math.round(document.querySelector(".fold .tool__lines").getBoundingClientRect().height)`);
if (!fold || !fold.note || fold.shut > 160 || opened <= fold.shut + 40) {
  console.error("长输出折叠不对（摘要该露着、折着该矮、展开该变高）：",
    JSON.stringify({ ...fold, opened }));
  process.exitCode = 1;
} else {
  console.log(`长输出 ${fold.rows} 行，折着 ${fold.shut}px（${fold.more}）→ 展开 ${opened}px`);
}

/* 拍之前把长输出**收回去**：刚才那一下是为了验展开，而这张图要一眼看全
   「想的轨迹 / 工具 / 还在等」三样，三十行摊开会把前两样顶出画面。 */
await cdp.eval(`document.querySelector(".fold__more").click(); true`);
await sleep(500);
await cdp.eval(`sheet.scrollTop = sheet.scrollHeight; true`);
console.log("拍了", await cdp.shot("15_think", { width: 1280, height: 1100, beyond: false }));

/* 代码块：行号必须**跟代码对齐**（两列同一个行高），而且**不能进 <code>** ——
   进去了复制出来每行都顶着一个数字。这两条都得量，看图看不出来。 */
await cdp.call("Page.navigate", { url });
await sleep(1200);
await feed(FLOW);
const code = JSON.parse(await cdp.eval(`JSON.stringify((() => {
  const fig = document.querySelector("figure.code");
  if (!fig) return null;
  const nums = fig.querySelector(".code__nums");
  const codeEl = fig.querySelector("code");
  const line = getComputedStyle(codeEl).lineHeight;
  return { nums: nums ? nums.textContent.split("\\n").length : 0,
           rows: codeEl.textContent.split("\\n").length,
           sameLead: nums ? getComputedStyle(nums).lineHeight === line : false,
           dirty: /^\\s*\\d/.test(codeEl.textContent),
           copy: !!fig.querySelector("[data-copy]") };
})())`));
if (!code || !code.copy || code.nums !== code.rows || !code.sameLead || code.dirty) {
  console.error("代码块不对（行号数该等于行数、行高该一致、正文里不许有行号）：",
    JSON.stringify(code));
  process.exitCode = 1;
} else {
  console.log(`代码块 ${code.rows} 行，行号对得上、没掺进正文，有复制按钮`);
}

/* 页签底下那条线是**一条会滑的线**，不是每个页签各自的边。
   量两件事：量到了宽度（没量到就是没画），切换之后位置真的变了。 */
const ind = JSON.parse(await cdp.eval(`JSON.stringify((() => {
  const box = document.getElementById("side-tabs");
  const read = () => ({ x: box.style.getPropertyValue("--ind-x"),
                        w: box.style.getPropertyValue("--ind-w") });
  const before = read();
  box.querySelector('[data-side="bots"]').click();
  const after = read();
  box.querySelector('[data-side="talks"]').click();
  return { before, after, on: box.dataset.ind || "",
           edge: getComputedStyle(box.querySelector('[data-side="talks"]')).borderBottomColor };
})())`));
if (ind.on !== "1" || !parseFloat(ind.before.w) || ind.before.x === ind.after.x) {
  console.error("页签的滑线不对（该量到宽度、切换后该换位置）：", JSON.stringify(ind));
  process.exitCode = 1;
} else {
  console.log(`页签滑线 ${ind.before.w} 宽，切过去 ${ind.before.x} → ${ind.after.x}`);
}

/* 划一段 → 浮出几个动词 → 点了**填进输入框不发出去**。
   最后那半条是这个功能的取舍所在，所以必须断言"没发出去"。 */
/* **异步的那段要让 `JSON.stringify` 在 await 之后跑。** 写成
   `JSON.stringify(异步函数())` 的话拿到的是个 Promise，序列化出来是 `{}` ——
   断言会挂在一个什么都没量到的对象上。 */
const pick = JSON.parse(await cdp.eval(`(async () => {
  const p = document.querySelector(".block:not(.block--you) .prose p");
  const sel = getSelection();
  sel.removeAllRanges();
  const r = document.createRange();
  r.selectNodeContents(p);
  sel.addRange(r);
  document.dispatchEvent(new MouseEvent("mouseup"));
  await new Promise((ok) => setTimeout(ok, 50));
  const bar = document.querySelector(".pick-bar");
  const shown = !bar.hidden;
  const box = bar.getBoundingClientRect();
  bar.querySelector("[data-pick]").click();
  return JSON.stringify({ shown, x: Math.round(box.x), y: Math.round(box.y),
    gone: bar.hidden, typed: input.value.length, quoted: input.value.includes(">") });
})()`));
if (!pick.shown || pick.x < 0 || pick.y < 0 || !pick.gone || !pick.typed || !pick.quoted) {
  console.error("划词那条不对（该浮出来、该在视口里、点完该填进输入框并引起来）：",
    JSON.stringify(pick));
  process.exitCode = 1;
} else {
  console.log(`划词浮条在 (${pick.x}, ${pick.y})，点完填进输入框 ${pick.typed} 字`);
}
await cdp.eval(`input.value = ""; getSelection().removeAllRanges(); true`);

// 出图那一屏
await cdp.call("Emulation.setDeviceMetricsOverride",
  { width: 1280, height: 1100, deviceScaleFactor: 1, mobile: false });
await cdp.call("Page.navigate", { url });
await sleep(1200);
const shotPath = await putImage();
await feed(JSON.parse(JSON.stringify(SHOT).replaceAll("@@SHOT@@", shotPath)));
/* 图是异步读回来的（走 invoke / fetch 再转 blob），**要等它真的落地，不能固定 sleep**。
   原来是 `sleep(900)`，机器一忙就不够 —— 表现是下面那条断言拿到 `[]`，
   看着像"出图坏了"，其实只是还没画完。跟 input.mjs 里那句「要等 agent 回话，
   不是等 DOM」是同一条：**别把断言挂在一个还没发生的事情上**。 */
for (let i = 0; i < 40; i++) {
  const ok = await cdp.eval(`[...document.querySelectorAll("figure.shot img")]
    .some((im) => im.getBoundingClientRect().width > 64)`);
  if (ok) break;
  await sleep(250);
}
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


/* 机器人：跟"会话"平级摆在侧栏顶上，**不在设置里**。
   点一个 = 开一段新对话（发 op:bot do:start），点「改」= 进编辑器那一屏。 */
/* **先切页签，再灌。** 切过去会向服务端要一次真列表（它盘上是空的），
   反过来的话那份真推送会把合成数据盖掉 —— CLAUDE.md 里记过这个坑。 */
await cdp.eval(`document.querySelector('[data-side="bots"]').click(); true`);
await sleep(600);
await cdp.eval(`handle({kind:"bots", current:"", rows:[
  {name:"代码评审", description:"只挑毛病", model:"deepseek-v4-pro", mode:"readonly", on:false},
  {name:"耐心老师", description:"讲清楚为什么", model:"", mode:"", on:false}]}); true`);
await sleep(400);
console.log("拍了", await cdp.shot("14_bots", { width: 1280, height: 900 }));
const zoo = JSON.parse(await cdp.eval(`JSON.stringify({
  tabs: [...document.querySelectorAll(".side__tab")].map((t) => t.textContent),
  rows: [...document.querySelectorAll("#bots .talk__title")].map((e) => e.textContent),
  talksHidden: document.getElementById("side-talks").hidden,
  // 会话搜索框在机器人页签下不该还挂着 —— 它搜的是会话
  findHidden: document.getElementById("side-find-wrap").hidden,
})`));
if (zoo.tabs.join("/") !== "会话/机器人" || !zoo.rows.includes("代码评审")
    || !zoo.rows.some((t) => t.includes("新建")) || !zoo.talksHidden || !zoo.findHidden) {
  console.error("机器人那一栏不对：", JSON.stringify(zoo));
  process.exitCode = 1;
} else {
  console.log(`机器人 ${zoo.rows.length - 1} 个，会话那栏让开了`);
}

// 点一个 = 开一段新对话
await cdp.eval(`window.__sent = []; const real = WebSocket.prototype.send;
  WebSocket.prototype.send = function (d) { window.__sent.push(d); return real.call(this, d); }; true`);
await cdp.eval(`[...document.querySelectorAll("#bots .talk")]
  .find((t) => t.textContent.includes("代码评审")).click(); true`);
await sleep(400);
/* **解析出来再断言。** `JSON.stringify(数组)` 出来的字符串里引号是转义的，
   拿 `'"start"'` 去 includes 永远匹配不上 —— 夹具自己骗了我一轮。 */
const started = JSON.parse(await cdp.eval(`JSON.stringify(window.__sent)`))
  .map((x) => { try { return JSON.parse(x); } catch { return {}; } });
if (!started.some((m) => m.op === "bot" && m.do === "start" && m.name === "代码评审")) {
  console.error("点机器人没发 op:bot do:start：", JSON.stringify(started));
  process.exitCode = 1;
} else {
  console.log("点一个机器人 = 开一段新对话");
}

// 「改」进编辑器，而且**不该顺手把这个机器人开起来**
await cdp.eval(`handle({kind:"bots", current:"", rows:[
  {name:"代码评审", description:"只挑毛病", model:"m", mode:"readonly", on:false}]});
  window.__sent = []; true`);
await sleep(300);
await cdp.eval(`document.querySelector("#bots .bot__edit").click(); true`);
await sleep(500);
const shop = JSON.parse(await cdp.eval(`JSON.stringify({
  open: !document.getElementById("botshop").hidden,
  sheet: document.getElementById("sheet").hidden,
  title: (document.querySelector("#bot-pane h2") || {}).textContent || "",
  fields: [...document.querySelectorAll("#bot-pane .form__name")].map((e) => e.textContent),
  started: (window.__sent || []).some((x) => x.includes("start")),
})`));
if (!shop.open || !shop.sheet || shop.title !== "代码评审" || shop.fields.length < 5
    || shop.started) {
  console.error("机器人编辑器不对（点「改」不该顺手把它开起来）：", JSON.stringify(shop));
  process.exitCode = 1;
} else {
  console.log(`机器人编辑器：${shop.title}，${shop.fields.length} 个字段，没顺手开起来`);
}
await cdp.eval(`showBotShop(false); document.querySelector('[data-side="talks"]').click(); true`);
await sleep(300);

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
// 第一项是「外观」（本机偏好），权限模式在它下面 —— 先点过去再量
await cdp.eval(`[...document.querySelectorAll("#shelf-list .shelf__row")]
  .find((r) => r.textContent.includes("权限模式")).click(); true`);
await sleep(300);
const conf = JSON.parse(await cdp.eval(`JSON.stringify({
  tabs: [...document.querySelectorAll("#shelf-tabs .tab")].map((t) => t.textContent.replace(/\\d+$/, "")),
  rows: [...document.querySelectorAll("#shelf-list .shelf__name")].map((e) => e.textContent),
  now: [...document.querySelectorAll("#shelf-list .shelf__now")].map((e) => e.textContent),
  pane: (document.querySelector("#shelf-pane h2") || {}).textContent || "",
  opts: [...document.querySelectorAll(".opt__name")].map((e) => e.textContent),
  marked: [...document.querySelectorAll(".opt--now .opt__name")].map((e) => e.textContent),
})`));
console.log("拍了", await cdp.shot("11_settings", { width: 1280, height: 900 }));
if (conf.tabs[0] !== "通用" || conf.rows.length < 9 || conf.pane !== "权限模式"
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

/* 模型那一项：能列、能开表单。**夹具连的是 devserver（听在回环上），
   所以这儿 trusted 是 false** —— 正好用来验"改不了密钥"那条闸画出来没有。 */
await cdp.eval(`[...document.querySelectorAll("#shelf-list .shelf__row")]
  .find((r) => r.textContent.includes("模型")).click(); true`);
await sleep(600);
const km = JSON.parse(await cdp.eval(`JSON.stringify({
  warn: [...document.querySelectorAll("#shelf-pane .note")].map((e) => e.textContent).join(""),
  addDisabled: [...document.querySelectorAll(".acts .act")]
    .filter((b) => b.textContent.includes("加一条")).map((b) => b.disabled),
  // 页面上任何地方都不该出现 key 这个字段 —— 服务端压根没发
  leaked: JSON.stringify(models || {}).includes('"key"'),
})`));
if (!km.warn.includes("改不了密钥") || String(km.addDisabled) !== "true" || km.leaked) {
  console.error("模型那一项不对（浏览器入口该拦住写密钥、且清单里不该有 key）：",
                JSON.stringify(km));
  process.exitCode = 1;
} else {
  console.log("模型清单不带 key，浏览器入口改不了密钥");
}

await cdp.eval(`document.querySelector('[data-tab="skills"]').click(); true`);
await sleep(500);
/* 「关掉的那份长什么样」跟工具那页一样，现场要自己铺 —— 不能指望跑夹具这台
   机器上正好有一份技能是关着的 */
const skillOffName = await cdp.eval(`(() => {
  const row = [...document.querySelectorAll("#shelf-list .shelf__row")]
    .find((r) => r.querySelector(".sw") &&
                 r.querySelector(".sw").getAttribute("aria-checked") !== "false");
  if (!row) return "";
  row.querySelector(".sw").click();
  return row.querySelector(".shelf__name").textContent;
})()`);
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
// 开回去，别把这台机器上的配置改了
if (skillOffName) {
  await cdp.eval(`(() => {
    const row = [...document.querySelectorAll("#shelf-list .shelf__row")]
      .find((r) => r.querySelector(".shelf__name").textContent === ${JSON.stringify(skillOffName)});
    if (row && row.querySelector(".sw").getAttribute("aria-checked") === "false")
      row.querySelector(".sw").click();
  })(); true`);
  await sleep(400);
}
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

/* 复古主题：**只换配色，排版一个字不动**。所以量两样 ——
   颜色真的换了，而版面上的坐标一个都没动。 */
const before = JSON.parse(await cdp.eval(`JSON.stringify({
  bg: getComputedStyle(document.documentElement).getPropertyValue("--bg").trim(),
  accent: getComputedStyle(document.documentElement).getPropertyValue("--accent").trim(),
  box: (() => { const r = document.querySelector("#shelf-list").getBoundingClientRect();
    return [Math.round(r.x), Math.round(r.width)]; })(),
})`));
await cdp.eval(`setTheme("retro"); drawShelf(); true`);
await sleep(400);
const after = JSON.parse(await cdp.eval(`JSON.stringify({
  bg: getComputedStyle(document.documentElement).getPropertyValue("--bg").trim(),
  accent: getComputedStyle(document.documentElement).getPropertyValue("--accent").trim(),
  box: (() => { const r = document.querySelector("#shelf-list").getBoundingClientRect();
    return [Math.round(r.x), Math.round(r.width)]; })(),
})`));
if (before.bg === after.bg || before.accent === after.accent) {
  console.error("换了主题颜色却没变：", JSON.stringify([before, after]));
  process.exitCode = 1;
} else if (String(before.box) !== String(after.box)) {
  console.error("主题只该换配色，版面动了：", JSON.stringify([before.box, after.box]));
  process.exitCode = 1;
} else {
  console.log(`复古主题 ${before.bg} → ${after.bg}，版面一格没动`);
}

await cdp.eval(`document.querySelector('[data-tab="skills"]') && 0; true`);
console.log("拍了", await cdp.shot("12_retro", { width: 1280, height: 900 }));

/* Anthropic 那套走同一道检查 —— **换主题只换颜色，版面一格不动**。
   另外量一下它跟复古分不分得开：两套都偏暖，底色要是撞在一起，
   那就是白加了一套。 */
await cdp.eval(`setTheme("anthropic"); drawShelf(); true`);
await sleep(400);
const anth = JSON.parse(await cdp.eval(`JSON.stringify({
  bg: getComputedStyle(document.documentElement).getPropertyValue("--bg").trim(),
  accent: getComputedStyle(document.documentElement).getPropertyValue("--accent").trim(),
  box: (() => { const r = document.querySelector("#shelf-list").getBoundingClientRect();
    return [Math.round(r.x), Math.round(r.width)]; })(),
})`));
if (String(anth.box) !== String(before.box)) {
  console.error("Anthropic 主题动了版面：", JSON.stringify([before.box, anth.box]));
  process.exitCode = 1;
} else if (anth.bg === after.bg || anth.bg === before.bg) {
  console.error("Anthropic 的底色跟别的主题撞了：", anth.bg);
  process.exitCode = 1;
} else {
  console.log(`Anthropic 主题 ${anth.bg} / ${anth.accent}，版面一格没动`);
}
console.log("拍了", await cdp.shot("13_anthropic", { width: 1280, height: 900 }));

await cdp.eval(`setTheme("light"); drawShelf(); true`);
await sleep(300);

// 工具集那一页：开关必须是看得见的方块，不是零尺寸
await cdp.eval(`document.querySelector('[data-tab="tools"]').click(); true`);
await sleep(400);
/* **「关掉的那一行长什么样」这个现场要自己铺。** 原来是直接断言"总得有一条是
   关着的"—— 而那取决于跑夹具这台机器上 enabled.json 里有什么，一份干净的配置
   下它必红。夹具喊狼来了几次之后就没人看它了。
   自己关一条、断言完再开回去，跟评测那边「每题在自己的临时目录里从头铺现场」
   是同一条规矩。 */
const swOffName = await cdp.eval(`(() => {
  const row = [...document.querySelectorAll("#shelf-list .shelf__row")]
    .find((r) => r.dataset.on !== "0" && !r.querySelector(".sw[disabled]"));
  if (!row) return "";
  row.querySelector(".sw").click();
  return row.querySelector(".shelf__name").textContent;
})()`);
await sleep(500);
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
// 关回去，别把这台机器上的配置改了
if (swOffName) {
  await cdp.eval(`(() => {
    const row = [...document.querySelectorAll("#shelf-list .shelf__row")]
      .find((r) => r.querySelector(".shelf__name").textContent === ${JSON.stringify(swOffName)});
    if (row && row.dataset.on === "0") row.querySelector(".sw").click();
  })(); true`);
  await sleep(400);
}
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
/* 清单至少要有「一份档案 + 新建行」两条。**不能要求三条** —— 那等于要求
   跑夹具这台机器上正好有两份档案，跟上面那条开关是同一种毛病。
   「增删改」那一段本来就会自己建一份，真正的多份场景在那儿验。 */
if (souls.rows.length < 2 || souls.switches !== 0 || !souls.soul || souls.acts.length !== 3) {
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
