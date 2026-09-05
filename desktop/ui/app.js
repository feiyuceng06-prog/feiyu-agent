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

let socket = null;
let busy = false;
/* 这一轮里正在写的那块正文。文本是一个 delta 一个 delta 来的，得攒起来重渲染，
   不能追加 DOM —— markdown 的记号会跨 delta。 */
let saying = null;
let sayingText = "";
let commands = [];
let seenAsk = new Set();
/* 这次的 help 事件要不要画出来。连上时我们自己发一次去取补全清单，
   那次只填数据、不画；用户敲 /help 那次才画。 */
let wantHelp = false;
/* 已经上传好、等着随下一条消息一起交出去的文件（工作区相对路径） */
let clips = [];
/* 启动时那几句环境说明（MCP / 项目钩子 / 项目技能），按来源存一份 */
const boots = new Map();

/* ── 小工具 ─────────────────────────────────────────────────── */

const esc = (s) =>
  String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function atBottom() {
  return sheetEl.scrollHeight - sheetEl.scrollTop - sheetEl.clientHeight < 80;
}

/* 只在本来就贴着底的时候才跟着滚。用户往回翻着看的时候把他拽回来是最烦人的。 */
function keepBottom(wasDown) {
  if (wasDown) sheetEl.scrollTop = sheetEl.scrollHeight;
}

function block(label, cls) {
  const wasDown = atBottom();
  const el = document.createElement("div");
  el.className = "block" + (cls ? " " + cls : "");
  el.innerHTML = `<div class="block__label">${esc(label)}</div><div class="block__body"></div>`;
  streamEl.append(el);
  keepBottom(wasDown);
  return el.querySelector(".block__body");
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

function md(src) {
  const lines = String(src).split("\n");
  const out = [];
  let i = 0;
  const isRow = (s) => /^\s*\|.*\|\s*$/.test(s);
  const cells = (s) => s.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());

  while (i < lines.length) {
    const line = lines[i];

    if (/^\s*```/.test(line)) {
      const body = [];
      i++;
      while (i < lines.length && !/^\s*```/.test(lines[i])) body.push(lines[i++]);
      i++;
      out.push(`<pre><code>${esc(body.join("\n"))}</code></pre>`);
      continue;
    }
    if (/^\s*(---|\*\*\*|___)\s*$/.test(line)) { out.push("<hr>"); i++; continue; }

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
    out.push(`<p>${inline(para.join("\n"))}</p>`);
  }
  return out.join("");
}

/* ── 事件 ──────────────────────────────────────────────────── */

/* 状态栏那三样：连接点、左边的目录、右边的模型/权限/上下文/花费。
   **只有能据以行动的数才上状态栏** —— 上下文给百分比不给绝对 token，
   这条从终端版一路沿用（用户为那个绝对数字抱怨过两次）。 */

function setDot(state, text) {
  $("dot").dataset.state = state;
  $("s-state").textContent = text;
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
  if (s.context_limit) {
    $("s-ctx").textContent = Math.round((s.context / s.context_limit) * 100) + "%";
  }
  if (typeof s.cost === "number") {
    $("s-cost").textContent = s.cost ? "¥" + s.cost.toFixed(4) : "¥0";
  }
  if (s.version) $("s-ver").textContent = "v" + s.version;
  $("send").hidden = busy;
  $("stop").hidden = !busy;
  setDot(busy ? "busy" : "ready", busy ? "干活中 · esc 中断" : "就绪");
}

/* 一块正文写完了：重渲染一次去掉光标。
   **两个地方都要调** —— turn_end 是一种收尾，中途起了个工具调用也是一种，
   只管前者的话被打断的那块会一直留着一个闪的光标。 */
