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
 *   23–26  —— 0.80.0：空对话那一组居中、发出第一句落到底、「以下是新的」、确认卡的键帽
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
  /* **量画出来的位置，不量属性。** 原来比的是两边的 line-height 属性，那俩一直都是
     20px —— 而行距是 <pre> 那个块撑的（24px），四行错开半行，这条照样绿了三个版本。
     现在比最后一个行号和最后一行代码的顶边：差过 2px 就是没对齐 */
  const lastTop = (el) => { const r = document.createRange(); r.selectNodeContents(el);
    const all = [...r.getClientRects()].filter((b) => b.width > 0);
    return all.length ? all[all.length - 1].top : NaN; };
  return { nums: nums ? nums.textContent.split("\\n").length : 0,
           rows: codeEl.textContent.split("\\n").length,
           drift: nums ? Math.round(lastTop(codeEl) - lastTop(nums)) : NaN,
           sameLead: nums ? Math.abs(lastTop(codeEl) - lastTop(nums)) <= 2 : false,
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
  // 0.80.0 起侧栏收起是**整块滑出视口**（定宽 240，不再压成 0 宽），
  // 所以量的是它露在屏幕上的那一截；滑完之后它还是 visibility: hidden
  const got = JSON.parse(await cdp.eval(`JSON.stringify({
    side: (() => {
      const el = document.getElementById("side");
      const r = el.getBoundingClientRect();
      if (getComputedStyle(el).visibility === "hidden") return 0;
      return Math.round(Math.max(0, Math.min(r.right, innerWidth) - Math.max(r.left, 0)));
    })(),
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

/* Anthropic 那套从 0.79.0 起**是一整套皮**（claude.ai 的观感），版面本来就变 ——
   「版面一格不动」那条只剩复古还守着。这儿只量它跟另两套分不分得开
   （都偏暖，底色撞了就是白加一套）；皮自己承诺的那几件事在文末那一节量。 */
await cdp.eval(`setTheme("anthropic"); drawShelf(); true`);
await sleep(400);
const anth = JSON.parse(await cdp.eval(`JSON.stringify({
  bg: getComputedStyle(document.documentElement).getPropertyValue("--bg").trim(),
  accent: getComputedStyle(document.documentElement).getPropertyValue("--accent").trim(),
})`));
if (anth.bg === after.bg || anth.bg === before.bg) {
  console.error("Anthropic 的底色跟别的主题撞了：", anth.bg);
  process.exitCode = 1;
} else {
  console.log(`Anthropic 主题 ${anth.bg} / ${anth.accent}`);
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

/* ── Anthropic 这套皮（0.79.0）────────────────────────────────
   它推翻了「直角」和「主题只换调色板」，所以上面那些网格断言管不到它。
   这儿量它自己承诺的几件事 —— **颜色和圆角光看图会放过去，得量**：
     气泡是圆的且有底色、它说的话是衬线、输入区是一张浮起来的卡片、
     meta-rail 真的没了、带字的实心按钮过得了 AA、窄屏不横滚、代码行号对得上，
   最后切回默认，确认这套皮**一点都没漏到别的主题上**。 */
const contrast = (fg, bg) => {
  const lum = (c) => { const [r, g, b] = (c.match(/[\d.]+/g) || [0, 0, 0]).slice(0, 3).map(Number)
    .map((v) => { v /= 255; return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; });
    return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
  const [a, b] = [lum(fg), lum(bg)];
  return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
};
await cdp.call("Emulation.setDeviceMetricsOverride",
  { width: 1280, height: 900, deviceScaleFactor: 1, mobile: false });
await cdp.call("Page.navigate", { url });
await sleep(1200);
await cdp.eval(`setTheme("anthropic"); true`);
await feed(FLOW);
await feed(ASK);
await sleep(900);
const skin = JSON.parse(await cdp.eval(`JSON.stringify((() => {
  const cs = (sel, pseudo) => { const el = document.querySelector(sel);
    return el ? getComputedStyle(el, pseudo) : null; };
  const you = cs(".block--you .block__body");
  const say = cs(".block--say .prose");
  const row = cs(".composer__row");
  const go = cs(".btn--primary");
  const lastTop = (el) => { const r = document.createRange(); r.selectNodeContents(el);
    const all = [...r.getClientRects()].filter((b) => b.width > 0);
    return all.length ? all[all.length - 1].top : NaN; };
  const fig = document.querySelector("figure.code");
  return {
    bg: getComputedStyle(document.documentElement).getPropertyValue("--bg").trim(),
    youRadius: you ? parseFloat(you.borderTopLeftRadius) : 0,
    youBg: you ? you.backgroundColor : "",
    sayFont: say ? say.fontFamily : "",
    rowRadius: row ? parseFloat(row.borderTopLeftRadius) : 0,
    rowShadow: row ? row.boxShadow : "none",
    rail: getComputedStyle(document.getElementById("stream"), "::before").content,
    label: cs(".block--say .block__label").display,
    goBg: go ? go.backgroundColor : "", goFg: go ? go.color : "",
    codeDrift: fig ? Math.round(lastTop(fig.querySelector("code")) - lastTop(fig.querySelector(".code__nums"))) : NaN,
  };
})())`));
console.log("拍了", await cdp.shot("16_anthropic_flow", { width: 1280, height: 900, beyond: false }));
const skinBad = [];
if (skin.youRadius < 12 || /rgba\(0, 0, 0, 0\)|transparent/.test(skin.youBg)) skinBad.push("「你」那句不是圆角气泡");
if (!/serif/i.test(skin.sayFont) || /^\s*"?(Adwaita Sans|Inter)/.test(skin.sayFont)) skinBad.push("它说的话不是衬线");
if (skin.rowRadius < 16 || skin.rowShadow === "none") skinBad.push("输入区不是一张浮起来的卡片");
if (skin.rail !== "none") skinBad.push("meta-rail 那条竖线还在");
if (skin.label !== "none") skinBad.push("「它说」那个大写标签还在");
if (!skin.goBg || contrast(skin.goFg, skin.goBg) < 4.5) skinBad.push(`实心按钮上的字不够 AA（${skin.goFg} on ${skin.goBg}）`);
if (!(Math.abs(skin.codeDrift) <= 2)) skinBad.push(`代码行号跟代码错开了 ${skin.codeDrift}px`);

await cdp.call("Page.navigate", { url });
await sleep(1200);
await cdp.eval(`setTheme("anthropic"); true`);
await feed(TALK);
console.log("拍了", await cdp.shot("17_anthropic_talk", { width: 1280, height: 900, beyond: false }));
await feed([{ kind: "cleared" }]);
await sleep(400);
const greet = await cdp.eval(`(() => { const h = document.querySelector(".hello__greet");
  return h ? getComputedStyle(h).display + "|" + h.textContent : "没有"; })()`);
if (!greet.startsWith("flex|") || greet.length < 7) skinBad.push(`空对话那句问候没画出来（${greet}）`);
console.log("拍了", await cdp.shot("18_anthropic_hello", { width: 1280, height: 900, beyond: false }));

await cdp.call("Emulation.setDeviceMetricsOverride",
  { width: 390, height: 900, deviceScaleFactor: 1, mobile: false });
await feed(FLOW.slice(0, 9));
await sleep(600);
const wideOut = JSON.parse(await cdp.eval(`JSON.stringify({
  page: document.documentElement.scrollWidth,
  sheet: sheet.scrollWidth - sheet.clientWidth,
  row: Math.round(document.querySelector(".composer__row").getBoundingClientRect().right),
})`));
if (wideOut.page > 390 || wideOut.sheet > 1 || wideOut.row > 390) skinBad.push(`390px 下横滚了：${JSON.stringify(wideOut)}`);
console.log("拍了", await cdp.shot("19_anthropic_narrow", { width: 390, height: 900, beyond: false }));

// 切回默认：meta-rail 和大写标签得原样回来 —— 皮漏到别的主题上就是这条红
await cdp.call("Emulation.setDeviceMetricsOverride",
  { width: 1280, height: 900, deviceScaleFactor: 1, mobile: false });
await cdp.eval(`setTheme("light"); true`);
await feed(FLOW.slice(0, 6));
await sleep(600);
const back = JSON.parse(await cdp.eval(`JSON.stringify({
  rail: getComputedStyle(document.getElementById("stream"), "::before").content,
  label: getComputedStyle(document.querySelector(".block__label")).display,
  radius: parseFloat(getComputedStyle(document.querySelector(".composer__row")).borderTopLeftRadius),
})`));
if (back.rail === "none" || back.label === "none" || back.radius !== 0) {
  skinBad.push(`切回默认之后没复原：${JSON.stringify(back)}`);
}
if (skinBad.length) {
  console.error("Anthropic 那套皮：", skinBad.join("；"), JSON.stringify(skin));
  process.exitCode = 1;
} else {
  console.log(`Anthropic 皮：气泡 ${skin.youRadius}px、衬线正文、输入卡片 ${skin.rowRadius}px、`
    + `按钮对比 ${contrast(skin.goFg, skin.goBg).toFixed(2)}:1、390px 不横滚、切回默认复原`);
}

/* ── 右下角换模型那张清单（0.79.1）────────────────────────────
   两个主题各拍一张打开的样子，量两件事：清单真的在视口里（零尺寸 / 跑到屏幕外
   在截图里就是「不存在」），390px 下也不横出去。键盘和点击那些在 input.mjs 里。 */
const MENU_MODELS = { kind: "models", trusted: true, rows: [
  ...["opus-5.5", "sonnet-5", "haiku-4.5", "fable-5.1"].map((n) => (
    { name: n, model: "anthropic/claude-" + n, vision: true, has_key: true, using: n === "opus-5.5" })),
  ...["gpt-6-luna", "gpt-6-sol", "gpt-6-astra"].map((n) => (
    { name: n, model: "openai/" + n, vision: true, has_key: true, using: false })),
  { name: "qwen3.8-flash", model: "qwen/qwen3.8-flash", vision: true, has_key: true, using: false },
  { name: "没配key的", model: "openai/gpt-x", vision: false, has_key: false, using: false },
] };
const menuBad = [];
for (const [t, width, name] of [["light", 1280, "20_model_menu"], ["anthropic", 1280, "21_anthropic_model_menu"],
                                 ["anthropic", 390, "22_anthropic_model_menu_narrow"]]) {
  await cdp.call("Emulation.setDeviceMetricsOverride", { width, height: 900, deviceScaleFactor: 1, mobile: false });
  await cdp.call("Page.navigate", { url });
  await sleep(1200);
  await cdp.eval(`setTheme("${t}"); true`);
  await feed(TALK.slice(0, 4));
  await cdp.eval(`document.getElementById("model").click(); true`);
  await sleep(700);                     // 真 agent 回的那份 models 先落地，再灌编好的
  await feed([MENU_MODELS]);
  await sleep(400);
  const box = JSON.parse(await cdp.eval(`JSON.stringify((() => {
    const r = document.querySelector("#models .models__card").getBoundingClientRect();
    return { x: Math.round(r.x), right: Math.round(r.right), w: Math.round(r.width), h: Math.round(r.height),
             rows: document.querySelectorAll("#models .models__row").length,
             page: document.documentElement.scrollWidth };
  })())`));
  if (box.w < 200 || box.h < 120 || box.rows !== 9 || box.x < 0 || box.right > width || box.page > width) {
    menuBad.push(`${name}: ${JSON.stringify(box)}`);
  }
  console.log("拍了", await cdp.shot(name, { width, height: 900, beyond: false }));
}
await cdp.eval(`setTheme("light"); true`);
await cdp.call("Emulation.setDeviceMetricsOverride", { width: 1280, height: 900, deviceScaleFactor: 1, mobile: false });
if (menuBad.length) {
  console.error("换模型那张清单不对（该在视口里、九条都在、不横出去）：", menuBad.join("；"));
  process.exitCode = 1;
} else {
  console.log("换模型的清单：两个主题都在视口里，390px 下不横出去");
}

/* ── 0.80.0：开口、点头、没看见的、手感 ──────────────────────
   量的全是「截图上看着没毛病、其实不对」的那一类：居中是不是真居中、滑完是不是
   真落回了底、长对话的开头是不是真滚得上去、线是不是铺在第一块新的前面、
   人在跟前的时候到底叫没叫。键盘答确认卡那些在 input.mjs 里（要真按键）。 */
const v80 = [];
const want80 = (ok, msg, detail) => { if (!ok) v80.push(`${msg}：${JSON.stringify(detail)}`); };

await cdp.call("Emulation.setDeviceMetricsOverride", { width: 1280, height: 900, deviceScaleFactor: 1, mobile: false });
await cdp.call("Page.navigate", { url });
await sleep(1500);
// 换过页，报错收集要重新挂 —— 不然文末那条「页面无 JS 报错」量的是一张空表
await cdp.eval(`window.__errs = []; addEventListener("error", (e) => __errs.push(String(e.message)));
  setTheme("anthropic"); true`);
for (let i = 0; i < 60; i++) {
  if (await cdp.eval(`commands.length > 0`)) break;
  await sleep(500);
}
await sleep(800);
await cdp.eval(`stream.replaceChildren(); syncHello(); true`);
await sleep(700);

// 一、空对话：问候 + 例子 + 输入框是一组，摆在视觉中心（上下留白 42 : 58）
const geo80 = async () => JSON.parse(await cdp.eval(`JSON.stringify((() => {
  const r = (el) => el.getBoundingClientRect();
  const hello = document.getElementById("hello");
  const row = r(document.querySelector(".composer__row"));
  const from = r(sheetEl).top, to = r(document.querySelector(".status")).top;
  return { above: Math.round((hello ? r(hello).top : 0) - from), below: Math.round(to - row.bottom),
           rowTop: Math.round(row.top), helloBottom: hello ? Math.round(r(hello).bottom) : 0 };
})())`));
const g80 = await geo80();
const share = g80.above / (g80.above + g80.below);
want80(share > 0.36 && share < 0.48 && g80.helloBottom <= g80.rowTop,
  "空对话那一组没摆在视觉中心（上下留白该是 42:58、问候在输入框上面）", g80);
console.log("拍了", await cdp.shot("23_anthropic_center", { width: 1280, height: 900, beyond: false }));
const hit80 = await cdp.eval(`(() => { const b = document.getElementById("side-toggle").getBoundingClientRect();
  return document.elementFromPoint(b.x + b.width / 2, b.y + b.height / 2)?.id || ""; })()`);
want80(hit80 === "side-toggle", "抬起来的那一组盖住了标题栏的按钮", hit80);

// 敲三行：输入卡片的顶边不动、往下长（字不在眼皮底下跳）
await cdp.eval(`inputEl.value = "一\\n二\\n三"; inputEl.dispatchEvent(new Event("input")); true`);
await sleep(300);
const g3 = await geo80();
want80(Math.abs(g3.rowTop - g80.rowTop) <= 1, "空对话里敲字换行，输入卡片的顶边跟着跳了",
  { before: g80.rowTop, after: g3.rowTop });
await cdp.eval(`inputEl.value = ""; inputEl.dispatchEvent(new Event("input")); setTheme("light"); true`);
await sleep(300);
const trLight = await cdp.eval(`getComputedStyle(document.querySelector(".composer")).translate`);
want80(trLight === "none", "默认主题的空对话也被抬起来了（只该对 Anthropic 那套皮生效）", trLight);
await cdp.eval(`setTheme("anthropic"); true`);
await sleep(300);

// 二、发出第一句：输入框和那句话一起落到底，问候撤干净
await cdp.eval(`glideArm = Date.now();
  handle({ kind: "turn_start", text: "把 build.mjs 里读 frontmatter 那段抽成一个函数" }); true`);
await sleep(120);
const mid80 = await cdp.eval(`getComputedStyle(document.querySelector(".composer")).translate`);
console.log("拍了", await cdp.shot("24_anthropic_glide", { width: 1280, height: 900, beyond: false }));
const end80 = JSON.parse(await cdp.eval(`JSON.stringify({
  tr: getComputedStyle(document.querySelector(".composer")).translate,
  hello: !!document.getElementById("hello"), leaving: document.querySelectorAll("[data-leaving]").length,
  glide: document.body.dataset.glide ?? null })`));
want80(mid80 !== "none" && end80.tr === "none" && !end80.hello && !end80.leaving && end80.glide === null,
  "第一句发出去之后没滑回底（要有中间态、滑完归零、问候撤干净）", { mid80, end80 });

// 三、长对话：开头滚得上去，到底了 atBottom 就是真。0.74.0 那条 flex-end 在两个引擎里各错一半：
//     Chrome 把开头放到滚动原点上面（够不着），WebKitGTK 把原点翻到底边（atBottom 恒假）
await feed(Array.from({ length: 40 }, (_, i) => ({ kind: "note", level: "notice", text: `第 ${i} 条` })));
await sleep(300);
const sc80 = JSON.parse(await cdp.eval(`JSON.stringify((() => {
  const down = { st: Math.round(sheetEl.scrollTop), max: sheetEl.scrollHeight - sheetEl.clientHeight,
                 bottom: atBottom(), toEndHidden: toEnd.hidden, jc: getComputedStyle(sheetEl).justifyContent };
  sheetEl.scrollTop = 0;
  const first = Math.round(streamEl.firstElementChild.getBoundingClientRect().top - sheetEl.getBoundingClientRect().top);
  return { ...down, first, topIsBottom: atBottom() };
})())`));
want80(sc80.max > 0 && Math.abs(sc80.st - sc80.max) <= 1 && sc80.bottom && sc80.toEndHidden
  && sc80.first >= 0 && !sc80.topIsBottom && sc80.jc !== "flex-end",
  "长对话的滚动不对（开头要滚得上去、到底时 atBottom 为真、「回到最新」藏着）", sc80);

// 四、没看见的：往回翻着的时候底下长出来的，线铺在第一块新的前面，药丸上数着
await cdp.eval(`sheetEl.scrollTop = 0; sheetEl.dispatchEvent(new Event("scroll")); true`);
await sleep(200);
await feed([
  { kind: "tool_start", name: "bash", label: "Bash", args: { command: "npm test" }, target: "npm test" },
  { kind: "tool_result", summary: "退出码 0", is_error: false },
  { kind: "note", level: "notice", text: "测试全过" },
]);
await sleep(400);
const un80 = JSON.parse(await cdp.eval(`JSON.stringify((() => {
  const mark = document.querySelector(".unread");
  return { count: unseen.count, marks: document.querySelectorAll(".unread").length,
           beforeTool: !!mark && mark.nextElementSibling.classList.contains("block--tool"),
           pill: toEnd.dataset.count ?? null, label: toEnd.querySelector(".to-end__n").textContent,
           hidden: toEnd.hidden, st: sheetEl.scrollTop };
})())`));
want80(un80.count === 2 && un80.marks === 1 && un80.beforeTool && un80.pill === "1"
  && !un80.hidden && un80.label.includes("2") && un80.st === 0,
  "往回翻时底下长出来的没标出来（线在第一块新的前面、药丸数着 2 条、没把人拽下去）", un80);
await cdp.eval(`toEnd.click(); true`);
await sleep(900);
// 线不在（上一条已经红了）也别在这儿抛 —— 夹具该报红，不该崩
const jump80 = JSON.parse(await cdp.eval(`JSON.stringify((() => {
  const mark = document.querySelector(".unread");
  return { markTop: mark ? Math.round(mark.getBoundingClientRect().top - sheetEl.getBoundingClientRect().top) : -1,
           h: sheetEl.clientHeight, count: unseen.count };
})())`));
want80(jump80.markTop >= 0 && jump80.markTop < jump80.h && jump80.count === 0,
  "点「N 条新」没落到那条线上（线要进视野、看到底之后数清零）", jump80);
console.log("拍了", await cdp.shot("25_anthropic_unread", { width: 1280, height: 900, beyond: false }));

// 五、人不在跟前才叫：人在的时候一声不吭；不在时要你点头带命令原文，跑完带它的第一句话
await cdp.eval(`window.__notes = []; notifyUser = (t, b) => __notes.push([t, b]);
  handle({ kind: "status", ready: true, busy: true, mode: "default", name: "m", model: "m" });
  handle({ kind: "ask", id: 9301, type: "approve", tool: "bash", label: "Bash", target: "", args: { command: "npm test" } });
  true`);
const here80 = await cdp.eval(`__notes.length`);
await cdp.eval(`window.dispatchEvent(new Event("blur"));
  handle({ kind: "ask", id: 9302, type: "approve", tool: "bash", label: "Bash", target: "",
           args: { command: "rm -rf dist/" } });
  turnFrom = Date.now() - 45000;
  lastSaid = "## 全绿了\\n三处都修好。";
  handle({ kind: "turn_end" });
  true`);
const away80 = JSON.parse(await cdp.eval(`JSON.stringify(__notes)`));
await cdp.eval(`window.dispatchEvent(new Event("focus"));
  handle({ kind: "status", ready: true, busy: false, mode: "default", name: "m", model: "m" }); true`);
want80(here80 === 0 && away80.length === 2 && away80[0][0].includes("回不去") && away80[0][1].includes("rm -rf dist/")
  && away80[1][0].startsWith("跑完了") && away80[1][1] === "全绿了",
  "系统通知叫得不对（人在不叫；不在时要你点头带命令、跑完带第一句）", { here80, away80 });

// 六、浮层：这套皮里浮着（不挤对话栏）；收起时 hidden 立刻为真，画面上先淡出再藏
const sheetH0 = await cdp.eval(`sheetEl.clientHeight`);
await cdp.eval(`inputEl.value = "/"; inputEl.dispatchEvent(new Event("input")); true`);
await sleep(300);
const pal80 = JSON.parse(await cdp.eval(`JSON.stringify({ open: !paletteEl.hidden,
  pos: getComputedStyle(paletteEl).position, sheetH: sheetEl.clientHeight })`));
await cdp.eval(`inputEl.value = ""; inputEl.dispatchEvent(new Event("input")); true`);
const out80 = JSON.parse(await cdp.eval(`JSON.stringify({ hidden: paletteEl.hidden,
  leaving: paletteEl.dataset.leaving ?? null, display: getComputedStyle(paletteEl).display })`));
await sleep(400);
const gone80 = await cdp.eval(`getComputedStyle(paletteEl).display`);
want80(pal80.open && pal80.pos === "absolute" && pal80.sheetH === sheetH0 && out80.hidden
  && out80.leaving === "1" && out80.display !== "none" && gone80 === "none",
  "补全菜单的进出不对（浮着不挤对话栏；收起先淡出再藏）", { sheetH0, pal80, out80, gone80 });

// 七、侧栏：滑出去的，不是一下子没的；滑完藏起来（Tab 走不进去）
await cdp.eval(`setSide(false); true`);
await sleep(100);
const sMid80 = await cdp.eval(`Math.round(document.getElementById("side").getBoundingClientRect().right)`);
await sleep(400);
const sEnd80 = JSON.parse(await cdp.eval(`JSON.stringify({
  right: Math.round(document.getElementById("side").getBoundingClientRect().right),
  vis: getComputedStyle(document.getElementById("side")).visibility })`));
await cdp.eval(`setSide(true); true`);
await sleep(400);
want80(sMid80 > 0 && sMid80 < 240 && sEnd80.right <= 0 && sEnd80.vis === "hidden",
  "侧栏收起不是滑出去的（中途该露一截、滑完藏起来）", { sMid80, sEnd80 });

// 八、确认卡：四个键帽，到点浮出来（按键那些在 input.mjs）
await cdp.eval(`stream.replaceChildren(); dropUnread();
  handle({ kind: "status", ready: true, busy: true, mode: "default", name: "m", model: "m" });
  handle({ kind: "turn_start", text: "跑一遍测试" });
  handle({ kind: "tool_start", name: "bash", label: "Bash", args: { command: "npm test" }, target: "npm test" });
  handle({ kind: "ask", id: 9401, type: "approve", tool: "bash", label: "Bash", target: "",
           args: { command: "npm test" }, rule: "Bash(npm:*)" });
  true`);
// 看的是「接不接键」那个状态，不是键帽的 opacity —— 没有等待的话，量的那一刻
// 淡入刚起步，opacity 恰好还是 0，照样骗得过去（回退验证时真被它骗了一次）
const early80 = await cdp.eval(`document.querySelector(".ask").dataset.armed ? "armed" : "0"`);
await sleep(900);
const keys80 = JSON.parse(await cdp.eval(`JSON.stringify([...document.querySelectorAll(".ask .btn__key")]
  .map((k) => k.textContent + ":" + getComputedStyle(k).opacity))`));
console.log("拍了", await cdp.shot("26_anthropic_ask_keys", { width: 1280, height: 900, beyond: false }));
want80(early80 === "0" && keys80.join() === "1:1,2:1,3:1,4:1",
  "确认卡的键帽不对（四个 1–4，刚出现时不露、到点浮出来）", { early80, keys80 });
await cdp.eval(`handle({ kind: "status", ready: true, busy: false, mode: "default", name: "m", model: "m" });
  setTheme("light"); true`);

// 九、系统里关了动效：上面这些一个都不演 —— 「一句话关掉」那条的延长线。
//     JS 那侧的 REDUCED 是开页面时读一次的，所以要先模拟媒体查询、再重新打开页面
await cdp.call("Emulation.setEmulatedMedia",
  { features: [{ name: "prefers-reduced-motion", value: "reduce" }] });
await cdp.call("Page.navigate", { url });
await sleep(1500);
await cdp.eval(`window.__errs = []; addEventListener("error", (e) => __errs.push(String(e.message)));
  setTheme("anthropic"); true`);
for (let i = 0; i < 60; i++) {
  if (await cdp.eval(`commands.length > 0`)) break;
  await sleep(500);
}
await sleep(800);
await cdp.eval(`stream.replaceChildren(); syncHello(); true`);
await sleep(300);
const rm80 = JSON.parse(await cdp.eval(`JSON.stringify((() => {
  glideArm = Date.now();
  handle({ kind: "turn_start", text: "你好" });
  const out = { reduced: REDUCED, motion: document.body.dataset.motion ?? null,
    glide: document.body.dataset.glide ?? null, leaving: document.querySelectorAll("[data-leaving]").length,
    tr: getComputedStyle(document.querySelector(".composer")).translate };
  inputEl.value = "/"; inputEl.dispatchEvent(new Event("input"));
  inputEl.value = ""; inputEl.dispatchEvent(new Event("input"));
  out.palLeaving = paletteEl.dataset.leaving ?? null;
  handle({ kind: "ask", id: 9501, type: "approve", tool: "bash", label: "Bash", target: "", args: { command: "ls" } });
  document.querySelector(".ask .btn").click();
  const tick = document.querySelector(".ask__tick");
  out.tick = tick ? getComputedStyle(tick).strokeDashoffset : "没有勾";
  return out;
})())`));
await cdp.call("Emulation.setEmulatedMedia", { features: [] });
want80(rm80.reduced && rm80.motion === null && rm80.glide === null && rm80.leaving === 0 && rm80.tr === "none"
  && rm80.palLeaving === null && parseFloat(rm80.tick) === 0,
  "系统关了动效，新加的这些还在演（滑、淡出、侧栏过渡都该没有，勾要直接画好）", rm80);

if (v80.length) {
  console.error("0.80.0 那几处：", v80.join("；"));
  process.exitCode = 1;
} else {
  console.log("0.80.0：空对话居中、敲字往下长、滑回底、长对话滚得到头、没看见的有线有数、"
    + "人不在才叫、浮层淡出、侧栏滑出、键帽到点浮出、关了动效一个都不演");
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
