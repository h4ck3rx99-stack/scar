"""Checks the LLM picks the right tool for phrasings the router doesn't know. Tools are stubbed (no OS effects)."""
import asyncio, sys, time
sys.path.insert(0, ".")
from jarvis.brain.agent import Agent

PHRASES = [
    "could you get rid of the opera window for a bit",
    "I want spotify on the left half of my screen",
    "bring up some relaxing jazz on youtube",
    "what's the tallest mountain in the world",
    "which windows do I have open right now",
    "turn the music way down",
]

async def main():
    said, calls = [], []
    async def speak(t): said.append(t)
    async def control(n): calls.append(("control", n))
    agent = Agent(ui=None, speak=speak, on_control=control)
    async def fake_call(name, args, from_router=False):
        calls.append((name, args))
        if name == "list_windows":
            return "Open windows: Opera (YouTube - Opera); Claude (Claude)"
        return "done"
    agent.tools.call = fake_call
    for p in PHRASES:
        said.clear(); calls.clear(); t = {}
        t0 = time.perf_counter()
        await agent._llm_turn(p, t)
        ft = (t.get("first_token", time.perf_counter()) - t0) * 1000
        print(f"{p!r}\n   calls={calls}\n   said={said}  first={ft:.0f}ms total={(time.perf_counter()-t0)*1000:.0f}ms")
    await agent.llm.aclose()
asyncio.run(main())
