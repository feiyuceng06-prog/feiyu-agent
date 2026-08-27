"""后台跑的 bash 命令 构建、dev server、跑一整套测试不该把整轮 turn 卡住 """

import os
import signal
import threading
import time
from collections import deque

# 每个后台命令在内存里最多留这么多还没读走的输出
# 满了从最早的丢——一个跑飞的 `yes` 不该把内存吃光，末尾那段才是有用的
MAX_CHARS = 200_000
# 杀进程组：先 TERM 给它收尾的机会，等这么久还在就 KILL
GRACE = 2.0


class Job:
    def __init__(self, jid: int, command: str, proc):
        self.id = jid
        self.command = command
        self.proc = proc
        self.started = time.monotonic()
        self.reported = False       # 结束的消息只往对话里塞一次
        self._chunks: deque[str] = deque()
        self._size = 0
        self._dropped = False
        self._lock = threading.Lock()
        # 不边跑边收的话管道写满 64KB 就把子进程堵死，看着像卡住
        threading.Thread(target=self._drain, daemon=True).start()

    def _drain(self) -> None:
        try:
            for line in self.proc.stdout:
                with self._lock:
                    self._chunks.append(line)
                    self._size += len(line)
                    while self._size > MAX_CHARS and len(self._chunks) > 1:
                        self._size -= len(self._chunks.popleft())
                        self._dropped = True
        except (ValueError, OSError):
            pass        # 被 kill 掉时管道会在读到一半时关掉
        finally:
            try:
                self.proc.stdout.close()
                self.proc.wait()
            except OSError:
                pass

    def take(self) -> tuple[str, bool]:
        """取走还没读过的输出，顺带说有没有丢过

        读过就不再给：模型会反复轮询，每次都灌整份日志的话上下文几轮就满了
        """
        with self._lock:
            text = "".join(self._chunks)
            self._chunks.clear()
            dropped, self._dropped, self._size = self._dropped, False, 0
        return text, dropped

    @property
    def code(self):
        return self.proc.poll()

    @property
    def running(self) -> bool:
        return self.proc.poll() is None

    @property
    def elapsed(self) -> int:
        return int(time.monotonic() - self.started)

    def status(self) -> str:
        if self.running:
            return "运行中 %ds" % self.elapsed
        return "已结束，退出码 %s" % self.code

    def kill(self) -> bool:
        """杀掉整个进程组 只杀 pid 的话 `npm run dev` 底下那个 node 会活下来 """
        if not self.running:
            return False
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(os.getpgid(self.proc.pid), sig)
            except (ProcessLookupError, PermissionError, OSError):
                return True
            deadline = time.monotonic() + GRACE
            while time.monotonic() < deadline:
                if not self.running:
                    return True
                time.sleep(0.05)
        return True


class Jobs:
    def __init__(self) -> None:
        self._items: dict[int, Job] = {}
        self._next = 1

    def start(self, command: str, proc) -> Job:
        job = Job(self._next, command, proc)
        self._items[job.id] = job
        self._next += 1
        return job

    def get(self, jid) -> Job | None:
        try:
            return self._items.get(int(jid))
        except (TypeError, ValueError):
            return None

    def all(self) -> list[Job]:
        return [self._items[k] for k in sorted(self._items)]

    def running(self) -> list[Job]:
        return [j for j in self.all() if j.running]

    def finished_since(self) -> list[Job]:
        """上次问过之后新结束的 只报一次，每轮都提醒的话它会被这条消息带跑偏 """
        fresh = [j for j in self.all() if not j.running and not j.reported]
        for job in fresh:
            job.reported = True
        return fresh

    def kill_all(self) -> int:
        """退出前收尾 留着的话用户关掉 feiyu 之后那些进程还在跑 """
        return sum(1 for job in self.all() if job.kill())
