"""Plain-language wording for the app: activity lines, outcome badges and the AI-connection summary."""

from __future__ import annotations

import time
from typing import Any

_TITLES = {
    "apps.launch": "Opening an app", "apps.open": "Opening in the default app", "apps.find": "Looking up apps",
    "windows.focus": "Switching windows", "windows.close": "Closing a window", "windows.list": "Checking open windows",
    "windows.arrange": "Arranging windows", "windows.wait": "Waiting for a window",
    "input.type": "Typing", "input.key": "Pressing keys", "input.click": "Clicking", "input.scroll": "Scrolling",
    "vision.click": "Clicking on the screen", "uia.invoke": "Pressing a button", "uia.set_value": "Filling in a field",
    "uia.inspect": "Reading the window", "clipboard.read": "Reading the clipboard", "clipboard.write": "Copying to the clipboard",
    "screen.capture": "Taking a screenshot", "screen.describe": "Looking at the screen", "screen.ocr": "Reading the screen",
    "vision.ask": "Looking at the screen",
    "fs.read": "Reading a file", "fs.write": "Writing a file", "fs.edit": "Editing a file", "fs.search": "Searching files",
    "fs.list": "Listing a folder", "fs.delete": "Moving to the Recycle Bin", "fs.move": "Moving files", "fs.copy": "Copying files",
    "fs.mkdir": "Creating a folder", "fs.info": "Checking a file", "fs.diff": "Comparing files",
    "documents.read": "Reading a document", "documents.write": "Saving a document",
    "terminal.run": "Running a command", "terminal.exec": "Running a program", "code.run": "Running code",
    "code.repo_map": "Mapping the project", "dev.run_tests": "Running tests", "dev.detect": "Inspecting the project",
    "dev.tooling": "Running project tooling", "dev.install": "Installing dependencies",
    "devserver.start": "Starting the dev server", "devserver.status": "Checking the dev server", "devserver.stop": "Stopping the dev server",
    "git.status": "Checking git status", "git.diff": "Reviewing changes", "git.commit": "Committing", "git.branch": "Managing branches",
    "git.log": "Reading history", "git.push": "Pushing to the remote", "git.pull": "Pulling from the remote", "git.clone": "Cloning",
    "git.stash": "Stashing changes", "git.integrate": "Merging changes", "github.issues": "Working with GitHub", "github.ci": "Checking CI",
    "browser.open": "Opening a web page", "browser.navigate": "Navigating", "browser.click": "Clicking on the page",
    "browser.type": "Typing on the page", "browser.extract": "Reading the page", "browser.download": "Downloading",
    "web.search": "Searching the web", "web.fetch": "Reading a web page", "web.research": "Researching",
    "memory.remember": "Remembering", "memory.recall": "Recalling", "memory.forget": "Forgetting", "memory.alias": "Saving a shortcut",
    "schedule.reminder": "Setting a reminder", "schedule.task": "Scheduling", "monitor.start": "Starting to watch",
    "notify.send": "Sending you a notification", "system.info": "Checking the system", "system.media": "Controlling media",
    "process.list": "Checking running apps", "process.stop": "Stopping a process", "process.start": "Starting a program",
    "email.send": "Sending an email", "email.search": "Searching email", "email.read": "Reading email", "email.draft": "Drafting an email",
    "message.send": "Sending a message", "message.recent": "Reading messages", "contacts.resolve": "Looking up a contact",
    "calendar.list": "Checking the calendar", "calendar.create": "Adding an event", "calendar.update": "Updating an event",
    "ask_user": "Asking you", "finish": "Wrapping up", "read_artifact": "Reading a saved result",
}

CONTROL_TOOLS = frozenset({"input.type", "input.key", "input.click", "input.scroll", "vision.click", "uia.invoke",
                           "uia.set_value", "windows.focus", "windows.arrange", "windows.close", "clipboard.write"})
CAPTURE_TOOLS = frozenset({"screen.capture", "screen.describe", "screen.ocr", "vision.ask", "vision.click"})


def tool_title(tool: str) -> str:
    return _TITLES.get(tool) or f"Working ({tool.split('.')[0]})"


def provider_summary(services: Any) -> dict[str, Any]:
    """AI connection in words, without provider jargon."""
    router = services.router
    settings = services.settings
    now = time.time()
    cloud_configured = router.cloud_available("reasoning")
    cooling = [h for h in services.health.all() if h.cooldown_until > now and h.provider not in ("llamacpp", "ollama")]
    if cloud_configured:
        usable = False
        for cand in router.catalog.categories.get("reasoning", []):
            spec = router.catalog.providers.get(cand.provider)
            if spec is None or spec.local or not router.credential_status(spec)[0]:
                continue
            if any(services.health.get(spec.id, m).usable() for m in cand.models):
                usable = True
                break
        if usable:
            return {"level": "cloud", "summary": "Cloud AI connected", "retry_in_s": None}
        retry = min((int(h.cooldown_until - now) for h in cooling), default=None)
        if retry is not None and retry < 86400:
            return {"level": "local" if _local_possible(settings) else "degraded",
                    "summary": f"Cloud AI is rate-limited, retrying in {retry}s" + (
                        "; using local AI meanwhile" if _local_possible(settings) else ""), "retry_in_s": retry}
        return {"level": "local" if _local_possible(settings) else "degraded",
                "summary": "Cloud AI key was rejected: check it in Settings", "retry_in_s": None}
    if _local_possible(settings):
        return {"level": "local", "summary": "Using local AI, so answers will be slower", "retry_in_s": None}
    return {"level": "none", "summary": "No AI available: only basic commands work", "retry_in_s": None}


def _local_possible(settings: Any) -> bool:
    """A local model can answer: SCAR has a GGUF to run, or Ollama is installed."""
    import shutil

    if settings.local_inference_policy == "never":
        return False
    return bool(settings.local_llm_model_path) or shutil.which("ollama") is not None


def approval_reason(reason: str, risk: str, tool: str, details: dict[str, Any]) -> str:
    """Why SCAR is asking, in plain words ("Moving files is a high-risk action…"), keeping any specific reasons."""
    import re

    m = re.match(r"(LOW|MEDIUM|HIGH|CRITICAL) risk needs approval at level (\d)", reason)
    extra = [str(r) for r in (details.get("risk_reasons") or []) if r and "needs approval" not in str(r)]
    if m:
        level = m.group(2)
        word = risk.lower()
        text = (f"{tool_title(tool)} is a {word}-risk action, and at your current setting (level {level}) SCAR asks before "
                f"{word}-risk actions.")
        if risk == "CRITICAL":
            text = f"{tool_title(tool)} is a critical action. SCAR always asks, at every setting, and needs the confirmation word."
        if extra:
            text += " Also: " + "; ".join(extra[:3]) + "."
        return text
    return reason[:1].upper() + reason[1:]
