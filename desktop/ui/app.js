/* Feiyu Desktop 的前端。
 *
 * 一条传输通道，收 agent 的结构化事件画成页面；发回去的是
 * {op: prompt | command | reply | interrupt | status | sessions | new | open | delete}。
 *
 * **传输有两条路**，靠 window.__TAURI__ 在不在自己判断：
 *   · 桌面（Tauri）—— invoke("send") / listen("feiyu://event")，进程内通道，不开端口
 *   · 开发（浏览器）—— WebSocket 连 feiyu-devserver。Tauri 在 Linux 上是 webkit2gtk，
 *     没有 CDP，headless Chrome 那套夹具接不上去，所以留这条路专门给测试用
 *
 * 没有框架，也没有外部依赖。markdown 渲染是自己写的一小份（见 md()）。
 * 信息架构照 Hermes Desktop（NousResearch/hermes-agent，MIT），样式是博客那一套。
 */
"use strict";

/* 在 Tauri 窗口里跑还是在浏览器里跑。**必须在最上面**：下面好几处在用它，
   而 const 不提升，放后面会撞 TDZ。 */
const TAURI = window.__TAURI__;

/* devserver 那条路要的令牌，从地址里取。桌面端没有这东西（也不需要 ——
   进程内通道，外面够不着）。 */
const TOKEN = new URLSearchParams(location.search).get("t") || "";

const $ = (id) => document.getElementById(id);
const streamEl = $("stream");
const sheetEl = $("sheet");
const inputEl = $("input");
const paletteEl = $("palette");
const modelsEl = $("models");
const modelPill = $("model");

let socket = null;
let busy = false;
/* 这一轮里正在写的那块正文。文本是一个 delta 一个 delta 来的，得攒起来重渲染，
   不能追加 DOM —— markdown 的记号会跨 delta。 */
let saying = null;
let sayingText = "";
let sayingTimer = 0;    // 攒着还没画的那一次重绘，见 scheduleSaying
let sayingPainted = 0;  // 上次画完的时刻
let commands = [];
let seenAsk = new Set();
/* 这次的 help 事件要不要画出来。连上时我们自己发一次去取补全清单，
   那次只填数据、不画；用户敲 /help 那次才画。 */
let wantHelp = false;
/* 已经上传好、等着随下一条消息一起交出去的文件（工作区相对路径） */
let clips = [];
/* 启动时那几句环境说明（MCP / 项目钩子 / 项目技能），按来源存一份 */
const boots = new Map();
/* 「回到最新」那个钮。**在这儿先占个名**：它是文件末尾才建的，而 `keepBottom`
   在那之前就可能被调到，而对着还没初始化的 const 连 `typeof` 都会抛。 */
let toEnd = null;

/* 0.80.0 那几样的状态，同一个理由全在这儿先占名 —— 用到它们的函数（block、
   syncHello、hidePalette……）都定义在前面，哪天在初始化时被同步调一次就撞 TDZ。 */

/* 人在不在看。**只认真发生过的 blur / 隐藏**，不拿 `document.hasFocus()` 猜初值 ——
   无头浏览器里它常年是 false，拿它当初值的话夹具里每一块都会被当成「没看见」。 */
let away = document.visibilityState === "hidden";
/* 没看见的：那条「以下是新的」（mark），和它后面攒了几块还没看到（count） */
const unseen = { mark: null, count: 0 };
/* 正等着人点头的那张卡：{ card, keys, from, timer, pressing } */
let pendingAsk = null;
/* 这一轮几时开始、它最后说了什么 —— 人不在时跑完了，通知里要说这两样 */
let turnFrom = 0;
let lastSaid = "";
/* 空对话里点发送的时刻。之后几秒内「开始」那块让位，就是这一下引起的 —— 要滑 */
let glideArm = 0;
let glideTimer = 0;
/* 空对话时输入框抬了多高（见 measureLift），和盯着尺寸变化的那个观察者 */
let liftPx = 0;
let liftWatch = null;
/* 浮层淡出时各自那只定时器（见 hideSoft） */
const leaving = new WeakMap();

/* ── 小工具 ─────────────────────────────────────────────────── */

/* 系统里关掉了动效就一个都不做。**JS 里也要问一次** —— CSS 那半边有媒体查询
   兜着，但用 JS 量高度、排 requestAnimationFrame 的那几处绕过了 CSS。 */
const REDUCED = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

const esc = (s) =>
  String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

/* 取 app.css 里的动效 token。JS 里起的过渡（Web Animations）也吃同一套数，
   不在这边另抄一份 —— 抄了就会有一天两边对不上 */
const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const cssMs = (name) => parseFloat(cssVar(name)) || 0;

/* 一块东西就地换了内容、高度跟着变：量出前后两个高度，中间补一段过渡，
   底下的东西就不是「跳」过去的。只动这一块自己的 height，动完就还原成 auto */
function settle(el, change) {
  const from = el.offsetHeight;
  change();
  if (REDUCED || !el.animate) return;
  const to = el.offsetHeight;
  if (Math.abs(from - to) < 2) return;
  el.animate([{ height: from + "px" }, { height: to + "px" }],
             { duration: cssMs("--slide-dur"), easing: cssVar("--ease-move") || "ease" });
}

