"""UI Automation worker (C9.4).

All UIA/COM calls run on one dedicated single-threaded-apartment thread with
COM initialised (``UIAutomationInitializerInThread``), never on the event loop.
Tree walks are bounded by depth and element count.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any, TypeVar

from scar.core.errors import ToolError

T = TypeVar("T")


@dataclass
class ElementInfo:
    id: int
    name: str
    control_type: str
    automation_id: str
    class_name: str
    left: int
    top: int
    right: int
    bottom: int
    enabled: bool
    offscreen: bool
    focused: bool
    depth: int
    patterns: list[str]
    value: str | None = None

    @property
    def center(self) -> tuple[int, int]:
        return (self.left + self.right) // 2, (self.top + self.bottom) // 2

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def line(self) -> str:
        label = f"[{self.id}] {self.control_type}"
        if self.name:
            label += f" '{self.name[:60]}'"
        if self.automation_id:
            label += f" #{self.automation_id[:40]}"
        if self.value:
            label += f" = '{self.value[:60]}'"
        if not self.enabled:
            label += " (disabled)"
        return "  " * self.depth + label


_PATTERN_NAMES = ["InvokePattern", "ValuePattern", "TogglePattern", "SelectionItemPattern", "ExpandCollapsePattern",
                  "ScrollPattern", "TextPattern", "RangeValuePattern", "WindowPattern"]


class UiaWorker:
    def __init__(self) -> None:
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="scar-uia",
                                                               initializer=self._init_thread)
        self._local = threading.local()
        # element handles cached per inspection so the model can act on "[12]"
        self._handles: dict[int, Any] = {}
        self._next_id = 1

    def _init_thread(self) -> None:
        import uiautomation as ua

        self._local.init = ua.UIAutomationInitializerInThread(debug=False)
        ua.SetGlobalSearchTimeout(2)

    async def call(self, fn: Callable[[], T]) -> T:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, fn)

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    # ---------------------------------------------------------------- inspection (UIA thread)
    def _describe(self, ctrl: Any, depth: int) -> ElementInfo:
        import uiautomation as ua

        r = ctrl.BoundingRectangle
        patterns: list[str] = []
        value: str | None = None
        for pname in _PATTERN_NAMES:
            try:
                pat = ctrl.GetPattern(getattr(ua.PatternId, pname))
            except Exception:  # noqa: BLE001 - COM errors on stale elements
                pat = None
            if pat is not None:
                patterns.append(pname.removesuffix("Pattern"))
                if pname == "ValuePattern":
                    try:
                        value = str(pat.Value)[:200]
                    except Exception:  # noqa: BLE001
                        value = None
        eid = self._next_id
        self._next_id += 1
        self._handles[eid] = ctrl
        if len(self._handles) > 5000:
            for k in sorted(self._handles)[:2500]:
                self._handles.pop(k, None)
        return ElementInfo(
            id=eid, name=str(ctrl.Name or ""), control_type=str(ctrl.ControlTypeName or ""),
            automation_id=str(ctrl.AutomationId or ""), class_name=str(ctrl.ClassName or ""),
            left=int(r.left), top=int(r.top), right=int(r.right), bottom=int(r.bottom), enabled=bool(ctrl.IsEnabled),
            offscreen=bool(ctrl.IsOffscreen), focused=bool(ctrl.HasKeyboardFocus), depth=depth, patterns=patterns,
            value=value,
        )

    def _root(self, hwnd: int | None) -> Any:
        import uiautomation as ua

        if hwnd:
            ctrl = ua.ControlFromHandle(hwnd)
            if ctrl is None:
                raise ToolError(f"no UI Automation element for window {hwnd}", "NotFound")
            return ctrl
        return ua.GetForegroundControl() if hasattr(ua, "GetForegroundControl") else ua.GetRootControl()

    def inspect_sync(self, hwnd: int | None, max_depth: int = 6, max_elements: int = 300,
                     include_offscreen: bool = False) -> list[ElementInfo]:
        root = self._root(hwnd)
        out: list[ElementInfo] = []
        stack: list[tuple[Any, int]] = [(root, 0)]
        while stack and len(out) < max_elements:
            ctrl, depth = stack.pop()
            try:
                info = self._describe(ctrl, depth)
            except Exception:  # noqa: BLE001 - element vanished mid-walk
                continue
            if include_offscreen or not info.offscreen or depth == 0:
                out.append(info)
            if depth >= max_depth:
                continue
            try:
                children = ctrl.GetChildren()
            except Exception:  # noqa: BLE001
                continue
            for child in reversed(children[:200]):
                stack.append((child, depth + 1))
        return out

    def find_sync(self, hwnd: int | None, name: str | None, control_type: str | None, automation_id: str | None,
                  max_depth: int = 12, max_elements: int = 3000, limit: int = 10) -> list[ElementInfo]:
        elems = self.inspect_sync(hwnd, max_depth=max_depth, max_elements=max_elements)
        out = []
        for e in elems:
            if name and name.lower() not in e.name.lower():
                continue
            if control_type and control_type.lower().removesuffix("control") != e.control_type.lower().removesuffix("control"):
                continue
            if automation_id and automation_id != e.automation_id:
                continue
            out.append(e)
        out.sort(key=lambda e: (e.name.lower() != (name or "").lower(), e.offscreen, e.depth))
        return out[:limit]

    def element(self, eid: int) -> Any:
        ctrl = self._handles.get(eid)
        if ctrl is None:
            raise ToolError(f"element [{eid}] is unknown or stale; inspect the window again", "StaleElement")
        return ctrl

    def act_sync(self, eid: int, action: str, value: str | None = None) -> str:
        import uiautomation as ua

        ctrl = self.element(eid)

        def pat(name: str) -> Any:
            p = ctrl.GetPattern(getattr(ua.PatternId, name))
            if p is None:
                raise ToolError(f"element [{eid}] does not support {name.removesuffix('Pattern')}", "PatternUnsupported")
            return p

        if action == "invoke":
            pat("InvokePattern").Invoke()
        elif action == "set_value":
            if value is None:
                raise ToolError("set_value needs a value", "InvalidInput")
            p = pat("ValuePattern")
            if p.IsReadOnly:
                raise ToolError(f"element [{eid}] is read-only", "ReadOnly")
            p.SetValue(value)
        elif action == "toggle":
            pat("TogglePattern").Toggle()
        elif action == "select":
            pat("SelectionItemPattern").Select()
        elif action == "expand":
            pat("ExpandCollapsePattern").Expand()
        elif action == "collapse":
            pat("ExpandCollapsePattern").Collapse()
        elif action == "focus":
            ctrl.SetFocus()
        elif action == "scroll_into_view":
            p = ctrl.GetPattern(ua.PatternId.ScrollItemPattern)
            if p is None:
                raise ToolError("element cannot scroll into view", "PatternUnsupported")
            p.ScrollIntoView()
        elif action == "get_text":
            p = ctrl.GetPattern(ua.PatternId.TextPattern)
            if p is not None:
                return str(p.DocumentRange.GetText(20000))
            vp = ctrl.GetPattern(ua.PatternId.ValuePattern)
            if vp is not None:
                return str(vp.Value)
            return str(ctrl.Name or "")
        else:
            raise ToolError(f"unknown UIA action {action}", "InvalidInput")
        return "ok"

    def value_of_sync(self, eid: int) -> tuple[str | None, bool | None]:
        """Current value and toggle state for postcondition checks."""
        import uiautomation as ua

        ctrl = self.element(eid)
        val = None
        state = None
        vp = ctrl.GetPattern(ua.PatternId.ValuePattern)
        if vp is not None:
            val = str(vp.Value)
        tp = ctrl.GetPattern(ua.PatternId.TogglePattern)
        if tp is not None:
            state = int(tp.ToggleState) == 1
        return val, state

    def window_text_sync(self, hwnd: int, max_chars: int = 20000) -> str:
        """All visible names/values in a window, depth-first (for 'read this' / verification)."""
        elems = self.inspect_sync(hwnd, max_depth=25, max_elements=2500)
        parts: list[str] = []
        total = 0
        for e in elems:
            for t in (e.name, e.value or ""):
                if t and t not in parts[-3:]:
                    parts.append(t)
                    total += len(t)
            if total > max_chars:
                break
        return "\n".join(parts)[:max_chars]