function closeSaying() {
  if (saying) {
    saying.innerHTML = `<div class="prose">${md(sayingText)}</div>`;
    fillShots(saying);
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

function handle(ev) {
  const wasDown = atBottom();
  switch (ev.kind) {
    case "hello":
    case "status":
      setStatus(ev);
      break;

    case "resumed":
      (ev.history || []).forEach((row) => {
        if (row.role === "user") block("你", "block--you").textContent = row.text;
        else block("它说").innerHTML = `<div class="prose">${md(row.text)}</div>`;
      });
      block("提示").innerHTML =
        `<p class="note">接上了上次的对话（${esc(ev.about || "")}）</p>`;
      break;

    case "turn_start":
      shownShots = new Set();
      closeSaying();
      block("你", "block--you").textContent = ev.text;
      break;

    case "tool_start":
      closeSaying();
      lastTool = ev;
      toolSeen.set(ev.id || "", ev);
      toolBlocks.set(ev.id || "", toolLine(ev));
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
      if (ev.detail && ev.detail.length) {
        const list = document.createElement("ul");
        list.className = "tool__lines";
        ev.detail.forEach((row) => {
          const li = document.createElement("li");
          li.dataset.kind = row.kind || "";
          li.textContent = row.text;
          list.append(li);
        });
        target.append(list);
      }
      if (ev.shows && !ev.is_error) showShot(target, ev.shows);
      break;
    }

    case "catalog":
      // **档案是另一条事件推的，别在这儿把它抹掉** —— 整个对象赋值下去,
      // shelf.data.profiles 变 undefined，下一次 drawShelf 就在 .length 上抛
      shelf.data.skills = ev.skills || [];
      shelf.data.tools = ev.tools || [];
      shelf.data.mcp = ev.mcp || [];
      shelf.got = true;
      if (shelfOpen()) drawShelf();
      break;

    case "detail":
      shelf.bodies[ev.what === "profile" ? "档案:" + ev.name : ev.name] =
        ev.body || (ev.what === "profile" ? "" : "（这份技能没有正文）");
      if (shelfOpen()) drawShelf();
      break;

    case "profiles":
      shelf.data.profiles = ev.rows || [];
      shelf.got = true;
      // 存完服务端会重推，这时候盘上那份才是真的 —— 把缓存的正文清掉重取
      (ev.rows || []).forEach((r) => { if (shelf.draft[r.name] === undefined)
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
      const body = block("钩子");
      body.innerHTML = `<p class="note" data-level="${ev.ok ? "notice" : "error"}">${esc(ev.text)}</p>`;
      break;
    }

    case "text":
      if (!saying) {
        saying = block("它说");
        sayingText = "";
      }
      sayingText += ev.delta;
      // 每次整份重渲染：markdown 的记号会跨 delta，追加 DOM 是对不上的
      saying.innerHTML = `<div class="prose">${md(sayingText)}<span class="cursor"></span></div>`;
      break;

    case "note":
      block(ev.level === "error" ? "出错" : ev.level === "warn" ? "注意" : "提示")
        .innerHTML = `<p class="note" data-level="${esc(ev.level)}">${esc(ev.text)}</p>`;
      break;

    case "plan":
      block("方案").innerHTML = `<div class="prose">${md(ev.text)}</div>`;
      break;

    case "preview":
      // 确认之前的 diff。挂在下一条 ask 上，这里先攒着。
      pendingPreview = ev.rows || [];
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

    case "turn_end":
      closeSaying();
      toolBlocks.clear();
      toolSeen.clear();
      pendingPreview = null;
      break;

    case "sessions":
      drawTalks(ev.rows || [], ev.current);
      break;

    case "opened":
      showShelf(false);
      (ev.history || []).forEach((row) => {
        if (row.role === "user") block("你", "block--you").textContent = row.text;
        else block("它说").innerHTML = `<div class="prose">${md(row.text)}</div>`;
      });
      break;

    case "cleared":
      // 只清对话。刊头那条启动信息说的是环境，跟聊了什么无关，留着
      streamEl.replaceChildren();
      break;

    case "help":
      commands = ev.commands || [];
      cmdArgs = ev.args || {};
      // 连上时会自己发一次 /help 去取补全清单 —— **那一次不该画出来**，
      // 不然每次开页面都白白多一块。只有用户自己敲的那次才画。
      if (wantHelp) {
        wantHelp = false;
        const rows = commands.map((c) =>
          `<tr><td><code>${esc(c.name)}</code></td><td>${esc(c.about)}</td></tr>`).join("");
        block("命令").innerHTML =
          `<div class="prose"><table><tbody>${rows}</tbody></table></div>`;
      }
      break;

    case "gone":
      online = false;
      setDot("off", "agent 已退出 · 关掉窗口重开");
      break;
  }
  keepBottom(wasDown);
}

let pendingPreview = null;

function askCard(ev) {
  if (seenAsk.has(ev.id)) return;      // 回放会把同一条 ask 再送一遍
  seenAsk.add(ev.id);
  const body = block("要你点头", "block--ask");
  const card = document.createElement("div");
  card.className = "ask";

  let question = "";
  let choices = [];
  if (ev.type === "approve") {
    question = `要 ${ev.label}${ev.target ? " " + ev.target : ""} 吗？`;
    choices = [
      ["yes", "同意一次", "btn--primary"],
      ["no", "拒绝", "btn--danger"],
      // **写「本轮」不是「本次会话」**：它是 _run_tools 那一批的局部标志，
      // 下一轮照样会问。终端那边的键位提示写的也是「[a] 本轮全放」。
      ["all", "本轮都允许", ""],
    ];
    if (ev.rule) choices.push(["save", "永久允许", "", ev.rule]);
  } else if (ev.type === "plan") {
    question = "按这个方案动手吗？";
    choices = [["yes", "动手", "btn--primary"], ["no", "还有意见", "btn--danger"]];
  } else if (ev.type === "trust") {
    const what = { hooks: "项目钩子", mcp: "项目 MCP 服务器", skills: "项目技能" }[ev.what] || ev.what;
    question = `${ev.where} 里有${what}（${(ev.names || []).join("、")}），要启用吗？`;
    choices = [["yes", "启用", "btn--primary"], ["no", "不启用", "btn--danger"]];
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
    btn.onclick = () => {
      send({ op: "reply", id: ev.id, value });
      card.classList.add("ask--done");
      row.replaceWith(
        Object.assign(document.createElement("p"),
          { className: "ask__answer", textContent: "已回答：" + label + (rule ? " " + rule : "") }));
    };
    row.append(btn);
  });
  // 自由输入那一路：ask_user 的选项常常不是用户真正想说的那句
  if (ev.type === "choice") {
    const other = document.createElement("button");
    other.type = "button";
    other.className = "btn";
    other.textContent = "自己写";
    other.onclick = () => {
      const said = prompt(question);
      if (said) {
        send({ op: "reply", id: ev.id, value: said });
        card.classList.add("ask--done");
        row.replaceWith(
          Object.assign(document.createElement("p"),
            { className: "ask__answer", textContent: "已回答：" + said }));
      }
    };
    row.append(other);
  }
  card.append(row);
  body.append(card);
  sheetEl.scrollTop = sheetEl.scrollHeight;
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

const shelf = { tab: "skills", find: "", pick: {},
                data: { skills: [], tools: [], mcp: [], profiles: [] },
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
    send({ op: "catalog" });
    send({ op: "profiles" });
    requestAnimationFrame(() => $("shelf-find").focus());
  }
}

function shelfRows() {
  const want = shelf.find.trim().toLowerCase();
  const rows = shelf.data[shelf.tab] || [];
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
  $("shelf-where").textContent = shelf.tab === "profiles"
    ? "一份档案就是 ~/.config/feiyu/profiles/ 下的一个 .md"
    : "开关记在 ~/.config/feiyu/enabled.json";
  $("shelf-hint").textContent = shelf.tab === "profiles"
    ? "人格设定每一轮都会发给模型，别写太长。换一份立刻生效。"
    : "关掉的东西既不进它的上下文，也调不出来。改动立刻生效。";
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
    if (shelf.tab === "profiles") {
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

function emptyWord(tab) {
  if (tab === "skills") return "还没有技能。一份技能就是一个 .md，放 ~/.config/feiyu/skills/ 下。";
  if (tab === "mcp") return "还没接 MCP 服务器。写 ~/.config/feiyu/mcp.json，改完要重启。";
  if (tab === "profiles") return "还没有配置档案。按下面的「新建一份」起一个。";
  return "一个工具都没有";
}

function rowMeta(row) {
  if (shelf.tab === "skills") return row.source || "";
  if (shelf.tab === "profiles") return (row.on ? "在用 · " : "") + (row.model || "跟随当前模型");
  if (shelf.tab === "mcp") return row.error ? "起不来" : (row.tools.length + " 个工具");
  // 锁住的那个在清单上就得说明白，不然人会去点那个灰开关然后以为是坏的
  return row.locked ? (row.label || "") + " · 常开" : (row.label || "");
}

function drawShelfPane(row) {
  const pane = $("shelf-pane");
  pane.textContent = "";
  if (!row) return;
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
  setDot("off", "正在换工作台…");
  try {
    const now = await TAURI.core.invoke("switch", { dir: path });
    // 换过去就是另一段对话了：正文、侧栏、附件全部归零
    streamEl.replaceChildren();
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
    setDot("ready", "换不过去：" + String(err).slice(0, 60));
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

function drawTalks(rows, current) {
  allTalks = rows || [];
  currentTalk = current;
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

  $("group-pinned").hidden = !top.length;
  $("pinned").replaceChildren(...top.map((r) => talkRow(r, currentTalk)));

  const list = $("talks");
  if (!rest.length) {
    list.innerHTML = `<li class="side__empty">${needle ? "没有匹配的会话" : "还没有历史对话"}</li>`;
  } else {
    list.replaceChildren(...rest.map((r) => talkRow(r, currentTalk)));
  }
}

function talkRow(row, current) {
  const li = document.createElement("li");
  li.className = "talk";
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

function setSide(on) {
  document.body.dataset.side = on ? "on" : "off";
  $("side-toggle").setAttribute("aria-expanded", on ? "true" : "false");
  try { localStorage.setItem(SIDE_KEY, on ? "on" : "off"); } catch { /* 无痕窗口 */ }
}

let sideOn = "on";
try { sideOn = localStorage.getItem(SIDE_KEY) || "on"; } catch { sideOn = "on"; }
setSide(sideOn !== "off");

$("side-toggle").onclick = () => setSide(document.body.dataset.side === "off");
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
  if (shelfOpen()) { send({ op: "catalog" }); send({ op: "profiles" }); }
}

function inbound(raw) {
  let ev;
  try { ev = JSON.parse(raw); } catch { return; }
  handle(ev);
}

function send(msg) {
  if (!online) {
    // **断线时不能默默吞掉。** 用户点了发送、字也从输入框里没了，却什么都没发生 ——
    // 那比报个错糟得多。
    setDot("off", "没连上，这条没发出去");
    return false;
  }
  const line = JSON.stringify(msg);
  if (TAURI) {
    TAURI.core.invoke("send", { line }).catch(() => {
      online = false;
      setDot("off", "agent 断了");
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
    setDot("off", "连接断了 · 正在重连");
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

function showPalette() {
  const typed = inputEl.value;
  if (!typed.startsWith("/") || typed.includes("\n")) return hidePalette();
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
  paletteEl.hidden = false;
  /* **选中那条要滚进视野。** 菜单最高 16rem，命令有十几条，
     按 ↑↓ 走到第八条之后高亮就跑到看不见的地方去了（用户拿截图来问过）。
     `block: "nearest"` —— 用 "center" 的话每按一下整个列表都在跳。 */
  paletteEl.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: "nearest" });
}

function hidePalette() {
  paletteEl.hidden = true;
  palItems = [];
  palIndex = 0;
  palMoved = false;
}

function pick(n) {
  const chosen = palItems[n];
  if (!chosen) return;
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

// esc 中断，跟终端一个键。菜单开着时上面那段会把事件截住。
document.addEventListener("keydown", (e) => {
  // 面板开着时 esc 是"退回对话"，不是"打断这一轮" —— 那儿根本没有正在跑的一轮
  if (e.key === "Escape" && shelfOpen()) { e.preventDefault(); showShelf(false); return; }
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

$("send").onclick = submit;
$("stop").onclick = () => send({ op: "interrupt" });

connect();
inputEl.focus();
