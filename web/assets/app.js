/* feiyu-web 前端。
 *
 * 一条 WebSocket，收 agent 的结构化事件，画成页面；发回去的是
 * {op: prompt | command | reply | interrupt | status}。
 *
 * 没有框架，也没有外部依赖 —— 跟这个项目的其余部分一个规矩。
 * markdown 渲染是自己写的一小份（见 md()）：agent 吐的是原始 markdown，
 * 怎么画是前端的事，把终端那套 ANSI 发过来再解析回去是最糟的做法。
 */
"use strict";

const $ = (id) => document.getElementById(id);
const streamEl = $("stream");
const sheetEl = $("sheet");
const inputEl = $("input");
const paletteEl = $("palette");
const stateEl = $("state");
const emptyEl = $("empty");

let socket = null;
let busy = false;
/* 这一轮里正在写的那块正文。文本是一个 delta 一个 delta 来的，得攒起来重渲染，
   不能追加 DOM —— markdown 的记号会跨 delta。 */
let saying = null;
let sayingText = "";
let commands = [];
let seenAsk = new Set();

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
  emptyEl.hidden = true;
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

function setStatus(s) {
  if (!s) return;
  busy = !!s.busy;
  // 从左边裁、保住尾巴 —— 裁右边的话看到的是 /tmp/xxx/yyy/…，
  // 恰好把「在哪个项目」裁掉了。跟终端的 ui.clip_path 同一个道理
  const short = (s.workspace || "—").replace(/^\/home\/[^/]+/, "~");
  $("m-workspace").textContent = short.length > 42 ? "…" + short.slice(-41) : short;
  $("m-workspace").title = s.workspace || "";
  $("m-model").textContent = s.ready ? (s.name || s.model || "—") : "未设置模型";
  const modes = { default: "默认", auto: "自动", plan: "计划", readonly: "只读" };
  $("m-mode").textContent = modes[s.mode] || s.mode || "—";
  $("m-mode").dataset.alert = s.mode === "auto" ? "1" : "0";
  if (s.context_limit) {
    const share = Math.round((s.context / s.context_limit) * 100);
    $("m-context").textContent = share + "%";
  }
  if (typeof s.cost === "number") {
    $("m-cost").textContent = s.cost ? "¥" + s.cost.toFixed(4) : "¥0";
  }
  $("send").hidden = busy;
  $("stop").hidden = !busy;
  stateEl.textContent = busy ? "干活中 · esc 中断" : "就绪";
  stateEl.dataset.alert = "0";
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

    case "cleared":
      streamEl.replaceChildren();
      emptyEl.hidden = false;
      break;

    case "help":
      commands = ev.commands || [];
      break;

    case "gone":
      stateEl.textContent = "agent 已退出 · 关掉这个页面，回终端重新起";
      stateEl.dataset.alert = "1";
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

/* ── 连接 ──────────────────────────────────────────────────── */

function send(msg) {
  if (socket && socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify(msg));
}

function connect() {
  socket = new WebSocket(`ws://${location.host}/ws`);
  socket.onopen = () => {
    stateEl.textContent = "就绪";
    stateEl.dataset.alert = "0";
    send({ op: "command", text: "/help" });
    send({ op: "status" });
  };
  socket.onmessage = (e) => {
    let ev;
    try { ev = JSON.parse(e.data); } catch { return; }
    handle(ev);
  };
  socket.onclose = () => {
    stateEl.textContent = "连接断了 · 正在重连";
    stateEl.dataset.alert = "1";
    setTimeout(connect, 1000);
  };
}

/* ── 输入 ──────────────────────────────────────────────────── */

function grow() {
  inputEl.style.height = "auto";
  inputEl.style.height = inputEl.scrollHeight + "px";
}

function submit() {
  const text = inputEl.value.trim();
  if (!text) return;
  send(text.startsWith("/") ? { op: "command", text } : { op: "prompt", text });
  inputEl.value = "";
  grow();
  hidePalette();
}

let palIndex = 0;
let palItems = [];

function showPalette() {
  const typed = inputEl.value;
  if (!typed.startsWith("/") || typed.includes("\n")) return hidePalette();
  const head = typed.split(" ")[0];
  palItems = commands.filter((c) => c.name.startsWith(head));
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
}

function pick(n) {
  const chosen = palItems[n];
  if (!chosen) return;
  inputEl.value = chosen.name + " ";
  hidePalette();
  inputEl.focus();
  grow();
}

inputEl.addEventListener("input", () => { grow(); showPalette(); });

inputEl.addEventListener("keydown", (e) => {
  if (!paletteEl.hidden && palItems.length) {
    if (e.key === "ArrowDown") { e.preventDefault(); palIndex = (palIndex + 1) % palItems.length; return showPalette(); }
    if (e.key === "ArrowUp") { e.preventDefault(); palIndex = (palIndex - 1 + palItems.length) % palItems.length; return showPalette(); }
    if (e.key === "Tab" || (e.key === "Enter" && !e.shiftKey)) { e.preventDefault(); return pick(palIndex); }
    if (e.key === "Escape") { e.preventDefault(); return hidePalette(); }
  }
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(); }
});

// esc 中断，跟终端一个键。焦点在输入框里时上面那段已经拦掉了补全的情况。
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && busy && paletteEl.hidden) send({ op: "interrupt" });
});

$("send").onclick = submit;
$("stop").onclick = () => send({ op: "interrupt" });

connect();
inputEl.focus();
