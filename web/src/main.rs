//! feiyu-web —— feiyu 的网页端。
//!
//! 一个静态二进制：HTTP + WebSocket 服务，前端资源编进二进制，底下监管一个
//! `feiyu --serve` 子进程，两边一行一个 JSON 对话。
//!
//! # 为什么这半边是 Rust
//!
//! **不是为了让 agent 跑得更快** —— 实测本地工具加起来不到 20ms，而一轮要等模型
//! 3 到 10 秒，重写工具层是把 20ms 优化成 5ms。这半边是 Rust 的理由只有一条：
//! Python 那边的硬性约定是「不引新依赖」（现在只有 anthropic / pillow /
//! prompt_toolkit），而网页端要 HTTP、WebSocket、静态资源和并发连接。放在这儿，
//! Python 侧一个依赖都不用加，同时用户拿到的是一个不用装运行时的可执行文件。
//!
//! # 谁能连上来
//!
//! 连上这个端口的人可以让 agent 在这台机器上写文件、跑命令，所以这里有三道门，
//! **三道都是必要的，别删任何一道**：
//!
//! 1. **只听 127.0.0.1**。不要改成可配置的；要从别的机器用就开 ssh 隧道
//!    （`ssh -L 7777:127.0.0.1:7777 这台机器`），那样认证是 ssh 的事。
//! 2. **一次性令牌**。每次启动随机生成，只出现在开浏览器的那个地址里。
//!    光靠「只听回环」不够：这台机器上**任何一个进程、任何一个用户**都能连回环地址。
//! 3. **Origin 和 Host 校验**。这一条治的是最要命的那种：
//!    **WebSocket 不受同源策略保护** —— 你在浏览器里随便打开一个网站，那个网站的
//!    脚本就能 `new WebSocket("ws://127.0.0.1:7777/ws")` 连上来，浏览器照连不误，
//!    也不会有 CORS 预检。实测过：这样连上之后可以 `/mode auto` 关掉确认、
//!    让 agent 读任意文件并把内容通过同一条连接送出去、跑任意命令。
//!    换句话说，**没有这道门，「访问一个网页」就等于「在你机器上执行任意代码」**。
//!    Host 校验另外挡 DNS rebinding（把 evil.com 解析到 127.0.0.1 绕开 Origin）。
//!
//! # 一个进程，多个标签页
//!
//! agent 只有一个（它有工作目录、有对话历史，多开等于几个 agent 抢同一个仓库）。
//! 浏览器开几个标签页都连到同一个上：事件走 broadcast 发给所有连接，
//! 谁都能发指令。新连上的标签页会先收到一份最近的事件回放，不然它是一片空白。

use std::collections::VecDeque;
use std::process::Stdio;
use std::sync::{Arc, Mutex};

