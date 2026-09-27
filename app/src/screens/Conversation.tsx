import { Bell, MessageCircleQuestion, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { ApprovalCard } from "../components/ApprovalCard";
import { Composer } from "../components/Composer";
import { TaskCard } from "../components/TaskCard";
import { Button, Kbd } from "../components/ui";
import { useRuntime } from "../store/runtime";

const EXAMPLES = [
  "Open VS Code in my project folder",
  "What's using my RAM?",
  "Run the tests in C:\\Projects\\app and fix what fails",
  "Research the best way to back up a laptop and save a summary to backup.md",
  "Remind me in 20 minutes to stretch",
  "What's on my screen?",
];

function Questions() {
  const questions = useRuntime((s) => s.questions);
  const answer = useRuntime((s) => s.answerQuestion);
  const [text, setText] = useState<Record<string, string>>({});
  const list = Object.values(questions);
  if (!list.length) return null;
  return (
    <>
      {list.map((q) => (
        <div key={q.questionId} className="question" role="group" aria-label="SCAR has a question">
          <MessageCircleQuestion size={18} aria-hidden />
          <div className="question-body">
            <p>{q.text}</p>
            {q.options.length > 0 && (
              <div className="question-options">
                {q.options.map((o) => (
                  <Button key={o} size="sm" onClick={() => void answer(q.questionId, o)}>
                    {o}
                  </Button>
                ))}
              </div>
            )}
            <form
              className="question-free"
              onSubmit={(e) => {
                e.preventDefault();
                const t = (text[q.questionId] ?? "").trim();
                if (t) void answer(q.questionId, t);
              }}
            >
              <input aria-label="Your answer" placeholder="Type an answer…" value={text[q.questionId] ?? ""} onChange={(e) => setText({ ...text, [q.questionId]: e.target.value })} />
              <Button size="sm" variant="primary" type="submit">
                Answer
              </Button>
            </form>
          </div>
        </div>
      ))}
    </>
  );
}

export function Conversation() {
  const order = useRuntime((s) => s.order);
  const tasks = useRuntime((s) => s.tasks);
  const approvals = useRuntime((s) => s.approvals);
  const notices = useRuntime((s) => s.notices);
  const dismiss = useRuntime((s) => s.dismissNotice);
  const endRef = useRef<HTMLDivElement>(null);
  const pending = Object.values(approvals).sort((a, b) => a.created_at.localeCompare(b.created_at));
  const [draft, setDraft] = useState<{ text: string; n: number } | null>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end", behavior: "smooth" });
  }, [order.length, pending.length, Object.values(tasks).map((t) => t.draft.length + t.steps.length + t.result.length).join(",")]);

  const visible = order.map((id) => tasks[id]).filter((t): t is NonNullable<typeof t> => Boolean(t) && !t!.background);

  return (
    <div className="conversation">
      <div className="conversation-scroll" role="log" aria-label="Conversation">
        {visible.length === 0 && pending.length === 0 ? (
          <div className="welcome">
            <img src="/scar-mark.svg" alt="" className="welcome-mark" />
            <h1>What should I do?</h1>
            <p className="muted">I can open apps and projects, work with files, run and fix tests, browse and research, and keep an eye on things. I ask before anything risky.</p>
            <div className="examples">
              {EXAMPLES.map((ex) => (
                <button key={ex} className="example" onClick={() => setDraft({ text: ex, n: (draft?.n ?? 0) + 1 })}>
                  {ex}
                </button>
              ))}
            </div>
            <p className="muted small">
              <Kbd>Ctrl</Kbd>+<Kbd>Alt</Kbd>+<Kbd>Space</Kbd> opens the Quick Bar from anywhere.
            </p>
          </div>
        ) : (
          <div className="thread">
            {visible.map((t) => (
              <TaskCard key={t.taskId} task={t} />
            ))}
          </div>
        )}
        {pending.length > 0 && (
          <div className="approval-queue">
            <ApprovalCard approval={pending[0]!} />
            {pending.length > 1 && <p className="muted small">{pending.length - 1} more waiting after this one.</p>}
          </div>
        )}
        <Questions />
        {notices.slice(-3).map((n) => (
          <div key={n.id} className={`notice notice-${n.tone}`} role="status">
            <Bell size={15} aria-hidden />
            <span>{n.text}</span>
            <button aria-label="Dismiss" className="icon-btn" onClick={() => dismiss(n.id)}>
              <X size={14} />
            </button>
          </div>
        ))}
        <div ref={endRef} />
      </div>
      {/* examples fill the input so the user can edit before sending; nothing runs without Enter */}
      <Composer autoFocus seed={draft} />
    </div>
  );
}
