from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Tuple, Any, Callable, Optional


SAFE_TOOLS = {"Read", "Grep", "Glob", "LS", "Task", "TaskOutput", "AskUserQuestion", "TodoWrite"}


def _escape_control_chars(value: str) -> str:
    """Replace ASCII control/escape bytes with a visible \\xNN sequence.

    tool_name and target can originate from an MCP server's own tool/resource
    naming, which is not restricted to a safe character set on this side.
    Printing them unescaped into the approval prompt lets that untrusted text
    manipulate the terminal (clear/overwrite the line, hide what is really
    being asked) instead of just being displayed. Covers C0 controls
    (0x00-0x1F, including ESC) and DEL (0x7F); printable Unicode is left
    untouched.
    """
    return "".join(ch if ch >= " " and ch != "\x7f" else "\\x%02x" % ord(ch) for ch in value)


@dataclass
class PermissionState:
    mode: str = "ask"  # ask | allow | deny | plan
    auto_approve: bool = False
    approved: set[Tuple[str, str]] = field(default_factory=set)
    denied: set[Tuple[str, str]] = field(default_factory=set)

    def _key(self, tool_name: str, target: str) -> Tuple[str, str]:
        return (tool_name, target)

    def is_approved(self, tool_name: str, target: str) -> bool:
        return self._key(tool_name, target) in self.approved

    def is_denied(self, tool_name: str, target: str) -> bool:
        return self._key(tool_name, target) in self.denied

    def approve(self, tool_name: str, target: str) -> None:
        self.approved.add(self._key(tool_name, target))

    def deny(self, tool_name: str, target: str) -> None:
        self.denied.add(self._key(tool_name, target))

    def check(self, tool_name: str, target: str) -> bool:
        if self.mode == "allow":
            return True
        if self.mode == "plan":
            return tool_name in SAFE_TOOLS
        if self.mode == "deny":
            return False
        if tool_name in SAFE_TOOLS:
            return True
        if self.auto_approve:
            return True
        if self.is_approved(tool_name, target):
            return True
        if self.is_denied(tool_name, target):
            return False

        # interactive prompt
        safe_tool_name = _escape_control_chars(tool_name)
        safe_target = _escape_control_chars(target)
        while True:
            reply = input(f"Allow {safe_tool_name} on '{safe_target}'? [y/N] ").strip().lower()
            if reply in {"y", "yes"}:
                self.approve(tool_name, target)
                return True
            if reply in {"n", "no", ""}:
                self.deny(tool_name, target)
                return False


@dataclass
class AppContext:
    root_dir: Path
    permissions: PermissionState
    read_files: set[Path] = field(default_factory=set)
    read_only: bool = False
    tasks: dict[str, Any] = field(default_factory=dict)
    todos: list[dict[str, str]] = field(default_factory=list)
    user_context: dict[str, str] = field(default_factory=dict)
    system_context: dict[str, str] = field(default_factory=dict)
    mcp_manager: Optional[Any] = None
    trace_logger: Optional[Callable[[dict[str, Any]], None]] = None
    tool_logger: Optional[Callable[[dict[str, Any]], None]] = None
    trace_log_max_chars: int = 10_000
    tool_log_max_chars: int = 10_000

    def resolve_path(self, file_path: str) -> Path:
        p = Path(file_path).expanduser()
        if not p.is_absolute():
            p = (self.root_dir / p).resolve()
        else:
            p = p.resolve()
        # enforce sandbox root
        root = self.root_dir.resolve()
        if root not in p.parents and p != root:
            raise PermissionError(f"Path '{p}' is outside the sandbox root '{root}'")
        return p

    def target_label(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.root_dir))
        except Exception:
            return str(path)

    def note_read(self, path: Path) -> None:
        self.read_files.add(path.resolve())

    def has_read(self, path: Path) -> bool:
        return path.resolve() in self.read_files
