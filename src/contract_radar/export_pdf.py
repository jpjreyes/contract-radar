"""A contract as a PDF, exactly as the requesting user may see it.

The text comes from the same served chunks as the viewer (row-level security + masking already applied), so a user
without personal-data access gets redaction bars where the values were; the original file is never sent. Every page
carries who exported it and when, and the export is written to the audit log by the caller.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from fpdf import FPDF

PLACEHOLDER = re.compile(r"\[(PERSON|EMAIL|PHONE|ADDRESS)_\d+\]")
BAR_MM = {"PERSON": 24, "EMAIL": 34, "PHONE": 26, "ADDRESS": 42}
LATIN = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-",
                       "…": "...", " ": " ", "•": "*", "­": ""})


def _latin(text: str) -> str:
    """The built-in PDF fonts cover Latin-1; map typographic characters and drop anything else."""
    text = re.sub(r"[ \t]{2,}", " ", text.translate(LATIN))  # source text pads with runs of spaces
    return text.encode("latin-1", "replace").decode("latin-1")


class _Doc(FPDF):
    def __init__(self, title: str, stamp: str):
        super().__init__(format="Letter")
        self.title_text, self.stamp = title, stamp
        self.set_margins(22, 20, 22)
        self.set_auto_page_break(True, 20)

    def header(self) -> None:
        self.set_font("Helvetica", size=7.5)
        self.set_text_color(110)
        self.cell(0, 4, _latin(self.title_text[:110]), new_x="LMARGIN", new_y="NEXT")
        self.ln(4)
        self.set_text_color(0)

    def footer(self) -> None:
        self.set_y(-14)
        self.set_font("Helvetica", size=7.5)
        self.set_text_color(110)
        self.cell(0, 4, _latin(self.stamp), align="L")
        self.cell(0, 4, f"{self.page_no()}/{{nb}}", align="R")


def render(name: str, user: str, masked: bool, texts: list[str]) -> bytes:
    when = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    note = "personal data redacted for this role" if masked else "includes personal data"
    pdf = _Doc(name.replace("_", " "), f"Contract Radar · exported by {user} on {when} · {note}")
    pdf.alias_nb_pages()
    pdf.add_page()
    pdf.set_font("Times", size=10.5)
    line_h = 5.0
    right = pdf.w - pdf.r_margin
    pdf.set_fill_color(18, 20, 23)
    for text in texts:
        pos = 0
        for m in PLACEHOLDER.finditer(text):
            if m.start() > pos:
                pdf.write(line_h, _latin(text[pos:m.start()]))
            w = BAR_MM[m.group(1)]
            if pdf.get_x() + w > right:
                pdf.ln(line_h)
            pdf.cell(w, line_h * 0.82, "", fill=True)
            pdf.set_x(pdf.get_x() + 0.8)
            pos = m.end()
        if pos < len(text):
            pdf.write(line_h, _latin(text[pos:]))
    return bytes(pdf.output())
