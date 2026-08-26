from pathlib import Path

from . import ui
from .providers import ToolOutput, build_backend
from .tools import TOOL_SCHEMAS, Toolbox

SYSTEM_PROMPT = """你是 Feiyu，一个在真实终端工作区里干活的编程 agent 你帮用户\
开发、调试、重构和理解代码 

工作方式：
- 改动之前先用工具了解项目 编辑前先读文件、搜索，不要凭空猜测内容 
- 改动要聚焦 优先用 edit_file，而不是重写整个文件 不要添加任务没要求的功能、\
抽象或错误处理 
- 改完代码要验证：用 bash 跑测试或相关命令 
- 写的代码要贴合周围的风格、命名和约定 
- 执行会改变状态的命令（删除、git push、安装）之前，先确认有足够依据支持这个\
具体操作 

沟通：
- 工具调用之间默认保持安静 只在有发现、要改变方向或遇到阻碍时说一句话 
- 做完后用一两句话说清结果，先说发生了什么 不用逐个复述你改过的文件，用户一直\
在看 
- 遇到小选择（起个名字、定个默认值、两种等价做法选哪个），自己挑个合理的并说明，\
别停下来问 涉及范围变化或破坏性操作时，先问 """


# 回答风格：接在 SYSTEM_PROMPT 后面的一段补充指令
STYLES = {
    "default": ("默认", ""),
    "concise": (
        "简洁",
        "\n\n再压缩一些：能一句话说完就不要两句 除非用户问，否则不解释实现细节 ",
    ),
    "explanatory": (
        "详细",
        "\n\n每次改动后多说一两句为什么这么改、放弃了什么方案，让用户看懂取舍 ",
    ),
    "teaching": (
        "教学",
        "\n\n把用户当成正在学这块的人：动手前先讲清思路，改完解释涉及的概念和为什么这样写 "
        "遇到值得一提的坑就顺带说明 ",
    ),
}

class Feiyu:
    def __init__(
        self,
        workspace: Path,
        *,
        provider: str = "anthropic",
        model: str | None = None,
        base_url: str | None = None,
    ):
        self.toolbox = Toolbox(workspace)
        self.style = "default"
        self.backend = build_backend(
            provider,
            system=SYSTEM_PROMPT,
            tools=TOOL_SCHEMAS,
            model=model,
            base_url=base_url,
        )

    @property
    def tokens(self) -> int:
        """累计计费量 """
        return getattr(self.backend, "tokens_total", 0)

    @property
    def context(self) -> int:
        """最近一次请求的规模，即当前对话占了多少上下文 """
        return getattr(self.backend, "tokens_context", 0)

    @property
    def cost(self) -> float | None:
        """累计花费（人民币） 价格表里没有这个模型时返回 None """
        if not getattr(self.backend, "priced", False):
            return None
        return getattr(self.backend, "cost", 0.0)

    def reset(self) -> None:
        self.backend.reset()

    @property
    def model(self) -> str:
        return self.backend.model

    def set_model(self, name: str) -> None:
        """同一厂商内换模型，对话历史照旧 """
        self.backend.model = name

    def list_models(self) -> list[str]:
        return self.backend.list_models()

    def set_style(self, name: str) -> None:
        self.style = name
        self.backend.set_system(SYSTEM_PROMPT + STYLES[name][1])

    def send(self, user_message: str) -> str:
        self.backend.add_user_message(user_message)
        while True:
            stream = ui.Stream()
            stream.start()
            try:
                # 中断/报错时也必须停掉 spinner 线程，否则它会继续往屏幕上糊帧
                turn = self.backend.run_turn(on_text=stream.write)
            finally:
                stream.finish()

            if not turn.tool_calls:
                return turn.text

            if stream.wrote_text:
                print()
            self.backend.add_tool_results(self._run_tools(turn.tool_calls))

    def _run_tools(self, calls) -> list[ToolOutput]:
        outputs = []
        for call in calls:
            print(ui.tool_call(call.name, call.input), flush=True)
            result = self.toolbox.run(call.name, call.input)
            summary = result.summary or result.content.splitlines()[0][:80]
            print(ui.tool_result(summary, result.detail, is_error=result.is_error))
            print()
            outputs.append(
                ToolOutput(id=call.id, content=result.content, is_error=result.is_error)
            )
        return outputs
