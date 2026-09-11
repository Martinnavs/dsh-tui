"""Status spinner (Rich ``Status``, used while waiting on a response) and
the plain-ANSI thinking spinner + blinking block cursor (used while the
console is busy streaming tokens, so Rich's own Live rendering can't own
the line). Combined with the other ChatUI mixins in core.py.
"""
import sys
import threading


class SpinnerMixin:
    def start_status(self) -> None:
        """Start the status spinner with rotating messages."""
        self._spinner_step = 0
        self._spinner_stop_event = threading.Event()
        self._update_spinner_text()
        self.status.start()
        self._schedule_spinner_rotation()

    def _update_spinner_text(self) -> None:
        """Update the spinner text to the current message."""
        msg = self._spinner_messages[self._spinner_step % len(self._spinner_messages)]
        style = self.theme.styles.get('prompt', 'bold dark_blue')
        self.status.update(f"[{style}] 💡 {msg}[/]")

    def _schedule_spinner_rotation(self) -> None:
        """Schedule the next spinner message rotation."""
        if self._spinner_stop_event and self._spinner_stop_event.is_set():
            return
        self._spinner_timer = threading.Timer(3.0, self._rotate_spinner)
        self._spinner_timer.daemon = True
        self._spinner_timer.start()

    def _rotate_spinner(self) -> None:
        """Advance to the next spinner message and schedule the next rotation."""
        if self._spinner_stop_event and self._spinner_stop_event.is_set():
            return
        self._spinner_step += 1
        self._update_spinner_text()
        self._schedule_spinner_rotation()

    def stop_status(self) -> None:
        """
        Safely stop the status spinner and cancel message rotation.

        Handles cases where status might not be initialized or already stopped.
        """
        if self._spinner_stop_event:
            self._spinner_stop_event.set()
        try:
            if self._spinner_timer:
                self._spinner_timer.cancel()
                self._spinner_timer = None
        except Exception:
            pass
        try:
            if self.status and hasattr(self.status, 'stop'):
                self.status.stop()
        except Exception:
            # Silently fail - status stopping is not critical
            pass

    def _start_thinking_spinner(self) -> None:
        """Start a background thread that shows an animated spinner with rotating messages."""
        # Stop any existing spinner to prevent zombie threads from accumulating
        self._stop_thinking_spinner()
        self._thinking_stop_event = threading.Event()

        _FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

        _stop_event = self._thinking_stop_event  # capture local ref; avoids NoneType if cleared

        def _animate() -> None:
            # Show blinking block cursor while thinking
            sys.stdout.write("\033[?25h\033[1 q")
            sys.stdout.flush()
            msg_idx = 0
            frame_idx = 0
            ticks = 0
            while True:
                frame = _FRAMES[frame_idx % len(_FRAMES)]
                msg = self._spinner_messages[msg_idx % len(self._spinner_messages)]
                line = f"\r\033[1;96m{frame} {msg}\033[0m\033[K"
                sys.stdout.write(line)
                sys.stdout.flush()
                frame_idx += 1
                ticks += 1
                # Rotate message every ~30 frames (~2.4s at 80ms/frame)
                if ticks % 30 == 0:
                    msg_idx += 1
                if _stop_event.wait(0.08):
                    break

        self._thinking_thread = threading.Thread(target=_animate, daemon=True)
        self._thinking_thread.start()

    def _stop_thinking_spinner(self) -> None:
        """Stop the rotating thinking display and clear the line."""
        stop_event = self._thinking_stop_event
        thread = self._thinking_thread
        if stop_event:
            stop_event.set()
        if thread:
            thread.join(timeout=1.0)
        self._thinking_stop_event = None
        self._thinking_thread = None
        # Erase the entire current line and restore blinking block cursor
        sys.stdout.write("\r\033[2K\033[?25h\033[1 q")
        sys.stdout.flush()

    def start_thinking(self) -> None:
        """Public API: start the animated thinking spinner."""
        self._start_thinking_spinner()

    def stop_thinking(self) -> None:
        """Public API: stop the animated thinking spinner."""
        self._stop_thinking_spinner()

    def _show_stream_cursor(self) -> None:
        """Hide terminal cursor and show a blinking white block."""
        if not self._stream_cursor_shown:
            sys.stdout.write("\033[?25l")          # hide terminal cursor
            sys.stdout.write("\033[5m█\033[0m")  # blinking white block
            sys.stdout.flush()
            self._stream_cursor_shown = True

    def _erase_stream_cursor(self) -> None:
        """Remove the blinking block and restore the terminal cursor."""
        if self._stream_cursor_shown:
            sys.stdout.write("\b \b")              # erase the block char
            sys.stdout.write("\033[?25h")           # restore terminal cursor
            sys.stdout.flush()
            self._stream_cursor_shown = False
