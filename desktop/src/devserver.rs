//! feiyu-devserver —— **只给测试用的**静态服务，不是产品的一部分。
//!
//! Tauri 在 Linux 上用的是 webkit2gtk，**没有 CDP**，`dev/shoot.mjs` / `dev/input.mjs`
//! 那套 headless Chrome 夹具接不上去。这个二进制把同一份 `ui/` 用 HTTP 端出来，
//! 夹具照旧能跑；前端靠 `window.__TAURI__` 在不在自己决定走哪条传输。
//!
//! `/ws` 和 `/upload` 那两道门（令牌 + Origin + Host）**照旧全在** —— 它虽然只在
//! 开发时起，但起的时候一样能让 agent 在这台机器上跑命令。静态资源不设门：
//! 前端要用相对路径才能在 Tauri 里跑，给一份 HTML 加门只是防指纹，不值这个代价。

mod pathguard;

use std::collections::VecDeque;
use std::process::Stdio;
use std::sync::{Arc, Mutex};

use axum::extract::ws::{Message, WebSocket, WebSocketUpgrade};
use axum::body::Bytes;
use axum::extract::{DefaultBodyLimit, RawQuery, State};
use axum::http::{header, HeaderMap, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::Router;
// axum 的 WebSocket 是 futures 的 Sink/Stream，split / next / send 都在这两个 trait 上
use futures_util::{SinkExt, StreamExt};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::process::{Child, ChildStdin, Command};
use tokio::sync::{broadcast, mpsc};

const INDEX_HTML: &str = include_str!("../ui/index.html");
const APP_CSS: &str = include_str!("../ui/app.css");
const APP_JS: &str = include_str!("../ui/app.js");
const LOGO_SVG: &str = include_str!("../ui/logo.svg");

/// 新标签页连上时回放多少条事件。够看清「刚才发生了什么」，又不至于把一场长任务
/// 的几千条工具事件全堆给它。
const REPLAY: usize = 400;

/// 一条 WebSocket 消息封顶多大。Python 那边另有一道 `MAX_TEXT`，两道都要。
const MAX_MESSAGE: usize = 1 << 20; // 1 MiB

/// 上传封顶多大。模型能吃下的东西本来就不大，几十 MB 的文件上传上去也只是占地方。
const MAX_UPLOAD: usize = 32 << 20; // 32 MiB
/// 上传的东西落在工作区里的哪儿。**必须在工作区内**，不然 agent 的文件工具够不着。
const UPLOAD_DIR: &str = ".feiyu/uploads";

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
    /// agent 的工作目录。上传的东西必须落在它下面，否则 agent 够不着。
    workspace: std::path::PathBuf,
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

fn no_token() -> Response {
    (
        StatusCode::FORBIDDEN,
        [(header::CONTENT_TYPE, "text/plain; charset=utf-8")],
        "地址里少了令牌。回到起 feiyu-web 的那个终端，用它印出来的完整地址打开。\n",
    )
        .into_response()
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

/// 把用户给的文件名收拾成一个**只是文件名**的东西。
///
/// 这一步不能省：名字是浏览器给的，`../../.ssh/authorized_keys` 这种必须变成
/// `authorized_keys`。只留最后一段，再把路径分隔符和一切控制字符换掉；
/// 空的、只有点的、隐藏文件一律另起一个名字。
fn safe_name(raw: &str) -> String {
    let tail = raw.rsplit(['/', '\\']).next().unwrap_or("");
    let cleaned: String = tail
        .chars()
        .map(|c| {
            // Windows 上不许出现的那些也一起换掉，免得同一份工作区换个系统就打不开
            let nope = matches!(c, '/' | '\\' | ':' | '*' | '?' | '"' | '<' | '>' | '|');
            if c.is_control() || nope { '_' } else { c }
        })
        .collect();
    let cleaned = cleaned.trim().trim_start_matches('.').to_string();
    if cleaned.is_empty() || cleaned.len() > 120 {
        format!("upload-{}", std::process::id())
    } else {
        cleaned
    }
}

/// 名字撞了就加个后缀，别默默盖掉别人的文件。
fn free_path(dir: &std::path::Path, name: &str) -> std::path::PathBuf {
    let candidate = dir.join(name);
    if !candidate.exists() {
        return candidate;
    }
    let (stem, ext) = match name.rsplit_once('.') {
        Some((a, b)) if !a.is_empty() => (a.to_string(), format!(".{b}")),
        _ => (name.to_string(), String::new()),
    };
    for n in 2..1000 {
        let next = dir.join(format!("{stem}-{n}{ext}"));
        if !next.exists() {
            return next;
        }
    }
    dir.join(format!("{stem}-{}{ext}", std::process::id()))
}

/// 一张图最多端多大。
const MAX_IMAGE: usize = 32 * 1024 * 1024;

fn mime_of(path: &std::path::Path) -> &'static str {
    match path.extension().and_then(|e| e.to_str()).unwrap_or("").to_ascii_lowercase().as_str() {
        "png" => "image/png",
        "jpg" | "jpeg" => "image/jpeg",
        "gif" => "image/gif",
        "webp" => "image/webp",
        "svg" => "image/svg+xml",
        _ => "application/octet-stream",
    }
}

/// `GET /file?t=<令牌>&p=<工作区相对路径>` —— 把工作区里的一张图端出来。
///
/// 桌面端那条路走的是 `invoke("read_image")`，这条只给夹具用。
/// **路径必须落在工作区内**，`canonicalize` 之后比前缀 —— 这个查询串是外面给的。
/// 不校验 Origin：`<img src>` 的请求浏览器本来就不带 Origin，令牌那道门够了。
async fn file(State(app): State<Arc<App>>, RawQuery(query): RawQuery) -> Response {
    if !app.token_ok(query.as_deref()) {
        return no_token();
    }
    let want = query
        .as_deref()
        .unwrap_or("")
        .split('&')
        .find_map(|kv| kv.strip_prefix("p="))
        .map(percent_decode)
        .unwrap_or_default();
    let target = match pathguard::under_root(&app.workspace, &want) {
        Some(p) => p,
        None => return (StatusCode::NOT_FOUND, "no such file\n").into_response(),
    };
    match std::fs::read(&target) {
        Ok(raw) if raw.len() <= MAX_IMAGE => (
            StatusCode::OK,
            [(header::CONTENT_TYPE, mime_of(&target)), (header::CACHE_CONTROL, "no-store")],
            raw,
        )
            .into_response(),
        Ok(_) => (StatusCode::PAYLOAD_TOO_LARGE, "too big\n").into_response(),
        Err(_) => (StatusCode::NOT_FOUND, "no such file\n").into_response(),
    }
}

async fn upload(
    State(app): State<Arc<App>>,
    RawQuery(query): RawQuery,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    if !app.token_ok(query.as_deref()) {
        return no_token();
    }
    // 上传是**写这台机器的磁盘**，Origin 这道门比读资源那边更要紧
    if !app.origin_ok(&headers) {
        return denied("origin not allowed");
    }
    let raw = headers
        .get("x-feiyu-filename")
        .and_then(|v| v.to_str().ok())
        .unwrap_or("");
    // 文件名走 header 传，因为它可能是中文；浏览器那边 encodeURIComponent 过
    let decoded = percent_decode(raw);
    let name = safe_name(&decoded);
    let dir = app.workspace.join(UPLOAD_DIR);
    if let Err(err) = std::fs::create_dir_all(&dir) {
        return (StatusCode::INTERNAL_SERVER_ERROR, format!("建不了上传目录：{err}\n"))
            .into_response();
    }
    let path = free_path(&dir, &name);
    if let Err(err) = std::fs::write(&path, &body) {
        return (StatusCode::INTERNAL_SERVER_ERROR, format!("写不进去：{err}\n")).into_response();
    }
    // 回相对工作区的路径 —— agent 的文件工具只认这种
    let shown = path
        .strip_prefix(&app.workspace)
        .unwrap_or(&path)
        .to_string_lossy()
        .replace('\\', "/");
    eprintln!("收下 {} （{} 字节）", shown, body.len());
    (
        StatusCode::OK,
        [(header::CONTENT_TYPE, "application/json; charset=utf-8")],
        format!(
            r#"{{"path":{},"size":{}}}"#,
            serde_json::to_string(&shown).unwrap_or_else(|_| "\"\"".into()),
            body.len()
        ),
    )
        .into_response()
}

/// `%E4%B8%AD` 这种还原成字符。只为一个文件名，不值得引个 urlencoding。
fn percent_decode(raw: &str) -> String {
    let bytes = raw.as_bytes();
    let mut out = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i] == b'%' && i + 2 < bytes.len() {
            if let Ok(byte) = u8::from_str_radix(&raw[i + 1..i + 3], 16) {
                out.push(byte);
                i += 3;
                continue;
            }
        }
        out.push(bytes[i]);
        i += 1;
    }
    String::from_utf8_lossy(&out).into_owned()
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
        workspace: workspace.clone(),
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
        .route("/app.js", get(|| async { asset(APP_JS, "text/javascript; charset=utf-8") }))
        .route("/logo.svg", get(|| async { asset(LOGO_SVG, "image/svg+xml") }))
        .route("/ws", get(ws_handler))
        .route("/file", get(file))
        .route("/upload", post(upload).layer(DefaultBodyLimit::max(MAX_UPLOAD)))
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
    fn 上传的文件名只能是文件名() {
        // 名字是浏览器给的，路径穿越必须在这里断掉
        assert_eq!(safe_name("../../.ssh/authorized_keys"), "authorized_keys");
        assert_eq!(safe_name("/etc/passwd"), "passwd");
        assert_eq!(safe_name("..\\..\\win.ini"), "win.ini");
        assert_eq!(safe_name("a/b/c.png"), "c.png");
        // 隐藏文件、空名字、只有点的，一律另起一个
        assert!(!safe_name(".bashrc").starts_with('.'));
        assert!(!safe_name("").is_empty());
        assert!(!safe_name("...").contains(".."));
        // 正常的名字原样留着，中文也是
        assert_eq!(safe_name("报表 2026.xlsx"), "报表 2026.xlsx");
        assert_eq!(safe_name("a:b*c?.txt"), "a_b_c_.txt");
    }

    #[test]
    fn 文件名里的百分号编码要还原() {
        assert_eq!(percent_decode("%E4%B8%AD%E6%96%87.txt"), "中文.txt");
        assert_eq!(percent_decode("plain.txt"), "plain.txt");
        assert_eq!(percent_decode("%ZZ"), "%ZZ");        // 坏的编码原样留着，不该崩
    }

    #[test]
    fn 令牌是随机的而且够长() {
        let (a, b) = (make_token(), make_token());
        assert_eq!(a.len(), 32);
        assert_ne!(a, b);
    }
}