/* 一段 markdown 的第一句话，去掉行首记号 —— 给系统通知用，那儿只认纯文本 */
function plainLine(text) {
  const line = String(text || "").split("\n").map((l) => l.trim())
    .find((l) => l && !l.startsWith("```")) || "";
  return line.replace(/^(#{1,6}\s+|>\s*|[-*+]\s+|\d+\.\s+)/, "").replace(/[*`]/g, "").slice(0, 120);
}

function atBottom() {
  return sheetEl.scrollHeight - sheetEl.scrollTop - sheetEl.clientHeight < 80;
}

/* 只在本来就贴着底的时候才跟着滚。用户往回翻着看的时候把他拽回来是最烦人的。 */
function keepBottom(wasDown) {
  if (wasDown) sheetEl.scrollTop = sheetEl.scrollHeight;
  // 没跟着滚 = 底下又长出了没看见的东西，「回到最新」该露面
  drawToEnd();
}

function block(label, cls) {
  const wasDown = atBottom();
  const el = document.createElement("div");
  el.className = "block" + (cls ? " " + cls : "");
  el.innerHTML = `<div class="block__label">${esc(label)}</div><div class="block__body"></div>`;
  streamEl.append(el);
  syncHello();
  // 线要在跟着滚之前铺：先滚再插的话，最后一块会被那条线顶到屏幕下面去
  markUnseen(el, wasDown);
  keepBottom(wasDown);
  return el.querySelector(".block__body");
}

/* ── 没看见的 ───────────────────────────────────────────────
   你往回翻着看、或者切到别的窗口的时候，底下照样在长。回来时新旧连成一片，
   分不出哪些是后来的（NN/g 说的变化盲视）。所以在第一块没看见的前面铺一条
   「以下是新的」，「回到最新」上数着攒了几块，点它落到那条线上 —— 从第一条新的读起。

   **看到底了只清数，不撤线**：线留着，往回翻时它还标着「从这儿起是后来长出来的」。
   下一批没看见的来了就挪过去，你开口说下一句时撤掉（那时你已经接着往下聊了）。 */
function markUnseen(el, wasDown) {
  if (el.classList.contains("block--wait")) return;     // 「在跑」来了又走，不算数
  if (!away && wasDown) return;                          // 长在眼前的，就是看见了
  // 上一批都看过了（数清零了），那条线作废，挪到这一批的第一块前面
  if (!unseen.mark || !unseen.mark.isConnected || !unseen.count) {
    if (unseen.mark) unseen.mark.remove();
    unseen.mark = document.createElement("div");
    unseen.mark.className = "unread";
    unseen.mark.setAttribute("role", "separator");
    unseen.mark.textContent = "以下是新的";
    el.before(unseen.mark);
  }
  unseen.count += 1;
}

/* 人在、而且看到底了：攒的数清掉 */
function sawAll() {
  if (away || !unseen.count || !atBottom()) return;
  unseen.count = 0;
  drawToEnd();
}

function dropUnread() {
  if (unseen.mark) unseen.mark.remove();
  unseen.mark = null;
  unseen.count = 0;
  drawToEnd();
}

function drawToEnd() {
  if (!toEnd) return;      // 它在文件末尾才建
  toEnd.hidden = atBottom();
  const n = unseen.count;
  if (n) {
    toEnd.dataset.count = "1";
    toEnd.querySelector(".to-end__n > span").textContent = `${n} 条新`;
  } else {
    // 字不清：药丸缩回去那一下里面还得有字，空着缩像是坏了
    delete toEnd.dataset.count;
  }
  toEnd.title = n ? `${n} 条新的，跳到第一条` : "回到最新";
  // 字在数清零后还留着（为了缩回去那一下），读屏得按现在的数念
  toEnd.setAttribute("aria-label", toEnd.title);
}

/* ── 一片空白的时候说点什么 ─────────────────────────────────
   原来第一次打开是：左边「还没有历史对话」，中间纯白，除了输入框里那句
   「我们要做什么？」再没有别的。**能干什么、该怎么开口，全靠猜。**

   写成跟 `.block` 一样的两列（记号列 + 正文列），这样刊头那条纵线不会断。
   例子是**可点的**：点一下填进输入框但不发出去 —— 让人能改，别替他做决定。
   `@` 和 `/` 那行是这里唯一该教的两个记号，别列成快捷键大全。 */
const HELLO = [
  "这个项目是做什么的？我该从哪看起",
  "跑一遍测试，失败的顺手修掉",
  "把 @ 里那段重复的抽成一个函数",
  "最近这次改动有什么风险？",
];

/* 问候那一行只有 Anthropic 那套皮画出来（claude.ai 那句衬线大字），
   别的主题里它是 `display: none` —— 那两套的「开始」本来就是安静的一行字 */
function greeting() {
  const h = new Date().getHours();
  return h < 5 ? "夜深了" : h < 11 ? "早上好" : h < 13 ? "中午好" : h < 18 ? "下午好" : "晚上好";
}

function syncHello() {
  let el = $("hello");
  // **"有没有内容"不能把这块自己算进去。** 第一版写的是
  // `streamEl.children.length > 0`，而这块本身就是 streamEl 的孩子 ——
  // 于是在空对话上连调两次就把它删了（第三次又加回来，来回跳）。
  // 现在所有调用点恰好都是"先清空/先追加"，所以没露出来；
  // 加一个调用点就会静默失效，而空状态没了是不会报错的。
  // 正在淡出的那块旧「开始」（没有 id 了）也不算内容
  const kids = [...streamEl.children].filter((c) => !c.dataset.leaving).length;
  const has = kids > (el ? 1 : 0);
  if (has) {
    if (el) leaveHello(el);
    delete document.body.dataset.empty;
    return;
  }
  document.body.dataset.empty = "1";
  if (el) return;
  el = document.createElement("div");
  el.id = "hello";
  el.className = "block hello";
  el.innerHTML = `<div class="block__label">开始</div><div class="block__body">`
    + `<h1 class="hello__greet"><span class="hello__mark" aria-hidden="true"></span>`
    + `${greeting()}</h1>`
    + `<p class="hello__lead">问它点什么，或者直接把要做的事说出来。</p>`
    + `<ul class="hello__list">`
    + HELLO.map((t) => `<li><button type="button" class="hello__pick">${esc(t)}</button></li>`).join("")
    + `</ul>`
    + `<p class="hello__keys"><code>@</code> 提一个文件　<code>/</code> 斜杠命令　`
    + `<code>Ctrl N</code> 新建会话</p>`
    + `</div>`;
  el.querySelectorAll(".hello__pick").forEach((b) => {
    b.onclick = () => {
      const box = $("input");
      box.value = b.textContent;
      box.focus();
      // 光标落到末尾，而不是选中整句 —— 多半是要接着改的
      box.setSelectionRange(box.value.length, box.value.length);
      box.dispatchEvent(new Event("input"));
    };
  });
  streamEl.append(el);
  // 它的高度会变（字体加载完、窗口变窄折行），变了输入框要重新摆
  if (liftWatch) liftWatch.observe(el);
}

/* 「开始」让位。空对话里刚点过发送的话（`glideArm`，Anthropic 那套皮），
   它淡出去、输入框和你那句话一起落到底（见 app.css「问候、例子和输入框是一组」）；
   别的时候 —— 开旧对话、机器人开场、别的主题 —— 直接撤，不演。 */
const GLIDE_ARM = 4000;

function leaveHello(el) {
  const glide = glideArm && Date.now() - glideArm < GLIDE_ARM && !REDUCED
    && document.documentElement.dataset.theme === "anthropic";
  glideArm = 0;
  if (!glide) { el.remove(); return; }
  el.removeAttribute("id");
  el.dataset.leaving = "1";
  document.body.dataset.glide = "1";
  setTimeout(() => el.remove(), 260);
  clearTimeout(glideTimer);
  glideTimer = setTimeout(() => { delete document.body.dataset.glide; }, 620);
}

/* ── markdown ───────────────────────────────────────────────
   够用就好：粗体、行内代码、围栏代码块、标题、列表、引用、表格、链接、分隔线。
   **斜体不认** —— 跟终端那边一致，`*` 在这个语境里多半是通配符。
   流式渲染的关键是每次都从原文整份重渲染，所以不用处理「半个记号落在
   chunk 边界上」那种事，那是终端那条路才有的问题。 */

function inline(text) {
  let out = esc(text);
  out = out.replace(/`([^`]+)`/g, (_, code) => `<code>${code}</code>`);
  out = out.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  /* 图片要在链接**之前**处理：![x](y) 的后半截本身就是一条合法的链接语法，
     反过来的话会渲染成一个前面挂着感叹号的链接。
     远程地址不当图画 —— CSP 是 img-src 'self' data: blob:，画出来必然是个破图标；
     本地那些交给 fillShots() 异步去读（那是唯一能拿到工作区文件的通道）。 */
  out = out.replace(/!\[([^\]]*)\]\(([^)\s]+)\)/g, (_, alt, src) =>
    /^(https?|data|blob):/.test(src)
      ? `<a href="${src}" target="_blank" rel="noreferrer noopener">${alt || src}</a>`
      : `<figure class="shot" data-src="${src}"><figcaption>${alt || src}</figcaption></figure>`);
  out = out.replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g,
    '<a href="$2" target="_blank" rel="noreferrer noopener">$1</a>');
  return out;
}

/* ── 工作区里的图 ───────────────────────────────────────────
   两条传输各有各的通道：桌面端走 invoke（回的是 ArrayBuffer，包成 blob URL），
   夹具那条走 devserver 的 /file。两边都在服务端比过工作区前缀 ——
   路径是 agent 给的，而 agent 会读网页、会跑用户给的命令，不是可信来源。 */

const MIME = { png: "image/png", jpg: "image/jpeg", jpeg: "image/jpeg",
               gif: "image/gif", webp: "image/webp", svg: "image/svg+xml" };

async function imgSrc(path) {
  if (!TAURI) return "/file?t=" + encodeURIComponent(TOKEN) + "&p=" + encodeURIComponent(path);
  const buf = await TAURI.core.invoke("read_image", { path });
  const kind = MIME[String(path).split(".").pop().toLowerCase()] || "image/png";
  return URL.createObjectURL(new Blob([buf], { type: kind }));
}

/* 这一轮里已经画过的图。正文里再 ![](同一个路径) 就不画第二遍 ——
   模型生成完常会顺手把路径重述一遍，那会让同一张图上下挨着出现两次。 */
let shownShots = new Set();

/* 工具那一块里把图画出来（image_generate 出的、read_file 看过的）。
   登记进 shownShots，正文里再提到同一个路径就不画第二遍。 */
function showShot(target, path) {
  shownShots.add(path);
  const wasDown = atBottom();
  const fig = document.createElement("figure");
  fig.className = "shot";
  fig.innerHTML = `<figcaption>${esc(path)}</figcaption>`;
  target.append(fig);
  imgSrc(path).then((url) => {
    const img = new Image();
    img.src = url;
    img.alt = path;
    img.onload = () => keepBottom(wasDown);
    fig.prepend(img);
  }).catch((err) => {
    fig.dataset.failed = "1";
    fig.querySelector("figcaption").textContent = path + " —— 画不出来：" + err;
  });
}

/* 把 <figure class="shot" data-src> 填成真图。md() 是同步的纯函数，
   而读文件是异步的，所以渲染完再回来补一道。 */
function fillShots(root) {
  root.querySelectorAll("figure.shot[data-src]").forEach((fig) => {
    const src = fig.dataset.src;
    delete fig.dataset.src;
    if (shownShots.has(src)) { fig.remove(); return; }
    imgSrc(src).then((url) => {
      const img = new Image();
      img.src = url;
      img.alt = src;
      fig.prepend(img);
    }).catch(() => { fig.dataset.failed = "1"; });
  });
}

/* 代码块：行号在左边一条**不跟着横滚**的槽里，右上角一个复制按钮。
 *
 * **行号不能进 `<code>`** —— 那是拿去复制的正文，掺进去的话复制出来每行都顶着
 * 一个数字。所以它是 `<pre>` 外面的一列，`overflow-x` 只加在 `<pre>` 上。
 * 两列的 line-height 必须是同一个**长度**（都写 20px），不然滚到第五十行就错开。
 *
 * 复制按钮不在这儿绑事件：`md()` 是纯函数，流式时一秒要跑十几次，
 * 每次绑一遍等于每次都重新建闭包。事件走 `streamEl` 上那一道委托。 */
const CODE_NUMS = 400;   // 再长就不画行号：几千行的输出画出来也没人数

function codeBlock(text, lang) {
  const rows = text.split("\n");
  const nums = rows.length > 1 && rows.length <= CODE_NUMS
    ? `<span class="code__nums" aria-hidden="true">${rows.map((_, n) => n + 1).join("\n")}</span>`
    : "";
  return '<figure class="code">'
    + '<figcaption class="code__bar">'
    + `<span class="code__lang">${esc(lang || "")}</span>`
    + '<button class="code__copy" type="button" data-copy>复制</button>'
    + "</figcaption>"
    + `<div class="code__body">${nums}<pre><code>${esc(text)}</code></pre></div>`
    + "</figure>";
}

/* **渲染结果按顶层块分开返回。** `md()` 把它拼成一整根字符串给不在乎结构的
   调用方用；流式那条路要的是这个数组 —— 有了它，`paintInto` 才能不做任何
   序列化就知道「这一帧只有最后一段变了」。见那个函数的注释。 */
function mdParts(src) {
  const lines = String(src).split("\n");
  const out = [];
  let i = 0;
  const isRow = (s) => /^\s*\|.*\|\s*$/.test(s);
  const cells = (s) => s.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());

  while (i < lines.length) {
    const line = lines[i];

    const fence = /^\s*```\s*([\w+#.-]*)/.exec(line);
    if (fence) {
      const body = [];
      i++;
      while (i < lines.length && !/^\s*```/.test(lines[i])) body.push(lines[i++]);
      i++;
      out.push(codeBlock(body.join("\n"), fence[1]));
      continue;
    }
    // `-{3,}` 不是 `---`：四个以上连字符也是分隔线（markdown 就这么定的），
    // 只认三个的话 `----` 会掉到下面的段落兜底里去，那儿原来会死循环
    if (/^\s*(-{3,}|\*{3,}|_{3,})\s*$/.test(line)) { out.push("<hr>"); i++; continue; }

    const head = /^(#{1,6})\s+(.*)$/.exec(line);
    if (head) {
      const level = Math.min(head[1].length, 3);
      out.push(`<h${level}>${inline(head[2])}</h${level}>`);
      i++;
      continue;
    }

    // 表格：一行 | … | 后面紧跟一行分隔线才算
    if (isRow(line) && i + 1 < lines.length && /^\s*\|[\s:|-]+\|\s*$/.test(lines[i + 1])) {
      const align = cells(lines[i + 1]).map((c) => (c.endsWith(":") ? "right" : "left"));
      const head2 = cells(line);
      i += 2;
      const body = [];
      while (i < lines.length && isRow(lines[i])) body.push(cells(lines[i++]));
      const th = head2.map((c, n) => `<th style="text-align:${align[n] || "left"}">${inline(c)}</th>`);
      const tr = body.map(
        (row) => "<tr>" + row.map((c, n) =>
          `<td style="text-align:${align[n] || "left"}">${inline(c)}</td>`).join("") + "</tr>");
      out.push(`<table><thead><tr>${th.join("")}</tr></thead><tbody>${tr.join("")}</tbody></table>`);
      continue;
    }

    if (/^\s*>\s?/.test(line)) {
      const body = [];
      while (i < lines.length && /^\s*>\s?/.test(lines[i])) body.push(lines[i++].replace(/^\s*>\s?/, ""));
      out.push(`<blockquote>${md(body.join("\n"))}</blockquote>`);
      continue;
    }

    const bullet = /^\s*([-*+]|\d+[.)])\s+/;
    if (bullet.test(line)) {
      const ordered = /^\s*\d/.test(line);
      const items = [];
      while (i < lines.length && bullet.test(lines[i])) {
        items.push(lines[i].replace(bullet, ""));
        i++;
        // 列表项的续行（缩进的非空行）并进同一项
        while (i < lines.length && /^\s{2,}\S/.test(lines[i]) && !bullet.test(lines[i])) {
          items[items.length - 1] += " " + lines[i].trim();
          i++;
        }
      }
      const tag = ordered ? "ol" : "ul";
      out.push(`<${tag}>${items.map((t) => `<li>${inline(t)}</li>`).join("")}</${tag}>`);
      continue;
    }

    if (!line.trim()) { i++; continue; }

    const para = [];
    while (i < lines.length && lines[i].trim() && !/^\s*(```|>|#{1,6}\s|---)/.test(lines[i])
           && !bullet.test(lines[i]) && !isRow(lines[i])) {
      para.push(lines[i++]);
    }
    /* **这一行是死循环的唯一出口，别删。** 上面那个 while 的停止条件里有几种情况
       前面所有分支都不认，最常见的是**没有分隔行的表格行**（`| a | b |`）——
       模型流式吐一张表时，表头落地、分隔行还没到的那一瞬间就是这个形状。
       那时 para 是空的、i 一步没动，外层 while 就原地转圈往 out 里塞 <p></p>：
       CPU 100%、内存无上限地涨，**实测 70 秒吃掉 16 G**，整个窗口卡死。
       兜成一段普通正文是对的（GitHub 对孤立表格行也是当段落渲染）。 */
    if (!para.length) para.push(lines[i++]);
    out.push(`<p>${inline(para.join("\n"))}</p>`);
  }
  return out;
}

const md = (src) => mdParts(src).join("");

/* ── 事件 ──────────────────────────────────────────────────── */

/* 状态栏那三样：连接点、左边的目录、右边的模型/权限/上下文/花费。
   **只有能据以行动的数才上状态栏** —— 上下文给百分比不给绝对 token，
   这条从终端版一路沿用（用户为那个绝对数字抱怨过两次）。 */

/* 接口那边的余额。**跟左边那个 ¥ 不是一回事**：那个是我们按你填的价格估的
   这一趟花了多少，这个是人家账上还剩多少。查不到就整个不显示 ——
   摆一个"—"在那儿只会让人以为坏了，而多数厂商本来就没有这个接口。 */
const LOW = 1;      // 少于这个数就染成 alert 色（单位跟着接口走，不换算）

function drawCredits(got) {
  const box = $("s-left");
  const sep = $("s-left-sep");
  if (!got || !got.shown) {
    box.hidden = sep.hidden = true;
    // 查不到的理由留在 title 上，鼠标停上去能看见，但不占地方
    box.title = (got && got.why) || "";
    return;
  }
  box.hidden = sep.hidden = false;
  box.textContent = "余 " + got.shown;
  box.dataset.low = typeof got.left === "number" && got.left < LOW ? "1" : "0";
  const bits = [];
  if (typeof got.total === "number") bits.push("充值 " + got.unit + got.total);
  if (typeof got.used === "number") bits.push("已用 " + got.unit + got.used.toFixed(4));
  bits.push("点一下现查一次");
  box.title = bits.join(" · ");
}

$("s-left").onclick = () => {
  $("s-left").textContent = "余 …";
  send({ op: "credits" });
};

/* 状态行上有两种话。
   一种是**现在是什么情况**：就绪 / 干活中 1:02 / 断了。随时可以重写，秒表
   每秒就重写一遍。另一种是**留言**：你刚点的那下没成（没连上、换不过去、
   agent 退了）。留言得等人看见，而秒表一秒冲一次 —— 不让开的话它活不过一秒，
   等于没说；0.50.x 定的「断线时不能默默吞掉」就是这么被吞掉的。

   **留言按住状态行，直到状态真的变了才让开**（连上了、这一轮完了、下一条留言）。
   让开的时机不拿秒数去猜：秒表该不该说话，取决于它说的还算不算数 —— 断着线的
   「干活中 12s」本来就是句瞎话。 */
let noteHeld = false;

function setDot(state, text) {
  noteHeld = false;
  $("dot").dataset.state = state;
  $("s-state").textContent = text;
}

/** 说一句要等人看见的话。秒表那一路不许冲掉它。 */
function sayOnce(state, text) {
  setDot(state, text);
  noteHeld = true;
}

/* ── 等了多久 ───────────────────────────────────────────────
   **一轮跑十分钟是真会发生的**（中转站 + 思考型模型，实测 622 秒），
   而屏幕上只写「干活中」的话，慢和死长得一模一样 —— 用户拿这个来问过
   「为什么卡死了」。终端那边的 spinner 本来就带计时，桌面端补上。

   过了 SLOW_AFTER 还没动静就把这句话说出来：它多半没坏，就是慢，
   而"要不要再等"只有用户自己判断得了。 */
const SLOW_AFTER = 90;
let waitFrom = 0;
let waitTick = 0;
let thoughtSize = 0;

function clock(secs) {
  const m = Math.floor(secs / 60);
  return m ? `${m}:${String(secs % 60).padStart(2, "0")}` : `${secs}s`;
}

/** `ambient` = 这一下是定时器自己跳的（秒表、迟来的「在跑」），不是刚发生了什么。 */
function drawWait(ambient) {
  if (!waitFrom) return;
  const secs = Math.round((Date.now() - waitFrom) / 1000);
  // 卡在你这儿的时候不算「慢」—— 那不是模型慢
  const slow = !pendingAsk && secs >= SLOW_AFTER;
  // 在想的时候说"在想"，别笼统地写"干活中"——那是这一版要治的病。
  // **等你点头的时候说「等你点头」**（0.80.0）：原来这时候秒表照样写「干活中 0:35」，
  // 可它什么都没在干，是在等你 —— 这句话一直在说反话
  const what = pendingAsk
    ? `等你点头 ${clock(Math.round((Date.now() - pendingAsk.from) / 1000))}`
    : thoughtSize ? `在想 ${clock(secs)}（已想 ${thoughtSize} 字）`
                  : `干活中 ${clock(secs)}`;
  // 留言还在的时候，定时器自己跳的那一下不写这行字；底下那块和想的轨迹照旧更新。
  // 挡的只有定时器：别处直接调进来的（起一轮、报想了多少字）都是「刚发生了
  // 什么」，那是该说话的。
  if (!(ambient && noteHeld))
    setDot(slow && !thoughtSize ? "slow" : "busy",
           slow && !thoughtSize ? `${what} · 比平时慢，还在等 · esc 中断`
                                : `${what} · esc 中断`);
  if (waitBlock) {
    waitBlock.querySelector(".churn__clock").textContent = clock(secs);
    waitBlock.querySelector(".churn__what").textContent =
      slow ? "等模型回话 · 比平时慢，还在等" : "等模型回话";
  }
  thinkHead();
}

function startWait() {
  if (waitFrom) return;                 // 已经在计了，别把秒数清零
  waitFrom = Date.now();
  thoughtSize = 0;
  drawWait();
  waitTick = setInterval(() => drawWait(true), 1000);
}

function stopWait() {
  clearInterval(waitTick);
  waitFrom = 0;
  waitTick = 0;
  thoughtSize = 0;
  showWait(false);
  closeThinking();
  dropAsk();          // 这一轮都停了，卡上的数字键不再算数
}

/* ── 想的过程 ───────────────────────────────────────────────
   0.58.0 起后端就报「想了多少字」，0.77.0 起连**原文**一起报。字数只能
   证明它还活着，原文才说得清它在往哪个方向走 —— 而「方向不对」是这类模型
   最贵的失败，越早看见越好。

   **在想的时候是展开的，想完自己收起来。** 想的过程有用是因为它**正在发生**；
   事后那一大段横在对话中间只会把真正说的话挤下去。人自己点过就听人的。 */
let think = null;         // { block, head, body, wrap, text, from, touched }
let thinkTimer = 0;

function thinkHead() {
  if (!think) return;
  const secs = Math.round((Date.now() - think.from) / 1000);
  think.head.querySelector(".think__what").textContent =
    think.done ? `想了 ${clock(secs)}` : `在想 ${clock(secs)}`;
}

function openThinking() {
  if (think) return;
  const body = block("在想", "block--think");
  body.innerHTML =
    '<div class="think" data-open="1">'
    + '<button class="think__head" type="button" aria-expanded="true">'
    + '<span class="think__mark" aria-hidden="true"></span>'
    + '<span class="think__what"></span>'
    + '<span class="think__chev" aria-hidden="true"></span>'
    + "</button>"
    + '<div class="think__wrap"><div class="think__body"><div class="prose"></div></div></div>'
    + "</div>";
  const box = body.firstElementChild;
  const it = {
    box,
    head: box.querySelector(".think__head"),
    prose: box.querySelector(".prose"),
    text: "",
    from: Date.now(),
    done: false,
    touched: false,      // 人自己点过展开/收起没有
  };
  /* **闭包里抓的是这一块自己（`it`），不是模块上那个 `think`。**
     `closeThinking()` 会把 `think` 置成 null（这一轮想完了），而这块轨迹还留在
     对话里 —— 抓外面那个变量的话，想完之后再点这个按钮就是在 null 上取属性。 */
  it.head.onclick = () => {
    it.touched = true;
    const open = it.box.dataset.open === "1";
    it.box.dataset.open = open ? "0" : "1";
    it.head.setAttribute("aria-expanded", String(!open));
  };
  think = it;
  thinkHead();
  return it;
}

function paintThinking() {
  thinkTimer = 0;
  if (!think) return;
  const wasDown = atBottom();
  paintInto(think.prose, mdParts(think.text));
  thinkHead();
  keepBottom(wasDown);
}

function closeThinking() {
  if (!think) return;
  clearTimeout(thinkTimer);
  thinkTimer = 0;
  paintInto(think.prose, mdParts(think.text));
  think.done = true;
  think.box.dataset.done = "1";      // 记号停下来，见 .think:not([data-done])
  thinkHead();
  if (!think.touched) {
    think.box.dataset.open = "0";
    think.head.setAttribute("aria-expanded", "false");
  }
  think = null;
}

/* ── 什么都还没发生的那一屏 ─────────────────────────────────
   一轮跑十分钟是真会发生的（实测 622 秒）。状态栏那个秒表是 0.58.0 补的，
   但**正文流里仍然一个字都没有** —— 而眼睛在正文流上。
   不思考的模型更是连「在想」那块都没有，这一块是它唯一的活体证明。 */
let waitBlock = null;
let waitArm = 0;
/* **等一下再画。** 快的模型一秒就开口，立刻画出来就是闪一下；而它真正要治的
   是"半分钟过去了屏幕上什么都没有"。另外工具是一条跑完接着报下一条的
   （串行那批 start/result 交替来），不等这一下的话每两条工具之间都会插进来一块。 */
const WAIT_AFTER = 800;

function armWait() {
  if (waitArm || waitBlock) return;
  waitArm = setTimeout(() => { waitArm = 0; showWait(true); }, WAIT_AFTER);
}

function showWait(on) {
  if (!on) {
    clearTimeout(waitArm);
    waitArm = 0;
    if (waitBlock) { waitBlock.closest(".block").remove(); waitBlock = null; syncHello(); }
    return;
  }
  if (waitBlock || !busy) return;
  waitBlock = block("在跑", "block--wait");
  waitBlock.innerHTML =
    '<div class="churn"><span class="churn__grid" aria-hidden="true">'
    + "<i></i>".repeat(9)
    + '</span><span class="churn__what">等模型回话</span>'
    + '<span class="churn__clock mono"></span></div>';
  // 这一下是 WAIT_AFTER 那个定时器带进来的，要的只是把块里的秒数填上 ——
  // 状态行该说什么没变，别把留言挤掉（断线之后它照样会跳，真踩到过）
  drawWait(true);
}

function shortPath(raw, cap = 46) {
  const short = (raw || "—").replace(/^\/home\/[^/]+/, "~");
  // 从左边裁、保住尾巴 —— 裁右边的话看到的是 /tmp/xxx/yyy/…，
  // 恰好把「在哪个项目」裁掉了。跟终端的 ui.clip_path 同一个道理
  return short.length > cap ? "…" + short.slice(-(cap - 1)) : short;
}

function setStatus(s) {
  if (!s) return;
  busy = !!s.busy;
  // 换模型那张清单底下那句「跑完才换」跟着 busy 走
  if (!modelsEl.hidden) drawModelsList();
  // **工作台不由 status 说了算。** 换台时旧进程可能还会再推一条 status 过来，
  // 让它写的话状态栏会闪回旧目录。桌面端以 setDesk 为准，浏览器里才用它。
  if (s.workspace && !TAURI) {
    $("s-cwd").textContent = shortPath(s.workspace);
    $("s-cwd").title = s.workspace;
  }
  $("model").textContent = s.ready ? (s.name || s.model || "—") : "未设置模型";
  const modes = { default: "默认", auto: "自动", plan: "计划", readonly: "只读" };
  $("s-mode").textContent = modes[s.mode] || s.mode || "—";
  $("s-mode").style.color = s.mode === "auto" ? "var(--alert)" : "";
  // **数字要带上它是什么。** 状态栏原来写「默认 · 23% · ¥0.0684 · v0.73.0」——
  // 23% 是什么的 23% 没人知道（是上下文占用），而四位小数是机器的精度不是人的：
  // 看一眼要判断的是"贵不贵"，不是"几厘几毫"。精确值留在 title 里，想查得到。
  if (s.context_limit) {
    const pct = Math.round((s.context / s.context_limit) * 100);
    $("s-ctx").textContent = "上下文 " + pct + "%";
    $("s-ctx").title = `${s.context} / ${s.context_limit} token`;
  }
  if (typeof s.cost === "number") {
    // 不到一分钱显示「<¥0.01」，别写一串 0 —— 那串 0 看着像精度，其实是噪音
    $("s-cost").textContent =
      !s.cost ? "¥0" : s.cost < 0.01 ? "<¥0.01" : "¥" + s.cost.toFixed(2);
    $("s-cost").title = "这段对话累计 ¥" + s.cost.toFixed(4);
  }
  drawCredits(s.credits);
  if (s.version) $("s-ver").textContent = "v" + s.version;
  $("send").hidden = busy;
  $("stop").hidden = !busy;
  if (busy) {
    startWait();
  } else {
    stopWait();
    setDot("ready", "就绪");
  }
}

/* 流式正文**不能每个 delta 画一次**。一个 token 一个 delta，而每次都要把整条消息
   重跑一遍 md() 再整块 innerHTML 重写（markdown 的记号跨 delta，追加 DOM 对不上，
   所以必须整份重渲染）——实测约 4.6 ms 一次，日志里真实出现过的 6696 token 那一轮
   就是三十秒主线程全占：窗口不重绘、点不动，看着就是卡死。
   合并到每帧最多画一次，几千份降到几百份，md() 那侧的累计开销也一起降下来。 */
const SAYING_GAP = 80;   // 两次重绘之间至少隔这么久（毫秒）

/* 把一份新渲染的结果贴到已经画着的那块上，**只动真正变了的那几个顶层块**。
 *
 * 0.68.1 把重绘从「每个 delta 一次」压到 12.5 次/秒，治的是总量；这儿治的是
 * 每一次的形状。整块 `innerHTML` 重写有三个代价，只有最后一个是性能：
 *
 *   一、**选区每 80ms 断一次** —— 模型说到一半想把上面那段话选出来复制，
 *       做不到，DOM 节点已经被换掉了。这是日常最疼的一个。
 *   二、已经定稿的段落每一帧都重建，所以没法只给**新出现**的那一段上淡入
 *       （给全部上的话就是每 80ms 全屏闪一次）。
 *   三、重排的代价跟整条消息的长度成正比，而不是跟这一帧改了多少成正比。
 *
 * 判定**不做任何序列化**：`mdParts` 直接给顶层块的字符串数组，跟上一帧缓存的
 * 那份逐个比即可。读 DOM 的 `outerHTML` 反过来也能比，但那是把整条消息
 * 重新序列化一遍，等于把刚省下的钱又花掉。
 *
 * **尾块就地改，不整个换掉。** 它每一帧都在长，换掉的话它每帧都是"刚出现"，
 * 淡入会每 80ms 重放一次。标签一样时只换孩子，节点本身留着。 */
function paintInto(host, parts) {
  const prev = host.__parts || [];
  let same = 0;
  while (same < parts.length && same < prev.length && prev[same] === parts[same]) same++;
  host.__parts = parts;

  const kids = [...host.children];
  // 只有最后一块变了、而且标签没变 —— 流式时绝大多数帧走这条
  if (same === parts.length - 1 && same === kids.length - 1
      && tagOf(parts[same]) === kids[same].tagName.toLowerCase()) {
    const box = document.createElement("div");
    box.innerHTML = parts[same];
    kids[same].replaceChildren(...box.firstElementChild.childNodes);
    return;
  }
  for (let n = kids.length - 1; n >= same; n--) kids[n].remove();
  parts.slice(same).forEach((html, n) => {
    const box = document.createElement("div");
    box.innerHTML = html;
    const el = box.firstElementChild;
    if (!el) return;
    // 第一块不上淡入：它跟外面那个 .block 的 rise 同时出现，两个动画叠一起
    if (same + n > 0) el.dataset.fresh = "1";
    host.append(el);
  });
}

const tagOf = (html) => (/^<([a-z0-9]+)/i.exec(html) || ["", ""])[1].toLowerCase();

/* 光标是**同一个节点搬来搬去**，不是每帧重建的 —— 重建的话 blink 那个动画
   每 80ms 从头开始，看着就是不闪。放进最后一块里面而不是 .prose 末尾：
   放外面的话「最后一块」永远是光标，`:not(:last-child)` 那条淡入规则就永远
   把真正的最后一段也算进去。 */
const cursorEl = document.createElement("span");
cursorEl.className = "cursor";

function paintSaying() {
  sayingTimer = 0;
  sayingPainted = performance.now();
  if (!saying) return;
  // 贴着底的话跟着长出来的字滚。**这一下以前是 WebKit 白送的**：`.sheet` 原来是
  // flex-end，产品那边的滚动原点在底边，内容往下长视野自己就跟着 —— 撤掉那条之后
  // （见 app.css `.sheet`）得自己跟，跟 paintThinking 同一个写法
  const wasDown = atBottom();
  const prose = saying.firstElementChild;
  paintInto(prose, mdParts(sayingText));
  (prose.lastElementChild || prose).append(cursorEl);
  keepBottom(wasDown);
}

/* **按固定间隔节流，不是按帧。** 一次重绘的代价随这条消息变长而线性上涨，
   按帧画的话一条流了一分钟的长消息要画几千次，加起来照样是十几秒的主线程；
   钉死 12.5 次/秒之后，总开销只跟"流了多久"有关，跟 delta 有多少无关。
   80 ms 的延迟人眼看不出来（光标还在闪）。 */
function scheduleSaying() {
  if (sayingTimer) return;
  const wait = Math.max(0, SAYING_GAP - (performance.now() - sayingPainted));
  sayingTimer = setTimeout(paintSaying, wait);
}

/* 一块正文写完了：重渲染一次去掉光标。
   **两个地方都要调** —— turn_end 是一种收尾，中途起了个工具调用也是一种，
   只管前者的话被打断的那块会一直留着一个闪的光标。
   **必须先把攒着的那一帧撤掉**：不撤的话它会在 saying 被置空之后才跑，
   要么什么都不画（还好），要么把光标又加回一块已经收尾的正文上。 */
function closeSaying() {
  if (sayingTimer) {
    clearTimeout(sayingTimer);
    sayingTimer = 0;
  }
  if (saying) {
    cursorEl.remove();
    paintInto(saying.firstElementChild, mdParts(sayingText));
    fillShots(saying);
    delete saying.dataset.live;
    // 人不在时一轮跑完，通知里要带它最后说的那句（见 notifyUser）
    if (sayingText.trim()) lastSaid = sayingText;
  }
  saying = null;
  sayingText = "";
}

function toolLine(ev) {
  const body = block("运行", "block--tool");
  const wrap = document.createElement("div");
  wrap.className = "tool";
  wrap.innerHTML =
    `<div class="tool__head"><span class="tool__name">${esc(ev.label || ev.name)}</span>` +
    (ev.target ? `<span class="tool__target">(${esc(ev.target)})</span>` : "") +
    `</div><div class="tool__pending">…</div>`;
  body.append(wrap);
  return wrap;
}

/* 长输出默认只露头几行，剩下的折起来。
 *
 * 一次 `bash npm test` 能回几百行，而**值得看的通常是前几行和"它挂没挂"**
 * —— 后者已经在 `tool__note` 那一行上了。几百行横在对话中间，上下文全被顶走，
 * 人得滚半天才找得回自己刚说的话。
 *
 * **摘要那行不折** —— 折起来就等于把"发生了什么"藏了。折的只有 detail。
 * 行数不到 `FOLD_AFTER` 的一个字不动：为十行输出加一个要点的按钮是净损失。 */
const FOLD_AFTER = 12;
const FOLD_KEEP = 6;

const SHUT = `calc(${FOLD_KEEP} * var(--leading-body))`;

/* 展开/收起的高度**必须量出来，不能按行数算** —— 行是会折的
   （`.tool__lines` 是 `pre-wrap` + `overflow-wrap: anywhere`），一条长命令
   占两行，按行数算出来的上限就会把末尾几行切掉，而且切了不报错。 */
function setFold(box, list, open) {
  box.dataset.open = open ? "1" : "0";
  if (REDUCED) { list.style.maxHeight = open ? "none" : SHUT; return; }
  list.style.maxHeight = list.scrollHeight + "px";   // 先钉在当前高度，none 过渡不了
  if (open) {
    // 动完把上限撤掉：内容之后再变（图加载完之类）不该被钉死在这个数上
    list.addEventListener("transitionend", function done() {
      list.removeEventListener("transitionend", done);
      if (box.dataset.open === "1") list.style.maxHeight = "none";
    });
  } else {
    requestAnimationFrame(() => { list.style.maxHeight = SHUT; });
  }
}

function foldLines(list, count) {
  if (count <= FOLD_AFTER) return list;
  const box = document.createElement("div");
  box.className = "fold";
  box.dataset.open = "0";
  list.style.maxHeight = SHUT;
  const more = document.createElement("button");
  more.type = "button";
  more.className = "fold__more";
  const say = () => {
    more.textContent = box.dataset.open === "1" ? "收起" : `还有 ${count - FOLD_KEEP} 行`;
  };
  more.onclick = () => {
    const wasDown = atBottom();
    setFold(box, list, box.dataset.open !== "1");
    say();
    keepBottom(wasDown);
  };
  say();
  box.append(list, more);
  return box;
}

/* 还没等到结果的那些工具块，**按调用 id 存**。
   并行时一批四条是「四条 tool_start 全来完，四条 tool_result 才开始来」，
   用一个 liveTool 槽位的话后三条结果会全挂到最后那条的标题下面 ——
   屏幕上就是四行一模一样的 `Fetch(同一个地址)` 配着四个不同的结果。
   用户拿截图来问过。 */
let toolBlocks = new Map();
/* 每次调用长什么样（标题要用）。确认卡插进来之后块被摘掉，
   结果要另起一块，那一块的标题得靠它。 */
let toolSeen = new Map();
let lastTool = null;

/* 事件里来的清单一律过这两道。**`ev.rows || []` 挡不住东西**：`{}` 是真值，
   `.forEach` 直接抛；数组里混一个 null，下一行 `row.name` 也抛。量出来是
   十处（`dev/fuzz_ui.mjs`）。服务端现在不会发这种形状，但 0.65.1 在另一半
   学过同一课 —— **保证不能建立在"下一支处理函数我会写对"上面**。
   分两个是因为 `paths` 那条来的是字符串，不是对象。 */
const rowsOf = (x) => (Array.isArray(x) ? x.filter((r) => r && typeof r === "object") : []);
const textsOf = (x) => (Array.isArray(x) ? x.filter((r) => typeof r === "string") : []);

function handle(ev) {
  const wasDown = atBottom();
  switch (ev.kind) {
    case "hello":
    case "status":
      setStatus(ev);
      break;

    case "resumed":
      rowsOf(ev.history).forEach((row) => {
        if (row.role === "user") block("你", "block--you").textContent = row.text;
        else block("它说", "block--say").innerHTML = `<div class="prose">${md(row.text)}</div>`;
      });
      block("提示", "block--note").innerHTML =
        `<p class="note">接上了上次的对话（${esc(ev.about || "")}）</p>`;
      break;

    case "turn_start":
      shownShots = new Set();
      closeSaying();
      turnFrom = Date.now();
      lastSaid = "";
      block("你", "block--you").textContent = ev.text;
      armWait();
      break;

    case "tool_start":
      closeSaying();
      closeThinking();
      showWait(false);
      lastTool = ev;
      toolSeen.set(ev.id || "", ev);
      toolBlocks.set(ev.id || "", toolLine(ev));
      break;

    case "thinking":
      /* 思考型模型可以想上十分钟一个字都不说。**这条事件是唯一的活体证明** ——
         数据一直在流，所以连超时都不会响，界面上却什么都没有。
         0.77.0 起它还带着想的原文（`delta`），画成一段可折叠的轨迹。
         **没有原文的后端照旧只更新秒表** —— 摆一块空轨迹在那儿更让人糊涂。 */
      thoughtSize = ev.size || 0;
      if (typeof ev.delta === "string" && ev.delta) {
        showWait(false);
        const t = think || openThinking();
        t.text += ev.delta;
        if (!thinkTimer) thinkTimer = setTimeout(paintThinking, SAYING_GAP);
      }
      drawWait();
      break;

    case "tool_progress": {
      // 参数还在生成 = 那条调用还没成形，只可能是最后开的那一块
      const live = [...toolBlocks.values()].pop();
      if (live) {
        const pending = live.querySelector(".tool__pending");
        if (pending) pending.textContent = "生成参数… " + ev.size + " 字";
      }
      break;
    }

    case "tool_result": {
      const cid = ev.id || "";
      // 找不到 = 中间插过一张确认卡（那时把块摘掉了）。**必须另起一块**，
      // 不然结果会画在授权它的那张卡上面，读起来像是先改了再问的
      const target = toolBlocks.get(cid)
        || toolLine(toolSeen.get(cid) || lastTool || { label: "结果" });
      toolBlocks.delete(cid);
      const pending = target.querySelector(".tool__pending");
      if (pending) pending.remove();
      const note = document.createElement("div");
      note.className = "tool__note";
      note.dataset.error = ev.is_error ? "1" : "0";
      note.textContent = ev.summary || "";
      target.append(note);
      const detail = rowsOf(ev.detail);
      if (detail.length) {
        const list = document.createElement("ul");
        list.className = "tool__lines";
        detail.forEach((row) => {
          const li = document.createElement("li");
          li.dataset.kind = row.kind || "";
          li.textContent = row.text;
          list.append(li);
        });
        target.append(foldLines(list, detail.length));
      }
      if (ev.shows && !ev.is_error) showShot(target, ev.shows);
      armWait();      // 工具跑完了，接下来又是等模型
      break;
    }

    case "catalog":
      // **档案是另一条事件推的，别在这儿把它抹掉** —— 整个对象赋值下去,
      // shelf.data.profiles 变 undefined，下一次 drawShelf 就在 .length 上抛
      shelf.data.skills = rowsOf(ev.skills);
      shelf.data.tools = rowsOf(ev.tools);
      shelf.data.mcp = rowsOf(ev.mcp);
      shelf.got = true;
      if (shelfOpen()) drawShelf();
      break;

    case "detail":
      shelf.bodies[{ profile: "档案:", bot: "机器人:" }[ev.what]
        ? { profile: "档案:", bot: "机器人:" }[ev.what] + ev.name : ev.name] =
        ev.body || (ev.what ? "" : "（这份技能没有正文）");
      if (shelfOpen()) drawShelf();
      if (botShopOpen()) drawBotShop();
      break;

    case "bots":
      bots = { rows: rowsOf(ev.rows), current: ev.current || "" };
      drawBots();
      break;

    case "models":
      models = ev;
      if (shelfOpen()) drawShelf();
      drawModelsList();
      break;

    case "settings":
      conf = ev;
      shelf.got = true;
      if (shelfOpen()) drawShelf();
      break;

    case "profiles":
      shelf.data.profiles = rowsOf(ev.rows);
      shelf.got = true;
      // 存完服务端会重推，这时候盘上那份才是真的 —— 把缓存的正文清掉重取
      rowsOf(ev.rows).forEach((r) => { if (shelf.draft[r.name] === undefined)
        delete shelf.bodies["档案:" + r.name]; });
      if (shelfOpen()) drawShelf();
      break;

    case "boot": {
      // 环境说明，不是对话：摆进状态栏，**不碰正文流**
      boots.set(ev.what, ev.text);
      const slot = $("s-boot");
      slot.textContent = [...boots.values()].join(" · ");
      slot.style.color = ev.level === "warn" ? "var(--alert)" : "";
      slot.hidden = false;
      $("s-boot-sep").hidden = false;
      break;
    }

    case "hook": {
      const body = block("钩子", "block--note");
      body.innerHTML = `<p class="note" data-level="${ev.ok ? "notice" : "error"}">${esc(ev.text)}</p>`;
      break;
    }

    case "text":
      thoughtSize = 0;          // 开口了就不再是"在想"
      closeThinking();          // 开口 = 上一段想完了，把轨迹收起来
      showWait(false);
      if (!saying) {
        saying = block("它说", "block--say");
        // `.prose` 要一次建好、之后只换它的孩子 —— paintInto 的缓存挂在它身上
        saying.innerHTML = '<div class="prose"></div>';
        saying.dataset.live = "1";
        sayingText = "";
      }
      sayingText += ev.delta;
      scheduleSaying();
      break;

    case "note":
      block(ev.level === "error" ? "出错" : ev.level === "warn" ? "注意" : "提示", "block--note")
        .innerHTML = `<p class="note" data-level="${esc(ev.level)}">${esc(ev.text)}</p>`;
      break;

    case "plan":
      block("方案", "block--say block--plan").innerHTML = `<div class="prose">${md(ev.text)}</div>`;
      break;

    case "preview":
      // 确认之前的 diff。挂在下一条 ask 上，这里先攒着。
      pendingPreview = rowsOf(ev.rows);
      break;

    case "ask":
      /* 卡要插在已经开着的那些工具行**下面**，所以先把它们全摘掉；
         结果回来时照 toolSeen 里那条另起一块，接在卡后面。
         **全摘不是只摘一条**：并行那批是「四条 start 全来完才开始问」，
         只摘一条的话另外三条的结果会画到授权它们的卡上面去。 */
      toolBlocks.forEach((el) => {
        const pending = el.querySelector(".tool__pending");
        if (pending) pending.textContent = "等你点头";
      });
      toolBlocks.clear();
      askCard(ev);
      break;

    case "turn_end": {
      closeSaying();
      toolBlocks.clear();
      toolSeen.clear();
      pendingPreview = null;
      dropAsk();
      // 人不在、而且跑得够久（过了 10 秒那条线，人多半已经去干别的了）：叫一声。
      // 回放进来的 turn_end 没有 turnFrom（那一轮不是这一页看着开始的），不会叫
      const secs = turnFrom ? Math.round((Date.now() - turnFrom) / 1000) : 0;
      if (away && secs >= LONG_TURN) {
        notifyUser(`跑完了（用时 ${clock(secs)}）`, plainLine(lastSaid) || "回来看看它做了什么");
      }
      turnFrom = 0;
      break;
    }

    case "sessions":
      drawTalks(rowsOf(ev.rows), ev.current);
      break;

    case "opened":
      showShelf(false);
      showBotShop(false);
      dropUnread();
      rowsOf(ev.history).forEach((row) => {
        if (row.role === "user") block("你", "block--you").textContent = row.text;
        else block("它说", "block--say").innerHTML = `<div class="prose">${md(row.text)}</div>`;
      });
      break;

    case "cleared":
      // 只清对话。刊头那条启动信息说的是环境，跟聊了什么无关，留着
      streamEl.replaceChildren();
      dropUnread();
      syncHello();
      break;

    case "paths":
      // `@` 补全的候选回来了 存下来再画一次：算候选的是服务端
      // （跟终端同一个 mentions.complete），前端不自己列目录
      mention = { prefix: ev.prefix || "", rows: textsOf(ev.rows) };
      showPalette();
      break;

    case "help":
      commands = rowsOf(ev.commands);
      cmdArgs = (ev.args && typeof ev.args === "object" && !Array.isArray(ev.args)) ? ev.args : {};
      // 连上时会自己发一次 /help 去取补全清单 —— **那一次不该画出来**，
      // 不然每次开页面都白白多一块。只有用户自己敲的那次才画。
      if (wantHelp) {
        wantHelp = false;
        const rows = commands.map((c) =>
          `<tr><td><code>${esc(c.name)}</code></td><td>${esc(c.about)}</td></tr>`).join("");
        block("命令", "block--say").innerHTML =
          `<div class="prose"><table><tbody>${rows}</tbody></table></div>`;
      }
      break;

    case "gone":
      online = false;
      // 它都没了，秒表还在那儿数「干活中」就是撒谎 —— 先把计时收掉再说话
      stopWait();
      sayOnce("off", "agent 已退出 · 关掉窗口重开");
      break;
  }
  keepBottom(wasDown);
}

let pendingPreview = null;

/* 这次确认危不危险 —— **决定它画不画成红的**。
 *
 * 红在这套设计里只有一个意思：「在一屏黑白里，需要你的东西一眼能找到」
 * （见 app.css 里 `--alert` 那段注释）。而原来**每一次确认都是红的**，
 * 包括改一行文件这种一天要点几十次的事。天天被红色叫住，红就不再是信号，
 * 只是背景噪音 —— 真出现 `rm -rf` 那次反而认不出来了。
 *
 * 所以只有「做了就回不去」的才吃红：删除、覆盖式的 git 操作、提权、写设备。
 * 判断放前端，跟 `ui.py` 那条分层原则一致：后端只给结构化事实，怎么画归这边。
 * **宁可漏判不可错判**：漏了只是少一次红，错了是天天见红、回到原点。
 */
const DANGER = [
  /\brm\s+-[a-z]*[rf]/,            // rm -rf / rm -f
  /\brmdir\b|\bunlink\b/,
  /\bgit\s+(push|reset\s+--hard|clean\s+-[a-z]*f|checkout\s+--\s)/,
  /\bsudo\b|\bsu\s+-/,
  /\bmkfs|\bdd\s+if=|\bshred\b/,
  /\bchmod\s+-R|\bchown\s+-R/,
  /\b(drop|truncate)\s+table\b/i,
  /:\s*>\s*\/dev\/|>\s*\/dev\/sd/,
  /\bkill(all)?\s+-9\b/,
  /\bnpm\s+publish\b|\bcargo\s+publish\b/,
];

function looksDangerous(ev) {
  if (ev.type !== "approve") return false;
  const cmd = String((ev.args && ev.args.command) || "");
  return DANGER.some((re) => re.test(cmd));
}

function askCard(ev) {
  if (seenAsk.has(ev.id)) return;      // 回放会把同一条 ask 再送一遍
  seenAsk.add(ev.id);
  const body = block("要你点头", "block--ask");
  const card = document.createElement("div");
  card.className = looksDangerous(ev) ? "ask ask--danger" : "ask";

  let question = "";
  let choices = [];
  if (ev.type === "approve") {
    question = `要 ${ev.label}${ev.target ? " " + ev.target : ""} 吗？`;
    // 读过外部内容之后要联网的那条（agent 的 `_egress`）：平时它不会停下来问，
    // 突然停了得一眼看出为什么 —— 光靠卡片上面那行提示，眼睛扫过去容易漏
    if (ev.args && ev.args.network) question += "（要联网）";
    // **按「点错了会怎样」排，不按「多常用」排。**
    // 点错「同意一次」最多白跑一步；点错「永久允许」是以后再也不问了 ——
    // 这两个原来并排、样式一样宽，手快很容易点到后面那个。
    // 现在前两个是代价最小的（同意这一次 / 拒绝），扩大授权的那两个
    // 靠 `.ask__row--wide` 推到右边、样式更弱，要点到得有意为之。
    choices = [
      ["yes", "同意一次", "btn--primary"],
      ["no", "拒绝", ""],
      // **写「本轮」不是「本次会话」**：它是 _run_tools 那一批的局部标志，
      // 下一轮照样会问。终端那边的键位提示写的也是「[a] 本轮全放」。
      ["all", "本轮都允许", "btn--wide"],
    ];
    if (ev.rule) choices.push(["save", "永久允许", "btn--wide", ev.rule]);
  } else if (ev.type === "plan") {
    question = "按这个方案动手吗？";
    choices = [["yes", "动手", "btn--primary"], ["no", "还有意见", ""]];
  } else if (ev.type === "trust") {
    const what = { hooks: "项目钩子", mcp: "项目 MCP 服务器", skills: "项目技能" }[ev.what] || ev.what;
    question = `${ev.where} 里有${what}（${(ev.names || []).join("、")}），要启用吗？`;
    choices = [["yes", "启用", "btn--primary"], ["no", "不启用", ""]];
  } else {
    question = ev.question || "";
    choices = (ev.options || []).map((o) => [o, o, ""]);
  }

  card.innerHTML = `<p class="ask__q">${esc(question)}</p>`;

  if (ev.type === "plan" && ev.plan) {
    const pre = document.createElement("div");
    pre.className = "prose";
    pre.innerHTML = md(ev.plan);
    card.append(pre);
  }
  if (ev.type === "approve" && ev.args) {
    const what = document.createElement("p");
    what.className = "ask__what";
    const shown = ev.args.command || ev.args.fact || ev.args.url || ev.args.path || "";
    if (shown) what.textContent = String(shown);
    if (what.textContent) card.append(what);
  }
  if (pendingPreview && pendingPreview.length) {
    const list = document.createElement("ul");
    list.className = "ask__diff";
    pendingPreview.forEach((row) => {
      const li = document.createElement("li");
      li.dataset.kind = row.kind || "";
      li.textContent = row.text;
      list.append(li);
    });
    card.append(list);
    pendingPreview = null;
  }

  const row = document.createElement("div");
  row.className = "ask__row";
  choices.forEach(([value, label, cls, rule]) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn " + cls;
    btn.textContent = label;
    // 规则是文件路径，单独包一层不参与大写（见 .btn code）
    if (rule) btn.insertAdjacentHTML("beforeend", ` <code>${esc(rule)}</code>`);
    btn.onclick = () => answerAsk(card, ev.id, value, label + (rule ? " " + rule : ""));
    row.append(btn);
  });
  // 自由输入那一路：ask_user 的选项常常不是用户真正想说的那句
  if (ev.type === "choice") {
    const other = document.createElement("button");
    other.type = "button";
    other.className = "btn";
    other.textContent = "自己写";
    other.onclick = () => ownAnswer(card, ev.id);
    row.append(other);
  }
  card.append(row);
  body.append(card);
  sheetEl.scrollTop = sheetEl.scrollHeight;
  armAsk(card, [...row.children], looksDangerous(ev));
  // 人不在跟前的话，屏幕上画得再醒目也没人看 —— 叫一声（见 notifyUser）。
  // bash 那条把命令原文带上：「要不要跑 rm -rf build」得看到命令才判断得了
  if (away) {
    const cmd = ev.args && ev.args.command ? "\n" + String(ev.args.command) : "";
    notifyUser(looksDangerous(ev) ? "要你点头（回不去的操作）" : "要你点头", question + cmd);
  }
}

/* ── 答一张卡 ───────────────────────────────────────────────
   一、**键盘能答**：按钮上是 1 2 3 4，跟摆的顺序一样（见文末那个 keydown）。
   二、卡片出现后**等一会儿才接键**。人可能正在打字 —— 卡片弹出来那一刻手指还在
       往下落，落下去的那个数字不该变成「同意」。键帽是到点才浮出来的，它浮出来
       就是「现在按才算数」。回不去的那档再多等一倍。
       （Firefox 装扩展、确认下载那种对话框，按钮也是先灰一下才能点，同一个道理。）
   三、**发不出去就别装作答了**：原来不管 send 成没成都把按钮换成「已回答」，
       断线时那是句假话 —— 0.50.x 定过「断线时不能默默吞掉」，这儿漏了。 */
const ARM_AFTER = 600;

function armAsk(card, keys, danger) {
  dropAsk();
  keys.slice(0, 9).forEach((btn, n) => {
    btn.insertAdjacentHTML("beforeend", `<kbd class="btn__key" aria-hidden="true">${n + 1}</kbd>`);
    btn.setAttribute("aria-keyshortcuts", String(n + 1));
  });
  const ask = { card, keys, from: Date.now(), timer: 0, pressing: false };
  ask.timer = setTimeout(() => { if (pendingAsk === ask) card.dataset.armed = "1"; },
                         danger ? ARM_AFTER * 2 : ARM_AFTER);
  pendingAsk = ask;
  drawWait();         // 状态行现在就改口说「等你点头」，别等秒表下一跳
}

/* 这张卡不再等键了（答了、这一轮停了、又来了一张新的）。键帽跟着收回去 */
function dropAsk() {
  if (!pendingAsk) return;
  clearTimeout(pendingAsk.timer);
  delete pendingAsk.card.dataset.armed;
  pendingAsk = null;
}

function answerAsk(card, id, value, shown) {
  if (card.classList.contains("ask--done")) return;
  if (!send({ op: "reply", id, value })) {
    if (pendingAsk && pendingAsk.card === card) pendingAsk.pressing = false;
    return;
  }
  if (pendingAsk && pendingAsk.card === card) dropAsk();
  // 从「一排按钮」收成「一行回执」，高度差二十来像素 —— 补一段过渡，底下的东西别跳
  settle(card, () => {
    card.classList.add("ask--done");
    const own = card.querySelector(".ask__own");
    if (own) own.remove();
    card.querySelector(".ask__row").replaceWith(receipt(shown));
  });
  drawWait();
}

/* 回执前面一个勾，是画出来的 —— 「收到了」，不用猜点没点上 */
function receipt(text) {
  const p = document.createElement("p");
  p.className = "ask__answer";
  p.innerHTML = '<svg class="ask__tick" viewBox="0 0 12 12" aria-hidden="true">'
    + '<path d="M2 6.5 5 9.5 10 3"/></svg>';
  p.append("已回答：" + text);
  return p;
}

/* 「自己写」：就在卡片里展开一行。原来是 `prompt()` —— 系统对话框，整个窗口
   卡住等它，长相跟这套排版毫无关系（删对话那块早就定了「不用 confirm() 弹窗」）。 */
function ownAnswer(card, id) {
  // 按数字键打开的也算「还没答」：收起来之后数字键得照样能用
  if (pendingAsk && pendingAsk.card === card) pendingAsk.pressing = false;
  let own = card.querySelector(".ask__own");
  if (!own) {
    own = document.createElement("form");
    own.className = "ask__own";
    own.innerHTML = '<input class="ask__own-box" type="text" spellcheck="false" autocomplete="off"'
      + ' placeholder="写你想说的…" aria-label="自己写的回答">'
      + '<button class="btn btn--primary" type="submit">发送</button>';
    const box = own.querySelector("input");
    own.onsubmit = (e) => {
      e.preventDefault();
      const said = box.value.trim();
      if (said) answerAsk(card, id, said, said);
      else box.focus();
    };
    // esc 只收起这一行 —— 全局那个 esc 在一轮跑着时会发 interrupt
    box.onkeydown = (e) => {
      if (e.key !== "Escape") return;
      e.stopPropagation();
      own.remove();
      inputEl.focus();
    };
    card.querySelector(".ask__row").after(own);
  }
  own.querySelector("input").focus();
}

/* ── 终端面板 ──────────────────────────────────────────────────
   一个真 pty 里的 shell，xterm 负责画。**它跟 agent 是两回事**：
   agent 跑命令走它自己那条 bash 工具（有权限门、有沙箱、结果进对话），
   这个面板是给你自己用的手。两边在同一个工作目录里，互不干涉。 */

let term = null;
let fit = null;
let termOn = false;

/* 配色跟全站一致：白纸墨字。xterm 默认是深色的，不给就是黑底。
   取的是 app.css 里那几个变量的实际值，改主题不用改两处。 */
function termTheme() {
  const css = getComputedStyle(document.documentElement);
  const ink = css.getPropertyValue("--text").trim() || "#000";
  return {
    background: css.getPropertyValue("--bg").trim() || "#fff",
    foreground: ink,
    cursor: css.getPropertyValue("--accent").trim() || "#000",
    selectionBackground: css.getPropertyValue("--selection").trim(),
    black: ink, red: css.getPropertyValue("--alert").trim(),
    blue: css.getPropertyValue("--accent").trim(),
    brightBlack: css.getPropertyValue("--faint").trim(),
  };
}

function openTerm() {
  if (term) return;
  const css = getComputedStyle(document.documentElement);
  term = new Terminal({
    fontFamily: css.getPropertyValue("--font-mono").trim() || "monospace",
    fontSize: 12,
    lineHeight: 1.35,
    cursorBlink: true,
    theme: termTheme(),
    // 面板不高，回滚多留一些
    scrollback: 5000,
  });
  fit = new FitAddon.FitAddon();
  term.loadAddon(fit);
  term.open($("term-body"));
  fit.fit();
  term.onData((data) => TAURI.core.invoke("term_write", { data }).catch(() => {}));
  term.onResize(({ cols, rows }) =>
    TAURI.core.invoke("term_resize", { cols, rows }).catch(() => {}));
  TAURI.event.listen("feiyu://term", (e) => term.write(e.payload));
  // **起 shell 要带上当前工作台**，不然它落在这个进程的 cwd 上，跟 agent 不在一处
  TAURI.core.invoke("term_start", {
    cwd: deskNow, cols: term.cols, rows: term.rows,
  }).catch((err) => term.write("\r\n起不来：" + err + "\r\n"));
  // 面板尺寸跟着窗口走
  new ResizeObserver(() => { try { fit.fit(); } catch { /* 面板收着的时候量不了 */ } })
    .observe($("term-body"));
}

function showTerm(on) {
  if (!TAURI) return;                 // 浏览器里没有 pty，这个面板没有意义
  termOn = on;
  $("term").hidden = !on;
  if (!on) return;
  openTerm();
  // 收起再展开时尺寸会变，重量一次；聚焦到终端，不然还得点一下
  requestAnimationFrame(() => { try { fit.fit(); } catch {} term.focus(); });
}

if (TAURI) {
  $("act-term").onclick = () => showTerm($("term").hidden);
  $("term-close").onclick = () => showTerm(false);
  document.addEventListener("keydown", (e) => {
    if (e.key === "`" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); showTerm($("term").hidden); }
  });
} else {
  $("act-term").hidden = true;
}

/* ── 技能与工具 ─────────────────────────────────────────────
   三份清单（技能 / 内置工具 / MCP 服务器），每项一个开关。关掉的东西
   既不进系统提示也不挂进请求 —— 判定在 Python 那边（feiyu/enabled.py），
   这里只负责画和把开关的动作发过去。

   **正文是点开才要的**：五十份技能各上万字，一次全推过来几百 KB，
   而这一屏一次只看得见一份。所以 catalog 里没有 body，选中时另发一条 detail。 */

const shelf = { tab: "general", find: "", pick: {},
                data: { general: [], skills: [], tools: [], mcp: [], profiles: [] },
                bodies: {}, got: false, draft: {} };

function shelfOpen() { return !$("shelf").hidden; }

function showShelf(on) {
  $("shelf").hidden = !on;
  // 它跟对话共占一格：不把这几样收起来的话两屏会叠在一起
  $("sheet").hidden = on;
  $("composer").hidden = on;
  $("nav-shelf").setAttribute("aria-pressed", String(on));
  if (on) {
    if (termOn) showTerm(false);
    if (botShopOpen()) showBotShop(false);
    send({ op: "catalog" });
    send({ op: "profiles" });
    send({ op: "settings" });
    // 藏着的元素量出来的 offsetWidth 是 0 —— 滑线要等它真的摆出来才量得到
    requestAnimationFrame(() => { $("shelf-find").focus(); syncInds(); });
  }
}

function shelfRows() {
  const want = shelf.find.trim().toLowerCase();
  const rows = shelf.tab === "general"
    // 服务端还没回话时先只列本机那几项 —— 它们不依赖 conf，没理由一起等
    ? SETTINGS.filter((r) => conf || r.local)
        .map((r) => ({ name: r.name, description: r.about, on: true }))
    : (shelf.data[shelf.tab] || []);
  if (!want) return rows;
  return rows.filter((r) =>
    (r.name + " " + (r.description || "") + " " + (r.source || "")).toLowerCase().includes(want));
}

function drawShelf() {
  $("n-skills").textContent = shelf.data.skills.length || "";
  $("n-tools").textContent = shelf.data.tools.length || "";
  $("n-mcp").textContent = shelf.data.mcp.length || "";
  $("n-profiles").textContent = shelf.data.profiles.length || "";
  // 底下那行说明跟着页签走 —— 档案存的不是开关，说成开关是错的
  // 底下那两行跟着页签走 —— 说错了比不说更糟（"开关记在 enabled.json"
  // 摆在设置页上是假话，那一页一个开关都没有）
  const FOOT = {
    general: ["按一下就生效，跟在终端敲那条斜杠命令是同一回事",
              "密钥只进不出：存下之后界面再也读不到它"],
    profiles: ["一份档案就是 ~/.config/feiyu/profiles/ 下的一个 .md",
               "人格设定每一轮都会发给模型，别写太长。换一份立刻生效。"],
  };
  const foot = FOOT[shelf.tab]
    || ["开关记在 ~/.config/feiyu/enabled.json",
        "关掉的东西既不进它的上下文，也调不出来。改动立刻生效。"];
  $("shelf-where").textContent = foot[0];
  $("shelf-hint").textContent = foot[1];
  const list = $("shelf-list");
  list.textContent = "";
  const rows = shelfRows();
  if (!rows.length) {
    const li = document.createElement("li");
    li.className = "shelf__empty";
    // **还没收到清单 ≠ 这台机器上没有** —— 两种情况长得一模一样，得分开说
    li.textContent = !shelf.got ? "读取中…"
      : shelf.find ? "没有对得上的" : emptyWord(shelf.tab);
    list.append(li);
    $("shelf-pane").textContent = "";
    return;
  }
  const picked = shelf.pick[shelf.tab];
  if (!rows.some((r) => r.name === picked)) shelf.pick[shelf.tab] = rows[0].name;
  rows.forEach((row) => {
    const li = document.createElement("li");
    li.className = "shelf__row";
    li.dataset.on = row.on ? "1" : "0";
    li.setAttribute("aria-current", String(row.name === shelf.pick[shelf.tab]));
    const left = document.createElement("div");
    left.innerHTML = `<div class="shelf__name"></div><div class="shelf__meta"></div>`;
    left.querySelector(".shelf__name").textContent = row.name;
    left.querySelector(".shelf__meta").textContent = rowMeta(row);
    if (shelf.tab === "general") {
      const now = document.createElement("span");
      now.className = "shelf__now";
      const spec = SETTINGS.find((r) => r.name === row.name);
      now.textContent = spec && (conf || spec.local) ? spec.meta(conf) : "";
      // 这一列只有一行高，长的值会被切掉 —— 停上去看得到完整的那份
      if (now.textContent) now.title = now.textContent;
      li.append(left, now);
    } else if (shelf.tab === "profiles") {
      /* **档案不给开关。** 它是「用哪一份」不是「开不开」—— 单选套上双态开关的皮，
         人会以为可以两份一起开。当前那份靠 ● 标出来，换用在详情栏里按。 */
      const mark = document.createElement("span");
      mark.className = "pick-dot";
      mark.textContent = row.on ? "●" : "";
      li.append(left, mark);
    } else {
      const sw = document.createElement("button");
      sw.className = "sw";
      sw.type = "button";
      sw.setAttribute("role", "switch");
      sw.setAttribute("aria-checked", String(!!row.on));
      sw.setAttribute("aria-label", (row.on ? "关掉 " : "打开 ") + row.name);
      if (row.locked) sw.disabled = true;
      // **开关必须 stopPropagation**：不然按一下开关的同时把这一项也选中了 ——
      // 跟删对话那个叉是同一类错
      sw.onclick = (e) => {
        e.stopPropagation();
        if (row.locked) return;
        send({ op: "toggle", kind: KIND_OF[shelf.tab], name: row.name, on: !row.on });
      };
      li.append(left, sw);
    }
    li.onclick = () => { shelf.pick[shelf.tab] = row.name; drawShelf(); };
    list.append(li);
  });
  if (shelf.tab === "profiles" && !shelf.find) list.append(newProfileRow());
  drawShelfPane(rows.find((r) => r.name === shelf.pick[shelf.tab]));
}

/* 新建走**就地的一行输入**，不用 window.prompt ——
   那在 webview 里是个模态框，一弹起来整个界面就不响应了（alert/confirm 同理）。
   跟侧栏删对话的确认是同一个路子：把那一行原地换掉。 */
function newProfileRow() {
  const li = document.createElement("li");
  li.className = "shelf__row shelf__row--new";
  li.innerHTML = `<div><div class="shelf__name">＋ 新建一份</div>` +
                 `<div class="shelf__meta">起一个新人格</div></div>`;
  li.onclick = () => {
    li.innerHTML = `<input class="newname" placeholder="档案名字，回车确认…"` +
                   ` spellcheck="false" autocomplete="off">`;
    const box = li.querySelector(".newname");
    box.focus();
    box.onkeydown = (e) => {
      if (e.key === "Escape") { e.stopPropagation(); drawShelf(); return; }
      if (e.key !== "Enter") return;
      const name = box.value.trim();
      if (!name) return drawShelf();
      shelf.pick.profiles = name;
      send({ op: "profile", action: "save", name,
             body: "你是……（在这里写这份人格：它是谁、怎么说话、什么不做）" });
    };
    box.onblur = () => { if (li.isConnected) drawShelf(); };
  };
  return li;
}

const KIND_OF = { skills: "skill", tools: "tool", mcp: "mcp" };

/* ── 外观 ───────────────────────────────────────────────────
   **主题是这个窗口自己的偏好**，跟侧栏收放、置顶一样存在浏览器里，不进协议。
   终端那边有自己的 `/theme`（换的是 ANSI 配色和记号），两边刻意不共用一个值 ——
   同一个名字在两处指的不是同一套东西，共用只会互相踩。 */
const THEME_KEY = "feiyu.theme";
const THEMES = [
  { key: "light", label: "默认", about: "纯白纸、IBM Blue，国际现代主义那套" },
  { key: "retro", label: "复古", about: "泛黄的纸、暖墨、赭色。只换配色，排版一个字没动" },
  { key: "anthropic", label: "Anthropic",
    about: "暖中性的纸、陶土橘。跟这台机器上 kitty / GTK / 壁纸同一套色" },
];

function theme() {
  try { return localStorage.getItem(THEME_KEY) || "light"; } catch { return "light"; }
}

function setTheme(name) {
  const want = THEMES.some((t) => t.key === name) ? name : "light";
  // 默认那套就是裸 :root，不挂属性 —— 常态不占位置
  if (want === "light") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = want;
  try { localStorage.setItem(THEME_KEY, want); } catch { /* 无痕窗口 */ }
  // 终端面板的配色是建的时候拿 CSS 变量算出来的一份快照，**换主题要重算**，
  // 不然它会顶着上一套配色留在那儿（termTheme() 只是算，不会自己应用）
  if (term) term.options.theme = termTheme();
  // 换了皮字号就变了，空对话那一组要重新摆（见 measureLift）。
  // **放进下一帧**：这个函数在模块求值时就跑一次，那时 rowBase 还在 TDZ 里
  requestAnimationFrame(() => { rowBase = 0; measureLift(); });
}

setTheme(theme());

/* ── 机器人 ─────────────────────────────────────────────────
   跟"会话"平级摆在侧栏顶上，**不在设置里**：设置是"现在这段对话怎么跑"，
   机器人是"开一段什么样的新对话"。点一个 = 新开一段，连人格、模型、
   权限模式、风格一起带上。

   人格那个位置跟配置档案是**共用**的（agent._persona）——一段对话要么套着
   机器人、要么套着档案，不会同时套两个。 */
let bots = { rows: [], current: "" };
let botPick = null;      // 编辑器里选中哪个；"" 表示在新建

function drawBots() {
  const list = $("bots");
  list.textContent = "";
  bots.rows.forEach((row) => {
    const li = document.createElement("li");
    li.className = "talk" + (row.on ? " talk--now" : "");
    li.innerHTML = `<div class="talk__title"></div><div class="bot__what"></div>`;
    li.querySelector(".talk__title").textContent = row.name;
    li.querySelector(".bot__what").textContent =
      [row.description, row.model, row.mode && row.mode + " 模式"]
        .filter(Boolean).join(" · ");
    const edit = document.createElement("button");
    edit.type = "button";
    edit.className = "bot__edit";
    edit.textContent = "改";
    // **要 stopPropagation**：不然点「改」的同时把这个机器人也开起来了 ——
    // 跟侧栏删对话那个叉是同一类错
    edit.onclick = (e) => { e.stopPropagation(); botPick = row.name; showBotShop(true); };
    li.append(edit);
    li.onclick = () => send({ op: "bot", do: "start", name: row.name });
    list.append(li);
  });

  const make = document.createElement("li");
  make.className = "talk talk--new";
  make.innerHTML = `<div class="talk__title">＋ 新建机器人</div>`
                 + `<div class="bot__what">人格 + 模型 + 权限模式，一键开一段</div>`;
  make.onclick = () => { botPick = ""; showBotShop(true); };
  list.append(make);

  if (bots.current) {
    const off = document.createElement("li");
    off.className = "talk";
    off.innerHTML = `<div class="talk__title">摘掉「${esc(bots.current)}」</div>`
                  + `<div class="bot__what">开一段不套人格的新对话</div>`;
    off.onclick = () => send({ op: "bot", do: "start", name: "" });
    list.append(off);
  }
  if (botShopOpen()) drawBotShop();
}

function setSideTab(which) {
  $("side-talks").hidden = which !== "talks";
  $("side-bots").hidden = which !== "bots";
  $("side-find-wrap").hidden = which !== "talks";
  [...$("side-tabs").children].forEach((b) =>
    b.setAttribute("aria-selected", String(b.dataset.side === which)));
  slideInd($("side-tabs"));
  if (which === "bots") send({ op: "bots" });
}

$("side-tabs").onclick = (e) => {
  const tab = e.target.closest(".side__tab");
  if (tab) setSideTab(tab.dataset.side);
};

/* 编辑器：跟设置面板占同一格，所以开一个要把另一个收起来 */
function botShopOpen() { return !$("botshop").hidden; }

function showBotShop(on) {
  if (on && shelfOpen()) showShelf(false);
  $("botshop").hidden = !on;
  $("sheet").hidden = on;
  $("composer").hidden = on;
  if (on) { send({ op: "bots" }); drawBotShop(); requestAnimationFrame(syncInds); }
}

$("bot-close").onclick = () => showBotShop(false);
$("bot-find").oninput = () => drawBotShop();

const BOT_FIELDS = [
  ["name", "名字", "侧栏里显示成这个", "text"],
  ["description", "一句话", "这个机器人是干嘛的", "text"],
  ["model", "模型", "留空就用当前那条", "pick-model"],
  ["mode", "权限模式", "留空就不改", "pick-mode"],
  ["style", "回答风格", "留空就不改", "pick-style"],
];

function drawBotShop() {
  const list = $("bot-list");
  const pane = $("bot-pane");
  list.textContent = "";
  pane.textContent = "";
  const want = $("bot-find").value.trim().toLowerCase();
  const rows = bots.rows.filter((r) =>
    !want || (r.name + " " + (r.description || "")).toLowerCase().includes(want));
  rows.forEach((row) => {
    const li = document.createElement("li");
    li.className = "shelf__row";
    li.setAttribute("aria-current", String(row.name === botPick));
    li.innerHTML = `<div><div class="shelf__name"></div><div class="shelf__meta"></div></div>`;
    li.querySelector(".shelf__name").textContent = row.name;
    li.querySelector(".shelf__meta").textContent =
      [row.model, row.mode].filter(Boolean).join(" · ") || "跟当前一样";
    li.onclick = () => { botPick = row.name; drawBotShop(); };
    list.append(li);
  });
  const make = document.createElement("li");
  make.className = "shelf__row shelf__row--new";
  make.innerHTML = `<div><div class="shelf__name">＋ 新建一个</div>`
                 + `<div class="shelf__meta">起一个新机器人</div></div>`;
  make.onclick = () => { botPick = ""; drawBotShop(); };
  list.append(make);

  const row = bots.rows.find((r) => r.name === botPick) || {};
  const head = document.createElement("h2");
  head.textContent = botPick || "新建一个机器人";
  const lead = document.createElement("p");
  lead.className = "lead";
  lead.textContent = "点侧栏里的它 = 开一段新对话，连模型和权限模式一起带上。"
                   + "人格设定每一轮都会发给模型，别写太长。";
  pane.append(head, lead);

  const got = {};
  const box = document.createElement("div");
  box.className = "form";
  BOT_FIELDS.forEach(([key, label, hint, kind]) => {
    const line = document.createElement("label");
    line.className = "form__row";
    line.innerHTML = `<span class="form__name"></span>`;
    line.querySelector(".form__name").textContent = label;
    let input;
    if (kind === "text") {
      input = document.createElement("input");
      input.className = "line";
      input.value = row[key] ?? "";
      input.placeholder = hint;
      input.spellcheck = false;
      if (key === "name" && botPick) input.readOnly = true;
    } else {
      input = document.createElement("select");
      input.className = "line";
      const opts = kind === "pick-model" ? (conf ? conf.models : [])
                 : kind === "pick-mode" ? (conf ? conf.modes.map((m) => m.key) : [])
                 : (conf ? conf.styles.map((m) => m.key) : []);
      [""].concat(opts).forEach((v) => {
        const o = document.createElement("option");
        o.value = v;
        o.textContent = v || "（不改）";
        if ((row[key] || "") === v) o.selected = true;
        input.append(o);
      });
    }
    input.onkeydown = (e) => { if (e.key === "Escape") e.stopPropagation(); };
    got[key] = input;
    line.append(input);
    box.append(line);
  });

  const soul = document.createElement("textarea");
  soul.className = "soul";
  soul.spellcheck = false;
  soul.placeholder = "你是……（在这里写这个机器人的人格：它是谁、怎么说话、什么不做）";
  const cached = shelf.bodies["机器人:" + botPick];
  soul.value = botPick ? (cached === undefined ? "读取中…" : cached) : "";
  if (botPick && cached === undefined) send({ op: "detail", kind: "bot", name: botPick });
  box.append(soul);
  pane.append(box);

  const acts = document.createElement("div");
  acts.className = "acts";
  const save = document.createElement("button");
  save.type = "button";
  save.className = "act act--go";
  save.textContent = "保存";
  save.onclick = () => {
    const name = (got.name.value || "").trim();
    if (!name) return;
    const out = { body: soul.value };
    ["description", "model", "mode", "style"].forEach((k) => { out[k] = got[k].value; });
    delete shelf.bodies["机器人:" + name];
    botPick = name;
    send({ op: "bot", do: "save", name, row: out });
  };
  const go = document.createElement("button");
  go.type = "button";
  go.className = "act";
  go.textContent = "开一段";
  go.disabled = !botPick;
  go.onclick = () => { showBotShop(false); send({ op: "bot", do: "start", name: botPick }); };
  acts.append(save, go);
  if (botPick) {
    const gone = document.createElement("button");
    gone.type = "button";
    gone.className = "act act--gone";
    gone.textContent = "删掉";
    gone.onclick = () => {
      if (!gone.dataset.sure) {
        gone.dataset.sure = "1";
        gone.textContent = "真的删掉？";
        return;
      }
      const name = botPick;
      botPick = null;
      send({ op: "bot", do: "drop", name });
    };
    acts.append(gone);
  }
  pane.append(acts);
}

/* ── 设置 ───────────────────────────────────────────────────
   一行一项，左边清单选哪一项，右边改。**改动一律走斜杠命令** ——
   界面上按一下和在终端敲 `/mode auto` 是同一条代码，不另写一套保存逻辑。
   服务端收到命令之后会把 settings 重推一遍，界面不自己猜结果。 */
let conf = null;

/* 每一项：名字、右边那栏怎么画。顺序就是清单的顺序。 */
const SETTINGS = [
  // local: 这一项不经服务端，改的是这个窗口自己的偏好
  { key: "theme", name: "外观", local: true,
    meta: () => (THEMES.find((t) => t.key === theme()) || THEMES[0]).label,
    about: "只换配色，排版和字体一个不动 —— 终端那边的 /theme 是另一套，互不相干" },
  { key: "mode", name: "权限模式", meta: (c) => label(c.modes, c.mode),
    about: "它动手之前要不要先问你" },
  { key: "style", name: "回答风格", meta: (c) => label(c.styles, c.style),
    about: "只影响它怎么说话，不换模型" },
  { key: "model", name: "模型", meta: (c) => c.model || "没设置",
    about: "主对话用哪一条配置。可以在这儿加、改、删 —— "
         + "已经存下的 key 不会发回来，所以改别的时候留空就是不动它" },
  { key: "helper", name: "旁活模型", meta: (c) => c.helper || "跟主模型一样",
    about: "压缩摘要、子 agent、网页提炼用哪条便宜的。省的是这些看不见的开销" },
  { key: "sandbox", name: "bash 沙箱", meta: (c) => c.sandbox.real
      + (c.sandbox.real === c.sandbox.want ? "" : "（想要 " + c.sandbox.want + "）"),
    about: "写是真隔离（内核的 mount namespace）；读只是挖空了几个目录，别当成读也隔离了" },
  { key: "verify", name: "校验命令", meta: (c) => c.verify || "没配",
    about: "它改完文件就自动跑一遍，不过就把报错交给它自己修" },
  { key: "allow", name: "权限规则", meta: (c) => c.allow.length + " 条放行"
      + (c.deny.length ? "、" + c.deny.length + " 条拒绝" : ""),
    about: "命中放行的直接跑不问你。确认时按「永久允许」也会写到这儿" },
  { key: "memory", name: "长期记忆", meta: (c) => c.memory.length + " 条",
    about: "它自己记下来的、跨会话带着的事实。只对这个工作台生效" },
];

function label(rows, key) {
  const hit = (rows || []).find((r) => r.key === key);
  return hit ? hit.label : key;
}

/* 改一项 = 发一条斜杠命令。**不要在这儿自己写盘** */
function tune(text) {
  send({ op: "command", text });
}

function pickRow(now, options, onPick) {
  const box = document.createElement("div");
  box.className = "opts";
  options.forEach((o) => {
    const one = document.createElement("button");
    one.type = "button";
    one.className = "opt" + (o.key === now ? " opt--now" : "");
    one.innerHTML = `<span class="opt__name"></span><span class="opt__about"></span>`;
    one.querySelector(".opt__name").textContent = o.label;
    one.querySelector(".opt__about").textContent = o.about || "";
    one.onclick = () => onPick(o.key);
    box.append(one);
  });
  return box;
}

function drawSetting(pane, key) {
  const c = conf;
  if (key === "theme") {
    const head = document.createElement("h2");
    head.textContent = "外观";
    const lead = document.createElement("p");
    lead.className = "lead";
    lead.textContent = SETTINGS[0].about;
    pane.append(head, lead);
    pane.append(pickRow(theme(), THEMES, (k) => { setTheme(k); drawShelf(); }));
    return;
  }
  const spec = SETTINGS.find((r) => r.key === key) || SETTINGS[0];
  const head = document.createElement("h2");
  head.textContent = spec.name;
  const lead = document.createElement("p");
  lead.className = "lead";
  lead.textContent = spec.about;
  pane.append(head, lead);

  if (key === "mode") {
    pane.append(pickRow(c.mode, c.modes, (k) => tune("/mode " + k)));
  } else if (key === "style") {
    pane.append(pickRow(c.style, c.styles, (k) => tune("/style " + k)));
  } else if (key === "model") {
    drawModels(pane);
  } else if (key === "helper") {
    pane.append(pickRow(c.helper || "off",
      [{ key: "off", label: "跟主模型一样", about: "不额外省，也不多一条配置要管" }]
        .concat(c.models.map((m) => ({ key: m, label: m }))),
      (k) => tune("/helper " + k)));
  } else if (key === "sandbox") {
    const about = { off: "不隔离，它能写工作区外面的东西",
                    workspace: "工作区和 /tmp 之外只读", strict: "再加上断网" };
    pane.append(pickRow(c.sandbox.want,
      c.sandbox.levels.map((l) => ({ key: l, label: l, about: about[l] || "" })),
      (k) => tune("/sandbox " + k)));
    if (c.sandbox.why) {
      const why = document.createElement("p");
      why.className = "note";
      why.dataset.level = "warn";
      why.textContent = "实际没生效：" + c.sandbox.why;
      pane.append(why);
    }
  } else if (key === "verify") {
    pane.append(oneLine(c.verify, "比如 npm test 或 .venv/bin/python -m unittest discover",
                        (v) => tune("/verify " + (v || "off"))));
  } else if (key === "allow") {
    pane.append(oneLine("", "加一条，比如 Bash(npm test:*)", (v) => v && tune("/allow " + v)));
    pane.append(ruleList(c.allow, "放行"), ruleList(c.deny, "拒绝"));
  } else if (key === "memory") {
    pane.append(memoryList(c.memory));
  }
}

/* ── 模型配置 ───────────────────────────────────────────────
   能加、能改、能删。**唯一守死的一条：key 只进不出** ——
   服务端从来不把已经存下的密钥发给前端（`push_models` 只给一个 has_key 布尔），
   所以前端就算被人拿下也读不走。改价格、改上限时 key 留空就是"不动原来那条"。

   写这条路只在桌面端开着（它不监听端口）；devserver 听在回环上，同机任何进程
   都能连，所以那边一律拒（`Server.trusted`）。 */
let models = null;
let editing = null;      // 正在编辑哪一条；"" 表示在新建

function drawModels(pane) {
  if (!models) {
    const wait = document.createElement("p");
    wait.className = "note";
    wait.textContent = "读取中…";
    pane.append(wait);
    send({ op: "models" });
    return;
  }
  if (!models.trusted) {
    const why = document.createElement("p");
    why.className = "note";
    why.dataset.level = "warn";
    why.textContent = "这是开发用的浏览器入口，改不了密钥。桌面端窗口里可以改。";
    pane.append(why);
  }
  const list = document.createElement("div");
  list.className = "opts";
  models.rows.forEach((row) => {
    const one = document.createElement("button");
    one.type = "button";
    one.className = "opt" + (row.using ? " opt--now" : "");
    one.innerHTML = `<span class="opt__name"></span><span class="opt__about"></span>`;
    one.querySelector(".opt__name").textContent = row.name;
    one.querySelector(".opt__about").textContent =
      row.model + (row.vision ? " · 认图" : "") + (row.helper ? " · 旁活用它" : "")
      + (row.has_key ? "" : " · 没有 key");
    one.onclick = () => tune("/model " + row.name);
    const edit = document.createElement("span");
    edit.className = "opt__edit";
    edit.textContent = "改";
    edit.onclick = (e) => { e.stopPropagation(); editing = row.name; drawShelf(); };
    one.append(edit);
    list.append(one);
  });
  pane.append(list);

  const acts = document.createElement("div");
  acts.className = "acts";
  const add = document.createElement("button");
  add.type = "button";
  add.className = "act";
  add.textContent = "＋ 加一条";
  add.disabled = !models.trusted;
  add.onclick = () => { editing = ""; drawShelf(); };
  acts.append(add);
  pane.append(acts);

  if (editing !== null && models.trusted) pane.append(modelForm());
}

const KEY_FIELDS = [
  ["name", "名称", "在 /model 里显示成这个", "text"],
  ["base_url", "接口地址", "多数接口填到 /v1 就行，留空走官方地址", "text"],
  ["model", "模型 id", "厂商那边叫什么就填什么", "text"],
  ["key", "API key", "留空 = 不动原来那条", "password"],
  ["max_tokens", "单轮输出上限", "不填按 8192；写长文件容易撞", "number"],
  ["limit", "上下文上限", "不填按 128k，撞过一次它会自己学", "number"],
];

function modelForm() {
  const row = models.rows.find((r) => r.name === editing) || {};
  const box = document.createElement("div");
  box.className = "form";
  const head = document.createElement("div");
  head.className = "rules__head";
  head.textContent = editing ? "改 " + editing : "加一条";
  box.append(head);

  const got = {};
  KEY_FIELDS.forEach(([key, label, hint, type]) => {
    const line = document.createElement("label");
    line.className = "form__row";
    line.innerHTML = `<span class="form__name"></span>`;
    line.querySelector(".form__name").textContent = label;
    const input = document.createElement("input");
    input.className = "line";
    input.type = type === "password" ? "password" : "text";
    input.value = key === "key" ? "" : (row[key] ?? "");
    input.placeholder = key === "key" && row.has_key ? "已设置，留空就不动" : hint;
    input.spellcheck = false;
    if (key === "name" && editing) input.readOnly = true;
    input.onkeydown = (e) => { if (e.key === "Escape") e.stopPropagation(); };
    got[key] = input;
    line.append(input);
    box.append(line);
  });

  const kind = document.createElement("label");
  kind.className = "form__row";
  kind.innerHTML = `<span class="form__name">接口类型</span>`;
  const pickKind = document.createElement("select");
  pickKind.className = "line";
  [["openai", "OpenAI 兼容（多数中转站）"], ["anthropic", "Anthropic 官方"]]
    .forEach(([v, t]) => {
      const o = document.createElement("option");
      o.value = v; o.textContent = t;
      if ((row.kind || "openai") === v) o.selected = true;
      pickKind.append(o);
    });
  kind.append(pickKind);
  box.append(kind);

  const sees = document.createElement("label");
  sees.className = "form__row";
  sees.innerHTML = `<span class="form__name">能看图</span>`;
  const seesBox = document.createElement("input");
  seesBox.type = "checkbox";
  seesBox.checked = !!row.vision;
  const seesWhy = document.createElement("span");
  seesWhy.className = "form__hint";
  seesWhy.textContent = "没标的模型收到图会直接 400，所以不猜，按你填的来";
  sees.append(seesBox, seesWhy);
  box.append(sees);

  const price = document.createElement("label");
  price.className = "form__row";
  price.innerHTML = `<span class="form__name">价格</span>`;
  const cells = ["缓存命中", "输入", "输出"].map((hint, n) => {
    const one = document.createElement("input");
    one.className = "line line--num";
    one.placeholder = hint;
    one.value = (row.price || [])[n] ?? "";
    one.spellcheck = false;
    return one;
  });
  const priceWhy = document.createElement("span");
  priceWhy.className = "form__hint";
  priceWhy.textContent = "¥ 每百万 token。不填就只显示 token 数，不显示金额";
  price.append(...cells, priceWhy);
  box.append(price);

  const acts = document.createElement("div");
  acts.className = "acts";
  const save = document.createElement("button");
  save.type = "button";
  save.className = "act act--go";
  save.textContent = "保存";
  save.onclick = () => {
    const out = { kind: pickKind.value, vision: seesBox.checked,
                  price: cells.map((c) => c.value.trim()) };
    KEY_FIELDS.forEach(([k]) => { out[k] = got[k].value.trim(); });
    if (editing) out.name = editing;
    editing = null;
    send({ op: "key", row: out });
  };
  const cancel = document.createElement("button");
  cancel.type = "button";
  cancel.className = "act";
  cancel.textContent = "取消";
  cancel.onclick = () => { editing = null; drawShelf(); };
  acts.append(save, cancel);
  if (editing) {
    const gone = document.createElement("button");
    gone.type = "button";
    gone.className = "act act--gone";
    gone.textContent = "删掉";
    gone.onclick = () => {
      if (!gone.dataset.sure) {
        gone.dataset.sure = "1";
        gone.textContent = "真的删掉？";
        return;
      }
      const name = editing;
      editing = null;
      send({ op: "key_drop", name });
    };
    acts.append(gone);
  }
  box.append(acts);
  return box;
}

function oneLine(value, hint, onSave) {
  const wrap = document.createElement("div");
  wrap.className = "acts";
  const box = document.createElement("input");
  box.className = "line";
  box.value = value || "";
  box.placeholder = hint;
  box.spellcheck = false;
  const save = document.createElement("button");
  save.type = "button";
  save.className = "act";
  save.textContent = "保存";
  save.onclick = () => onSave(box.value.trim());
  box.onkeydown = (e) => {
    if (e.key === "Enter") { e.preventDefault(); onSave(box.value.trim()); }
    if (e.key === "Escape") e.stopPropagation();     // 别顺手把面板关了
  };
  wrap.append(box, save);
  return wrap;
}

function ruleList(rows, what) {
  const box = document.createElement("div");
  box.className = "rules";
  const head = document.createElement("div");
  head.className = "rules__head";
  head.textContent = what + "（" + rows.length + "）";
  box.append(head);
  rows.forEach((r) => {
    const one = document.createElement("div");
    one.className = "rules__one";
    one.textContent = r;
    box.append(one);
  });
  return box;
}

function memoryList(rows) {
  const box = document.createElement("div");
  box.className = "rules";
  if (!rows.length) {
    const empty = document.createElement("div");
    empty.className = "rules__one";
    empty.textContent = "这个工作台还没记住任何东西。";
    box.append(empty);
    return box;
  }
  rows.forEach((r, n) => {
    const one = document.createElement("div");
    one.className = "rules__one rules__one--memo";
    one.innerHTML = `<span class="rules__when"></span><span class="rules__text"></span>`;
    one.querySelector(".rules__when").textContent = r.when;
    one.querySelector(".rules__text").textContent = r.text;
    const gone = document.createElement("button");
    gone.type = "button";
    gone.className = "act act--gone";
    gone.textContent = "删掉";
    // **是 forget 不是 rm**（真敲过一遍才发现，rm 会回一句"未知用法"，
    // 按钮看着按下去了其实什么也没发生）。序号从 1 起，跟 /memory 里显示的一致。
    gone.onclick = () => tune("/memory forget " + (n + 1));
    one.append(gone);
    box.append(one);
  });
  return box;
}

function emptyWord(tab) {
  if (tab === "general") return "读取中…";
  if (tab === "skills") return "还没有技能。一份技能就是一个 .md，放 ~/.config/feiyu/skills/ 下。";
  if (tab === "mcp") return "还没接 MCP 服务器。写 ~/.config/feiyu/mcp.json，改完要重启。";
  if (tab === "profiles") return "还没有配置档案。按下面的「新建一份」起一个。";
  return "一个工具都没有";
}

function rowMeta(row) {
  if (shelf.tab === "general") return "";
  if (shelf.tab === "skills") {
    const pack = (row.extras || []).length;
    return pack ? `${row.source} · 包（${pack} 个附属文件）` : (row.source || "");
  }
  if (shelf.tab === "profiles") return (row.on ? "在用 · " : "") + (row.model || "跟随当前模型");
  if (shelf.tab === "mcp") return row.error ? "起不来" : (row.tools.length + " 个工具");
  // 锁住的那个在清单上就得说明白，不然人会去点那个灰开关然后以为是坏的
  return row.locked ? (row.label || "") + " · 常开" : (row.label || "");
}

function drawShelfPane(row) {
  const pane = $("shelf-pane");
  pane.textContent = "";
  if (!row) return;
  if (shelf.tab === "general") {
    const spec = SETTINGS.find((r) => r.name === row.name);
    if (spec && (conf || spec.local)) drawSetting(pane, spec.key);
    return;
  }
  const head = document.createElement("h2");
  head.textContent = row.name;
  pane.append(head);
  const words = row.error || row.description || "";
  if (words) {
    const lead = document.createElement("p");
    lead.className = "lead";
    lead.textContent = words;
    pane.append(lead);
  }

  const facts = document.createElement("dl");
  facts.className = "facts";
  const put = (k, v) => {
    if (!v) return;
    const dt = document.createElement("dt");
    dt.textContent = k;
    const dd = document.createElement("dd");
    dd.textContent = v;
    facts.append(dt, dd);
  };
  if (shelf.tab === "profiles") {
    put("状态", row.on ? "正在用" : "没在用");
    put("模型", row.model || "跟随当前模型");
    put("文件", row.path);
    pane.append(facts);
    drawSoul(pane, row);
    return;
  }
  put("状态", row.locked ? "常开（关不掉）" : row.on ? "开着" : "已关");
  if (shelf.tab === "skills") {
    put("来自", row.source);
    put("文件", row.path);
    put("正文", row.chars + " 字");
    // 包形式的技能：附属文件是**按需**读的，这儿只列名字
    put("附属文件", (row.extras || []).join("、"));
  } else if (shelf.tab === "tools") {
    put("规则名", row.label);
    put("参数", (row.args || []).join("、"));
  } else {
    put("工具", String(row.tools.length));
  }
  pane.append(facts);

  if (shelf.tab === "mcp") {
    if (!row.error) {
      const why = document.createElement("p");
      why.className = "lead";
      // 关掉不等于不起进程：配置在 mcp.json 里，改了要重启。这儿关的是"要不要
      // 把它的工具挂给模型" —— 不说清楚的话，关了之后进程还在会让人以为没生效
      why.textContent = row.on
        ? "它的工具会挂给模型。关掉只是不挂，进程照旧起着（要不要起写在 mcp.json 里，改了得重启）。"
        : "已关：它的工具不会挂给模型，模型也看不到这台服务器。";
      pane.append(why);
    }
    const box = document.createElement("div");
    box.className = "shelf__body-text";
    box.textContent = row.tools.map((t) => t.name + (t.description ? " — " + t.description : ""))
      .join("\n") || "（这台服务器没报出工具）";
    pane.append(box);
    return;
  }
  if (shelf.tab !== "skills") return;

  const box = document.createElement("div");
  box.className = "shelf__body-text";
  const had = shelf.bodies[row.name];
  box.textContent = had === undefined ? "读正文…" : had;
  pane.append(box);
  if (had === undefined) send({ op: "detail", kind: "skill", name: row.name });
}

/* 人格正文可以就地改。**没写完的稿子要留住**（shelf.draft）——
   点开另一份再点回来、或者服务端推了新清单重画，都不该把你打的字冲掉。 */
function drawSoul(pane, row) {
  const box = document.createElement("textarea");
  box.className = "soul";
  box.spellcheck = false;
  const draft = shelf.draft[row.name];
  const saved = shelf.bodies["档案:" + row.name];
  box.value = draft !== undefined ? draft : (saved === undefined ? "读取中…" : saved);
  box.oninput = () => { shelf.draft[row.name] = box.value; drawActs(); };
  pane.append(box);
  if (saved === undefined) send({ op: "detail", kind: "profile", name: row.name });

  const acts = document.createElement("div");
  acts.className = "acts";
  pane.append(acts);

  function drawActs() {
    acts.textContent = "";
    const dirty = shelf.draft[row.name] !== undefined
      && shelf.draft[row.name] !== shelf.bodies["档案:" + row.name];
    const use = document.createElement("button");
    use.type = "button";
    use.className = "act" + (row.on ? " act--now" : "");
    use.textContent = row.on ? "正在用 · 点这里摘掉" : "用这份";
    use.onclick = () => send({ op: "profile", action: row.on ? "off" : "use", name: row.name });
    const save = document.createElement("button");
    save.type = "button";
    save.className = "act";
    save.textContent = "保存";
    save.disabled = !dirty;
    save.onclick = () => {
      const body = shelf.draft[row.name] ?? "";
      shelf.bodies["档案:" + row.name] = body;
      delete shelf.draft[row.name];
      send({ op: "profile", action: "save", name: row.name, body });
    };
    const said = document.createElement("span");
    said.className = "acts__said";
    said.textContent = dirty ? "有没保存的改动" : "";
    const gone = document.createElement("button");
    gone.type = "button";
    gone.className = "act act--gone";
    gone.textContent = "删掉";
    gone.onclick = () => {
      if (gone.dataset.sure) return send({ op: "profile", action: "delete", name: row.name });
      // 问一句再删，跟侧栏删对话一个路子：真数据值得问，但不弹模态框
      gone.dataset.sure = "1";
      gone.textContent = "真的删掉？";
    };
    acts.append(use, save, said, gone);
  }
  drawActs();
}

$("nav-shelf").onclick = () => showShelf(!shelfOpen());
$("shelf-find").oninput = (e) => { shelf.find = e.target.value; drawShelf(); };
$("shelf-tabs").onclick = (e) => {
  const tab = e.target.closest(".tab");
  if (!tab) return;
  shelf.tab = tab.dataset.tab;
  [...$("shelf-tabs").children].forEach((b) =>
    b.setAttribute("aria-selected", String(b === tab)));
  slideInd($("shelf-tabs"));
  drawShelf();
};

/* ── 工作台 ────────────────────────────────────────────────── */

/* 去过哪些工作台记在浏览器里 —— 跟侧栏收放、置顶一样是本机偏好，不进协议。 */
const DESK_KEY = "feiyu.desks";
const DESK_MAX = 8;

function desks() {
  try { return JSON.parse(localStorage.getItem(DESK_KEY) || "[]"); } catch { return []; }
}

function rememberDesk(path) {
  if (!path) return;
  const list = [path, ...desks().filter((p) => p !== path)].slice(0, DESK_MAX);
  try { localStorage.setItem(DESK_KEY, JSON.stringify(list)); } catch { /* 无痕窗口 */ }
}

let deskNow = "";

function setDesk(path) {
  deskNow = path || "";
  // 侧栏那一列比状态栏窄得多，budget 单给
  $("desk-path").textContent = shortPath(path, 28);
  $("desk-path").title = path || "";
  $("s-cwd").textContent = shortPath(path);
  $("s-cwd").title = path || "";
  rememberDesk(path);
}

function drawDesks() {
  const list = $("desks");
  const rows = desks().filter((p) => p !== deskNow);
  list.replaceChildren(...rows.map((p) => {
    const li = document.createElement("li");
    li.textContent = shortPath(p);
    li.title = p;
    li.onclick = () => goDesk(p);
    return li;
  }));
  const pick = document.createElement("li");
  pick.dataset.pick = "1";
  pick.textContent = "选个目录…";
  pick.onclick = pickDesk;
  list.append(pick);
}

async function goDesk(path) {
  $("desks").hidden = true;
  if (!TAURI || path === deskNow) return;
  sayOnce("off", "正在换工作台…");
  try {
    const now = await TAURI.core.invoke("switch", { dir: path });
    // 换过去就是另一段对话了：正文、侧栏、附件全部归零
    streamEl.replaceChildren();
    syncHello();
    clips = [];
    drawClips();
    boots.clear();
    $("s-boot").hidden = true;
    $("s-boot-sep").hidden = true;
    setDesk(now);
    // 面板里那个 shell 也跟着换过去（Rust 那边已经把旧的收掉了）
    if (term) {
      term.reset();
      TAURI.core.invoke("term_start", { cwd: now, cols: term.cols, rows: term.rows })
        .catch(() => {});
    }
    hello();
  } catch (err) {
    sayOnce("ready", "换不过去：" + String(err).slice(0, 60));
  }
}

async function pickDesk() {
  if (!TAURI) return;
  const dir = await TAURI.dialog.open({ directory: true, multiple: false, title: "选一个工作台" });
  if (dir) goDesk(dir);
}

$("desk").onclick = () => {
  const list = $("desks");
  if (list.hidden) drawDesks();
  list.hidden = !list.hidden;
};

/* ── 侧栏 ──────────────────────────────────────────────────── */

/* 置顶哪几条记在浏览器里 —— 这是「这台机器上这个人的偏好」，不是会话状态，
   所以不进协议。跟侧栏收放同一个道理。 */
const PIN_KEY = "feiyu.pinned";
const SIDE_KEY = "feiyu.side";

function pinned() {
  try { return new Set(JSON.parse(localStorage.getItem(PIN_KEY) || "[]")); }
  catch { return new Set(); }
}

function setPinned(set) {
  try { localStorage.setItem(PIN_KEY, JSON.stringify([...set])); } catch { /* 无痕窗口 */ }
}

let allTalks = [];
let currentTalk = null;

/* 列表进场只在**清单真的换了**的时候做一次。搜索框每敲一个字都会重画，
   那时候再让五十行依次浮进来就是在晃人眼睛 —— 筛选是连续动作，不是新内容。 */
let talksFresh = false;

function drawTalks(rows, current) {
  allTalks = rows || [];
  currentTalk = current;
  talksFresh = true;
  renderTalks();
}

/* 搜索只在本地筛已经拿到的那份列表 —— 列表是摘要，本来就全在手上，
   没必要为了筛几条字去问后端。 */
function renderTalks() {
  const needle = $("find").value.trim().toLowerCase();
  const keep = allTalks.filter((r) => !needle || (r.title || "").toLowerCase().includes(needle));
  const pins = pinned();
  const top = keep.filter((r) => pins.has(r.id));
  const rest = keep.filter((r) => !pins.has(r.id));

  const fresh = talksFresh;
  talksFresh = false;

  $("group-pinned").hidden = !top.length;
  $("pinned").replaceChildren(...top.map((r, n) => talkRow(r, currentTalk, fresh && n)));

  const list = $("talks");
  if (!rest.length) {
    list.innerHTML = `<li class="side__empty">${needle ? "没有匹配的会话" : "还没有历史对话"}</li>`;
  } else {
    list.replaceChildren(...rest.map((r, n) => talkRow(r, currentTalk, fresh && n)));
  }
}

function talkRow(row, current, nth) {
  const li = document.createElement("li");
  li.className = "talk";
  if (nth !== false) {
    // 错开一点点依次浮进来。**封顶**：一百条会话的话最后一条要等两秒才出现
    li.dataset.fresh = "1";
    li.style.setProperty("--n", Math.min(nth, 12));
  }
  li.setAttribute("aria-current", row.id === current ? "true" : "false");
  li.innerHTML = `<span class="talk__title">${esc(row.title)}</span>` +
                 `<span class="talk__meta">${esc(row.when)} · ${row.count} 条</span>`;
  li.onclick = (e) => {
    // shift+点 = 置顶 / 取消置顶，跟 Hermes 一样
    if (e.shiftKey) {
      const pins = pinned();
      pins.has(row.id) ? pins.delete(row.id) : pins.add(row.id);
      setPinned(pins);
      renderTalks();
      return;
    }
    send({ op: "open", id: row.id });
  };

  const del = document.createElement("button");
  del.type = "button";
  del.className = "talk__x";
  del.textContent = "×";
  del.title = "删掉这段对话";
  del.onclick = (e) => {
    // **必须 stopPropagation**：不然点叉的同时也把这段对话打开了
    e.stopPropagation();
    askDelete(li, row);
  };
  li.append(del);
  return li;
}

/* 删之前问一句。**不用 confirm() 弹窗**：一段对话是真数据，值得问，
   但那个弹窗跟这套排版格格不入，而且会挡住整个窗口。
   就地把这一行换成「删掉？确认 / 取消」，跟确认工具调用是同一个路子。 */
function askDelete(li, row) {
  const back = li.cloneNode(true);
  li.classList.add("talk--asking");
  li.onclick = null;
  li.innerHTML = `<span class="talk__title">删掉「${esc(row.title)}」？</span>`;
  const bar = document.createElement("div");
  bar.className = "talk__ask";
  const yes = document.createElement("button");
  yes.type = "button";
  yes.className = "btn btn--danger btn--small";
  yes.textContent = "删掉";
  yes.onclick = (e) => { e.stopPropagation(); send({ op: "delete", id: row.id }); };
  const no = document.createElement("button");
  no.type = "button";
  no.className = "btn btn--small";
  no.textContent = "取消";
  no.onclick = (e) => { e.stopPropagation(); li.replaceWith(back); wire(back, row); };
  bar.append(yes, no);
  li.append(bar);
}

/* cloneNode 不带事件，取消之后要把那一行重新接上 */
function wire(li, row) {
  li.onclick = () => send({ op: "open", id: row.id });
  const del = li.querySelector(".talk__x");
  if (del) del.onclick = (e) => { e.stopPropagation(); askDelete(li, row); };
}

function setSide(on, remember = true) {
  document.body.dataset.side = on ? "on" : "off";
  $("side-toggle").setAttribute("aria-expanded", on ? "true" : "false");
  $("scrim").hidden = !on;
  // 收起来时侧栏宽 0，页签量出来也是 0 —— 展开回来要重新量一次
  if (on) requestAnimationFrame(syncInds);
  // 窄屏那次自动收起**不进记忆**：窗口拉回来时该恢复成你原本的选择
  if (!remember) return;
  try { localStorage.setItem(SIDE_KEY, on ? "on" : "off"); } catch { /* 无痕窗口 */ }
}

/* 窗口窄到侧栏要浮在内容上面时（跟 CSS 里那个 46rem 断点同一个数），
   **自动收起来**。不收的话它盖住 240px 正文而且没有任何地方能关掉 ——
   桌面窗口最小 720px，正好落在这一档里，缩到最小就看不见字了。
   拉回来时恢复成你原本的选择。 */
const NARROW = window.matchMedia("(max-width: 46rem)");
let sideWas = null;

function watchNarrow() {
  if (NARROW.matches) {
    document.body.dataset.narrow = "1";
    if (sideWas === null) {
      sideWas = document.body.dataset.side;
      setSide(false, false);
    }
  } else {
    delete document.body.dataset.narrow;
    if (sideWas !== null) {
      setSide(sideWas !== "off", false);
      sideWas = null;
    }
  }
}

let sideOn = "on";
try { sideOn = localStorage.getItem(SIDE_KEY) || "on"; } catch { sideOn = "on"; }
setSide(sideOn !== "off");
watchNarrow();
NARROW.addEventListener("change", watchNarrow);

$("side-toggle").onclick = () => setSide(document.body.dataset.side === "off");
// 浮着的时候点旁边就收起来 —— 遮罩平时是 display:none，点不到
$("scrim").onclick = () => setSide(false);
$("new-talk").onclick = () => { showShelf(false); send({ op: "new" }); };
$("find").addEventListener("input", renderTalks);

/* 窗口按钮。只有桌面端有窗口可操作，浏览器里把它们藏起来。 */
if (TAURI) {
  const win = TAURI.window.getCurrentWindow();
  $("win-min").onclick = () => win.minimize();
  $("win-max").onclick = () => win.toggleMaximize();
  $("win-close").onclick = () => win.close();
  // 双击标题栏最大化 / 还原，跟系统窗口一个习惯
  document.querySelector(".bar").addEventListener("dblclick", (e) => {
    if (!e.target.closest("button, label, input")) win.toggleMaximize();
  });
  // 八个缩放热区。decorations:false 之后系统不给边框，只能自己拉。
  document.querySelectorAll(".grips i").forEach((grip) => {
    grip.addEventListener("mousedown", (e) => {
      e.preventDefault();
      win.startResizeDragging(grip.dataset.dir);
    });
  });
} else {
  // 浏览器里没有窗口可操作，这些都藏掉
  ["win-min", "win-max", "win-close"].forEach((id) => { $(id).hidden = true; });
  document.querySelector(".grips").hidden = true;
}

/* ── 上传 ──────────────────────────────────────────────────── */

function drawClips() {
  const list = $("clips");
  list.hidden = !clips.length;
  list.replaceChildren(...clips.map((clip, n) => {
    const li = document.createElement("li");
    li.className = "clip";
    li.dataset.state = clip.state;
    li.append(document.createTextNode(clip.name +
      (clip.state === "uploading" ? " 上传中…" : clip.state === "failed" ? " 失败" : "")));
    if (clip.state !== "uploading") {
      const x = document.createElement("button");
      x.type = "button";
      x.textContent = "×";
      x.title = "去掉";
      x.onclick = () => { clips.splice(n, 1); drawClips(); };
      li.append(x);
    }
    return li;
  }));
}

async function upload(file) {
  const clip = { name: file.name, state: "uploading", path: "" };
  clips.push(clip);
  drawClips();
  try {
    let path;
    if (TAURI) {
      const bytes = new Uint8Array(await file.arrayBuffer());
      path = await TAURI.core.invoke("upload", { name: file.name, data: [...bytes] });
    } else {
      const reply = await fetch(`/upload?t=${encodeURIComponent(TOKEN)}`, {
        method: "POST",
        // 文件名走 header：它可能是中文，也可能带路径分隔符（服务端会收拾）
        headers: { "x-feiyu-filename": encodeURIComponent(file.name) },
        body: file,
      });
      if (!reply.ok) throw new Error(await reply.text());
      path = (await reply.json()).path;
    }
    clip.state = "ready";
    clip.path = path;
    clip.name = path;
  } catch (err) {
    clip.state = "failed";
    clip.error = String(err).slice(0, 120);
  }
  drawClips();
}

$("file").onchange = (e) => {
  [...e.target.files].forEach(upload);
  e.target.value = "";          // 同一个文件想再传一次也得能触发 change
};

// 拖进来也算
document.addEventListener("dragover", (e) => e.preventDefault());
document.addEventListener("drop", (e) => {
  e.preventDefault();
  [...(e.dataTransfer?.files || [])].forEach(upload);
});

/* ── 传输 ──────────────────────────────────────────────────── */

let online = false;

function hello() {
  online = true;
  setDot("ready", "就绪");
  send({ op: "command", text: "/help" });
  send({ op: "status" });
  send({ op: "sessions" });
  // 面板开着的时候连上（或断线重连），得把清单重新要一遍 ——
  // 不要的话它会一直空着，而空清单跟"这台机器上没有技能"长得一模一样
  send({ op: "bots" });
  if (shelfOpen()) {
    send({ op: "catalog" });
    send({ op: "profiles" });
    send({ op: "settings" });
  }
}

function inbound(raw) {
  let ev;
  try { ev = JSON.parse(raw); } catch { return; }
  /* **这道 catch 才是保证。** 上面那些 rowsOf 挡的是已知的十处，
     而下一支处理函数是谁写的、写没写对，这儿管不着 —— 服务端 0.65.1
     那道 `except` 是同一个位置的同一件事。
     抛出去的话，`handle` 里那条语句之后的一句都不跑：`turn_end` 断在半路，
     `toolBlocks` 就留着上一轮的块，下一轮的结果会画到上一轮的调用上去。
     （`busy` 不在这条路上 —— 它跟着 `status` 走，服务端每轮都会重推一份，
     所以"秒表停不下来"这种最糟的形态本来就不会发生。）
     所以：接住、说一声、把这一轮的残留收干净，接着听下一条。 */
  try {
    handle(ev);
  } catch (err) {
    console.error("[feiyu] 这条事件没处理完：", ev && ev.kind, err);
    closeSaying();
    toolBlocks.clear();
    toolSeen.clear();
    pendingPreview = null;
    block("出错", "block--note").innerHTML =
      `<p class="note" data-level="error">界面没接住一条 ${esc(String(ev && ev.kind))} 事件：` +
      `${esc(String((err && err.message) || err))}<br>对话本身没事，接着说就行。</p>`;
  }
}

function send(msg) {
  if (!online) {
    // **断线时不能默默吞掉。** 用户点了发送、字也从输入框里没了，却什么都没发生 ——
    // 那比报个错糟得多。
    sayOnce("off", "没连上，这条没发出去");
    return false;
  }
  const line = JSON.stringify(msg);
  if (TAURI) {
    TAURI.core.invoke("send", { line }).catch(() => {
      online = false;
      sayOnce("off", "agent 断了");
    });
  } else {
    socket.send(line);
  }
  return true;
}

function connect() {
  if (TAURI) {
    // 桌面：进程内通道，没有端口、没有令牌、没有 Origin 那一套
    TAURI.event.listen("feiyu://event", (e) => inbound(e.payload));
    TAURI.core.invoke("where_am_i").then(setDesk);
    hello();
    return;
  }
  // 开发：连 feiyu-devserver，好让 headless Chrome 那套夹具还能跑
  socket = new WebSocket(`ws://${location.host}/ws?t=${encodeURIComponent(TOKEN)}`);
  socket.onopen = hello;
  socket.onmessage = (e) => inbound(e.data);
  socket.onclose = () => {
    online = false;
    sayOnce("off", "连接断了 · 正在重连");
    setTimeout(connect, 1000);
  };
}

/* ── 输入 ──────────────────────────────────────────────────── */

function grow() {
  inputEl.style.height = "auto";
  inputEl.style.height = inputEl.scrollHeight + "px";
}

function submit() {
  let text = inputEl.value.trim();
  const ready = clips.filter((c) => c.state === "ready");
  // 附件不是单独发的，是**跟这句话一起**交出去：agent 拿到的就是一句带路径的话，
  // 它用本来就有的 read_file 去看（图片、docx、pdf 那些它都认）
  if (ready.length && !text.startsWith("/")) {
    const lines = ready.map((c) => "- " + c.path).join("\n");
    text = (text ? text + "\n\n" : "") + "我上传了这些文件（相对工作区）：\n" + lines;
  }
  if (!text) return;
  if (text === "/help") wantHelp = true;
  // 没发出去就把话留在输入框里，别让用户重打一遍
  if (!send(text.startsWith("/") ? { op: "command", text } : { op: "prompt", text })) return;
  // 空对话里发出的第一句：「开始」让位时要演那一下落到底（见 leaveHello）
  if ($("hello")) glideArm = Date.now();
  // 你开口接着聊了，「以下是新的」那条线完成了它的事
  dropUnread();
  if (ready.length && !text.startsWith("/")) {
    clips = clips.filter((c) => c.state !== "ready");
    drawClips();
  }
  inputEl.value = "";
  grow();
  hidePalette();
}

let palIndex = 0;
let palItems = [];
/* 用户有没有自己用 ↑↓ 挑过。**回车的含义全看这个**：
   没挑过 = 回车是「发送我打的这句」，挑过 = 回车是「用我选的那条」。
   不分这两种的话，打完 `/mode auto` 一回车会被补全吃掉，
   变成 `/model `，参数无声丢掉、也没发出去 —— 这是最烦人的那个 bug。 */
let palMoved = false;

/* 每条命令的参数有哪些，服务端给（模型清单、档案、技能都会变，
   写死在前端迟早对不上）。见 serve.Server.push_help。 */
let cmdArgs = {};

/* `@路径` 补全最近一次的结果 {prefix, rows}。

   跟斜杠命令**共用同一个菜单和同一套键位**：两套菜单就是两套上下键、
   两套 Tab、两处 esc，而它们从来不会同时出现（`/` 只在行首、`@` 不在行首）。

   候选一律问服务端要（`op: "paths"` → `mentions.complete()`），前端不自己
   列目录 —— "点开头要不要出现"、"哪些目录跳过" 这些规则只该有一处，
   两边各写一套的结果就是终端和桌面端补出来的东西不一样。 */
let mention = null;

// `@` 后面已经打了什么（光标之前那一段），不在一个没打完的 mention 上就 null
// 判法跟 mentions.typing() 一一对应，改一处要改两处
function mentionAt() {
  const before = inputEl.value.slice(0, inputEl.selectionStart);
  const at = before.lastIndexOf("@");
  if (at < 0) return null;
  if (at > 0 && !" \t\n\r([{（「【".includes(before[at - 1])) return null;
  const typed = before.slice(at + 1);
  if (/\s/.test(typed)) return null;
  return { at, typed };
}

function showPalette() {
  const typed = inputEl.value;
  if (!typed.startsWith("/") || typed.includes("\n")) return showMentions();
  const cut = typed.indexOf(" ");
  if (cut < 0) {
    // 还在打命令名。按**整串**筛，不是按第一个词 —— 原来是 `typed.split(" ")[0]`，
    // 于是打完 `/mode auto` 菜单还开着，一回车被补全吃掉，变成 `/model `。
    palItems = commands
      .filter((c) => c.name.startsWith(typed))
      .map((c) => ({ text: c.name, name: c.name, about: c.about, whole: false }));
    // 完全相同的排最前：打全了 `/mode` 再按 Tab，不该给你补成 `/model`
    palItems.sort((a, b) => (b.name === typed) - (a.name === typed));
  } else {
    /* 打了空格 = 在写参数了。**这时候要接着补参数**，不是收起来 ——
       原来一见空格就 hidePalette，于是 `/mode ` 之后你得自己记住有哪四档。
       筛的是参数那一截，补出去的是「命令 + 参数」整串。 */
    const head = typed.slice(0, cut);
    const rest = typed.slice(cut + 1);
    if (rest.includes(" ")) return hidePalette();      // 参数超过一个词就不猜了
    palItems = (cmdArgs[head] || [])
      .filter((a) => a.startsWith(rest))
      .map((a) => ({ text: head + " " + a, name: a, about: head.slice(1), whole: true }));
    palItems.sort((a, b) => (b.name === rest) - (a.name === rest));
  }
  return paint();
}

function showMentions() {
  const spot = mentionAt();
  if (!spot) return hidePalette();
  if (!mention || mention.prefix !== spot.typed) {
    // 还没问过这一截 菜单先留着别收，收了再弹是一次肉眼可见的闪
    return send({ op: "paths", prefix: spot.typed });
  }
  palItems = mention.rows.map((path) => ({
    text: path, name: path, about: path.endsWith("/") ? "目录" : "文件",
    at: spot.at, path,
  }));
  return paint();
}

function paint() {
  if (!palItems.length) return hidePalette();
  palIndex = Math.min(palIndex, palItems.length - 1);
  paletteEl.replaceChildren(...palItems.map((c, n) => {
    const el = document.createElement("div");
    el.className = "palette__item";
    el.setAttribute("aria-selected", n === palIndex ? "true" : "false");
    el.innerHTML = `<span class="palette__name">${esc(c.name)}</span>` +
                   `<span class="palette__about">${esc(c.about)}</span>`;
    el.onmousedown = (e) => { e.preventDefault(); pick(n); };
    return el;
  }));
  showSoft(paletteEl);
  openModels(false);          // 两张清单占的是同一块地方，一次只开一张
  /* **选中那条要滚进视野。** 菜单最高 16rem，命令有十几条，
     按 ↑↓ 走到第八条之后高亮就跑到看不见的地方去了（用户拿截图来问过）。
     `block: "nearest"` —— 用 "center" 的话每按一下整个列表都在跳。 */
  paletteEl.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: "nearest" });
}

