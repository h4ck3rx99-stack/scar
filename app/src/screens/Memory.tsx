import * as Dialog from "@radix-ui/react-dialog";
import clsx from "clsx";
import { Brain, Pencil, Search, Trash2 } from "lucide-react";
import { useState } from "react";
import { humanError } from "../api/client";
import { Button, Card, EmptyState, ErrorState, Skeleton } from "../components/ui";
import { useApiGet } from "../hooks/useApi";
import { useRuntime } from "../store/runtime";

interface Memory { id: string; category: string; text: string; key: string | null; project: string | null; importance: number; source: string; trust: string; created_at: string; updated_at: string }

const CATEGORY_WORDS: Record<string, string> = {
  semantic: "Facts", preference: "Preferences", project: "Projects", alias: "Shortcuts (“my project folder”)",
  episodic: "Recent events", task_history: "What SCAR did", tool_history: "Tool history", conversation: "Conversation",
};

export function MemoryScreen() {
  const [query, setQuery] = useState("");
  const [applied, setApplied] = useState("");
  const [category, setCategory] = useState("");
  const path = `/memory?${new URLSearchParams({ ...(applied ? { q: applied } : {}), ...(category ? { category } : {}) })}`;
  const list = useApiGet<Memory[]>(path, [path]);
  const api = useRuntime((s) => s.api);
  const [editing, setEditing] = useState<{ id: string; text: string } | null>(null);
  const [err, setErr] = useState("");
  const [wipeOpen, setWipeOpen] = useState(false);
  const [wipeText, setWipeText] = useState("");

  async function save() {
    if (!editing) return;
    try {
      await api?.patch(`/memory/${editing.id}`, { text: editing.text });
      setEditing(null);
      await list.reload();
    } catch (e) {
      setErr(humanError(e));
    }
  }

  async function forget(id: string) {
    try {
      await api?.del(`/memory/${id}`);
      await list.reload();
    } catch (e) {
      setErr(humanError(e));
    }
  }

  async function wipe() {
    try {
      await api?.post("/memory/wipe", { confirm: "FORGET EVERYTHING" });
      setWipeOpen(false);
      setWipeText("");
      await list.reload();
    } catch (e) {
      setErr(humanError(e));
    }
  }

  const grouped = new Map<string, Memory[]>();
  for (const m of list.data ?? []) grouped.set(m.category, [...(grouped.get(m.category) ?? []), m]);

  return (
    <div className="page">
      <div className="page-head">
        <h1>Memory</h1>
        <Button variant="danger" size="sm" onClick={() => setWipeOpen(true)}>
          Forget everything
        </Button>
      </div>
      <p className="muted page-intro">
        What SCAR remembers stays on this computer, in its local database. Secrets, passwords and card numbers are never stored. Edit anything that's wrong, or forget it.
      </p>
      {err && <ErrorState message={err} />}
      <form
        className="toolbar"
        onSubmit={(e) => {
          e.preventDefault();
          setApplied(query.trim());
        }}
      >
        <div className="search">
          <Search size={15} aria-hidden />
          <input aria-label="Search memory" placeholder="Search what SCAR remembers" value={query} onChange={(e) => setQuery(e.target.value)} />
        </div>
        <select aria-label="Category" value={category} onChange={(e) => setCategory(e.target.value)}>
          <option value="">All categories</option>
          {Object.entries(CATEGORY_WORDS).map(([k, v]) => (
            <option key={k} value={k}>
              {v}
            </option>
          ))}
        </select>
      </form>
      {list.loading ? (
        <Skeleton lines={5} />
      ) : list.error ? (
        <ErrorState message={list.error} />
      ) : !list.data?.length ? (
        <EmptyState title={applied ? "Nothing matches that." : "Nothing remembered yet."} icon={<Brain size={22} />}>
          Tell SCAR “remember that my project folder is C:\Projects\site” and it will use it later.
        </EmptyState>
      ) : (
        [...grouped.entries()].map(([cat, items]) => (
          <Card key={cat} title={CATEGORY_WORDS[cat] ?? cat}>
            <ul className="rows">
              {items.map((m) => (
                <li key={m.id} className={clsx("row", editing?.id === m.id && "editing")}>
                  {editing?.id === m.id ? (
                    <form
                      className="inline-form grow"
                      onSubmit={(e) => {
                        e.preventDefault();
                        void save();
                      }}
                    >
                      <input aria-label="Memory text" value={editing.text} onChange={(e) => setEditing({ ...editing, text: e.target.value })} autoFocus />
                      <Button type="submit" variant="primary" size="sm">
                        Save
                      </Button>
                      <Button type="button" variant="ghost" size="sm" onClick={() => setEditing(null)}>
                        Cancel
                      </Button>
                    </form>
                  ) : (
                    <>
                      <div className="row-main">
                        <span className="row-title">{m.text}</span>
                        <span className="row-sub">
                          {new Date(m.updated_at).toLocaleDateString()} · from {m.source === "user" ? "you" : m.source}
                        </span>
                      </div>
                      <Button size="sm" variant="ghost" icon={<Pencil size={13} aria-hidden />} onClick={() => setEditing({ id: m.id, text: m.text })}>
                        Edit
                      </Button>
                      <Button size="sm" variant="ghost" icon={<Trash2 size={13} aria-hidden />} onClick={() => void forget(m.id)}>
                        Forget
                      </Button>
                    </>
                  )}
                </li>
              ))}
            </ul>
          </Card>
        ))
      )}
      <Dialog.Root open={wipeOpen} onOpenChange={setWipeOpen}>
        <Dialog.Portal>
          <Dialog.Overlay className="overlay" />
          <Dialog.Content className="dialog">
            <Dialog.Title>Forget everything?</Dialog.Title>
            <Dialog.Description className="muted">
              This deletes every memory: shortcuts, preferences, facts and task history. It can't be undone. Type <strong>FORGET EVERYTHING</strong> to confirm.
            </Dialog.Description>
            <input aria-label="Type FORGET EVERYTHING" value={wipeText} onChange={(e) => setWipeText(e.target.value)} autoFocus />
            <div className="dialog-actions">
              <Dialog.Close asChild>
                <Button variant="ghost">Keep my memories</Button>
              </Dialog.Close>
              <Button variant="danger" disabled={wipeText.trim() !== "FORGET EVERYTHING"} onClick={() => void wipe()}>
                Forget everything
              </Button>
            </div>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </div>
  );
}
