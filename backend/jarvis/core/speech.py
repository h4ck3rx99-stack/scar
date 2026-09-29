"""Ordered sentence -> TTS -> speaker pipeline. Sentence N+1 is synthesised while sentence N plays."""
import asyncio
import concurrent.futures
import logging
import time

log = logging.getLogger("speech")


class SpeechOut:
    def __init__(self, tts, speaker, ui, on_first_audio=None):
        self.tts, self.speaker, self.ui = tts, speaker, ui
        self.on_first_audio = on_first_audio
        self.queue: asyncio.Queue = asyncio.Queue()
        self.epoch = 0                    # bumped on cancel; stale sentences are dropped
        self._busy = 0
        self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="tts")
        self._worker = None
        self.spoken_in_turn = 0

    def start(self):
        self._worker = asyncio.get_running_loop().create_task(self._run())

    async def say(self, text: str):
        text = text.strip()
        if not text:
            return
        await self.ui.send("say", text=text)
        self._busy += 1
        self.queue.put_nowait((self.epoch, text))

    def cancel(self):
        self.epoch += 1
        while not self.queue.empty():
            self.queue.get_nowait()
            self._busy -= 1
        self.speaker.stop()

    def new_turn(self):
        self.spoken_in_turn = 0

    async def _run(self):
        loop = asyncio.get_running_loop()
        while True:
            epoch, text = await self.queue.get()
            try:
                if epoch != self.epoch:
                    continue
                samples, sr = await loop.run_in_executor(self._pool, self.tts.synth, text)
                if epoch != self.epoch or not len(samples):
                    continue
                self.speaker.play(samples, sr)
                if self.spoken_in_turn == 0 and self.on_first_audio:
                    self.on_first_audio(time.perf_counter())
                self.spoken_in_turn += 1
            except Exception:  # noqa: BLE001 - one bad sentence must not kill the voice
                log.exception("tts failed for %r", text)
            finally:
                self._busy -= 1

    async def wait_done(self):
        """Until everything queued has been synthesised AND has finished playing out of the speaker."""
        while self._busy > 0:
            await asyncio.sleep(0.02)
        await self.speaker.wait_idle()