function hidePalette() {
  hideSoft(paletteEl);
  palItems = [];
  palIndex = 0;
  palMoved = false;
}

/* 浮层收起：`hidden` 立刻为真（键盘、点击、夹具看到的都是「已经收了」），
   画面上借 `data-leaving` 再淡出 --pop-out 那么久（见 app.css「浮层」）。
   同一个浮层淡到一半又被叫出来，就地撤掉淡出，接着进场 */
function hideSoft(el) {
  if (el.hidden) return;
  el.hidden = true;
  if (REDUCED) return;
  el.dataset.leaving = "1";
  clearTimeout(leaving.get(el));
  leaving.set(el, setTimeout(() => { delete el.dataset.leaving; }, cssMs("--pop-out") + 80));
}

function showSoft(el) {
  clearTimeout(leaving.get(el));
  delete el.dataset.leaving;
  el.hidden = false;
}

function pick(n) {
  const chosen = palItems[n];
  if (!chosen) return;
  if (chosen.path !== undefined) {
    // `@` 可能在句子中间，只能换掉 `@` 到光标这一段，不能整框覆盖
    const caret = inputEl.selectionStart;
    const folder = chosen.path.endsWith("/");
    const head = inputEl.value.slice(0, chosen.at + 1) + chosen.path + (folder ? "" : " ");
    inputEl.value = head + inputEl.value.slice(caret);
    hidePalette();
    inputEl.focus();
    inputEl.setSelectionRange(head.length, head.length);
    grow();
    if (folder) showPalette();      // 补到目录就接着往下一层补
    return;
  }
  // 补参数时 text 已经是「命令 + 参数」整串，后面不再加空格 —— 那一条打完了
  inputEl.value = chosen.whole ? chosen.text : chosen.text + " ";
  hidePalette();
  inputEl.focus();
  grow();
  // 补完命令名接着把参数菜单打开，省一次回车
  if (!chosen.whole) showPalette();
}

