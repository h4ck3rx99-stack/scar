import { describe, expect, it } from "vitest";
import type { EventEnvelope } from "../api/types";
import { initialState, outcomeFor, reduce, runningTaskIds, type RuntimeState } from "./reducer";

let seq = 0;
const ev = (event: Record<string, unknown>): EventEnvelope => ({ v: 1, seq: ++seq, type: "event", event: { at: new Date().toISOString(), message: "", ...event } });

function run(events: Record<string, unknown>[], start: RuntimeState = initialState): RuntimeState {
  return events.reduce<RuntimeState>((s, e) => reduce(s, ev(e)), start);
}

describe("honest outcomes", () => {
  it("verified success", () => {
    expect(outcomeFor("succeeded", true, "Opened proj in Visual Studio Code")).toEqual({ kind: "verified" });
  });
  it("unverifiable success is never shown as verified", () => {
    const o = outcomeFor("succeeded", null, "Posted it. (I couldn't verify this automatically.)");
    expect(o.kind).toBe("unverified");
  });
  it("a failed verification is a failure with its reason", () => {
    const o = outcomeFor("failed", false, "Saved it. However, verification failed: requested change was made.");
    expect(o).toEqual({ kind: "failed", reason: "requested change was made." });
  });
  it("denied action ends as failed, not done", () => {
    const s = run([
      { kind: "task_started", task_id: "t1", objective: "delete the logs" },
      { kind: "tool_called", task_id: "t1", tool: "fs.delete", action_id: "a1", title: "Moving to the Recycle Bin" },
      { kind: "tool_completed", task_id: "t1", action_id: "a1", status: "denied", message: "Not approved" },
      { kind: "task_failed", task_id: "t1", error: "failed", message: "I didn't delete anything: you denied it." },
    ]);
    expect(s.tasks.t1!.outcome.kind).toBe("failed");
    expect(s.tasks.t1!.steps[0]!.status).toBe("denied");
  });
  it("cancelled", () => {
    const s = run([{ kind: "task_started", task_id: "t2" }, { kind: "task_failed", task_id: "t2", error: "cancelled", message: "Cancelled (by user)." }]);
    expect(s.tasks.t2!.outcome.kind).toBe("cancelled");
    expect(runningTaskIds(s)).toEqual([]);
  });
});

describe("streaming and timeline", () => {
  it("accumulates deltas, resets on retry and clears the draft when the task ends", () => {
    let s = run([
      { kind: "task_started", task_id: "t3", objective: "what is RAM" },
      { kind: "assistant_delta", task_id: "t3", text: "Ra" },
      { kind: "assistant_delta", task_id: "t3", reset: true },
      { kind: "assistant_delta", task_id: "t3", text: "RAM is " },
      { kind: "assistant_delta", task_id: "t3", text: "memory." },
    ]);
    expect(s.tasks.t3!.draft).toBe("RAM is memory.");
    expect(runningTaskIds(s)).toEqual(["t3"]);
    s = run([{ kind: "task_completed", task_id: "t3", verified: null, message: "RAM is memory." }], s);
    expect(s.tasks.t3!.draft).toBe("");
    expect(s.tasks.t3!.result).toBe("RAM is memory.");
  });

  it("approvals come and go by request id", () => {
    const approval = { request_id: "apr1", args_hash: "h".repeat(64), risk: "HIGH", summary: "move a.txt", reason: "moving files", details: {}, critical: false, grantable: true, confirmation_code: "", allowed_responses: ["allow_once", "deny"], created_at: "", expires_at: "", task_id: "t4", tool: "fs.move" };
    let s = run([{ kind: "approval_requested", request_id: "apr1", approval }]);
    expect(Object.keys(s.approvals)).toEqual(["apr1"]);
    s = run([{ kind: "approval_resolved", request_id: "apr1", decision: "deny" }], s);
    expect(s.approvals).toEqual({});
  });

  it("control indicator follows control_active", () => {
    let s = run([{ kind: "control_active", active: true, what: "Typing" }]);
    expect(s.control).toEqual({ active: true, what: "Typing" });
    s = run([{ kind: "control_active", active: false }], s);
    expect(s.control.active).toBe(false);
  });

  it("snapshot restores pending approvals and running tasks after a reconnect", () => {
    const s = reduce(initialState, {
      v: 1, seq: 1, type: "snapshot", snapshot: {
        state: "waiting", provider: { level: "cloud", summary: "Cloud AI connected" },
        pending_approvals: [], running_tasks: [{ task_id: "t9", objective: "x", status: "running", result_summary: "", verified: null }],
        voice: { state: "off" }, game_mode: false, killswitch_hotkey: "<ctrl>+<alt>+<shift>+k",
      },
    } as unknown as EventEnvelope);
    expect(s.assistant).toBe("waiting");
    expect(s.order).toEqual(["t9"]);
  });
});