use axum::extract::ws::{Message, WebSocket, WebSocketUpgrade};
use axum::extract::{RawQuery, State};
use axum::http::{header, HeaderMap, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::routing::get;
use axum::Router;
// axum 的 WebSocket 是 futures 的 Sink/Stream，split / next / send 都在这两个 trait 上
use futures_util::{SinkExt, StreamExt};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::process::{Child, ChildStdin, Command};
use tokio::sync::{broadcast, mpsc};

const INDEX_HTML: &str = include_str!("../assets/index.html");
const APP_CSS: &str = include_str!("../assets/app.css");
const APP_JS: &str = include_str!("../assets/app.js");
const LOGO_SVG: &str = include_str!("../assets/logo.svg");

/// 新标签页连上时回放多少条事件。够看清「刚才发生了什么」，又不至于把一场长任务
/// 的几千条工具事件全堆给它。
const REPLAY: usize = 400;

/// 一条 WebSocket 消息封顶多大。Python 那边另有一道 `MAX_TEXT`，两道都要。
const MAX_MESSAGE: usize = 1 << 20; // 1 MiB

struct App {
    /// 发给所有浏览器的事件。
    events: broadcast::Sender<String>,
    /// 浏览器发来的指令，汇总到写 stdin 的那个任务。
    to_agent: mpsc::UnboundedSender<String>,
    /// 最近的事件，给新连上的标签页回放。
    replay: Mutex<VecDeque<String>>,
    /// 这一次启动的令牌，见文件头第 2 道门。
    token: String,
    /// 监听的端口，用来核 Origin / Host。
    port: u16,
}

impl App {
    fn record(&self, line: &str) {
        let mut log = self.replay.lock().unwrap();
        if log.len() == REPLAY {
            log.pop_front();
        }
        log.push_back(line.to_string());
    }

    fn token_ok(&self, query: Option<&str>) -> bool {
        token_ok(&self.token, query)
    }

    /// Origin 和 Host 都必须是「本机上的我们自己」。
    ///
    /// Origin 治的是「随便打开的网页连过来」：浏览器发 WebSocket 时**一定**会带
    /// Origin，而同源策略管不住 WebSocket，所以只能在服务端自己核。
    /// 没有 Origin 的一律拒——浏览器不会不带，不带的是脚本，而脚本没有正当理由连这里。
    ///
    /// Host 治的是 DNS rebinding：攻击者把自己的域名解析到 127.0.0.1，
    /// 这时 Origin 是他的域名（已经被上面拦了），Host 也是他的域名（被这里拦）。
    fn origin_ok(&self, headers: &HeaderMap) -> bool {
        origin_ok(
            self.port,
            headers.get(header::ORIGIN).and_then(|v| v.to_str().ok()),
            headers.get(header::HOST).and_then(|v| v.to_str().ok()),
        )
    }
}

/// 令牌对不对。定时比较，不要写成 `a == b`——虽然这里的时间差基本没法利用，
/// 但按常数时间写不花什么力气。
fn token_ok(expected: &str, query: Option<&str>) -> bool {
    let Some(given) = query.and_then(param) else {
        return false;
    };
    let (expected, given) = (expected.as_bytes(), given.as_bytes());
    if given.len() != expected.len() || expected.is_empty() {
        return false;
    }
    let mut diff = 0u8;
    for (a, b) in expected.iter().zip(given) {
        diff |= a ^ b;
    }
    diff == 0
}

/// Origin 和 Host 都必须是「本机上的我们自己」。见 `App::origin_ok` 上面那段。
fn origin_ok(port: u16, origin: Option<&str>, host: Option<&str>) -> bool {
    let allowed = [
        format!("http://127.0.0.1:{port}"),
        format!("http://localhost:{port}"),
        format!("http://[::1]:{port}"),
    ];
    let Some(origin) = origin else { return false };
    if !allowed.iter().any(|ok| ok == origin) {
        return false;
    }
    let Some(host) = host else { return false };
    matches!(host.rsplit_once(':'), Some((name, given))
        if given == port.to_string()
            && (name == "127.0.0.1" || name == "localhost" || name == "[::1]"))
}

/// 从 `t=xxx&…` 里取出令牌。够用就好，不为这一个参数引 url 解析库。
fn param(query: &str) -> Option<&str> {
    query
        .split('&')
        .find_map(|pair| pair.strip_prefix("t="))
}

/// 一次性令牌。走 `/dev/urandom`，不为一个随机数引 rand（这个程序本来就只跑 POSIX）。
fn make_token() -> String {
    let mut raw = [0u8; 16];
    match std::fs::File::open("/dev/urandom")
        .and_then(|mut f| std::io::Read::read_exact(&mut f, &mut raw))
    {
        Ok(()) => raw.iter().map(|b| format!("{b:02x}")).collect(),
        // 读不到熵就不要退而求其次编一个假随机 —— 那等于没有这道门。
        Err(err) => {
            eprintln!("读不了 /dev/urandom（{err}），没法生成令牌，不启动。");
            std::process::exit(1);
        }
    }
}

struct Args {
    port: u16,
    workspace: String,
    model: Option<String>,
    resume: bool,
    open: bool,
}

fn usage() -> ! {
    eprintln!(
        "feiyu-web —— feiyu 的网页端\n\
         \n\
         用法：feiyu-web [选项]\n\
         \n\
           -w, --workspace <目录>  agent 干活的目录（默认当前目录）\n\
           -m, --model <名字>      用 /key 清单里的哪一条（默认上次用的）\n\
           -r, --resume            接着上次在这个目录的对话\n\
           -p, --port <端口>       监听端口（默认 7777）\n\
               --no-open          不要自动打开浏览器\n\
           -h, --help              显示这段\n\
         \n\
         只监听 127.0.0.1。连上的人能让它在这台机器上写文件、跑命令，\n\
         所以不要把它暴露到网络上；要远程用就开 ssh 隧道。\n\
         \n\
         终端版是 feiyu。"
    );
    std::process::exit(2)
}

fn parse_args() -> Args {
    let mut out = Args {
        port: 7777,
        workspace: ".".into(),
        model: None,
        resume: false,
        open: true,
    };
    let mut argv = std::env::args().skip(1);
    while let Some(arg) = argv.next() {
        match arg.as_str() {
            "-h" | "--help" => usage(),
            "-r" | "--resume" => out.resume = true,
            "--no-open" => out.open = false,
            "-w" | "--workspace" => out.workspace = argv.next().unwrap_or_else(|| usage()),
            "-m" | "--model" => out.model = argv.next(),
            "-p" | "--port" => {
                out.port = argv
                    .next()
                    .and_then(|v| v.parse().ok())
                    .unwrap_or_else(|| usage())
            }
            other => {
                eprintln!("不认识的选项 {other}");
                usage()
            }
        }
    }
    out
}

/// 找 `feiyu` 那个可执行文件。
///
/// `FEIYU_BIN` 优先，其次是跟本二进制同一棵源码树里的 `.venv/bin/feiyu`
/// （从 `web/target/...` 里跑起来的开发场景），最后交给 PATH。
fn feiyu_bin() -> String {
    if let Ok(explicit) = std::env::var("FEIYU_BIN") {
        if !explicit.is_empty() {
            return explicit;
        }
    }
    if let Ok(exe) = std::env::current_exe() {
        for base in exe.ancestors().skip(1).take(5) {
            let candidate = base.join(".venv/bin/feiyu");
            if candidate.is_file() {
                return candidate.to_string_lossy().into_owned();
            }
        }
    }
    "feiyu".into()
}

fn spawn_agent(args: &Args) -> std::io::Result<Child> {
    let bin = feiyu_bin();
    let mut cmd = Command::new(&bin);
    cmd.arg("--serve").arg("-w").arg(&args.workspace);
    if let Some(model) = &args.model {
        cmd.arg("-m").arg(model);
    }
    if args.resume {
        cmd.arg("--resume");
    }
    cmd.stdin(Stdio::piped())
        .stdout(Stdio::piped())
        // stderr 原样留给终端：agent 那边人看的输出和 Python 的报错都在这儿，
        // 吞掉的话装配出问题时屏幕上什么都没有。
        .stderr(Stdio::inherit())
        .kill_on_drop(true);
    cmd.spawn()
}

async fn pump_stdin(mut stdin: ChildStdin, mut rx: mpsc::UnboundedReceiver<String>) {
    while let Some(line) = rx.recv().await {
        if stdin.write_all(line.as_bytes()).await.is_err() || stdin.write_all(b"\n").await.is_err()
        {
            break;
        }
        if stdin.flush().await.is_err() {
            break;
        }
    }
}

fn denied(why: &str) -> Response {
    (StatusCode::FORBIDDEN, format!("{why}\n")).into_response()
}

async fn ws_handler(
    State(app): State<Arc<App>>,
    headers: HeaderMap,
    RawQuery(query): RawQuery,
    ws: WebSocketUpgrade,
) -> Response {
    // 三道门都要过。顺序无所谓，但**一道都不能少**，理由见文件头。
    if !app.origin_ok(&headers) {
        eprintln!("挡下一条来路不明的 WebSocket（Origin/Host 不对）");
        return denied("origin not allowed");
    }
    if !app.token_ok(query.as_deref()) {
        eprintln!("挡下一条没有令牌的 WebSocket");
        return denied("bad token");
    }
    // 一条消息封个顶。协议里最大的一条就是用户打的一段话，几十 KB 顶天了；
    // 不封顶的话一个坏掉的客户端可以拿一条巨帧把内存吃光。
    ws.max_message_size(MAX_MESSAGE)
        .max_frame_size(MAX_MESSAGE)
        .on_upgrade(move |socket| client(socket, app))
}

async fn serve_index(State(app): State<Arc<App>>, RawQuery(query): RawQuery) -> Response {
    if !app.token_ok(query.as_deref()) {
        return no_token();
    }
    // 页面里两个资源地址也得带令牌，而令牌是启动时才有的，所以在这儿填。
    // 只有 index 需要填，app.css / app.js 里没有占位符。
    let page = INDEX_HTML.replace("__TOKEN__", &app.token);
    (
        StatusCode::OK,
        [
            (header::CONTENT_TYPE, "text/html; charset=utf-8"),
            (header::CACHE_CONTROL, "no-store"),
        ],
        page,
    )
        .into_response()
}

fn no_token() -> Response {
    (
        StatusCode::FORBIDDEN,
        [(header::CONTENT_TYPE, "text/plain; charset=utf-8")],
        "地址里少了令牌。回到起 feiyu-web 的那个终端，用它印出来的完整地址打开。\n",
    )
        .into_response()
}

async fn serve_asset(
    State(app): State<Arc<App>>,
    RawQuery(query): RawQuery,
    body: &'static str,
    mime: &'static str,
) -> Response {
    // 静态资源也要令牌：拿不到页面就拿不到里面那份 app.js，
    // 也就少一条「先摸清楚协议长什么样」的路。
    if !app.token_ok(query.as_deref()) {
        return no_token();
    }
    asset(body, mime)
}

async fn client(socket: WebSocket, app: Arc<App>) {
    let (mut sink, mut stream) = socket.split();

    // 先订阅再回放：反过来的话回放和订阅之间到的事件会丢。
    let mut rx = app.events.subscribe();
    let backlog: Vec<String> = app.replay.lock().unwrap().iter().cloned().collect();
    for line in backlog {
        if sink.send(Message::Text(line.into())).await.is_err() {
            return;
        }
    }

    let to_agent = app.to_agent.clone();
    let mut send_task = tokio::spawn(async move {
        loop {
            match rx.recv().await {
                Ok(line) => {
                    if sink.send(Message::Text(line.into())).await.is_err() {
                        break;
                    }
                }
                // 这个标签页读得太慢被落下了。跳过丢掉的那些接着发，
                // 比断开连接好：漏几条工具事件不影响后面看得懂。
                Err(broadcast::error::RecvError::Lagged(_)) => continue,
                Err(broadcast::error::RecvError::Closed) => break,
            }
        }
    });

    let mut recv_task = tokio::spawn(async move {
        while let Some(Ok(msg)) = stream.next().await {
            if let Message::Text(text) = msg {
                if to_agent.send(text.to_string()).is_err() {
                    break;
                }
            }
        }
    });

    tokio::select! {
        _ = &mut send_task => recv_task.abort(),
        _ = &mut recv_task => send_task.abort(),
    }
}

fn asset(body: &'static str, mime: &'static str) -> Response {
    (
        StatusCode::OK,
        [
            (header::CONTENT_TYPE, mime),
            (header::CACHE_CONTROL, "no-store"),
        ],
        body,
    )
        .into_response()
}

#[tokio::main]
async fn main() {
    let args = parse_args();
    let workspace = std::fs::canonicalize(&args.workspace)
        .unwrap_or_else(|_| std::path::PathBuf::from(&args.workspace));

    let mut child = match spawn_agent(&args) {
        Ok(child) => child,
        Err(err) => {
            eprintln!("起不来 feiyu（{}）：{err}", feiyu_bin());
            eprintln!("先确认终端里敲 feiyu 能跑；装在别处就用 FEIYU_BIN 指过去。");
            std::process::exit(1);
        }
    };
    let stdin = child.stdin.take().expect("stdin");
    let stdout = child.stdout.take().expect("stdout");

    let (events, _) = broadcast::channel::<String>(4096);
    let (to_agent, from_web) = mpsc::unbounded_channel::<String>();
    let app = Arc::new(App {
        events: events.clone(),
        to_agent,
        replay: Mutex::new(VecDeque::with_capacity(REPLAY)),
        token: make_token(),
        port: args.port,
    });

    tokio::spawn(pump_stdin(stdin, from_web));

    // agent 的每一行事件：记一份给回放，再广播给所有标签页。
    let reader_app = app.clone();
    tokio::spawn(async move {
        let mut lines = BufReader::new(stdout).lines();
        while let Ok(Some(line)) = lines.next_line().await {
            if line.trim().is_empty() {
                continue;
            }
            reader_app.record(&line);
            let _ = reader_app.events.send(line);
        }
        // agent 没了就没有继续开着的意义 —— 它才是这个服务的全部内容。
        let _ = reader_app
            .events
            .send(r#"{"kind":"gone","text":"agent 进程结束了"}"#.into());
        tokio::time::sleep(std::time::Duration::from_millis(300)).await;
        std::process::exit(0);
    });

    let router = Router::new()
        .route("/", get(serve_index))
        .route("/app.css", get(|s, q| serve_asset(s, q, APP_CSS, "text/css; charset=utf-8")))
        .route("/app.js", get(|s, q| serve_asset(s, q, APP_JS, "text/javascript; charset=utf-8")))
        .route("/logo.svg", get(|s, q| serve_asset(s, q, LOGO_SVG, "image/svg+xml")))
        .route("/ws", get(ws_handler))
        .with_state(app.clone());

    // 只听回环地址。理由见文件头，别改成可配置的。
    let addr = format!("127.0.0.1:{}", args.port);
    let listener = match tokio::net::TcpListener::bind(&addr).await {
        Ok(listener) => listener,
        Err(err) => {
            eprintln!("监听 {addr} 失败：{err}");
            eprintln!("多半是端口被占了，换一个：feiyu-web -p 7788");
            let _ = child.kill().await;
            std::process::exit(1);
        }
    };
    // 令牌在地址里。**别把它另外印一遍**，一份就够，多印一份多一处泄露。
    let url = format!("http://{addr}/?t={}", app.token);
    eprintln!("feiyu-web  {url}");
    eprintln!("工作目录   {}", workspace.display());
    eprintln!("地址里的令牌每次启动都不一样，别分享这个链接。停下来按 ctrl-c。");
    if args.open {
        let _ = Command::new("xdg-open").arg(&url).stdout(Stdio::null()).stderr(Stdio::null()).spawn();
    }

    // ctrl-c 要有一条**确定的**收尾路径，不能靠「信号正好落在哪一行」。
    // SIGINT 是发给整个前台进程组的，子进程也吃到一份，那一下可能落在它某个
    // 阻塞调用上，甩出一页 traceback，还会跳过它自己的清理（杀后台命令、
    // 关语言服务器和 MCP 进程）。所以这边先按协议请它停，给它一点时间，再动手。
    let shutdown = async {
        let _ = tokio::signal::ctrl_c().await;
        eprintln!("\n收工，正在收尾…");
    };
    let served = axum::serve(listener, router).with_graceful_shutdown(shutdown);
    if let Err(err) = served.await {
        eprintln!("服务停了：{err}");
    }
    let _ = app.to_agent.send(r#"{"op":"stop"}"#.to_string());
    // 等它自己退。它要关语言服务器和 MCP 的子进程，急不得，但也不能无限等。
    let deadline = std::time::Duration::from_secs(5);
    if tokio::time::timeout(deadline, child.wait()).await.is_err() {
        eprintln!("agent 没在 {} 秒内退干净，直接杀掉。", deadline.as_secs());
        let _ = child.kill().await;
    }
}


#[cfg(test)]
mod tests {
    use super::*;

    const T: &str = "0123456789abcdef0123456789abcdef";

    #[test]
    fn 令牌只认一模一样的() {
        assert!(token_ok(T, Some(&format!("t={T}"))));
        assert!(token_ok(T, Some(&format!("x=1&t={T}"))));
        assert!(!token_ok(T, Some("t=")));
        assert!(!token_ok(T, Some(&format!("t={}", &T[..31]))));   // 短一位
        assert!(!token_ok(T, Some(&format!("t={T}0"))));           // 长一位
        assert!(!token_ok(T, Some("nope=1")));
        assert!(!token_ok(T, None));
        // 令牌本身是空的时候一律不放行，别让「没生成」变成「谁都能进」
        assert!(!token_ok("", Some("t=")));
    }

    #[test]
    fn 随便一个网站连不上来() {
        // 这一条是整个服务最要紧的检查：WebSocket 不受同源策略保护
        let host = Some("127.0.0.1:7777");
        assert!(!origin_ok(7777, Some("https://evil.example"), host));
        assert!(!origin_ok(7777, Some("null"), host));
        assert!(!origin_ok(7777, None, host));
        assert!(!origin_ok(7777, Some("http://127.0.0.1:9999"), host));   // 别的端口
    }

    #[test]
    fn dns_rebinding_被_host_挡住() {
        let origin = Some("http://127.0.0.1:7777");
        assert!(!origin_ok(7777, origin, Some("evil.example:7777")));
        assert!(!origin_ok(7777, origin, Some("127.0.0.1:9999")));
        assert!(!origin_ok(7777, origin, None));
    }

    #[test]
    fn 自己的页面放行() {
        for name in ["127.0.0.1", "localhost", "[::1]"] {
            assert!(origin_ok(7777, Some(&format!("http://{name}:7777")),
                              Some(&format!("{name}:7777"))), "{name}");
        }
    }

    #[test]
    fn 令牌是随机的而且够长() {
        let (a, b) = (make_token(), make_token());
        assert_eq!(a.len(), 32);
        assert_ne!(a, b);
    }
}
