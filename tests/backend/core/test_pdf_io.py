"""Tests for core.pdf_io (pdfminer.six + ReportLab replacement for PyMuPDF)."""
from pathlib import Path

import pytest

from core import pdf_io


def _write(path: Path, lines: list[tuple[float, float, str]], pages: int = 1) -> None:
    w = pdf_io.PdfWriter(path, 612, 792)
    for p in range(pages):
        if p:
            w.new_page()
        for x, y, text in lines:
            w.insert_text(x, y, f"{text} p{p + 1}", fontsize=11)
    w.save()


def test_round_trip_text_and_page_count(tmp_path):
    pdf = tmp_path / "a.pdf"
    _write(pdf, [(72, 72, "Alex Example"), (72, 400, "Experience")], pages=2)
    text = pdf_io.extract_text(pdf)
    assert "Alex Example p1" in text and "Experience p2" in text
    assert pdf_io.page_count(pdf) == 2


def test_blocks_use_top_left_origin(tmp_path):
    pdf = tmp_path / "b.pdf"
    _write(pdf, [(72, 100, "Top block"), (300, 600, "Lower block")])
    blocks = {b.text.strip(): b for b in pdf_io.extract_blocks(pdf)}
    top, low = blocks["Top block p1"], blocks["Lower block p1"]
    assert top.bbox[1] < low.bbox[1]  # y grows downward
    assert top.bbox[0] == pytest.approx(72, abs=2) and low.bbox[0] == pytest.approx(300, abs=2)


def test_text_length_uses_font_metrics():
    assert pdf_io.text_length("iiii", 11) < pdf_io.text_length("WWWW", 11)
    assert pdf_io.text_length("abc", 22) == pytest.approx(2 * pdf_io.text_length("abc", 11))


def test_self_test_reports_machine():
    result = pdf_io.self_test()
    assert result["pdf"] == "ok" and result["machine"]


def test_no_pymupdf_dependency():
    root = Path(__file__).resolve().parents[3]
    assert "pymupdf" not in (root / "pyproject.toml").read_text().lower()
    assert 'name = "pymupdf"' not in (root / "uv.lock").read_text().lower()
