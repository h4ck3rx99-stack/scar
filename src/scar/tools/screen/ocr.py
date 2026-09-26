"""OCR via the built-in Windows.Media.Ocr engine (PyWinRT). CPU-light, free, offline."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from PIL import Image

from scar.core.errors import CapabilityUnavailable


@dataclass
class OcrWord:
    text: str
    x: int
    y: int
    w: int
    h: int


@dataclass
class OcrLine:
    text: str
    x: int
    y: int
    w: int
    h: int
    words: list[OcrWord]

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class OcrResult:
    text: str
    lines: list[OcrLine]
    language: str


class OcrEngine:
    """Wraps Windows.Media.Ocr. Coordinates are returned in *image* pixels."""

    def __init__(self) -> None:
        self._engine = None
        self.error: str | None = None

    def available(self) -> tuple[bool, str]:
        try:
            self._get()
        except CapabilityUnavailable as exc:
            return False, str(exc)
        return True, ""

    def _get(self):  # type: ignore[no-untyped-def]
        if self._engine is not None:
            return self._engine
        try:
            import winrt.windows.media.ocr as wocr
        except ImportError as exc:
            raise CapabilityUnavailable("Windows OCR (PyWinRT winrt-Windows.Media.Ocr) not installed",
                                        "docs/troubleshooting.md#ocr") from exc
        eng = wocr.OcrEngine.try_create_from_user_profile_languages()
        if eng is None:
            raise CapabilityUnavailable("no OCR language pack is installed for the user's languages",
                                        "docs/troubleshooting.md#ocr")
        self._engine = eng
        return eng

    async def recognize(self, image: Image.Image) -> OcrResult:
        import winrt.windows.graphics.imaging as gi
        import winrt.windows.storage.streams as ss

        eng = self._get()
        scale = 1.0
        img = image
        # small text OCRs better when upscaled; the engine caps dimensions at 10000
        if max(img.size) < 1400 and min(img.size) < 700:
            scale = 2.0
        max_dim = 9800
        if max(img.size) * scale > max_dim:
            scale = max_dim / max(img.size)
        if scale != 1.0:
            img = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))), Image.Resampling.LANCZOS)
        rgba = img.convert("RGBA")
        r, g, b, a = rgba.split()
        bgra = Image.merge("RGBA", (b, g, r, a)).tobytes()
        writer = ss.DataWriter()
        writer.write_bytes(bgra)
        bitmap = gi.SoftwareBitmap.create_copy_from_buffer(writer.detach_buffer(), gi.BitmapPixelFormat.BGRA8, img.width, img.height)
        res = await eng.recognize_async(bitmap)
        lines: list[OcrLine] = []
        for ln in res.lines:
            words = [OcrWord(w.text, int(w.bounding_rect.x / scale), int(w.bounding_rect.y / scale),
                             int(w.bounding_rect.width / scale), int(w.bounding_rect.height / scale)) for w in ln.words]
            if not words:
                continue
            x0 = min(w.x for w in words)
            y0 = min(w.y for w in words)
            x1 = max(w.x + w.w for w in words)
            y1 = max(w.y + w.h for w in words)
            lines.append(OcrLine(ln.text, x0, y0, x1 - x0, y1 - y0, words))
        lang = ""
        try:
            lang = str(eng.recognizer_language.language_tag)
        except AttributeError:
            lang = ""
        return OcrResult(text="\n".join(ln.text for ln in lines), lines=lines, language=lang)
