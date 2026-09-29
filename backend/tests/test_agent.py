"""The LLM path of the agent, with a scripted fake LLM (no server needed). Guards the streaming/speech
code that crashed every LLM turn once (NameError in the clause splitter)."""
import asyncio

from jarvis.brain import agent as agent_mod
from jarvis.brain.agent import Agent


class FakeLLM:
    def __init__(self, *scripts):
        self.scripts = list(scripts)   # one list of (kind, data) per call
        self.calls = []

    async def stream(self, messages, tools=None, max_tokens=400):
        self.calls.append({"messages": messages, "tools": [t["function"]["name"] for t in tools or []]})
        for item in self.scripts.pop(0):
            yield item


def make_agent(*scripts):
    said, controls, tool_calls = [], [], []

    async def speak(t):
        said.append(t)

    async def control(n):
        controls.append(n)

    a = Agent(ui=None, speak=speak, on_control=control)
    a.llm = FakeLLM(*scripts)

    async def fake_tool(name, args, from_router=False):
        tool_calls.append((name, args))
        if name == "weather":
            return {"place": "Bengaluru", "now": "24°C, partly cloudy"}
        return "done"

    a.tools.call = fake_tool
    return a, said, tool_calls


def run(coro):
    return asyncio.run(coro)


def test_streamed_answer_is_spoken_sentence_by_sentence():
    chunks = [("text", t) for t in ["Tokyo is the ", "capital of Japan. ", "It has about fourteen ", "million people."]]
    a, said, _ = make_agent(chunks)
    run(a.handle("what's the capital of japan", {}))
    assert said == ["Tokyo is the capital of Japan.", "It has about fourteen million people."]


def test_long_first_sentence_starts_speaking_at_a_clause():
    long = "Well, the mitochondria is the part of the cell that makes energy, which is why people call it the powerhouse."
    a, said, _ = make_agent([("text", w + " ") for w in long.split()])
    run(a.handle("what does the mitochondria do", {}))
    assert len(said) >= 2 and " ".join(said).replace("  ", " ").strip() == long


def test_tool_call_runs_tool_and_stays_quiet():
    a, said, tools = make_agent([("tool_calls", [{"id": "1", "name": "window_action", "arguments": {"app": "spotify", "action": "minimize"}, "raw": "{}"}])])
    run(a.handle("hide spotify for a bit", {}))
    assert tools == [("window_action", {"app": "spotify", "action": "minimize"})]
    assert said == []


def test_follow_up_offers_ignore_and_respects_it():
    a, said, tools = make_agent([("text", "YES")], [("tool_calls", [{"id": "1", "name": "ignore", "arguments": {}, "raw": "{}"}])])
    out = run(a.handle("and then he said the index was wrong", {}, follow_up=True))
    assert out == "ignored" and said == [] and tools == []
    assert "ignore" in a.llm.calls[1]["tools"]


def test_follow_up_directed_check_no_drops_it_before_the_main_llm():
    a, said, tools = make_agent([("text", "NO")])
    out = run(a.handle("blue box, right?", {}, follow_up=True))
    assert out == "ignored" and said == [] and tools == []
    assert len(a.llm.calls) == 1   # only the yes/no check ran


def test_ignore_not_offered_after_wake_word():
    a, _, _ = make_agent([("text", "Sure.")])
    run(a.handle("tell me something", {}))
    assert "ignore" not in a.llm.calls[0]["tools"]


def test_long_follow_up_is_ignored_without_llm():
    a, said, _ = make_agent()
    text = " ".join(["word"] * 40)
    assert run(a.handle(text, {}, follow_up=True)) == "ignored"
    assert a.llm.calls == [] and said == []


def test_data_tool_result_is_phrased_by_second_pass():
    a, said, tools = make_agent(
        [("tool_calls", [{"id": "1", "name": "weather", "arguments": {}, "raw": "{}"}])],
        [("text", "It's 24 degrees and partly cloudy in Bengaluru.")],
    )
    run(a.handle("do I need a jacket", {}))
    assert tools[0][0] == "weather"
    assert said == ["It's 24 degrees and partly cloudy in Bengaluru."]
    assert "Bengaluru" in a.llm.calls[1]["messages"][-1]["content"]


def test_tool_calls_are_capped():
    many = [{"id": str(i), "name": "open", "arguments": {"name": f"site{i}"}, "raw": "{}"} for i in range(6)]
    a, _, tools = make_agent([("tool_calls", many)])
    run(a.handle("set up my whole study session", {}))
    assert len(tools) == agent_mod.MAX_TOOL_CALLS


def test_router_hit_skips_llm():
    a, _, tools = make_agent()
    run(a.handle("minimize chrome", {}))
    assert tools == [("window_action", {"app": "chrome", "action": "minimize"})] and a.llm.calls == []
