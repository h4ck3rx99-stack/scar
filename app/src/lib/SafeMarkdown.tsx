// Renders model output, tool output, web/email/OCR text as Markdown with no raw HTML.
// - raw HTML in the source is dropped (skipHtml), never parsed
// - only http(s) and mailto links survive; they open in the default browser after validation, never in the app
// - images are not loaded (they would be remote requests); their alt text is shown instead
// - nothing rendered here can call the API, approve anything or change settings: it is text
import ReactMarkdown, { defaultUrlTransform, type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { openExternal } from "./shell";

const SAFE_URL = /^(https?:|mailto:)/i;

export function safeUrl(url: string): string {
  const t = defaultUrlTransform(url);
  return SAFE_URL.test(t) ? t : "";
}

const components: Components = {
  a({ href, children }) {
    const url = href ? safeUrl(href) : "";
    if (!url) return <span className="md-link-disabled">{children}</span>;
    return (
      <a
        href={url}
        title={url}
        rel="noreferrer noopener"
        onClick={(e) => {
          e.preventDefault();
          if (url.startsWith("mailto:")) return;
          void openExternal(url);
        }}
      >
        {children}
      </a>
    );
  },
  img({ alt }) {
    return <span className="md-image-placeholder">[image{alt ? `: ${alt}` : ""}]</span>;
  },
  code({ className, children }) {
    return <code className={className}>{children}</code>;
  },
};

export function SafeMarkdown({ text, className }: { text: string; className?: string }) {
  return (
    <div className={className ?? "md"}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml urlTransform={safeUrl} components={components}>
        {text}
      </ReactMarkdown>
    </div>
  );
}
