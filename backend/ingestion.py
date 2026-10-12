"""Unified native + selected-model vision extraction.

Output is ordered page/slide/block evidence for indexing and audit. Native
transcription is never overridden by uncertain model-generated descriptions.
"""
from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

import fitz
from docx import Document as WordDocument
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph
from PIL import Image
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE

from backend.vision import VisionSession
from backend.office_renderer import OfficeRenderError, convert_pptx_to_pdf

MAX_PAGES = 300
MAX_TEXT = 700_000


@dataclass
class Unit:
    page: int | None
    section: str | None
    content: str
    native: bool = False
    visual: bool = False
    issue: str | None = None


@dataclass
class Extraction:
    units: list[Unit]
    pages: int
    warnings: list[str]

    @property
    def segments(self) -> list[tuple[int | None, str | None, str]]:
        return [(unit.page, unit.section, unit.content) for unit in self.units if unit.content.strip()]


def _combine(native: str, visual: str | None) -> str:
    return ("Native document content:\n" + native + "\n\n" if native else "") + (
        "Visual analysis (model-interpreted; check source image for uncertain labels):\n" + visual if visual else ""
    )


def _warn(unit: Unit, vision: VisionSession | None, *, renderer: bool = True, render_issue: str | None = None) -> Unit:
    if not unit.visual:
        unit.issue = ((vision.warning() if vision else None) or
                      (render_issue if not renderer and render_issue else
                       "Slide rendering was unavailable; check LibreOffice on the backend computer." if not renderer else
                       "Visual material was not interpreted; select a vision-capable model and reprocess."))
    return unit


def _render_office(source: Path, output: Path) -> Path:
    """Convert the entire presentation to a page-aligned PDF using LibreOffice."""
    return convert_pptx_to_pdf(source, output)


def _render_page(page: fitz.Page) -> bytes:
    # capped ~1500px for slides; the vision layer also enforces 30 MP
    factor = min(2.1, 1600 / max(page.rect.width, page.rect.height, 1))
    return page.get_pixmap(matrix=fitz.Matrix(factor, factor), alpha=False).tobytes("png")


def _slides_text(slide) -> str:
    parts: list[str] = []
    def visit(shape):
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            for child in shape.shapes:
                visit(child)
            return
        if getattr(shape, "has_text_frame", False) and shape.text_frame.text.strip():
            parts.append(shape.text_frame.text.strip())
        if getattr(shape, "has_table", False):
            for row in shape.table.rows:
                parts.append(" | ".join(cell.text.strip() for cell in row.cells))
        if getattr(shape, "has_chart", False):
            chart = shape.chart
            if chart.has_title:
                parts.append("Chart: " + chart.chart_title.text_frame.text)
            for label, attr in (("Category axis", "category_axis"), ("Value axis", "value_axis")):
                try:
                    axis = getattr(chart, attr)
                    if axis.has_title:
                        parts.append(f"{label}: {axis.axis_title.text_frame.text}")
                except (AttributeError, ValueError):
                    pass
            try:
                categories = [str(c.label) for c in chart.plots[0].categories]
            except (ValueError, AttributeError, TypeError, IndexError):
                categories = []
            for series in chart.series:
                parts.append("Chart series " + str(series.name) + ": " + ", ".join(
                    f"{categories[i]}: {v}" if i < len(categories) else str(v)
                    for i, v in enumerate(series.values[:100])))
    for shape in slide.shapes:
        visit(shape)
    if slide.has_notes_slide and slide.notes_slide.notes_text_frame is not None:
        notes = slide.notes_slide.notes_text_frame.text.strip()
        if notes:
            parts.append("Speaker notes (may contain teacher commentary or answer key):\n" + notes)
    return "\n".join(parts)


def _pptx(path: Path, vision: VisionSession, warnings: list[str]) -> tuple[list[Unit], int]:
    pres = Presentation(str(path))
    count = len(pres.slides)
    if count > MAX_PAGES:
        raise ValueError("PowerPoint exceeds the 300-slide limit")
    native = [_slides_text(slide) for slide in pres.slides]
    image_pdf = None
    temp = tempfile.TemporaryDirectory(prefix="questgen-ppt-")
    try:
        render_issue = None
        if vision.enabled:
            try:
                rendered = _render_office(path, Path(temp.name))
                if rendered:
                    image_pdf = fitz.open(str(rendered))
                    if len(image_pdf) != count:
                        warnings.append(f"LibreOffice rendered {len(image_pdf)} of {count} slides; uncovered slides cannot be visually analyzed.")
                else:
                    render_issue = "LibreOffice did not return a slide PDF. Check installation on the backend computer."
            except OfficeRenderError as exc:
                render_issue = str(exc)
            except (fitz.FileDataError, RuntimeError):
                render_issue = "LibreOffice produced an unreadable slide PDF. Open the presentation in LibreOffice and reprocess."
            if render_issue:
                warnings.append(render_issue)
        units = []
        for index, text in enumerate(native):
            description = None
            if image_pdf is not None and index < len(image_pdf):
                description = vision.analyze(_render_page(image_pdf[index]), text, f"slide {index + 1}")
            unit = Unit(index + 1, None, _combine(text, description), bool(text), bool(description))
            if not description:
                _warn(unit, vision, renderer=not vision.enabled or image_pdf is not None, render_issue=render_issue)
            units.append(unit)
        missing = [str(unit.page) for unit in units if not unit.visual]
        if missing and vision.enabled and image_pdf is not None:
            preview = ", ".join(missing[:20]) + (f" and {len(missing)-20} more" if len(missing) > 20 else "")
            warnings.append(
                f"Visual coverage: {count - len(missing)}/{count} slides. Missing slide(s): {preview}. "
                "Use Reprocess to retry; previously successful vision results are cached."
            )
        return units, count
    finally:
        if image_pdf:
            image_pdf.close()
        temp.cleanup()