inputEl.addEventListener("input", () => {
  // 每次改动都重新算一遍，之前挑的那条不算数了
  palMoved = false;
  grow();
  showPalette();
});

/* 光标挪了也要重算：`@` 补全看的是"光标之前那一段"，
   而点一下、按左右键都不触发 input —— 不听这个的话菜单会留在原地，
   补出去的东西落在别的位置上。

   挂在 document 上而不是 inputEl 上：元素级的 selectionchange 是这两年
   才有的事，WebKitGTK（Tauri 在 Linux 上用的就是它）未必有；document 级的
   哪儿都有，多一次 activeElement 判断而已。 */
document.addEventListener("selectionchange", () => {
  if (document.activeElement !== inputEl) return;
  if (!paletteEl.hidden || mentionAt()) showPalette();
});

inputEl.addEventListener("keydown", (e) => {
  if (!paletteEl.hidden && palItems.length) {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const step = e.key === "ArrowDown" ? 1 : palItems.length - 1;
      palIndex = (palIndex + step) % palItems.length;
      palMoved = true;
      return showPalette();
    }
    // Tab 永远是补全；回车只在「你自己挑过」时才是补全，否则是发送
    if (e.key === "Tab") { e.preventDefault(); return pick(palIndex); }
    if (e.key === "Enter" && !e.shiftKey && palMoved) { e.preventDefault(); return pick(palIndex); }
    if (e.key === "Escape") {
      // **要 stopPropagation。** 只 preventDefault 的话事件照样冒到 document 上，
      // 那边看到菜单已经关了，就把这一轮给中断了 —— 用户只是想收起菜单。
      e.preventDefault();
      e.stopPropagation();
      return hidePalette();
    }
  }
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(); }
});

