"""往 `feiyu --serve` 灌畸形消息，看它活不活得下来

    python dev/fuzz_serve.py            # 两轮都跑
    python dev/fuzz_serve.py --only ops # 只灌畸形 op

两组输入：

1. **畸形 op** —— 类型不对、少字段、`../` 路径、半截 JSON。
2. **每条斜杠命令 × 五种垃圾参数** —— 空、不存在的名字、`../`、多参数、超长。

判据只有一条，但它是硬的：**跑完之后还答得上 `status`，而且退出码为 0**。
一条歪消息带走整个进程的话，用户丢的是整段对话加上所有后台命令 ——
0.65.1 就是这么发现 `run()` 那个循环没有兜底的（`{"op":"bot","do":"save",
"row":[1,2,3]}` 一条就够）。

**别拿真 key 反复扫**：`/compact` 和 `/review` 会真的调一次模型。
这个夹具默认把 XDG_CONFIG_HOME 指到一个临时目录，所以没有模型配置、
那两条会早退；要连真配置跑就自己设环境变量。
"""

import json
import os
import subprocess
import sys
import tempfile
import time

BAD_OPS = [
    '{"op":"bot"}',
    '{"op":"bot","do":"start"}',
    '{"op":"bot","do":"start","name":null}',
    '{"op":"bot","do":"start","name":123}',
    '{"op":"bot","do":"start","name":"../../etc/passwd"}',
    '{"op":"bot","do":"save","row":null}',
    '{"op":"bot","do":"save","row":{"name":"../x","body":"y"}}',
    '{"op":"bot","do":"save","row":[1,2,3]}',
    '{"op":"bot","do":"drop","name":{"a":1}}',
    '{"op":"bot","do":"没这个动作","name":"甲"}',
    '{"op":"detail","what":"bot"}',
    '{"op":"detail","what":"bot","name":["x"]}',
    '{"op":"open","id":"../../../etc/passwd"}',
    '{"op":"delete","id":null}',
    '{"op":"reply","id":"不是数字"}',
    '{"op":"reply","id":[1],"answer":{"x":1}}',
    '{"op":"toggle","kind":"tools"}',
    '{"op":"key","row":{"name":"../x","model":"m"}}',
    '{"op":"key_drop"}',
    '{"op":"credits","name":[1]}',
    '{"op":"paths"}',
    '{"op":"paths","prefix":null}',
    '{"op":"paths","prefix":["a"]}',
    '{"op":"paths","prefix":"../../../etc/"}',
    '{"op":"paths","prefix":"' + "x" * 500 + '"}',
    '{"op":"model_list"}',
    '{"op":"model_list","ticket":"x","kind":["openai"],"base_url":3,"key":{"a":1}}',
    '{"op":"model_list","base_url":"file:///etc/passwd","key":"sk"}',
    '{"op":"model_list","name":["甲"],"ticket":null}',
    '{"op":"没这个 op"}',
    "不是 json",
    '{"op":',
    "[]",
    "null",
    '{"op":"command"}',
    '{"op":"cycle_mode"}',
]

JUNK = ("", " 没这个", " ../../etc/passwd", " 1 2 3", " " + "x" * 500)


def command_lines():
    from feiyu import ui
    out = []
    for name in sorted(ui.SLASH):
        if name in ("/exit", "/clear"):     # 一个会退出，一个会删掉存盘
            continue
        for arg in JUNK:
            out.append(json.dumps({"op": "command", "text": name + arg}))
    return out


def run(lines, label):
    home = tempfile.mkdtemp()
    env = dict(os.environ,
               XDG_CONFIG_HOME=os.path.join(home, "cfg"),
               XDG_STATE_HOME=os.path.join(home, "state"))
    ws = os.path.join(home, "ws")
    os.makedirs(ws)
    child = subprocess.Popen(
        [sys.executable, "-m", "feiyu", "--serve", "--own-window", "-w", ws],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, env=env, bufsize=1)
    died = None
    for line in lines:
        try:
            child.stdin.write(line + "\n")
            child.stdin.flush()
        except BrokenPipeError:
            died = line
            break
        time.sleep(0.05)
    try:
        child.stdin.write('{"op":"status"}\n{"op":"stop"}\n')
        child.stdin.flush()
    except BrokenPipeError:
        pass
    try:
        out, err = child.communicate(timeout=40)
    except subprocess.TimeoutExpired:
        child.kill()
        out, err = child.communicate()
        print("!! %s：卡住了，没退出" % label)
        return False
    answered = any('"kind": "status"' in x for x in out.splitlines())
    ok = died is None and child.returncode == 0 and answered
    print("%s %s：灌了 %d 条，退出码 %s，最后%s答得上 status"
          % ("OK" if ok else "!!", label, len(lines), child.returncode,
             "" if answered else "**不**"))
    if died:
        print("   死在这条上：", died)
    if err.strip() and "Traceback" in err:
        print("   stderr 里有 traceback：")
        print("   " + err.strip().splitlines()[-1])
    return ok


def main() -> int:
    only = sys.argv[2] if len(sys.argv) > 2 and sys.argv[1] == "--only" else ""
    good = True
    if only in ("", "ops"):
        good &= run(BAD_OPS, "畸形 op")
    if only in ("", "commands"):
        good &= run(command_lines(), "斜杠命令 × 垃圾参数")
    return 0 if good else 1


if __name__ == "__main__":
    sys.exit(main())
