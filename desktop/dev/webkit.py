"""WebKitGTK 那一侧的夹具 —— 跟产品同一个引擎（webkit2gtk-4.1），离屏窗口，不上屏。

`shoot.mjs` / `input.mjs` 跑在 headless Chrome 上（webkit2gtk 没有 CDP，接不上），
可两个引擎的行为不是处处一样。0.74.0 那条 `.sheet { justify-content: flex-end }`
超出一屏之后：Chrome 把开头放到滚动原点上面（够不着）；WebKitGTK 把它当
column-reverse，scrollTop 变成 −max…0、0 在底边 —— 于是产品里 `atBottom()` 恒为假，
「回到最新」一直挂着，往回翻时正在读的那段被推走。**Chrome 那套夹具永远量不出后一种**，
它在那边恒为真。所以滚动、布局、过渡这类引擎行为，在这儿再量一遍。

用法：python3 desktop/dev/webkit.py <devserver 地址>
    要系统的 python-gobject 和 webkit2gtk-4.1（不是 .venv 里的 python），要有图形会话。
    两个环境变量是必须的：不关的话 GDK 在离屏窗口上起不来 GL，进程直接 core dump。
"""
import json
import os
import sys

os.environ.setdefault("WEBKIT_DISABLE_DMABUF_RENDERER", "1")
os.environ.setdefault("WEBKIT_DISABLE_COMPOSITING_MODE", "1")

import gi  # noqa: E402

gi.require_version("Gtk", "3.0")
gi.require_version("WebKit2", "4.1")
from gi.repository import GLib, Gtk, WebKit2  # noqa: E402

URL = sys.argv[1]
W, H = 1280, 900

# (等多久毫秒, 标签, 页面里跑的表达式 —— 返回 JSON 字符串, 判据；判据为 None 的只是动作)
STEPS = [
    (2500, "空对话那一组摆在视觉中心", """JSON.stringify((() => {
        const r = (el) => el.getBoundingClientRect();
        const top = r(document.getElementById("hello")).top - r(sheetEl).top;
        const below = r(document.querySelector(".status")).top - r(document.querySelector(".composer__row")).bottom;
        const b = r(document.getElementById("side-toggle"));
        return { share: top / (top + below),
                 bar: document.elementFromPoint(b.x + b.width / 2, b.y + b.height / 2)?.id };
    })())""", lambda v: 0.36 < v["share"] < 0.48 and v["bar"] == "side-toggle"),
    (0, "发出第一句", """JSON.stringify((() => { glideArm = Date.now();
        handle({ kind: "turn_start", text: "把 build.mjs 里读 frontmatter 那段抽成一个函数" });
        return getComputedStyle(document.querySelector(".composer")).translate; })())""", None),
    (150, "落到底有中间态", 'JSON.stringify(getComputedStyle(document.querySelector(".composer")).translate)',
     lambda v: v != "none"),
    (700, "落完归零、问候撤干净", """JSON.stringify({ tr: getComputedStyle(document.querySelector(".composer")).translate,
        hello: !!document.getElementById("hello") })""", lambda v: v["tr"] == "none" and not v["hello"]),
    (0, "灌一长串", """JSON.stringify((() => { for (let i = 0; i < 40; i++)
        handle({ kind: "note", level: "notice", text: "第 " + i + " 条" }); return 1; })())""", None),
    (300, "到底了就是到底了（atBottom 为真、「回到最新」藏着）", """JSON.stringify({
        st: Math.round(sheetEl.scrollTop), max: sheetEl.scrollHeight - sheetEl.clientHeight,
        bottom: atBottom(), hidden: toEnd.hidden })""",
     lambda v: v["max"] > 0 and abs(v["st"] - v["max"]) <= 1 and v["bottom"] and v["hidden"]),
    (0, "开头滚得上去、滚动原点在顶上", """JSON.stringify((() => { sheetEl.scrollTop = -500;
        const st = sheetEl.scrollTop;
        const first = streamEl.firstElementChild.getBoundingClientRect().top - sheetEl.getBoundingClientRect().top;
        return { st, first: Math.round(first) }; })())""", lambda v: v["st"] == 0 and v["first"] >= 0),
    (0, "往回翻着的时候底下长东西，正在读的那段不动", """JSON.stringify((() => {
        sheetEl.scrollTop = 200;
        const probe = streamEl.children[8], before = probe.getBoundingClientRect().top;
        handle({ kind: "note", level: "notice", text: "新的一条" });
        return { moved: Math.round(probe.getBoundingClientRect().top - before), st: sheetEl.scrollTop,
                 count: unseen.count, mark: !!document.querySelector(".unread") }; })())""",
     lambda v: v["moved"] == 0 and v["st"] == 200 and v["count"] == 1 and v["mark"]),
    # data-motion 是开页面后第二帧才挂的，而离屏窗口里 requestAnimationFrame 不一定跑 ——
    # 这儿量的是 WebKit 做不做这段过渡，「等第一帧再挂」那件事归 shoot.mjs 量。
    # 挂上和收起分两步走，跟产品里一样（data-motion 早在人去点侧栏之前就挂好了）
    (0, "挂上过渡", 'JSON.stringify((() => { document.body.dataset.motion = "1"; return 1; })())', None),
    # **不在固定时刻抽一帧量位置**：离屏窗口是软件绘制，一来一回的 IPC 时快时慢，
    # 110ms 那一下有时落在滑完之后（量出 0，其实滑了）。改成问 WebKit 起没起这两段过渡 ——
    # 列宽和侧栏的平移都得有，缺一个就是挤扁或者跳
    (150, "侧栏是滑出去的（列宽和平移两段过渡都起了）", """JSON.stringify((() => { setSide(false);
        return document.getAnimations().filter((a) => a.transitionProperty).map((a) => a.transitionProperty); })())""",
     lambda v: "translate" in v and "grid-template-columns" in v),
    (400, "滑完藏起来", """JSON.stringify({ right: Math.round(document.getElementById("side").getBoundingClientRect().right),
        vis: getComputedStyle(document.getElementById("side")).visibility })""",
     lambda v: v["right"] <= 0 and v["vis"] == "hidden"),
]