/* shift+tab 循环切权限模式，跟终端同一个键。
   **两边调的是同一个 agent.cycle_mode()**（终端那边是 prompt_toolkit 的键位，
   也不是斜杠命令），不各写一套模式顺序。
   挂在输入框上：Tab 在别处是移焦点，别抢。 */
inputEl.addEventListener("keydown", (e) => {
  if (e.key === "Tab" && e.shiftKey && paletteEl.hidden) {
    e.preventDefault();
    send({ op: "cycle_mode" });
  }
});

/* ── 右下角换模型 ───────────────────────────────────────────
   点那个模型名 = 在它头顶弹一张清单，点一条就换过去。这个钮从 0.52.0 起就摆在那儿，
   有 title、悬停会变色，**却从来没绑过点击** —— 看着能点其实是死的（0.77.1 那条：
   比不给还糟）。用户拿截图来要的。

   **换还是走 `/model <名字>`**（One action, one home），跟设置面板那张表同一条路；
   这儿只是把「敲命令」换成「点一下」。清单每次打开都现取（`models` 那个 op）——
   设置里刚加的那条得立刻出现在这儿。
   一轮正跑着的时候命令会排在它后面，所以要说一声「这一轮跑完才换过去」，
   不然点了没动静，跟以前一样像是死的。 */
