//! 终端面板底下那个真 shell。
//!
//! 用 pty 起一个 `$SHELL`，输出按块发给前端由 xterm 画。**是真 pty 不是管道**：
//! 管道下 `ls` 不上色、`top` 之类根本不工作，交互式程序全废。
//!
//! 它跟 agent 是**两回事**：agent 跑命令走它自己那条 `bash` 工具（有权限门、
//! 有沙箱、结果进对话）。这个面板是给人自己用的手，两边都在同一个工作目录里，
//! 但互不干涉 —— 别把它做成「agent 的命令在这儿跑」，那会绕开权限门。

use std::io::{Read, Write};
use std::sync::{Arc, Mutex};

use portable_pty::{CommandBuilder, NativePtySystem, PtySize, PtySystem};
use tauri::{Emitter, Manager};

/// 终端的输出按这个事件名发给前端。
pub const CHANNEL: &str = "feiyu://term";

#[derive(Default)]
pub struct Term {
    writer: Mutex<Option<Box<dyn Write + Send>>>,
    master: Mutex<Option<Box<dyn portable_pty::MasterPty + Send>>>,
    child: Mutex<Option<Box<dyn portable_pty::Child + Send + Sync>>>,
}

impl Term {
    /// 关掉当前这个 shell。换工作台、关窗口都要走它。
    pub fn stop(&self) {
        if let Ok(mut w) = self.writer.lock() {
            w.take();
        }
        if let Ok(mut m) = self.master.lock() {
            m.take();
        }
        if let Ok(mut c) = self.child.lock()
            && let Some(mut child) = c.take() {
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}

/// 起一个 shell。已经有一个就先收掉 —— 一个面板一个 shell，不做多标签。
#[tauri::command]
pub fn term_start(
    app: tauri::AppHandle,
    term: tauri::State<'_, Arc<Term>>,
    cwd: String,
    cols: u16,
    rows: u16,
) -> Result<(), String> {
    term.stop();
    let pty = NativePtySystem::default();
    let pair = pty
        .openpty(PtySize { rows, cols, pixel_width: 0, pixel_height: 0 })
        .map_err(|e| e.to_string())?;

    let shell = if cfg!(windows) {
        std::env::var("COMSPEC").unwrap_or_else(|_| "cmd.exe".into())
    } else {
        std::env::var("SHELL").unwrap_or_else(|_| "/bin/sh".into())
    };
    let mut cmd = CommandBuilder::new(&shell);
    cmd.cwd(&cwd);
    // 交互式：不然 zsh/bash 不读 rc、没有提示符，看着像卡住了
    if !cfg!(windows) { cmd.args(["-i"]); }
    // 有些提示符会按 TERM 决定画不画颜色
    cmd.env("TERM", "xterm-256color");

    let child = pair.slave.spawn_command(cmd).map_err(|e| e.to_string())?;
    let mut reader = pair.master.try_clone_reader().map_err(|e| e.to_string())?;
    let writer = pair.master.take_writer().map_err(|e| e.to_string())?;

    *term.writer.lock().map_err(|_| "锁坏了")? = Some(writer);
    *term.child.lock().map_err(|_| "锁坏了")? = Some(child);
    *term.master.lock().map_err(|_| "锁坏了")? = Some(pair.master);

    std::thread::spawn(move || {
        let mut buf = [0u8; 8192];
        loop {
            match reader.read(&mut buf) {
                Ok(0) | Err(_) => break,
                Ok(n) => {
                    // **按字节发，不要在这儿按行切**：终端的转义序列会跨读，
                    // 切开就会把光标控制拆散，画面开始错位。
                    let chunk = String::from_utf8_lossy(&buf[..n]).into_owned();
                    let _ = app.emit(CHANNEL, chunk);
                }
            }
        }
        let _ = app.emit(CHANNEL, "\r\n\x1b[90m（shell 结束了）\x1b[0m\r\n".to_string());
    });
    Ok(())
}

#[tauri::command]
pub fn term_write(term: tauri::State<'_, Arc<Term>>, data: String) -> Result<(), String> {
    let mut guard = term.writer.lock().map_err(|_| "锁坏了")?;
    let writer = guard.as_mut().ok_or("终端还没起")?;
    writer.write_all(data.as_bytes()).map_err(|e| e.to_string())?;
    writer.flush().map_err(|e| e.to_string())
}

#[tauri::command]
pub fn term_resize(term: tauri::State<'_, Arc<Term>>, cols: u16, rows: u16) -> Result<(), String> {
    let guard = term.master.lock().map_err(|_| "锁坏了")?;
    let master = guard.as_ref().ok_or("终端还没起")?;
    master
        .resize(PtySize { rows, cols, pixel_width: 0, pixel_height: 0 })
        .map_err(|e| e.to_string())
}

#[tauri::command]
pub fn term_stop(term: tauri::State<'_, Arc<Term>>) {
    term.stop();
}

/// 窗口关掉 / 换工作台时把 shell 一起收摊。
pub fn stop_for(window: &tauri::Window) {
    window.state::<Arc<Term>>().stop();
}
