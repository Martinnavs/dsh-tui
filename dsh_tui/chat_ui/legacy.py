"""Legacy standalone helpers predating ChatUI (voice-prompt loop, a bare
`main()` smoke entry point). Not used by `dsh_tui.cli`; kept only for
backward compatibility with any external caller still importing them
from `dsh_tui.chat_ui`.
"""
import asyncio
from typing import Any, Callable, Optional

from rich.console import Console
from rich.prompt import Prompt
from rich.status import Status

from .core import ChatUI


async def text_prompt(console: Console = None,
                status: Status = None,
                message: str = "Type '\\bye' to stop.",
                prompt_color: str = "bold blue",
                cursor: str = "[bold]User[/bold]:",
                tool_registry: Any = None,
                is_voice: bool = False,
                say: Optional[Callable] = None,
                listen: Optional[Callable] = None,
                prompted_once: bool = True,
                is_safety_task: bool = False) -> str:
    assert Console is not None, "Console must be provided for CLI prompt."
    if is_voice:
        if not prompted_once:
            console.print(message, style="info")
            if say:
                await say(text=message)
        try:
            if is_safety_task:
                prompt = f"[{prompt_color}] 💡 On-it... Say 'stop' to stop all tasks.[/]"
            else:
                prompt = f"[{prompt_color}] 🎤 Listening... Say 'goodbye' to end the session.[/]"
            status.update(prompt) if status else None
            status.start() if status else None
            task = await listen(tool_registry=tool_registry, status=status, prompt_color=prompt_color, is_safety_task=is_safety_task)
            status.stop() if status else None
            if is_safety_task:
                console.print(f"Stopping all tasks...", style="warning")
            elif task is not None:
                console.print(f"{cursor} : {task}", style="prompt")
            return task

        except Exception as e:
            console.print(f"An error occurred: {str(e)}. Please try again", style="error")
            return None
    else:
        # put status on upper layout
        if status and isinstance(status, Status):
            status.stop() if status else None
        if not prompted_once and console:
            console.print(message, style="info")
        loop = asyncio.get_running_loop()
        prompt = f"[bold]{cursor}[/bold]"
        if is_safety_task:
            prompt = f"[{prompt_color}] 🎤 On-it... Press 'Ctl+c' key to stop all tasks.[/]"
            status.update(prompt)
            status.start()
        try:
            console.clear()
            task = await loop.run_in_executor(None, lambda: Prompt.ask(prompt, console=console))
        except KeyboardInterrupt:
            task = None
        if is_safety_task:
            if status:
                status.stop()
            console.print(f"Stopping all tasks...", style="warning")
        return task if task is not None else ""


def main():
    """Entry point"""
    # Check for rich library
    try:
        from rich import __version__
        console = Console()
        console.print(f"[dim]Rich version: {__version__}[/]\n")
    except ImportError:
        print("Please install rich: pip install rich")
        return

    chat = ChatUI()
    #chat.run()


if __name__ == "__main__":
    main()
