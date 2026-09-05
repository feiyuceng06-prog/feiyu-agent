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
//! # 只听 127.0.0.1
//!
//! **这一条不要改成可配置的。** 连上这个端口的人可以让 agent 在这台机器上写文件、
//! 跑命令。它没有登录、没有令牌，唯一的边界就是「只有本机能连」。要从别的机器用，
//! 开一条 ssh 隧道（`ssh -L 7777:127.0.0.1:7777 这台机器`），那样认证是 ssh 的事。
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
use axum::extract::State;
use axum::http::{header, StatusCode};
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

/// 新标签页连上时回放多少条事件。够看清「刚才发生了什么」，又不至于把一场长任务
/// 的几千条工具事件全堆给它。
const REPLAY: usize = 400;

struct App {
    /// 发给所有浏览器的事件。
    events: broadcast::Sender<String>,
    /// 浏览器发来的指令，汇总到写 stdin 的那个任务。
    to_agent: mpsc::UnboundedSender<String>,
    /// 最近的事件，给新连上的标签页回放。
    replay: Mutex<VecDeque<String>>,
}

impl App {
    fn record(&self, line: &str) {
        let mut log = self.replay.lock().unwrap();
        if log.len() == REPLAY {
            log.pop_front();
        }
        log.push_back(line.to_string());
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

async fn ws_handler(ws: WebSocketUpgrade, State(app): State<Arc<App>>) -> Response {
    ws.on_upgrade(move |socket| client(socket, app))
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
        .route("/", get(|| async { asset(INDEX_HTML, "text/html; charset=utf-8") }))
        .route("/app.css", get(|| async { asset(APP_CSS, "text/css; charset=utf-8") }))
        .route(
            "/app.js",
            get(|| async { asset(APP_JS, "text/javascript; charset=utf-8") }),
        )
        .route("/ws", get(ws_handler))
        .with_state(app);

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
    let url = format!("http://{addr}/");
    eprintln!("feiyu-web  {url}");
    eprintln!("工作目录   {}", workspace.display());
    eprintln!("停下来按 ctrl-c。");
    if args.open {
        let _ = Command::new("xdg-open").arg(&url).stdout(Stdio::null()).stderr(Stdio::null()).spawn();
    }

    if let Err(err) = axum::serve(listener, router).await {
        eprintln!("服务停了：{err}");
    }
    let _ = child.kill().await;
}
