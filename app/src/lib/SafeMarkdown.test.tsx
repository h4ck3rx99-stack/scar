import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { SafeMarkdown, safeUrl } from "./SafeMarkdown";

// Payloads that model output, web pages, emails or OCR text could contain.
const XSS = [
  `<script>window.__pwned = 1</script>`,
  `<img src=x onerror="window.__pwned=1">`,
  `<iframe src="javascript:window.__pwned=1"></iframe>`,
  `<svg onload="window.__pwned=1"></svg>`,
  `[click me](javascript:window.__pwned=1)`,
  `[data](data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==)`,
  `[vb](vbscript:msgbox(1))`,
  `![x](https://evil.example/track.png)`,
  `<a href="javascript:alert(1)">x</a>`,
  `<details open ontoggle="window.__pwned=1">`,
  `<form action="https://evil.example"><button>Approve</button></form>`,
  `<style>body{display:none}</style>`,
  `[x](JaVaScRiPt:alert(1))`,
  `<button onclick="fetch('http://127.0.0.1')">Allow once</button>`,
];

describe("SafeMarkdown", () => {
  it.each(XSS)("renders %s inertly", (payload) => {
    const { container } = render(<SafeMarkdown text={`before\n\n${payload}\n\nafter`} />);
    const html = container.innerHTML.toLowerCase();
    for (const bad of ["<script", "<iframe", "<svg", "<img", "<form", "<style", "<button", "<details", "onerror", "onload", "ontoggle", "onclick"]) {
      expect(html).not.toContain(bad);
    }
    for (const a of container.querySelectorAll("a")) {
      expect(a.getAttribute("href") ?? "").toMatch(/^(https?:|mailto:)/);
    }
    expect((window as unknown as { __pwned?: number }).__pwned).toBeUndefined();
  });

  it("keeps ordinary markdown", () => {
    const { container } = render(<SafeMarkdown text={"**Done.** See [docs](https://example.com)\n\n- one\n- two\n\n`code`"} />);
    expect(container.querySelector("strong")?.textContent).toBe("Done.");
    expect(container.querySelector("a")?.getAttribute("href")).toBe("https://example.com");
    expect(container.querySelectorAll("li")).toHaveLength(2);
  });

  it("only allows http(s) and mailto URLs", () => {
    expect(safeUrl("https://a.example/x")).toBe("https://a.example/x");
    expect(safeUrl("mailto:me@example.com")).toBe("mailto:me@example.com");
    expect(safeUrl("javascript:alert(1)")).toBe("");
    expect(safeUrl("file:///C:/Windows/System32")).toBe("");
    expect(safeUrl("scar://approve")).toBe("");
  });
});
