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
  out = out.replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g,
    '<a href="$2" target="_blank" rel="noreferrer noopener">$1</a>');
  return out;
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

function shortPath(raw) {
  const short = (raw || "—").replace(/^\/home\/[^/]+/, "~");
  // 从左边裁、保住尾巴 —— 裁右边的话看到的是 /tmp/xxx/yyy/…，
  // 恰好把「在哪个项目」裁掉了。跟终端的 ui.clip_path 同一个道理
  return short.length > 46 ? "…" + short.slice(-45) : short;
}

function setStatus(s) {
  if (!s) return;
  busy = !!s.busy;
  if (s.workspace) {
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
  if (saying) saying.innerHTML = `<div class="prose">${md(sayingText)}</div>`;
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

let liveTool = null;
/* 最后一次工具调用是什么。确认卡插进来之后 liveTool 会被清掉，
   之后的结果要另起一块，那一块的标题得靠它。 */
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
      closeSaying();
      block("你", "block--you").textContent = ev.text;
      break;

    case "tool_start":
      closeSaying();
      lastTool = ev;
      liveTool = toolLine(ev);
      break;

    case "tool_progress":
      if (liveTool) {
        const pending = liveTool.querySelector(".tool__pending");
        if (pending) pending.textContent = "生成参数… " + ev.size + " 字";
      }
      break;

    case "tool_result": {
      // liveTool 为空 = 中间插过一张确认卡。**必须另起一块**，
      // 不然结果会画在授权它的那张卡上面，读起来像是先改了再问的
      const target = liveTool || toolLine(lastTool || { label: "结果" });
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
      liveTool = null;
      break;
    }

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
      // 卡要插在当前这块工具行**下面**，所以先把它收掉；
      // 结果回来时会照 lastTool 另起一块，接在卡后面
      if (liveTool) {
        const pending = liveTool.querySelector(".tool__pending");
        if (pending) pending.textContent = "等你点头";
        liveTool = null;
      }
      askCard(ev);
      break;

    case "turn_end":
      closeSaying();
      liveTool = null;
      pendingPreview = null;
      break;

    case "sessions":
      drawTalks(ev.rows || [], ev.current);
      break;

    case "opened":
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
      ["all", "本次会话都允许", ""],
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
$("new-talk").onclick = () => send({ op: "new" });
$("find").addEventListener("input", renderTalks);

/* 侧栏那几个导航项和标题栏那几个钮**都走斜杠命令** —— 一个动作一个来源，
   不给它们另开一套（这是 Hermes DESIGN.md 的 "One action, one home"）。 */
const shortcuts = {
  "nav-skills": "/skills", "nav-jobs": "/jobs", "nav-memory": "/memory",
  "act-skills": "/help", "act-cost": "/cost", "act-mode": "/mode",
};
for (const [id, cmd] of Object.entries(shortcuts)) {
  const el = $(id);
  if (el) el.onclick = () => { if (cmd === "/help") wantHelp = true; send({ op: "command", text: cmd }); };
}

/* 窗口按钮。只有桌面端有窗口可操作，浏览器里把它们藏起来。 */
if (TAURI) {
  const win = TAURI.window.getCurrentWindow();
  $("win-min").onclick = () => win.minimize();
  $("win-max").onclick = () => win.toggleMaximize();
  $("win-close").onclick = () => win.close();
} else {
  ["win-min", "win-max", "win-close"].forEach((id) => { $(id).hidden = true; });
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
    TAURI.core.invoke("where_am_i").then((w) => { $("s-cwd").textContent = shortPath(w); });
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

function showPalette() {
  const typed = inputEl.value;
  if (!typed.startsWith("/") || typed.includes("\n")) return hidePalette();
  // **打了空格就是在写参数了**，这时候不该再插手（终端那边的补全也是这个口径）。
  // 下面按整串筛本来也会筛空，这一行是把意图写明白。
  if (typed.includes(" ")) return hidePalette();
  // 按**整串**筛，不是按第一个词。原来是 `typed.split(" ")[0]`，于是打完
  // `/mode auto` 菜单还开着，一回车被补全吃掉，变成 `/model `——参数没了，
  // 也没发出去。这是这块最烦人的那个 bug。
  palItems = commands.filter((c) => c.name.startsWith(typed));
  // 完全相同的排最前：打全了 `/mode` 再按 Tab，不该给你补成 `/model`
  palItems.sort((a, b) => (b.name === typed) - (a.name === typed));
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
  inputEl.value = chosen.name + " ";
  hidePalette();
  inputEl.focus();
  grow();
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

// esc 中断，跟终端一个键。菜单开着时上面那段会把事件截住。
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && busy) send({ op: "interrupt" });
  // 侧栏上写着 Ctrl N，那就得真的能用 —— 写了快捷键不实现比不写更糟
  if (e.key === "n" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); send({ op: "new" }); }
  if (e.key === "b" && (e.ctrlKey || e.metaKey)) {
    e.preventDefault();
    setSide(document.body.dataset.side === "off");
  }
});

$("send").onclick = submit;
$("stop").onclick = () => send({ op: "interrupt" });

connect();
inputEl.focus();
