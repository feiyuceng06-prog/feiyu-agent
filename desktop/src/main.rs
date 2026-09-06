//! feiyu-desktop —— feiyu 的桌面端。
//!
//! 一个 Tauri 窗口，底下监管一个 `feiyu --serve` 子进程，两边一行一个 JSON。
//!
//! # 跟 0.51 那个网页端的区别
//!
//! 网页端是 HTTP + WebSocket 听在 127.0.0.1 上，为此要有一次性令牌、Origin 校验、
//! Host 校验（**WebSocket 不受同源策略保护**，随便一个网页都能连本机端口）。
//! 桌面端**根本不监听端口**，那一整套安全面直接消失 —— 前端和 agent 之间是
//! Tauri 的 `invoke` / `emit`，走的是进程内的通道，外面够不着。
//!
//! # 为什么还留着一个 devserver
//!
//! Tauri 在 Linux 上用的是 webkit2gtk，**没有 CDP**，headless Chrome 那套视觉夹具
//! （`dev/shoot.mjs` / `dev/input.mjs`）接不上去。`src/devserver.rs` 把同一份 `ui/`
//! 用 HTTP 端出来，夹具照旧能跑。它是测试用的，不是产品的一部分。
//! 前端靠 `window.__TAURI__` 在不在，自己决定走哪条传输。

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::io::{BufRead, BufReader, Write};
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::Mutex;

mod pathguard;
mod term;

use tauri::{Emitter, Manager};

/// agent 说的每一行都按这个事件名广播给前端。
const CHANNEL: &str = "feiyu://event";

/// 上传落在工作区里的哪儿。**必须在工作区内**，否则 agent 的文件工具够不着。
const UPLOAD_DIR: &str = ".feiyu/uploads";

struct Agent {
    stdin: Mutex<Option<ChildStdin>>,
    child: Mutex<Option<Child>>,
    /// 换工作台就是换它，跟着重起一个子进程。见 `switch`。
    workspace: Mutex<std::path::PathBuf>,
    /// 起子进程时要带的模型，换工作台时沿用。
    model: Option<String>,
}

/// 找 `feiyu` 那个可执行文件。`FEIYU_BIN` 优先，其次是同一棵源码树里的
/// `.venv/bin/feiyu`（开发时从 target/ 里跑起来的情况），最后交给 PATH。
fn feiyu_bin() -> String {
    if let Ok(explicit) = std::env::var("FEIYU_BIN") {
        if !explicit.is_empty() {
            return explicit;
        }
    }
    if let Ok(exe) = std::env::current_exe() {
        for base in exe.ancestors().skip(1).take(6) {
            let candidate = base.join(".venv/bin/feiyu");
            if candidate.is_file() {
                return candidate.to_string_lossy().into_owned();
            }
        }
    }
    "feiyu".into()
}

/// 前端发过来的一行指令，原样写进 agent 的 stdin。
#[tauri::command]
fn send(state: tauri::State<'_, Agent>, line: String) -> Result<(), String> {
    // 一行一个 JSON —— 正文里的换行在 JSON 里本来就是转义的，这里再挡一道，
    // 免得一条畸形消息把协议截成两条
    let line = line.replace('\n', " ");
    // 只打 op，不打正文 —— stderr 是用户的终端，别把他打的话原样喷出来。
    // 桌面端出问题时这是唯一能看见桥接有没有通的东西。
    if let Some(op) = line.split('"').nth(3) {
        eprintln!("→ {op}");
    }
    let mut guard = state.stdin.lock().map_err(|_| "stdin 锁坏了")?;
    let stdin = guard.as_mut().ok_or("agent 已经退出了")?;
    stdin
        .write_all(line.as_bytes())
        .and_then(|_| stdin.write_all(b"\n"))
        .and_then(|_| stdin.flush())
        .map_err(|e| e.to_string())
}

