// Small, accessible building blocks. Radix supplies behaviour (focus, keyboard, ARIA); styling comes from tokens.
import * as SwitchPrimitive from "@radix-ui/react-switch";
import * as TooltipPrimitive from "@radix-ui/react-tooltip";
import clsx from "clsx";
import { AlertTriangle, Inbox, Loader2, WifiOff } from "lucide-react";
import type { ButtonHTMLAttributes, ReactNode } from "react";
import { forwardRef } from "react";

type Variant = "primary" | "secondary" | "ghost" | "danger";

export const Button = forwardRef<HTMLButtonElement, ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; size?: "sm" | "md"; busy?: boolean; icon?: ReactNode }>(
  function Button({ variant = "secondary", size = "md", busy, icon, className, children, disabled, ...rest }, ref) {
    return (
      <button ref={ref} className={clsx("btn", `btn-${variant}`, `btn-${size}`, className)} disabled={disabled || busy} aria-busy={busy || undefined} {...rest}>
        {busy ? <Loader2 className="spin" size={15} aria-hidden /> : icon}
        {children && <span>{children}</span>}
      </button>
    );
  },
);

export function IconButton({ label, children, className, ...rest }: ButtonHTMLAttributes<HTMLButtonElement> & { label: string }) {
  return (
    <Tip label={label}>
      <button aria-label={label} className={clsx("icon-btn", className)} {...rest}>
        {children}
      </button>
    </Tip>
  );
}

export function Tip({ label, children }: { label: string; children: ReactNode }) {
  return (
    <TooltipPrimitive.Root delayDuration={350}>
      <TooltipPrimitive.Trigger asChild>{children}</TooltipPrimitive.Trigger>
      <TooltipPrimitive.Portal>
        <TooltipPrimitive.Content className="tooltip" sideOffset={6}>
          {label}
        </TooltipPrimitive.Content>
      </TooltipPrimitive.Portal>
    </TooltipPrimitive.Root>
  );
}

export function Switch({ checked, onChange, label, disabled, id }: { checked: boolean; onChange: (v: boolean) => void; label: string; disabled?: boolean; id?: string }) {
  return (
    <SwitchPrimitive.Root id={id} className="switch" checked={checked} onCheckedChange={onChange} disabled={disabled} aria-label={label}>
      <SwitchPrimitive.Thumb className="switch-thumb" />
    </SwitchPrimitive.Root>
  );
}

export function Card({ title, actions, children, className, subtitle }: { title?: ReactNode; subtitle?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={clsx("card", className)}>
      {(title || actions) && (
        <header className="card-head">
          <div>
            {title && <h2 className="card-title">{title}</h2>}
            {subtitle && <p className="card-sub">{subtitle}</p>}
          </div>
          {actions && <div className="card-actions">{actions}</div>}
        </header>
      )}
      {children}
    </section>
  );
}

export function Badge({ tone = "neutral", children }: { tone?: "neutral" | "ok" | "warn" | "danger" | "info" | "accent"; children: ReactNode }) {
  return <span className={clsx("badge", `badge-${tone}`)}>{children}</span>;
}

export function Skeleton({ lines = 3 }: { lines?: number }) {
  return (
    <div className="skeleton" role="status" aria-busy="true" aria-label="Loading">
      {Array.from({ length: lines }, (_, i) => (
        <div key={i} className="skeleton-line" style={{ width: `${88 - i * 14}%` }} />
      ))}
    </div>
  );
}

export function EmptyState({ title, children, icon }: { title: string; children?: ReactNode; icon?: ReactNode }) {
  return (
    <div className="empty">
      <div className="empty-icon" aria-hidden>
        {icon ?? <Inbox size={22} />}
      </div>
      <p className="empty-title">{title}</p>
      {children && <div className="empty-body">{children}</div>}
    </div>
  );
}

export function ErrorState({ message, action }: { message: string; action?: ReactNode }) {
  return (
    <div className="error-state" role="alert">
      <AlertTriangle size={18} aria-hidden />
      <div>
        <p>{message}</p>
        {action}
      </div>
    </div>
  );
}

export function OfflineState({ message }: { message: string }) {
  return (
    <div className="error-state offline" role="status">
      <WifiOff size={18} aria-hidden />
      <p>{message}</p>
    </div>
  );
}

export function Kbd({ children }: { children: ReactNode }) {
  return <kbd className="kbd">{children}</kbd>;
}

export function Field({ label, hint, children, htmlFor }: { label: string; hint?: ReactNode; children: ReactNode; htmlFor?: string }) {
  return (
    <div className="field">
      <label className="field-label" htmlFor={htmlFor}>
        {label}
      </label>
      {children}
      {hint && <p className="field-hint">{hint}</p>}
    </div>
  );
}

export function Spinner({ label }: { label?: string }) {
  return (
    <span className="spinner" role="status">
      <Loader2 className="spin" size={16} aria-hidden />
      {label && <span>{label}</span>}
    </span>
  );
}
