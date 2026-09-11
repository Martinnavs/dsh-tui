"""Text-based UI components for the application.

`ChatUI` is assembled from mixins split across this package (see
`core.py` for the full list); this file re-exports the same public
surface the module previously exposed as a single flat `chat_ui.py`.
"""
from .core import ChatUI
from .legacy import main, text_prompt
from .message import HORIZONTAL_THICK, Message

__all__ = ["ChatUI", "Message", "HORIZONTAL_THICK", "text_prompt", "main"]