win = Gtk.OffscreenWindow()
win.set_default_size(W, H)
# **一次性的上下文**：默认的会把 localStorage 存到 ~/.local/share/<脚本名>/ 下，
# 上一轮收起过侧栏，下一轮一开页面侧栏就是收着的 —— 「收侧栏」那一步什么都不发生，
# 断言挂在一个根本没发生的动作上（真踩到过，查了半天以为是 WebKit 不演过渡）
view = WebKit2.WebView.new_with_context(WebKit2.WebContext.new_ephemeral())
view.set_size_request(W, H)
win.add(view)
win.show_all()
state = {"loads": 0, "bad": 0}


def run(i):
    if i >= len(STEPS):
        print(f"\n{len([s for s in STEPS if s[3]]) - state['bad']}/{len([s for s in STEPS if s[3]])} 通过")
        Gtk.main_quit()
        return False
    wait, label, js, judge = STEPS[i]

    def got(v, res):
        try:
            value = json.loads(v.evaluate_javascript_finish(res).to_string())
        except (GLib.Error, ValueError) as err:
            value, judge_ok = str(err), False
        else:
            judge_ok = judge(value) if judge else True
        if judge:
            print(("  OK " if judge_ok else "  !! ") + label + ("" if judge_ok else f"  → {value}"), flush=True)
            state["bad"] += not judge_ok
        run(i + 1)

    GLib.timeout_add(wait, lambda: view.evaluate_javascript(js, -1, None, None, None, got) and False)
    return False


def loaded(v, event):
    if event != WebKit2.LoadEvent.FINISHED:
        return
    state["loads"] += 1
    if state["loads"] == 1:
        # 皮存在 localStorage 里：先写进去再重开一次
        v.evaluate_javascript('localStorage.setItem("feiyu.theme", "anthropic"); "ok"', -1, None, None, None,
                              lambda *_: v.reload())
    elif state["loads"] == 2:
        run(0)


view.connect("load-changed", loaded)
view.load_uri(URL)
GLib.timeout_add(90000, lambda: (print("超时"), Gtk.main_quit()))
Gtk.main()
sys.exit(1 if state["bad"] or state["loads"] < 2 else 0)