def _pdf(path: Path, vision: VisionSession, warnings: list[str]) -> tuple[list[Unit], int]:
    try:
        source = fitz.open(str(path))
    except (fitz.FileDataError, RuntimeError) as exc:
        raise ValueError("Invalid or unreadable PDF") from exc
    with source:
        if source.needs_pass:
            raise ValueError("Encrypted PDFs are not supported")
        if source.page_count > MAX_PAGES:
            raise ValueError("PDF exceeds the 300-page limit")
        units = []
        for number, page in enumerate(source, 1):
            text = page.get_text("text", sort=True).strip()
            # Diagrams / scanned text: image objects and abundant vector drawings.
            needs_visual = len(text) < 80 or bool(page.get_images(full=True)) or len(page.get_drawings()) > 12
            description = None
            if needs_visual and vision.enabled:
                description = vision.analyze(_render_page(page), text, f"page {number}")
            unit = Unit(number, None, _combine(text, description), bool(text), bool(description))
            if needs_visual and not description:
                _warn(unit, vision)
            units.append(unit)
    return units, len(units)


def _docx(path: Path, vision: VisionSession, warnings: list[str]) -> tuple[list[Unit], int]:
    doc = WordDocument(str(path))
    section: str | None = None
    units: list[Unit] = []
    seen_images: set[str] = set()
    # Reading order: docx body iter_children rather than paragraphs followed by tables.
    for element in doc.element.body.iterchildren():
        if element.tag == qn("w:p"):
            para = Paragraph(element, doc)
            text = para.text.strip()
            if para.style and para.style.name.lower().startswith("heading") and text:
                section = text[:160]
            if text:
                units.append(Unit(None, section, text, True))
        elif element.tag == qn("w:tbl"):
            table = Table(element, doc)
            rows = [" | ".join(cell.text.strip() for cell in row.cells) for row in table.rows]
            content = "\n".join(rows).strip()
            if content:
                units.append(Unit(None, section, "Table:\n" + content, True))
        # Descriptive vision of embedded figures, with safe uniqueness and source
        # presence; DOCX relationship binary is not trusted as instructions.
        for node in element.iter():
            if node.tag != qn("a:blip"):
                continue
            rel = node.get(qn("r:embed"))
            if rel in seen_images or not rel or rel not in doc.part.related_parts:
                continue
            seen_images.add(rel)
            data = doc.part.related_parts[rel].blob
            try:
                with Image.open(BytesIO(data)) as image:
                    image.verify()
                description = vision.analyze(data, "", f"image {len(seen_images)} in {section or 'document'}")
            except (OSError, ValueError):
                description = None
            unit = Unit(None, section, _combine("", description), False, bool(description))
            if not description:
                _warn(unit, vision)
            units.append(unit)
    return units, 0


def _markdown(path: Path) -> tuple[list[Unit], int]:
    text = path.read_text(encoding="utf-8-sig")
    lines, section, buf, units = text.splitlines(), None, [], []
    fence = None
    def flush():
        if buf:
            units.append(Unit(None, section, "\n".join(buf).strip(), True))
            buf.clear()
    for line in lines:
        code = re.match(r"^\s*(`{3,}|~{3,})", line)
        if code:
            marker = code.group(1)[0]
            if fence is None:
                fence = marker
            elif fence == marker:
                fence = None
        if fence is None and not code and (match := re.match(r"^(#{1,6})\s+(.+)", line)):
            flush()
            section = match.group(2).strip()[:160]
            buf.append(line)
        else:
            buf.append(line)
    flush()
    return units, 0


def extract(path: Path, extension: str, document_sha: str) -> Extraction:
    warnings: list[str] = []
    vision = VisionSession(document_sha)
    if extension == ".pptx":
        units, pages = _pptx(path, vision, warnings)
    elif extension == ".pdf":
        units, pages = _pdf(path, vision, warnings)
    elif extension == ".docx":
        units, pages = _docx(path, vision, warnings)
    elif extension == ".md":
        units, pages = _markdown(path)
    elif extension == ".txt":
        units, pages = [Unit(None, None, path.read_text(encoding="utf-8-sig").strip(), True)], 0
    else:
        # Standalone images rely on the selected vision-capable model, not OCR.
        visual = vision.analyze(path.read_bytes(), "", "uploaded image")
        unit = Unit(None, None, _combine("", visual), False, bool(visual))
        if not visual:
            _warn(unit, vision)
        units, pages = [unit], 0
    if not units or not any(unit.content.strip() for unit in units):
        issue = vision.warning() or (warnings[0] if warnings else "No readable source content")
        raise ValueError(f"Document has no readable content. {issue}")
    size = sum(len(unit.content) for unit in units)
    if size > MAX_TEXT:
        raise ValueError("Extracted content exceeds the 700,000-character limit")
    for unit in units:
        if unit.issue and unit.issue not in warnings:
            warnings.append(unit.issue)
    return Extraction(units, pages, warnings)
