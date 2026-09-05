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

use tauri::{Emitter, Manager};

/// agent 说的每一行都按这个事件名广播给前端。
const CHANNEL: &str = "feiyu://event";

/// 上传落在工作区里的哪儿。**必须在工作区内**，否则 agent 的文件工具够不着。
const UPLOAD_DIR: &str = ".feiyu/uploads";

struct Agent {
    stdin: Mutex<Option<ChildStdin>>,
    child: Mutex<Option<Child>>,
    workspace: std::path::PathBuf,
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
    let dir = state.workspace.join(UPLOAD_DIR);
    std::fs::create_dir_all(&dir).map_err(|e| format!("建不了上传目录：{e}"))?;
    let path = free_path(&dir, &safe_name(&name));
    std::fs::write(&path, &data).map_err(|e| format!("写不进去：{e}"))?;
    Ok(path
        .strip_prefix(&state.workspace)
        .unwrap_or(&path)
        .to_string_lossy()
        .replace('\\', "/"))
}

/// 前端要拿工作目录填状态栏。**函数名不要叫 workspace** —— `generate_handler!`
/// 展开出来的代码在 main 的作用域里按名字取函数，会被同名的局部变量遮住。
#[tauri::command]
fn where_am_i(state: tauri::State<'_, Agent>) -> String {
    state.workspace.to_string_lossy().into_owned()
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

fn main() {
    // NVIDIA + WebKitGTK 的 dmabuf 渲染路径在 Wayland 上会直接协议错误退出
    // （Gdk-Message: Error 71）。关掉它是官方绕法，**必须赶在 GTK 初始化之前设**。
    if std::env::var_os("WEBKIT_DISABLE_DMABUF_RENDERER").is_none() {
        unsafe { std::env::set_var("WEBKIT_DISABLE_DMABUF_RENDERER", "1") };
    }

    let args = parse_args();
    let workspace = std::fs::canonicalize(&args.workspace)
        .unwrap_or_else(|_| std::path::PathBuf::from(&args.workspace));

    let bin = feiyu_bin();
    let mut cmd = Command::new(&bin);
    cmd.arg("--serve").arg("-w").arg(&workspace);
    if let Some(model) = &args.model {
        cmd.arg("-m").arg(model);
    }
    if args.resume {
        cmd.arg("--resume");
    }
    // stderr 原样留给终端：agent 那边人看的输出和 Python 的报错都在这儿
    let mut child = match cmd
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit())
        .spawn()
    {
        Ok(child) => child,
        Err(err) => {
            eprintln!("起不来 feiyu（{bin}）：{err}");
            eprintln!("先确认终端里敲 feiyu 能跑；装在别处就用 FEIYU_BIN 指过去。");
            std::process::exit(1);
        }
    };
    let stdin = child.stdin.take().expect("stdin");
    let stdout = child.stdout.take().expect("stdout");

    let agent = Agent {
        stdin: Mutex::new(Some(stdin)),
        child: Mutex::new(Some(child)),
        workspace,
    };

    tauri::Builder::default()
        .manage(agent)
        .invoke_handler(tauri::generate_handler![send, upload, where_am_i])
        .setup(move |app| {
            let handle = app.handle().clone();
            // agent 的每一行事件原样丢给前端。**整行转发不解析** ——
            // 解析是前端的事，这边多认一层就多一处要跟着协议改的地方。
            std::thread::spawn(move || {
                for line in BufReader::new(stdout).lines() {
                    let Ok(line) = line else { break };
                    if line.trim().is_empty() {
                        continue;
                    }
                    let _ = handle.emit(CHANNEL, line);
                }
                let _ = handle.emit(CHANNEL, r#"{"kind":"gone","text":"agent 进程结束了"}"#);
            });
            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::Destroyed = event {
                // 关窗口就把 agent 一起收摊。留着的话用户已经没有任何句柄能找到它了。
                let state = window.state::<Agent>();
                if let Ok(mut guard) = state.stdin.lock() {
                    guard.take();          // 关掉 stdin，让它自己走干净的退出路径
                }
                if let Ok(mut guard) = state.child.lock() {
                    if let Some(mut child) = guard.take() {
                        std::thread::spawn(move || {
                            std::thread::sleep(std::time::Duration::from_millis(1500));
                            let _ = child.kill();
                        });
                    }
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("窗口起不来");
}
