"""Render the editable synthetic Markdown runbooks as searchable PDFs."""

from __future__ import annotations

import html
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

ROOT = Path(__file__).resolve().parents[1]
RUNBOOK_DIR = ROOT / "data" / "runbooks"


def render(markdown: Path) -> Path:
    output = markdown.with_suffix(".pdf")
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="RunbookTitle", parent=styles["Title"], alignment=TA_LEFT,
                              textColor=colors.HexColor("#163a5f"), spaceAfter=14))
    styles.add(ParagraphStyle(name="RunbookH2", parent=styles["Heading2"],
                              textColor=colors.HexColor("#176b63"), spaceBefore=10, spaceAfter=5))
    styles.add(ParagraphStyle(name="RunbookBody", parent=styles["BodyText"],
                              leading=15, spaceAfter=6))
    story = []
    for raw in markdown.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            story.append(Spacer(1, 3))
        elif line.startswith("# "):
            story.append(Paragraph(html.escape(line[2:]), styles["RunbookTitle"]))
        elif line.startswith("## "):
            story.append(Paragraph(html.escape(line[3:]), styles["RunbookH2"]))
        elif line.startswith("- "):
            story.append(Paragraph("&#8226; " + html.escape(line[2:]), styles["RunbookBody"]))
        else:
            story.append(Paragraph(html.escape(line.replace("  ", " ")), styles["RunbookBody"]))
    doc = SimpleDocTemplate(str(output), pagesize=letter, rightMargin=0.8 * inch,
                            leftMargin=0.8 * inch, topMargin=0.7 * inch, bottomMargin=0.7 * inch,
                            title=markdown.stem.replace("_", " ").title(),
                            author="Synthetic Operations Project")
    doc.build(story)
    return output


if __name__ == "__main__":
    for path in sorted(RUNBOOK_DIR.glob("*.md")):
        print(render(path).relative_to(ROOT))