// moved：人动过键盘没有。没动过的话高亮一直跟着「在用的那条」—— 清单是点开之后才
// 取回来的，第一次画的时候还没有它，定死成第 0 条就停在了别人身上
const menu = { find: "", at: 0, moved: false, list: null, foot: null, box: null };
// 清单长了才给筛选框：三五条的时候它只是多一个要看的东西
const MENU_FIND_AFTER = 8;

function modelsOpen() { return !modelsEl.hidden; }

function menuRows() {
  const q = menu.find.trim().toLowerCase();
  const rows = rowsOf(models && models.rows);
  return q ? rows.filter((r) => (r.name + " " + r.model).toLowerCase().includes(q)) : rows;
}

function openModels(on) {
  if (on) showSoft(modelsEl);
  else hideSoft(modelsEl);
  modelPill.setAttribute("aria-expanded", String(on));
  if (!on) {
    // 焦点还在清单里的话还给输入框，不然它落在一个藏起来的元素上，键盘就没处去了
    if (modelsEl.contains(document.activeElement)) inputEl.focus();
    return;
  }
  hidePalette();
  menu.find = "";
  menu.at = 0;
  menu.moved = false;
  modelsEl.replaceChildren();
  const card = document.createElement("div");
  card.className = "models__card";
  menu.box = document.createElement("input");
  menu.box.className = "models__find";
  menu.box.type = "search";
  menu.box.placeholder = "筛一下…";
  menu.box.setAttribute("aria-label", "筛选模型");
  menu.box.spellcheck = false;
  menu.box.oninput = () => {
    menu.find = menu.box.value;
    menu.at = 0;
    menu.moved = true;
    drawModelsList();
  };
  menu.list = document.createElement("ul");
  menu.list.className = "models__list";
  menu.list.setAttribute("role", "listbox");
  menu.list.setAttribute("aria-label", "模型");
  menu.list.tabIndex = -1;
  menu.foot = document.createElement("p");
  menu.foot.className = "models__foot";
  card.append(menu.box, menu.list, menu.foot);
  modelsEl.append(card);
  send({ op: "models" });
  drawModelsList();
}

