"""按一下 ctrl-c，看它退不退得干净。

    python web/dev/ctrlc.py <工作目录> <状态目录> <端口>

**必须挂在真 pty 上跑成前台任务。** 非交互 shell 起的后台任务会把 SIGINT 继承成
忽略，那种环境下按什么都没反应，测了等于没测。

即便这样也**复现不了** 0.50.2 修的那条 traceback：Rust 要是先死，Python 走的是
stdin EOF 那条干净路径。所以这个脚本验的是「退干净了、没有残留进程」，
不是「那个竞态不存在了」——后者靠的是两边都堵上（见 CLAUDE.md）。
"""
import os
import pty
import select
import signal
import sys
import time

ws, state, port = sys.argv[1], sys.argv[2], sys.argv[3]
pid, fd = pty.fork()
if pid == 0:
    os.chdir(ws)
    os.environ["XDG_STATE_HOME"] = state
    os.execvp("feiyu-web", ["feiyu-web", "-p", port, "--no-open"])

out = b""
deadline = time.time() + 8
while time.time() < deadline:
    if select.select([fd], [], [], 0.2)[0]:
        try:
            out += os.read(fd, 65536)
        except OSError:
            break

os.write(fd, b"\x03")                 # ctrl-c，跟用户按的是同一个字节

alive = True
end = time.time() + 8
while time.time() < end:
    if select.select([fd], [], [], 0.2)[0]:
        try:
            out += os.read(fd, 65536)
        except OSError:
            pass
    if os.waitpid(pid, os.WNOHANG)[0]:
        alive = False
        break

if alive:
    os.kill(pid, signal.SIGKILL)
    os.waitpid(pid, 0)

text = out.decode("utf-8", "replace")
print(text)
print("=" * 46)
print("traceback 次数:", text.count("Traceback"))
print("ctrl-c 之后还活着吗:", "是（有问题）" if alive else "否（干净退出）")
