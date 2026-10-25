"""Permissively licensed PDF reading and writing.

Replaces PyMuPDF (AGPL-3.0). Reading uses pdfminer.six (MIT); writing uses
ReportLab (BSD). Coordinates in this module use a top-left origin with y
growing downward, which is the convention the callers were written for.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from pdfminer.high_level import extract_pages, extract_text as _pm_extract_text
from pdfminer.layout import LAParams, LTChar, LTTextBoxHorizontal, LTTextLineHorizontal
from reportlab.lib.colors import Color
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas as _rl_canvas

# Map the font aliases used by callers to ReportLab base-14 names.
_FONTS = {
    "helv": "Helvetica",
    "helvetica": "Helvetica",
    "helvetica-bold": "Helvetica-Bold",
    "hebo": "Helvetica-Bold",
}


def font_name(alias: str) -> str:
    return _FONTS.get(alias.lower(), alias)


def text_length(text: str, fontsize: float, font: str = "helv") -> float:
    """Rendered width of ``text`` in points, using real font metrics."""
    return stringWidth(text, font_name(font), fontsize)


# A resume is a few pages; a 2000-page or 200 MB "resume" (wrong file picked) must not pin
# the CPU for minutes. Callers that need text read at most this many pages.
RESUME_MAX_PAGES = 20
RESUME_MAX_BYTES = 50 * 1024 * 1024


class PdfTooLarge(ValueError):
    pass


def extract_text(path: str | Path, max_pages: int = 0) -> str:
    """Text of the PDF (all pages, or the first ``max_pages``), in reading order."""
    return _pm_extract_text(str(path), maxpages=max_pages) or ""


def extract_resume_text(path: str | Path) -> str:
    """Text of a resume PDF, bounded in size and pages. Raises PdfTooLarge for a huge file."""
    size = Path(path).stat().st_size
    if size > RESUME_MAX_BYTES:
        raise PdfTooLarge(f"The PDF is {size // (1024 * 1024)} MB; a resume should be under "
                          f"{RESUME_MAX_BYTES // (1024 * 1024)} MB. Pick the right file or export a smaller one.")
    return extract_text(path, max_pages=RESUME_MAX_PAGES)


NO_TEXT_MESSAGE = ("No text could be read from this PDF. It looks like a scanned or image-only file; "
                   "export your resume as a text PDF (Save as PDF from Word or Google Docs) so forms can use it.")


@dataclass
class TextBlock:
    page: int
    bbox: tuple[float, float, float, float]  # x0, top, x1, bottom (top-left origin)
    text: str


def extract_blocks(path: str | Path) -> list[TextBlock]:
    """Text blocks (paragraph-like groups) per page, in reading order."""
    blocks: list[TextBlock] = []
    for page_num, layout in enumerate(extract_pages(str(path), laparams=LAParams())):
        height = layout.height
        items = [el for el in layout if isinstance(el, LTTextBoxHorizontal)]
        items.sort(key=lambda el: (-el.y1, el.x0))
        for el in items:
            text = el.get_text().strip()
            if not text:
                continue
            blocks.append(TextBlock(
                page=page_num,
                bbox=(el.x0, height - el.y1, el.x1, height - el.y0),
                text=text,
            ))
    return blocks


@dataclass
class TextLine:
    page: int
    x0: float
    top: float
    x1: float
    bottom: float
    text: str
    page_width: float
    page_height: float


def extract_lines(path: str | Path) -> list[TextLine]:
    """Individual text lines with positions; used for layout checks."""
    out: list[TextLine] = []
    for page_num, layout in enumerate(extract_pages(str(path), laparams=LAParams())):
        h, w = layout.height, layout.width

        def walk(node: Iterable):
            for el in node:
                if isinstance(el, LTTextLineHorizontal):
                    t = el.get_text().strip()
                    if t:
                        out.append(TextLine(page_num, el.x0, h - el.y1, el.x1, h - el.y0, t, w, h))
                elif hasattr(el, "__iter__") and not isinstance(el, LTChar):
                    walk(el)
        walk(layout)
    return out


def page_count(path: str | Path) -> int:
    return sum(1 for _ in extract_pages(str(path)))


class PdfWriter:
    """Minimal page-based writer with a top-left coordinate system."""

    def __init__(self, path: str | Path, width: float, height: float):
        self.path = str(path)
        self.width = width
        self.height = height
        self._c = _rl_canvas.Canvas(self.path, pagesize=(width, height))
        self._pages = 1
        self._dirty = False

    def new_page(self) -> None:
        self._c.showPage()
        self._pages += 1
        self._dirty = False

    @property
    def pages(self) -> int:
        return self._pages

    def insert_text(self, x: float, baseline_y: float, text: str, *, fontsize: float,
                    font: str = "helv", color: tuple[float, float, float] = (0, 0, 0)) -> None:
        self._c.setFillColor(Color(*color))
        self._c.setFont(font_name(font), fontsize)
        self._c.drawString(x, self.height - baseline_y, text)
        self._dirty = True

    def draw_line(self, x0: float, y0: float, x1: float, y1: float, *,
                  color: tuple[float, float, float] = (0, 0, 0), width: float = 1.0) -> None:
        self._c.setStrokeColor(Color(*color))
        self._c.setLineWidth(width)
        self._c.line(x0, self.height - y0, x1, self.height - y1)
        self._dirty = True

    def save(self) -> None:
        self._c.save()


def self_test() -> dict:
    """Round-trip a PDF through ReportLab and pdfminer.

    Used by ``applyqueue-backend --self-test`` so CI can prove a frozen
    (PyInstaller) sidecar bundles both libraries and their data files on each
    architecture (notably Intel macOS).
    """
    import platform
    import tempfile

    marker = "ApplyQueue PDF self-test"
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "self_test.pdf"
        w = PdfWriter(out, 612, 792)
        w.insert_text(72, 72, marker, fontsize=12)
        w.draw_line(72, 80, 300, 80)
        w.save()
        text = extract_text(out)
        pages = page_count(out)
    if marker not in text or pages != 1:
        raise RuntimeError(f"PDF round trip failed: pages={pages} text={text[:80]!r}")
    return {"pdf": "ok", "machine": platform.machine(), "python": platform.python_version()}