function drawModelsList() {
  if (!modelsOpen() || !menu.list) return;
  const all = rowsOf(models && models.rows);
  const rows = menuRows();
  // 键盘只在点得动的那几条之间走：没 key 的换过去必然 401
  const live = rows.filter((r) => r.has_key);
  if (!menu.moved) menu.at = Math.max(0, live.findIndex((r) => r.using));
  menu.at = Math.max(0, Math.min(menu.at, live.length - 1));
  const lit = live[menu.at];

  menu.box.hidden = all.length <= MENU_FIND_AFTER;
  // 点开那一刻清单还没取回来的话，筛选框是藏着的、焦点落在列表上；
  // 清单到了、框出来了，焦点得跟过去 —— 不然打的字没地方去
  if (!menu.box.hidden && document.activeElement === menu.list) menu.box.focus();
  // **不分组、不加厂商小标题**（用户看过说不要）：每一行底下那行 id 本来就带着厂商，
  // 小标题是同一件事说两遍。顺序就是配置里的顺序，那是他自己排的
  const out = [];
  rows.forEach((row) => {
    const li = document.createElement("li");
    li.className = "models__row";
    li.setAttribute("role", "option");
    li.setAttribute("aria-selected", String(row === lit));
    if (row.using) li.dataset.now = "1";
    if (!row.has_key) li.setAttribute("aria-disabled", "true");
    li.innerHTML = `<span class="models__name"></span><span class="models__id"></span>`;
    li.querySelector(".models__name").textContent = row.name;
    li.querySelector(".models__id").textContent =
      row.model + (row.vision ? " · 认图" : "") + (row.has_key ? "" : " · 没有 key");
    if (row.using) li.insertAdjacentHTML("beforeend", `<span class="models__now">在用</span>`);
    li.onclick = () => pickModel(row);
    out.push(li);
  });
  menu.list.replaceChildren(...out);
  const hot = menu.list.querySelector('[aria-selected="true"]');
  if (hot) hot.scrollIntoView({ block: "nearest" });

  menu.foot.textContent = !models ? "读取中…"
    : !all.length ? "还没配模型 —— Ctrl K 打开设置，在「模型」里加一条"
    : !rows.length ? "没有叫这个的"
    : busy ? "这一轮还在跑，跑完才换过去"
    : "";
  menu.foot.hidden = !menu.foot.textContent;
}

