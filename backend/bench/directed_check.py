"""How well does the follow-up 'was this said to Scar?' check separate real follow-ups from background talk?
Background lines are real transcripts from logs/jarvis.log. Needs llama-server on :8080."""
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jarvis.brain.agent import Agent  # noqa: E402

DIRECTED = [
    ("It's 25 degrees and cloudy in Bengaluru.", "and tomorrow?"),
    ("It's 25 degrees and cloudy in Bengaluru.", "should I take an umbrella"),
    ("Opening YouTube.", "search for lofi music"),
    ("Tokyo is the capital of Japan.", "what's its population"),
    ("Timer set for 5 minutes.", "make it 10 instead"),
    ("Opening notepad.", "close it"),
    ("The mitochondria makes energy for the cell.", "explain that more simply"),
    ("Here's the Gmail tab.", "thanks"),
]
BACKGROUND = [
    ("It's 25 degrees and cloudy in Bengaluru.", "Blue box, right?"),
    ("Opening YouTube.", "This is very good. It's all about the same."),
    ("Tokyo is the capital of Japan.", "Why 20 into 4, 20 into 5? Because of your discount."),
    ("Timer set for 5 minutes.", "Don't forget to like, comment and subscribe."),
    ("Opening notepad.", "It is greater than 100"),
    ("Here's the Gmail tab.", "No, it's just bad amounts to me."),
    ("The mitochondria makes energy for the cell.", "I'm not waiting for you to see that"),
    ("Opening YouTube.", "Till your last index, you can take your digits."),
]


async def main():
    agent = Agent(ui=None, speak=None, on_control=None)
    ok = 0
    t0 = time.perf_counter()
    for group, want in ((DIRECTED, True), (BACKGROUND, False)):
        for said, heard in group:
            agent.memory.turns = [{"role": "user", "content": "..."}, {"role": "assistant", "content": said}]
            got = await agent._is_directed(heard)
            ok += got == want
            print(f"{'OK ' if got == want else 'BAD'} want={'to-Scar' if want else 'backgrnd'} got={'to-Scar' if got else 'backgrnd'}  {heard!r}")
    n = len(DIRECTED) + len(BACKGROUND)
    print(f"\n{ok}/{n} correct, {(time.perf_counter() - t0) / n * 1000:.0f} ms per check")
    await agent.llm.aclose()


asyncio.run(main())
