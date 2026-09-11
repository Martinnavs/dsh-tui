"""Raw-terminal input: history navigation, bracketed paste, the vim-style
scroll-mode toggle, and the platform-specific (Unix/Windows) raw-input
loops. Combined with the other ChatUI mixins in core.py.

Relies on ``ScrollbackMixin`` (scroll_up/scroll_down/...) and
``RenderingMixin`` (``_print_frame``/``render``).
"""
import os
import sys

from rich.align import Align
from rich.text import Text

if sys.platform != "win32":
    import tty
    import termios
    import select as _select
else:
    import msvcrt

from typing import Optional


class InputMixin:
    _SCROLL_MODE_PROMPT = "-- SCROLL -- (j/k, gg/G, Enter/i/Esc to type) "
    _SCROLL_MODE_NOTHING_PROMPT = "-- SCROLL -- (nothing to scroll yet; Enter/i/Esc to type) "

    def _input_with_history(self, prompt: str = "➤ ") -> str:
        """Dispatch to the platform-appropriate raw-input implementation."""
        if sys.platform == "win32":
            return self._input_with_history_windows(prompt)
        return self._input_with_history_unix(prompt)

    @staticmethod
    def _redraw_line(prompt: str, current_input: list[str], prev_lines: int = 1) -> int:
        """Clear and redraw the prompt with the given input (supports multiline).

        Returns:
            Number of display lines used.
        """
        # Move cursor up to the first line of the current display
        if prev_lines > 1:
            sys.stdout.write(f'\033[{prev_lines - 1}A')
        # Clear from start of first line to end of screen
        sys.stdout.write('\r\033[J')

        text = ''.join(current_input)
        lines = text.split('\n')
        sys.stdout.write(f"\033[1;32m{prompt}\033[0m{lines[0]}")
        for line in lines[1:]:
            sys.stdout.write(f"\r\n\033[1;32m  \033[0m{line}")
        sys.stdout.flush()
        return len(lines)

    def _repaint_with_input(
        self,
        fd: int,
        prompt: str,
        current_input: list[str],
        cursor_pos: int,
    ) -> tuple[int, int]:
        """Redraw the full frame (chat panel + disclaimer) under an in-progress input line.

        Called after a scroll key while `get_user_input`'s raw-mode read loop is
        still running. Rich's prints need OPOST/ONLCR for `\\n` to render as
        `\\r\\n` (which `tty.setraw` clears), but restoring the *full* cooked
        termios settings to get that back — as an earlier version of this
        function did — also re-enables ECHO/ICANON/ISIG/IXON for the print
        window. That is a real, reproducible bug: a keystroke that arrives
        during those ~3-5ms gets echoed as garbage into the frame being
        painted, and a repeated Ctrl+U in particular is interpreted by the
        line discipline as VKILL and silently destroys whatever input was
        already queued. So only the output flags are patched onto the raw
        attributes already in effect — input discipline (no echo, no
        canonical processing, no signal chars, no software flow control)
        stays exactly as raw mode left it for the whole repaint.
        """
        raw_attrs = termios.tcgetattr(fd)  # type: ignore[name-defined]
        cooked_output_attrs = list(raw_attrs)
        cooked_output_attrs[1] |= termios.OPOST | termios.ONLCR  # type: ignore[name-defined]
        termios.tcsetattr(fd, termios.TCSADRAIN, cooked_output_attrs)  # type: ignore[name-defined]
        self._print_frame()
        termios.tcsetattr(fd, termios.TCSADRAIN, raw_attrs)  # type: ignore[name-defined]
        num_display_lines = self._redraw_line(prompt, current_input, prev_lines=1)
        if '\n' in current_input[cursor_pos:]:
            cursor_pos = len(current_input)
        else:
            n = len(current_input) - cursor_pos
            if n > 0:
                sys.stdout.write(f'\033[{n}D')
                sys.stdout.flush()
        return num_display_lines, cursor_pos

    def _handle_arrow_keys(
        self,
        code: str,
        prompt: str,
        current_input: list[str],
        cursor_pos: int,
        temp_history_index: int,
        saved_input: str,
        num_display_lines: int = 1,
    ) -> tuple[list[str], int, int, str, int]:
        """Handle up/down/left/right arrow key navigation.

        Returns:
            Updated (current_input, cursor_pos, temp_history_index, saved_input, num_display_lines).
        """
        if code == 'A' and self.input_history:  # Up arrow
            if temp_history_index == -1:
                saved_input = ''.join(current_input)
                temp_history_index = len(self.input_history) - 1
            elif temp_history_index > 0:
                temp_history_index -= 1

            if temp_history_index >= 0:
                current_input = list(self.input_history[temp_history_index])
                cursor_pos = len(current_input)
                num_display_lines = self._redraw_line(prompt, current_input, num_display_lines)

        elif code == 'B' and temp_history_index != -1:  # Down arrow
            temp_history_index += 1
            if temp_history_index >= len(self.input_history):
                temp_history_index = -1
                current_input = list(saved_input)
            else:
                current_input = list(self.input_history[temp_history_index])
            cursor_pos = len(current_input)
            num_display_lines = self._redraw_line(prompt, current_input, num_display_lines)

        elif code == 'C' and cursor_pos < len(current_input):  # Right arrow
            cursor_pos += 1
            sys.stdout.write('\033[C')
            sys.stdout.flush()

        elif code == 'D' and cursor_pos > 0:  # Left arrow
            cursor_pos -= 1
            sys.stdout.write('\033[D')
            sys.stdout.flush()

        return current_input, cursor_pos, temp_history_index, saved_input, num_display_lines

    @staticmethod
    def _handle_delete_key(
        current_input: list[str],
        cursor_pos: int,
        fd: int,
    ) -> tuple[list[str], int]:
        """Handle the Delete key (ESC [ 3 ~), removing the char at cursor_pos."""
        rlist, _, _ = _select.select([fd], [], [], 0.05)
        if not rlist:
            return current_input, cursor_pos
        next3 = os.read(fd, 1).decode('utf-8', errors='replace')
        if next3 == '~' and cursor_pos < len(current_input):
            del current_input[cursor_pos]
            sys.stdout.write(''.join(current_input[cursor_pos:]) + ' ')
            sys.stdout.write('\033[' + str(len(current_input) - cursor_pos + 1) + 'D')
            sys.stdout.flush()
        return current_input, cursor_pos

    @staticmethod
    def _drain_escape_sequence(fd: int) -> None:
        """Drain remaining bytes of an unrecognized escape sequence without blocking.

        Reads until no byte arrives within 20 ms or a typical sequence terminator
        ('~' or an uppercase ASCII letter) is seen — whichever comes first.
        This prevents stray bytes from unhandled sequences (PgUp, PgDn, Fn keys,
        etc.) from leaking into normal character input.
        """
        while True:
            rlist, _, _ = _select.select([fd], [], [], 0.02)
            if not rlist:
                break
            ch = os.read(fd, 1).decode('utf-8', errors='replace')
            if ch == '~' or ('A' <= ch <= 'Z'):
                break

    @staticmethod
    def _handle_backspace(
        current_input: list[str],
        cursor_pos: int,
    ) -> tuple[list[str], int]:
        """Handle the Backspace key, removing the char before cursor_pos.

        Returns:
            Updated (current_input, cursor_pos).
        """
        if cursor_pos > 0:
            cursor_pos -= 1
            del current_input[cursor_pos]
            sys.stdout.write('\b' + ''.join(current_input[cursor_pos:]) + ' ')
            sys.stdout.write('\033[' + str(len(current_input) - cursor_pos + 1) + 'D')
            sys.stdout.flush()
        return current_input, cursor_pos

    @staticmethod
    def _handle_printable(
        char: str,
        current_input: list[str],
        cursor_pos: int,
    ) -> tuple[list[str], int]:
        """Insert a printable character at cursor_pos and update the display.

        Returns:
            Updated (current_input, cursor_pos).
        """
        current_input.insert(cursor_pos, char)
        cursor_pos += 1
        sys.stdout.write(char + ''.join(current_input[cursor_pos:]))
        if cursor_pos < len(current_input):
            sys.stdout.write('\033[' + str(len(current_input) - cursor_pos) + 'D')
        sys.stdout.flush()
        return current_input, cursor_pos

    def _input_with_history_unix(self, prompt: str = "➤ ") -> str:
        """
        Get user input with history navigation using up/down arrow keys.
        Uses termios/tty for raw terminal control (Unix/Linux/macOS only).
        Supports bracketed paste mode for pasting multi-line text.

        A standalone Esc toggles a vim-style scroll mode: while active, j/k
        and Up/Down arrows scroll the transcript instead of navigating input
        history, gg/G jump to top/bottom, and Enter/i/Esc return to typing
        with the in-progress input line untouched. This exists because a
        terminal reporting a mouse-wheel scroll as plain arrow-key bytes is
        indistinguishable from a real arrow keypress — the mode makes the
        meaning explicit instead of guessing.

        Args:
            prompt: The prompt string to display

        Returns:
            The user's input string
        """
        # Enable bracketed paste mode + show blinking bar cursor + bold green prompt
        sys.stdout.write(f"\033[?2004h\033[?25h\033[1 q\033[1;32m{prompt}\033[0m")
        sys.stdout.flush()

        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)  # type: ignore[name-defined]

        try:
            tty.setraw(fd)  # type: ignore[name-defined]

            current_input: list[str] = []
            cursor_pos = 0
            temp_history_index = -1
            saved_input = ""
            in_paste = False
            num_display_lines = 1
            scroll_mode = False
            display_prompt = prompt
            pending_char: Optional[str] = None

            while True:
                if pending_char is not None:
                    char = pending_char
                    pending_char = None
                else:
                    char = os.read(fd, 1).decode('utf-8', errors='replace')

                if char in ('\r', '\n'):
                    if scroll_mode:
                        scroll_mode = False
                        display_prompt = prompt
                        num_display_lines, cursor_pos = self._repaint_with_input(
                            fd, display_prompt, current_input, cursor_pos,
                        )
                    elif in_paste:
                        # Insert literal newline during paste
                        current_input.insert(cursor_pos, '\n')
                        cursor_pos += 1
                        num_display_lines += 1
                        sys.stdout.write('\r\n\033[1;32m  \033[0m')
                        sys.stdout.flush()
                    else:
                        # Submit input
                        sys.stdout.write('\r\n')
                        sys.stdout.flush()
                        result = ''.join(current_input)
                        if result.strip() and (not self.input_history or self.input_history[-1] != result):
                            self.input_history.append(result)
                        self.history_index = -1
                        return result

                elif char == '\x03':  # Ctrl+C
                    sys.stdout.write('\r\n')
                    sys.stdout.flush()
                    raise KeyboardInterrupt

                elif char == '\x04':  # Ctrl+D
                    sys.stdout.write('\r\n')
                    sys.stdout.flush()
                    raise EOFError

                elif char == '\x15':  # Ctrl+U — scroll up (PgUp equivalent)
                    if self.scroll_up():
                        num_display_lines, cursor_pos = self._repaint_with_input(
                            fd, display_prompt, current_input, cursor_pos,
                        )

                elif char == '\x06':  # Ctrl+F — scroll down (PgDn equivalent)
                    # Ctrl+B is not bound here (would mirror PgUp) because it is
                    # the tmux prefix key and never reaches this loop under tmux.
                    # Ctrl+D is not reused for this (would mirror PgDn) because
                    # \x04 already raises EOFError above; rebinding it here would
                    # shadow that existing exit binding.
                    if self.scroll_down():
                        num_display_lines, cursor_pos = self._repaint_with_input(
                            fd, display_prompt, current_input, cursor_pos,
                        )

                elif char == '\x1b':  # Escape sequence
                    # Use select with a 50 ms timeout to distinguish a standalone
                    # ESC key from the start of a multi-byte terminal sequence.
                    # Without this, a bare ESC press would block the next read(1)
                    # call and silently consume (drop) the following keypress.
                    rlist, _, _ = _select.select([fd], [], [], 0.05)
                    if not rlist:
                        # Standalone ESC — toggle vim-style scroll mode.
                        scroll_mode = not scroll_mode
                        if scroll_mode:
                            display_prompt = (
                                self._SCROLL_MODE_NOTHING_PROMPT
                                if self._max_scroll_offset() == 0
                                else self._SCROLL_MODE_PROMPT
                            )
                        else:
                            display_prompt = prompt
                        num_display_lines, cursor_pos = self._repaint_with_input(
                            fd, display_prompt, current_input, cursor_pos,
                        )
                        continue

                    next1 = os.read(fd, 1).decode('utf-8', errors='replace')
                    if next1 == '[':
                        rlist, _, _ = _select.select([fd], [], [], 0.05)
                        if not rlist:
                            continue
                        next2 = os.read(fd, 1).decode('utf-8', errors='replace')
                        if next2 in ('A', 'B', 'C', 'D'):  # Arrow keys only
                            if scroll_mode and next2 in ('A', 'B'):
                                # Up/Down while scrolling — and, not coincidentally,
                                # what a wheel-scroll-as-arrow-keys terminal sends.
                                changed = self.scroll_up() if next2 == 'A' else self.scroll_down()
                                if changed:
                                    num_display_lines, cursor_pos = self._repaint_with_input(
                                        fd, display_prompt, current_input, cursor_pos,
                                    )
                            elif not scroll_mode:
                                current_input, cursor_pos, temp_history_index, saved_input, num_display_lines = (
                                    self._handle_arrow_keys(
                                        next2, display_prompt, current_input, cursor_pos,
                                        temp_history_index, saved_input, num_display_lines,
                                    )
                                )
                            # scroll_mode and Left/Right: intentionally a no-op.
                        elif next2 == '2':
                            # \e[2~  = Insert key
                            # \e[200~ = bracketed-paste start
                            # \e[201~ = bracketed-paste end
                            rlist, _, _ = _select.select([fd], [], [], 0.05)
                            if not rlist:
                                continue
                            next3 = os.read(fd, 1).decode('utf-8', errors='replace')
                            if next3 == '~':
                                pass  # Insert key — ignore
                            elif next3 == '0':
                                # Could be \e[20~  (F9) or \e[200~/\e[201~ (paste)
                                rlist, _, _ = _select.select([fd], [], [], 0.05)
                                if not rlist:
                                    continue
                                next4 = os.read(fd, 1).decode('utf-8', errors='replace')
                                if next4 == '~':
                                    pass  # \e[20~ = F9 — ignore (was the bug: previously
                                          # read one extra byte here, dropping user input)
                                elif next4 in ('0', '1'):
                                    # \e[200~ or \e[201~: one more byte expected ('~')
                                    rlist, _, _ = _select.select([fd], [], [], 0.05)
                                    if rlist:
                                        next5 = os.read(fd, 1).decode('utf-8', errors='replace')
                                        if next5 == '~':
                                            in_paste = (next4 == '0')
                                            if in_paste and scroll_mode:
                                                # A paste starting mid-scroll-mode
                                                # implicitly returns to typing —
                                                # otherwise scroll_mode's printable-
                                                # char swallowing would silently
                                                # drop the pasted content.
                                                scroll_mode = False
                                                display_prompt = prompt
                                                num_display_lines, cursor_pos = self._repaint_with_input(
                                                    fd, display_prompt, current_input, cursor_pos,
                                                )
                                else:
                                    self._drain_escape_sequence(fd)
                            else:
                                self._drain_escape_sequence(fd)
                        elif next2 == '3' and not scroll_mode:  # Delete key: \e[3~
                            current_input, cursor_pos = self._handle_delete_key(
                                current_input, cursor_pos, fd,
                            )
                        elif next2 in ('5', '6'):  # PgUp \e[5~ / PgDn \e[6~
                            rlist, _, _ = _select.select([fd], [], [], 0.05)
                            next3 = os.read(fd, 1).decode('utf-8', errors='replace') if rlist else ''
                            if next3 == '~':
                                changed = self.scroll_up() if next2 == '5' else self.scroll_down()
                                if changed:
                                    num_display_lines, cursor_pos = self._repaint_with_input(
                                        fd, display_prompt, current_input, cursor_pos,
                                    )
                            else:
                                # Modified PgUp/PgDn (e.g. \e[5;2~) — unhandled in v1,
                                # drain like any other unrecognized sequence.
                                self._drain_escape_sequence(fd)
                        elif next2 in ('1', '4'):  # Home \e[1~ / End \e[4~
                            rlist, _, _ = _select.select([fd], [], [], 0.05)
                            next3 = os.read(fd, 1).decode('utf-8', errors='replace') if rlist else ''
                            if next3 == '~':
                                changed = self.scroll_to_top() if next2 == '1' else self.scroll_to_bottom()
                                if changed:
                                    num_display_lines, cursor_pos = self._repaint_with_input(
                                        fd, display_prompt, current_input, cursor_pos,
                                    )
                            else:
                                # Modified Home/End (e.g. \e[1;5H) — unhandled in v1.
                                self._drain_escape_sequence(fd)
                        elif next2 in ('H', 'F'):  # Home \e[H / End \e[F (no trailing byte)
                            changed = self.scroll_to_top() if next2 == 'H' else self.scroll_to_bottom()
                            if changed:
                                num_display_lines, cursor_pos = self._repaint_with_input(
                                    fd, display_prompt, current_input, cursor_pos,
                                )
                        else:
                            # Unrecognised sequence (Fn keys, modified arrows, etc.).
                            # Drain any remaining bytes so they don't appear as text.
                            self._drain_escape_sequence(fd)

                elif char in ('\x7f', '\b') and not scroll_mode:  # Backspace
                    current_input, cursor_pos = self._handle_backspace(
                        current_input, cursor_pos,
                    )

                elif ' ' <= char <= '~':  # Printable characters
                    if scroll_mode:
                        # j/k/gg/G drive scrolling; i (or Enter/Esc, handled
                        # above) returns to typing. Anything else is swallowed
                        # so it can't leak into the input buffer while scrolling.
                        if char == 'j':
                            if self.scroll_down():
                                num_display_lines, cursor_pos = self._repaint_with_input(
                                    fd, display_prompt, current_input, cursor_pos,
                                )
                        elif char == 'k':
                            if self.scroll_up():
                                num_display_lines, cursor_pos = self._repaint_with_input(
                                    fd, display_prompt, current_input, cursor_pos,
                                )
                        elif char == 'G':
                            if self.scroll_to_bottom():
                                num_display_lines, cursor_pos = self._repaint_with_input(
                                    fd, display_prompt, current_input, cursor_pos,
                                )
                        elif char == 'g':
                            # gg — peek for a second 'g' within a short window.
                            # A non-'g' peeked byte (e.g. the Esc/Enter/i exit
                            # keys, or j/k) is not discarded — it's queued as
                            # pending_char so the next loop iteration processes
                            # it normally instead of swallowing it.
                            rlist, _, _ = _select.select([fd], [], [], 0.3)
                            if rlist:
                                peeked = os.read(fd, 1).decode('utf-8', errors='replace')
                                if peeked == 'g':
                                    if self.scroll_to_top():
                                        num_display_lines, cursor_pos = self._repaint_with_input(
                                            fd, display_prompt, current_input, cursor_pos,
                                        )
                                else:
                                    pending_char = peeked
                        elif char == 'i':
                            scroll_mode = False
                            display_prompt = prompt
                            num_display_lines, cursor_pos = self._repaint_with_input(
                                fd, display_prompt, current_input, cursor_pos,
                            )
                    else:
                        current_input, cursor_pos = self._handle_printable(
                            char, current_input, cursor_pos,
                        )

        finally:
            # Disable bracketed paste mode and restore terminal
            sys.stdout.write("\033[?2004l")
            sys.stdout.flush()
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)  # type: ignore[name-defined]

    def _input_with_history_windows(self, prompt: str = "➤ ") -> str:
        """
        Get user input with history navigation using up/down arrow keys.
        Uses msvcrt for raw character reading (Windows only).

        Arrow keys on Windows emit two bytes via msvcrt.getwch():
          prefix \x00 or \xe0, then a scan code:
            \x48 = Up, \x50 = Down, \x4d = Right, \x4b = Left, \x53 = Delete

        Args:
            prompt: The prompt string to display

        Returns:
            The user's input string
        """
        # Show blinking bar cursor + bold green prompt
        sys.stdout.write(f"\033[?25h\033[5 q\033[1;32m{prompt}\033[0m")
        sys.stdout.flush()

        current_input = []
        cursor_pos = 0
        temp_history_index = -1
        saved_input = ""

        while True:
            char = msvcrt.getwch()

            if char in ('\r', '\n'):  # Enter key
                sys.stdout.write('\r\n')
                sys.stdout.flush()
                result = ''.join(current_input)
                if result.strip() and (not self.input_history or self.input_history[-1] != result):
                    self.input_history.append(result)
                self.history_index = -1
                return result

            elif char == '\x03':  # Ctrl+C
                sys.stdout.write('\r\n')
                sys.stdout.flush()
                raise KeyboardInterrupt

            elif char == '\x04':  # Ctrl+D
                sys.stdout.write('\r\n')
                sys.stdout.flush()
                raise EOFError

            elif char in ('\x00', '\xe0'):  # Special key prefix (arrows, Delete, etc.)
                scan = msvcrt.getwch()

                if scan == '\x48':  # Up arrow
                    if self.input_history:
                        if temp_history_index == -1:
                            saved_input = ''.join(current_input)
                            temp_history_index = len(self.input_history) - 1
                        elif temp_history_index > 0:
                            temp_history_index -= 1

                        if temp_history_index >= 0:
                            sys.stdout.write('\r' + ' ' * (len(prompt) + len(current_input) + 5) + '\r')
                            sys.stdout.write(f"\033[1;32m{prompt}\033[0m")
                            current_input = list(self.input_history[temp_history_index])
                            cursor_pos = len(current_input)
                            sys.stdout.write(''.join(current_input))
                            sys.stdout.flush()

                elif scan == '\x50':  # Down arrow
                    if temp_history_index != -1:
                        temp_history_index += 1
                        sys.stdout.write('\r' + ' ' * (len(prompt) + len(current_input) + 5) + '\r')
                        sys.stdout.write(f"\033[1;32m{prompt}\033[0m")

                        if temp_history_index >= len(self.input_history):
                            temp_history_index = -1
                            current_input = list(saved_input)
                        else:
                            current_input = list(self.input_history[temp_history_index])

                        cursor_pos = len(current_input)
                        sys.stdout.write(''.join(current_input))
                        sys.stdout.flush()

                elif scan == '\x4d':  # Right arrow
                    if cursor_pos < len(current_input):
                        cursor_pos += 1
                        sys.stdout.write('\033[C')
                        sys.stdout.flush()

                elif scan == '\x4b':  # Left arrow
                    if cursor_pos > 0:
                        cursor_pos -= 1
                        sys.stdout.write('\033[D')
                        sys.stdout.flush()

                elif scan == '\x53':  # Delete key
                    if cursor_pos < len(current_input):
                        del current_input[cursor_pos]
                        sys.stdout.write(''.join(current_input[cursor_pos:]) + ' ')
                        sys.stdout.write('\033[' + str(len(current_input) - cursor_pos + 1) + 'D')
                        sys.stdout.flush()

            elif char in ('\x7f', '\x08'):  # Backspace
                if cursor_pos > 0:
                    cursor_pos -= 1
                    del current_input[cursor_pos]
                    sys.stdout.write('\b' + ''.join(current_input[cursor_pos:]) + ' ')
                    sys.stdout.write('\033[' + str(len(current_input) - cursor_pos + 1) + 'D')
                    sys.stdout.flush()

            elif char >= ' ' and char <= '~':  # Printable characters
                current_input.insert(cursor_pos, char)
                cursor_pos += 1
                sys.stdout.write(char + ''.join(current_input[cursor_pos:]))
                if cursor_pos < len(current_input):
                    sys.stdout.write('\033[' + str(len(current_input) - cursor_pos) + 'D')
                sys.stdout.flush()

    def get_user_input(self) -> str:
        """Get user input from the console with history support (up/down arrows)"""
        self.stop_status()
        self.stop_thinking()  # ensure no spinner thread is writing to stdout while user types
        while True:
            self._print_frame()
            try:
                user_input = self._input_with_history("➤ ")
                self.add_message("user", user_input)
                self.console.clear()

                # Show panel with the user's message, then a plain (non-Live)
                # thinking indicator so the console is free for streaming tokens.
                self.console.print(self.render())
                self.console.print(Align.center(Text(self.disclaimer, style="bright_white")))
                # The panel above already shows the corrected text; this says
                # what changed, and now is when it can be said without landing
                # on top of a line being typed.
                self.flush_corrections()
                self.console.print()
                return user_input
            # capture control-d
            except EOFError:
                return "\\bye"
            except KeyboardInterrupt:
                return "\\bye"