function pickModel(row) {
  if (!row || !row.has_key) return;
  openModels(false);
  if (!row.using) tune("/model " + row.name);
}

modelPill.onclick = () => {
  openModels(!modelsOpen());
  if (!modelsOpen()) return;
  // 条数多的时候直接能打字筛；少的时候焦点给清单，上下键照样能走
  (menu.box.hidden ? menu.list : menu.box).focus();
};

modelsEl.addEventListener("keydown", (e) => {
  const live = menuRows().filter((r) => r.has_key);
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault();
    if (!live.length) return;
    menu.at = (menu.at + (e.key === "ArrowDown" ? 1 : live.length - 1)) % live.length;
    menu.moved = true;
    drawModelsList();
  } else if (e.key === "Enter") {
    e.preventDefault();
    pickModel(live[menu.at]);
  } else if (e.key === "Escape") {
    // **要 stopPropagation**，理由跟补全菜单那条一样：冒到 document 上，
    // 那边看见菜单已经关了、一轮又正跑着，就把这一轮打断了
    e.preventDefault();
    e.stopPropagation();
    openModels(false);
  }
});

// 点清单外面就收起来。钮本身除外 —— 它的 onclick 自己管开关，这儿再关一次就开不开了
document.addEventListener("mousedown", (e) => {
  if (modelsOpen() && !modelsEl.contains(e.target) && !modelPill.contains(e.target)) {
    openModels(false);
  }
});

// esc 中断，跟终端一个键。菜单开着时上面那段会把事件截住。
document.addEventListener("keydown", (e) => {
  // 换模型那张清单开着、焦点却不在它里面（刚点完那个钮）：esc 只收起它
  if (e.key === "Escape" && modelsOpen()) { e.preventDefault(); openModels(false); return; }
  // 面板开着时 esc 是"退回对话"，不是"打断这一轮" —— 那儿根本没有正在跑的一轮
  if (e.key === "Escape" && shelfOpen()) { e.preventDefault(); showShelf(false); return; }
  if (e.key === "Escape" && botShopOpen()) { e.preventDefault(); showBotShop(false); return; }
  if (e.key === "Escape" && busy) send({ op: "interrupt" });
  if (e.key === "k" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); showShelf(!shelfOpen()); }
  // 侧栏上写着 Ctrl N，那就得真的能用 —— 写了快捷键不实现比不写更糟
  if (e.key === "n" && (e.ctrlKey || e.metaKey)) {
    e.preventDefault();
    showShelf(false);
    send({ op: "new" });
  }
  if (e.key === "b" && (e.ctrlKey || e.metaKey)) {
    e.preventDefault();
    setSide(document.body.dataset.side === "off");
  }
});

/* 确认卡的数字键（见 armAsk）。**只在没在打字的时候接**：焦点在别的输入框里
   （设置、筛选、自己写），或者在输入框里而且框里已经有字 —— 那时候数字就是字。
   补全、换模型、设置面板开着的时候也不接：手在那儿干别的。
   输入法正在拼的时候（isComposing）数字是在选候选词，更不能接。 */
document.addEventListener("keydown", (e) => {
  const ask = pendingAsk;
  if (!ask || !ask.card.dataset.armed || ask.pressing) return;
  if (e.ctrlKey || e.metaKey || e.altKey || e.isComposing || e.repeat) return;
  if (!/^[1-9]$/.test(e.key)) return;
  const at = document.activeElement;
  const typing = at && at.matches("input, textarea, select, [contenteditable]")
    && !(at === inputEl && !inputEl.value);
  if (typing || !paletteEl.hidden || modelsOpen() || shelfOpen() || botShopOpen()) return;
  const btn = ask.keys[Number(e.key) - 1];
  if (!btn) return;
  e.preventDefault();
  ask.pressing = true;
  // 键盘没有 :active —— 先让它按下去一下，看得见按的是哪个，再点
  btn.dataset.pressed = "1";
  setTimeout(() => { delete btn.dataset.pressed; btn.click(); }, REDUCED ? 0 : 110);
});

$("send").onclick = submit;
$("stop").onclick = () => send({ op: "interrupt" });

/* ── 页签底下那条线会滑 ──────────────────────────────────────
   原来是每个页签自己的 `border-bottom-color`，切换时旧的灭新的亮 —— 两个页签
   之间没有连续性，眼睛得重新找一次"现在在哪一页"。改成整条容器一条线，
   位置和宽度由变量喂进去，CSS 负责在两个位置之间过渡。

   **量出来的数要写进 CSS 变量，不要直接写 style.left** —— 前者能被
   `prefers-reduced-motion` 那条媒体查询一句话关掉，后者关不掉。 */
function slideInd(box) {
  const on = box.querySelector('[aria-selected="true"]');
  if (!on) return;
  box.dataset.ind = "1";      // 第一次量到之前不画，免得从左上角飞过来
  box.style.setProperty("--ind-x", on.offsetLeft + "px");
  box.style.setProperty("--ind-w", on.offsetWidth + "px");
}

/* **写成函数声明不是 const** —— `setSide()` 在模块求值时就会跑一次，
   那时候 const 还在 TDZ 里，取一下就抛。 */
function syncInds() {
  document.querySelectorAll(".side__tabs, .tabs").forEach(slideInd);
}
window.addEventListener("resize", syncInds);
// 字体加载完宽度会变一次，那时候线还停在旧位置上
if (document.fonts) document.fonts.ready.then(syncInds);

/* ── 代码块上那个复制 ────────────────────────────────────────
   **走委托，不给每个按钮绑。** 流式时 `md()` 一秒跑十几次，每次绑一遍等于
   每次重建一批闭包；而且 `paintInto` 换节点时绑上去的东西会一起没掉。 */
streamEl.addEventListener("click", async (e) => {
  /* 链接得自己送出去：webview 里 target="_blank" 没有新标签页可开，
     点下去一点反应都没有 —— 看着像个链接却是死的，比不给链接还糟。 */
  const link = e.target.closest("a[href]");
  if (link) {
    e.preventDefault();
    TAURI.core.invoke("open_url", { url: link.href }).catch(() => {});
    return;
  }

  const btn = e.target.closest("[data-copy]");
  if (!btn) return;
  const code = btn.closest(".code").querySelector("code");
  const said = (ok) => {
    btn.textContent = ok ? "已复制" : "复制不了";
    setTimeout(() => { btn.textContent = "复制"; }, 1200);
  };
  try {
    await navigator.clipboard.writeText(code.textContent);
    said(true);
  } catch {
    // webview 里 `navigator.clipboard` 要安全上下文，未必给。退回老办法。
    const box = document.createElement("textarea");
    box.value = code.textContent;
    box.style.cssText = "position:fixed;opacity:0";
    document.body.append(box);
    box.select();
    let ok = false;
    try { ok = document.execCommand("copy"); } catch { ok = false; }
    box.remove();
    said(ok);
  }
});

/* ── 划一段交给它 ────────────────────────────────────────────
   选中正文里的一段 → 浮出几个动词。点了是**填进输入框**不是直接发出去：
   跟「开始」那几个例子同一条规矩 —— 让人能改，别替他做决定。
   多按一下回车换来的是「我还能补一句」，这笔账划得来。 */
const PICKS = [
  ["解释", "解释一下这段："],
  ["改写", "把这段改写得更清楚："],
  ["缩短", "把这段压短，意思别丢："],
  ["照着做", "照这段说的做："],
];

const pickBar = document.createElement("div");
pickBar.className = "pick-bar";
pickBar.hidden = true;
pickBar.innerHTML = PICKS.map(([name]) =>
  `<button type="button" data-pick="${esc(name)}">${esc(name)}</button>`).join("");
document.body.append(pickBar);

let picked = "";

function hidePick() {
  hideSoft(pickBar);
  picked = "";
}

function showPick() {
  const sel = window.getSelection();
  const text = sel && !sel.isCollapsed ? sel.toString().trim() : "";
  // 太短的多半是误触（双击一个词也会选中），而输入框里的选中跟这个无关
  if (text.length < 8 || !sel.anchorNode
      || !streamEl.contains(sel.anchorNode.parentElement || sel.anchorNode)) {
    return hidePick();
  }
  const box = sel.getRangeAt(0).getBoundingClientRect();
  if (!box.width && !box.height) return hidePick();
  picked = text;
  showSoft(pickBar);
  // 先摆出来才量得到自己的尺寸；夹在视口里，别飘到看不见的地方
  const w = pickBar.offsetWidth;
  const h = pickBar.offsetHeight;
  const x = Math.min(Math.max(8, box.left + box.width / 2 - w / 2), innerWidth - w - 8);
  // 上面放不下就翻到选区下方，长出来的原点跟着翻（见 app.css `.pick-bar[data-below]`）
  const below = box.top - h - 8 < 8;
  // **竖着也要夹住**：选区比一屏还长、或者一截已经滚出去了，上下两个位置都可能在视口外。
  // 原来只夹了横向 —— 上面那句「夹在视口里」只做了一半。0.80.0 对话栏真能滚了之后，
  // 夹具选中的第一段落在视口上方，浮条跑到了 y = −250
  const y = Math.min(Math.max(8, below ? box.bottom + 8 : box.top - h - 8), innerHeight - h - 8);
  if (below) pickBar.dataset.below = "1";
  else delete pickBar.dataset.below;
  pickBar.style.left = Math.round(x) + "px";
  pickBar.style.top = Math.round(y) + "px";
}

document.addEventListener("mouseup", (e) => {
  if (pickBar.contains(e.target)) return;
  // 选区在 mouseup 这一拍还没定下来，等一帧
  setTimeout(showPick, 0);
});
document.addEventListener("keyup", (e) => { if (e.shiftKey) showPick(); });
sheetEl.addEventListener("scroll", hidePick);
document.addEventListener("keydown", (e) => { if (e.key === "Escape") hidePick(); });

pickBar.onclick = (e) => {
  const btn = e.target.closest("[data-pick]");
  if (!btn || !picked) return;
  const how = (PICKS.find(([name]) => name === btn.dataset.pick) || [])[1] || "";
  // 引起来，它才分得清哪句是指令哪句是被处理的原文
  inputEl.value = how + "\n\n" + picked.split("\n").map((l) => "> " + l).join("\n") + "\n\n";
  hidePick();
  window.getSelection().removeAllRanges();
  inputEl.focus();
  inputEl.setSelectionRange(inputEl.value.length, inputEl.value.length);
  inputEl.dispatchEvent(new Event("input"));
};

/* ── 回到底部 ────────────────────────────────────────────────
   往回翻着看的时候我们刻意不把人拽回去（`keepBottom`），代价是他得自己滚回来 ——
   而一轮跑起来之后底下一直在长，滚是滚不到头的。给一个入口。 */
toEnd = document.createElement("button");
toEnd.type = "button";
toEnd.className = "to-end";
toEnd.title = "回到最新";
toEnd.hidden = true;
// 第二格是「3 条新」那截字，平时 0 宽（见 app.css `.to-end`）
toEnd.innerHTML = '<span class="to-end__arrow" aria-hidden="true">↓</span>'
  + '<span class="to-end__n"><span></span></span>';
/* **摆进 body 那张网格里，跟 `.sheet` 同一格。** 网格项是可以重叠的，
   所以它既不参与对话流的排版、也不会跟着内容滚走 —— 放进 `.sheet` 里面的话，
   要么占掉底下一条空带（显隐时还会抖一下），要么 `position: absolute`
   跟着滚动内容飘上去。 */
document.body.append(toEnd);
/* 有攒着没看的：落到「以下是新的」那条线上，从第一条新的读起。
   线已经在视野上面了（新的那截看过一部分了），就直接到底。 */
toEnd.onclick = () => {
  const mark = unseen.count && unseen.mark && unseen.mark.isConnected ? unseen.mark : null;
  const at = mark
    ? mark.getBoundingClientRect().top - sheetEl.getBoundingClientRect().top + sheetEl.scrollTop - 16
    : -1;
  sheetEl.scrollTo({
    top: mark && at > sheetEl.scrollTop + 8 ? at : sheetEl.scrollHeight,
    behavior: REDUCED ? "auto" : "smooth",
  });
};
sheetEl.addEventListener("scroll", () => { sawAll(); drawToEnd(); });

/* ── 你在不在 ────────────────────────────────────────────────
   两处用它：没看见的那条线（markUnseen）和下面的系统通知。
   回来的方式不止 focus 一种（从别的工作区切回来、直接点一下窗口），
   按下鼠标也算回来了 —— 捕获阶段听，别被哪个 stopPropagation 截走。 */
window.addEventListener("blur", () => { away = true; });
window.addEventListener("focus", () => { away = false; sawAll(); });
document.addEventListener("visibilitychange", () => { if (document.hidden) away = true; });
document.addEventListener("pointerdown", () => { if (away) { away = false; sawAll(); } }, true);

/* ── 你不在的时候叫你一声 ────────────────────────────────────
   它在等你点头、或者一轮长活跑完了，而窗口不在前台 —— 这时候屏幕上画什么都没用，
   原来它就那么等着，等到你自己想起来切回来看。
   **只在人不在的时候叫**：看着的时候再弹一条系统通知就是噪音。
   Wayland 上「窗口闪一下」那条路是空的（tao 的 request_user_attention 走 GTK3 的
   urgency hint，Wayland 下不起作用），所以走系统通知：Rust 那边调 notify-send。
   浏览器那条路（devserver，只给测试用）不发 —— 夹具把这个函数换掉，数它叫了几次。 */
const LONG_TURN = 10;       // 秒。NN/g：过了 10 秒，人的注意力就不在这个对话上了

function notifyUser(title, body) {
  if (!TAURI) return;
  TAURI.core.invoke("notify", { title, body }).catch(() => { /* 叫不出来不耽误别的 */ });
}

/* ── 空对话时输入框在正中（Anthropic 那套皮） ─────────────────
   见 app.css「问候、例子和输入框是一组」。这儿只管量：「开始」的顶到输入卡片的底
   是一组，抬到上下留白 42 : 58 的位置，抬多少写进 --lift。
   **敲字换行时卡片往下长、顶边不动**：量的时候拿「空着时那一行的高度」（rowBase）
   当它的高度 —— 多出来的那截算成往下长，而不是整组重新居中（那样每换一行，
   输入框就往上窜半行，字在眼皮底下跳）。抬到 0 之后再长，就只能往上长了。
   「开始」的位置用 offsetTop 量，不用它自己的 getBoundingClientRect：
   它刚出现时在浮现（translate 8px），量进去就差那 8px，浮现完也没人再量。 */
const LIFT_AT = 0.42;
const rowEl = document.querySelector(".composer__row");
const statusEl = document.querySelector(".status");
let rowBase = 0;

function measureLift() {
  const hello = $("hello");
  if (!hello || document.documentElement.dataset.theme !== "anthropic"
      || sheetEl.hidden || !$("term").hidden) return setLift(0);
  const row = rowEl.getBoundingClientRect();
  if (!row.height) return;       // 设置面板开着时输入区是藏着的，量出来全是 0
  if (!inputEl.value || !rowBase) rowBase = row.height;
  // 对话流和输入框被同一个 --lift 挪着，加回来就是没挪之前的位置
  const top = streamEl.getBoundingClientRect().top + liftPx + hello.offsetTop;
  const bottom = row.top + liftPx + rowBase;
  const from = sheetEl.getBoundingClientRect().top;
  const free = statusEl.getBoundingClientRect().top - from - (bottom - top);
  setLift(Math.max(0, Math.round(top - from - Math.max(0, free) * LIFT_AT)));
}

function setLift(px) {
  if (px === liftPx) return;
  liftPx = px;
  document.body.style.setProperty("--lift", px + "px");
}

liftWatch = new ResizeObserver(() => measureLift());
liftWatch.observe(sheetEl);
liftWatch.observe(rowEl);

syncHello();      // 一进来就是空的，这一下把「开始」那块摆上
syncInds();
connect();
inputEl.focus();

// 侧栏收放的过渡等第一帧画完才挂上：不然「上次是收着的」启动时也要演一遍
if (!REDUCED) {
  requestAnimationFrame(() => requestAnimationFrame(() => { document.body.dataset.motion = "1"; }));
}