/// 把用户给的文件名收拾成一个**只是文件名**的东西。
///
/// 名字是拖进来的，`../../.ssh/authorized_keys` 这种必须变成 `authorized_keys`。
fn safe_name(raw: &str) -> String {
    let tail = raw.rsplit(['/', '\\']).next().unwrap_or("");
    let cleaned: String = tail
        .chars()
        .map(|c| {
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
    let first = dir.join(name);
    if !first.exists() {
        return first;
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

#[tauri::command]
fn upload(state: tauri::State<'_, Agent>, name: String, data: Vec<u8>) -> Result<String, String> {
    let here = state.workspace.lock().map_err(|_| "锁坏了")?.clone();
    let dir = here.join(UPLOAD_DIR);
    std::fs::create_dir_all(&dir).map_err(|e| format!("建不了上传目录：{e}"))?;
    let path = free_path(&dir, &safe_name(&name));
    std::fs::write(&path, &data).map_err(|e| format!("写不进去：{e}"))?;
    Ok(path
        .strip_prefix(&here)
        .unwrap_or(&path)
        .to_string_lossy()
        .replace('\\', "/"))
}

/// 一张图最多读多大。出图接口那边也是这个数。
const MAX_IMAGE: usize = 32 * 1024 * 1024;

/// 前端要把工作区里的一张图画出来（agent 出的图、read_file 看过的图）。
///
/// **路径必须落在工作区内**：它来自 agent 的输出，而 agent 会读网页、会跑命令，
/// 不是可信来源。`canonicalize` 之后比前缀，符号链接指到外面也拦得住。
///
/// 回的是 `ipc::Response`（原始字节，到 JS 那边是 ArrayBuffer）。**不要回 `Vec<u8>`** ——
/// 那会被序列化成一个个数字的 JSON 数组，一张 2MB 的图变成 8MB 的文本。
#[tauri::command]
fn read_image(
    state: tauri::State<'_, Agent>,
    path: String,
) -> Result<tauri::ipc::Response, String> {
    let here = state.workspace.lock().map_err(|_| "锁坏了")?.clone();
    let target = pathguard::under_root(&here, &path).ok_or("这个路径不在工作区里")?;
    let raw = std::fs::read(&target).map_err(|e| format!("读不了：{e}"))?;
    if raw.len() > MAX_IMAGE {
        return Err(format!("这张图 {} 字节，太大了", raw.len()));
    }
    Ok(tauri::ipc::Response::new(raw))
}

/// 前端要拿工作目录填状态栏。**函数名不要叫 workspace** —— `generate_handler!`
/// 展开出来的代码在 main 的作用域里按名字取函数，会被同名的局部变量遮住。
#[tauri::command]
fn where_am_i(state: tauri::State<'_, Agent>) -> String {
    state.workspace.lock().map(|w| w.to_string_lossy().into_owned()).unwrap_or_default()
}

struct Args {
    workspace: String,
    model: Option<String>,
    resume: bool,
}

fn parse_args() -> Args {
    let mut out = Args { workspace: ".".into(), model: None, resume: false };
    let mut argv = std::env::args().skip(1);
    while let Some(arg) = argv.next() {
        match arg.as_str() {
            "-r" | "--resume" => out.resume = true,
            "-w" | "--workspace" => out.workspace = argv.next().unwrap_or_else(|| ".".into()),
            "-m" | "--model" => out.model = argv.next(),
            "-h" | "--help" => {
                eprintln!(
                    "feiyu —— 桌面端\n\n\
                     用法：feiyu-desktop [选项]\n\n  \
                     -w, --workspace <目录>  agent 干活的目录（默认当前目录）\n  \
                     -m, --model <名字>      用 /key 清单里的哪一条\n  \
                     -r, --resume            接着上次在这个目录的对话\n\n\
                     终端版是 feiyu。"
                );
                std::process::exit(0);
            }
            _ => {}
        }
    }
    out
}
/// 起一个 `feiyu --serve`，并接一条把它 stdout 转成事件的线程。
///
/// 一个子进程一条读线程，stdout 一关它自己就结束 —— 换工作台时旧的那条会自然退掉。
fn start_agent(
    app: &tauri::AppHandle,
    workspace: &std::path::Path,
    model: Option<&str>,
    resume: bool,
) -> Result<(Child, ChildStdin), String> {
    let bin = feiyu_bin();
    let mut cmd = Command::new(&bin);
    // `--own-window`：**只有这个二进制会传。** 它是"前后端之间没有网络"的凭据 ——
    // 桌面端走 invoke/emit 的进程内通道，外面够不着；而 devserver 是听在回环上的，
    // 同机任何进程都能连。写密钥那条路只对前者开（见 serve.Server.trusted）。
    cmd.arg("--serve").arg("--own-window").arg("-w").arg(workspace);
    if let Some(model) = model {
        cmd.arg("-m").arg(model);
    }
    if resume {
        cmd.arg("--resume");
    }
    // stderr 原样留给终端：agent 那边人看的输出和 Python 的报错都在这儿
    let mut child = cmd
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit())
        .spawn()
        .map_err(|e| format!("起不来 feiyu（{bin}）：{e}"))?;
    let stdin = child.stdin.take().ok_or("拿不到 stdin")?;
    let stdout = child.stdout.take().ok_or("拿不到 stdout")?;
    let handle = app.clone();
    std::thread::spawn(move || {
        // agent 的每一行事件原样丢给前端。**整行转发不解析** ——
        // 解析是前端的事，这边多认一层就多一处要跟着协议改的地方。
        for line in BufReader::new(stdout).lines() {
            let Ok(line) = line else { break };
            if !line.trim().is_empty() {
                let _ = handle.emit(CHANNEL, line);
            }
        }
    });
    Ok((child, stdin))
}

/// 收掉当前这个 agent。先关 stdin 让它走自己那条干净的退出路径，再动手。
fn stop_agent(state: &Agent) {
    if let Ok(mut guard) = state.stdin.lock() {
        guard.take();
    }
    if let Ok(mut guard) = state.child.lock() {
        if let Some(mut old) = guard.take() {
            std::thread::sleep(std::time::Duration::from_millis(400));
            let _ = old.kill();
            let _ = old.wait();
        }
    }
}

/// 换工作台：把旧的收摊，按新目录重起一个。
///
/// **不是就地改工作目录。** agent 的工作目录是它构造时定死的 —— 文件版本跟踪、
/// 语言服务器、项目钩子/技能/MCP、按目录存的会话和进度全挂在上面，就地改等于
/// 要把那一摞东西挨个重挂一遍。重起一个干净得多，代价只是几百毫秒。
#[tauri::command]
fn switch(
    app: tauri::AppHandle,
    state: tauri::State<'_, Agent>,
    dir: String,
) -> Result<String, String> {
    let next = std::fs::canonicalize(&dir).map_err(|e| format!("{dir} 打不开：{e}"))?;
    if !next.is_dir() {
        return Err(format!("{} 不是一个目录", next.display()));
    }
    stop_agent(&state);
    // 面板里那个 shell 也跟着走：它是「在这个工作台上」的手
    app.state::<std::sync::Arc<term::Term>>().stop();
    // **换过来之后不带 --resume**：那是「接着上次在这个目录的对话」，
    // 而用户刚点的是「去另一个工作台」，默认给他一段新的。
    let model = state.model.clone();
    let (child, stdin) = start_agent(&app, &next, model.as_deref(), false)?;
    *state.child.lock().map_err(|_| "锁坏了")? = Some(child);
    *state.stdin.lock().map_err(|_| "锁坏了")? = Some(stdin);
    *state.workspace.lock().map_err(|_| "锁坏了")? = next.clone();
    eprintln!("换到 {}", next.display());
    Ok(next.to_string_lossy().into_owned())
}

fn main() {
    // NVIDIA + WebKitGTK 的 dmabuf 渲染路径在 Wayland 上会直接协议错误退出
    // （Gdk-Message: Error 71）。关掉它是官方绕法，**必须赶在 GTK 初始化之前设**。
    if std::env::var_os("WEBKIT_DISABLE_DMABUF_RENDERER").is_none() {
        unsafe { std::env::set_var("WEBKIT_DISABLE_DMABUF_RENDERER", "1") };
    }

    let args = parse_args();
    let workspace = std::fs::canonicalize(&args.workspace)
        .unwrap_or_else(|_| std::path::PathBuf::from(&args.workspace));

    let agent = Agent {
        stdin: Mutex::new(None),
        child: Mutex::new(None),
        workspace: Mutex::new(workspace.clone()),
        model: args.model.clone(),
    };
    let boot = (workspace, args.model, args.resume);

    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .manage(agent)
        .manage(std::sync::Arc::new(term::Term::default()))
        .invoke_handler(tauri::generate_handler![
            send, upload, read_image, where_am_i, switch,
            term::term_start, term::term_write, term::term_resize, term::term_stop
        ])
        .setup(move |app| {
            let (workspace, model, resume) = boot;
            match start_agent(app.handle(), &workspace, model.as_deref(), resume) {
                Ok((child, stdin)) => {
                    let state = app.state::<Agent>();
                    *state.child.lock().unwrap() = Some(child);
                    *state.stdin.lock().unwrap() = Some(stdin);
                }
                Err(err) => {
                    eprintln!("{err}");
                    eprintln!("先确认终端里敲 feiyu 能跑；装在别处就用 FEIYU_BIN 指过去。");
                    std::process::exit(1);
                }
            }
            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::Destroyed = event {
                // 关窗口就把 agent 和面板里那个 shell 一起收摊。
                // 留着的话用户已经没有任何句柄能找到它们了。
                stop_agent(&window.state::<Agent>());
                term::stop_for(window);
            }
        })
        .run(tauri::generate_context!())
        .expect("窗口起不来");
}
