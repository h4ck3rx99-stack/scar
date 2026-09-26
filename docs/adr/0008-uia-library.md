# ADR 0008: UI Automation via the `uiautomation` package on a dedicated STA thread

Status: accepted (2026-09-26)

## Decision

uiautomation (Apache-2.0, comtypes-based) exposes control patterns directly (Invoke, Value, Toggle, SelectionItem, ExpandCollapse, ScrollItem, Text). All calls run on one worker thread initialised with UIAutomationInitializerInThread. Element handles are cached per inspection so the model can act on `[id]`s.

## Alternatives and rationale

pywinauto's UIA backend wraps the same COM API with more abstraction than needed and slower tree walks.
