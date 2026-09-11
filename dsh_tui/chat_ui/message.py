"""Small standalone pieces shared across the ChatUI mixins: the message
record type and a Box style constant, neither of which depends on any
mixin's state.
"""
from dataclasses import dataclass
from typing import Literal

from rich.box import Box

HORIZONTAL_THICK = Box(
    "━━━━\n"
    "    \n"
    "━━━━\n"
    "    \n"
    "    \n"
    "    \n"
    "    \n"
    "━━━━\n"
)


@dataclass
class Message:
    """Strongly typed message structure"""
    role: Literal["user", "assistant", "system", "tool_call", "tool_result"]
    content: str
    timestamp: str
    elapsed: str = ""
    name: str = ""
