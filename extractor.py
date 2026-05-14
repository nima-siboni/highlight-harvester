"""PDF highlight extraction pipeline.

Given PDF bytes and a 1-based inclusive page range, returns highlights grouped
under the source document's heading hierarchy. Emits markdown (for preview) and
DOCX (for download).
"""

from __future__ import annotations

import io
import re
from collections import Counter
from dataclasses import dataclass, field

import fitz
from docx import Document
from docx.shared import Pt


BBox = tuple[float, float, float, float]


@dataclass
class HighlightRecord:
    id: str
    page_index: int
    annot_index: int
    quad_bboxes: list[BBox]
    union_bbox: BBox
    normalized_text: str
    status: str  # ok | empty


@dataclass
class HeadingRecord:
    id: str
    level: int
    number: str
    title: str
    page_index: int
    y_top: float
    source: str  # outline | regex


@dataclass
class GroupedHighlight:
    heading_path: list[HeadingRecord]
    paragraph_key: str
    merged_text: str
    page_index: int
    fragments: list[HighlightRecord] = field(default_factory=list)


# --- geometry -----------------------------------------------------------------


def overlap_ratio(a: BBox, b: BBox) -> float:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    inter = (ix1 - ix0) * (iy1 - iy0)
    area = max((ax1 - ax0) * (ay1 - ay0), 1e-6)
    return inter / area


def union_bbox(bboxes: list[BBox]) -> BBox:
    xs0 = [b[0] for b in bboxes]
    ys0 = [b[1] for b in bboxes]
    xs1 = [b[2] for b in bboxes]
    ys1 = [b[3] for b in bboxes]
    return (min(xs0), min(ys0), max(xs1), max(ys1))


def annotation_quad_bboxes(annot) -> list[BBox]:
    bboxes: list[BBox] = []
    vertices = getattr(annot, "vertices", None)
    if vertices:
        for i in range(0, len(vertices), 4):
            pts = vertices[i : i + 4]
            if len(pts) < 4:
                break
            xs = [p[0] if isinstance(p, tuple) else p.x for p in pts]
            ys = [p[1] if isinstance(p, tuple) else p.y for p in pts]
            bboxes.append((min(xs), min(ys), max(xs), max(ys)))
    if not bboxes:
        try:
            quads = annot.quads()
        except Exception:
            quads = None
        if quads:
            for q in quads:
                r = q.rect
                bboxes.append((r.x0, r.y0, r.x1, r.y1))
    if not bboxes:
        r = annot.rect
        bboxes.append((r.x0, r.y0, r.x1, r.y1))
    return sorted(bboxes, key=lambda b: (round(b[1], 1), b[0]))


# --- text reconstruction ------------------------------------------------------


def words_in_highlight(page, quad_bboxes: list[BBox], min_overlap: float = 0.15):
    # PyMuPDF tuple: (x0, y0, x1, y1, word, block_no, line_no, word_no)
    words = page.get_text("words")
    selected = []
    for w in words:
        wb = (w[0], w[1], w[2], w[3])
        if any(overlap_ratio(wb, hb) >= min_overlap for hb in quad_bboxes):
            selected.append(w)
    selected.sort(key=lambda w: (w[5], w[6], w[7], w[1], w[0]))
    return selected


def reconstruct_text(words) -> str:
    if not words:
        return ""
    lines = []
    current_key = None
    current = []
    for w in words:
        key = (w[5], w[6])
        if key != current_key and current:
            lines.append(" ".join(x[4] for x in sorted(current, key=lambda z: z[0])))
            current = []
        current_key = key
        current.append(w)
    if current:
        lines.append(" ".join(x[4] for x in sorted(current, key=lambda z: z[0])))
    return normalize_text("\n".join(lines))


def normalize_text(text: str) -> str:
    text = text.replace("­", "")
    text = text.replace("ﬁ", "fi").replace("ﬂ", "fl")
    text = re.sub(r"-\s*\n\s*", "", text)
    text = re.sub(r"\s*\n\s*", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


# --- highlight extraction -----------------------------------------------------


def iter_highlight_annotations(doc, start_idx: int, end_idx: int):
    for page_index in range(start_idx, end_idx + 1):
        page = doc.load_page(page_index)
        annot = page.first_annot
        annot_index = 0
        while annot:
            try:
                kind = annot.type[1]
            except Exception:
                kind = ""
            if kind == "Highlight":
                yield page_index, page, annot_index, annot
            annot = annot.next
            annot_index += 1


def extract_highlight_records(doc, start_idx: int, end_idx: int):
    """Yields (HighlightRecord, selected_words) tuples."""
    for page_index, page, annot_index, annot in iter_highlight_annotations(doc, start_idx, end_idx):
        quads = annotation_quad_bboxes(annot)
        words = words_in_highlight(page, quads)
        text = reconstruct_text(words)
        rec = HighlightRecord(
            id=f"p{page_index}-a{annot_index}",
            page_index=page_index,
            annot_index=annot_index,
            quad_bboxes=quads,
            union_bbox=union_bbox(quads),
            normalized_text=text,
            status="ok" if text else "empty",
        )
        yield rec, words


# --- headings -----------------------------------------------------------------


_NUMBERED_HEADING = re.compile(r"^([1-9]\d?(?:\.\d+){0,4})\s+(.{3,})$")


def build_heading_timeline(doc) -> list[HeadingRecord]:
    headings: list[HeadingRecord] = []
    seen: set[tuple[int, str]] = set()  # (page_index, title_lower) dedupe

    # 1) PDF outline
    try:
        toc = doc.get_toc(simple=False)
    except Exception:
        toc = []
    for i, entry in enumerate(toc):
        level, title, page = entry[0], entry[1], entry[2]
        page_index = max(0, page - 1)
        # try to pull number prefix from title
        m = _NUMBERED_HEADING.match(title.strip())
        if m:
            number, clean = m.group(1), m.group(2).strip()
        else:
            number, clean = "", title.strip()
        h = HeadingRecord(
            id=f"o{i}",
            level=level,
            number=number,
            title=clean,
            page_index=page_index,
            y_top=0.0,
            source="outline",
        )
        headings.append(h)
        seen.add((page_index, clean.lower()))

    # 2) Body-text regex sweep — fills in y-positions and catches headings the
    # outline missed. Skip pages with garbage word counts to keep it cheap.
    for page_index in range(doc.page_count):
        try:
            page = doc.load_page(page_index)
        except Exception:
            continue
        blocks = page.get_text("blocks")
        for b in blocks:
            x0, y0, x1, y1, text = b[0], b[1], b[2], b[3], b[4]
            for line in text.splitlines():
                line = line.strip()
                if not line or len(line) > 200:
                    continue
                m = _NUMBERED_HEADING.match(line)
                if not m:
                    continue
                number, title = m.group(1), m.group(2).strip()
                # filter: skip TOC continuation lines (dots + page num)
                if re.search(r"\.{2,}\s*\d+$", title):
                    continue
                key = (page_index, title.lower())
                if key in seen:
                    # If matching outline entry has no y_top, enrich it
                    for h in headings:
                        if h.page_index == page_index and h.title.lower() == title.lower() and h.y_top == 0.0:
                            h.y_top = y0
                            break
                    continue
                seen.add(key)
                headings.append(
                    HeadingRecord(
                        id=f"r{page_index}-{len(headings)}",
                        level=number.count(".") + 1,
                        number=number,
                        title=title,
                        page_index=page_index,
                        y_top=y0,
                        source="regex",
                    )
                )

    headings.sort(key=lambda h: (h.page_index, h.y_top, h.level))
    return headings


def assign_heading_path(highlight: HighlightRecord, headings: list[HeadingRecord]) -> list[HeadingRecord]:
    h_key = (highlight.page_index, highlight.union_bbox[1])
    active: dict[int, HeadingRecord] = {}
    for heading in headings:
        if (heading.page_index, heading.y_top) > h_key:
            break
        # clear deeper levels
        for lvl in list(active):
            if lvl >= heading.level:
                del active[lvl]
        active[heading.level] = heading
    return [active[lvl] for lvl in sorted(active)]


# --- grouping -----------------------------------------------------------------


def paragraph_key_for_highlight(highlight: HighlightRecord, selected_words, heading_path: list[HeadingRecord]) -> str:
    leaf = heading_path[-1].id if heading_path else "_root"
    if selected_words:
        block_no = Counter(w[5] for w in selected_words).most_common(1)[0][0]
        return f"heading={leaf}|page={highlight.page_index}|block={block_no}"
    y_bin = round(highlight.union_bbox[1] / 36)
    return f"heading={leaf}|page={highlight.page_index}|ybin={y_bin}"


def merge_fragments(fragments: list[HighlightRecord]) -> str:
    ordered = sorted(
        fragments,
        key=lambda h: (h.page_index, h.union_bbox[1], h.union_bbox[0], h.annot_index),
    )
    cleaned: list[str] = []
    for h in ordered:
        text = h.normalized_text.strip()
        if not text:
            continue
        if any(text == prior or text in prior for prior in cleaned):
            continue
        cleaned.append(text)
    return " ... ".join(cleaned)


# --- top-level pipeline -------------------------------------------------------


@dataclass
class ExtractionResult:
    grouped: list[GroupedHighlight]
    total_annotations: int
    empty_highlights: int
    headings_total: int


def extract(pdf_bytes: bytes, start_page: int, end_page: int) -> ExtractionResult:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        n = doc.page_count
        start_idx = max(0, start_page - 1)
        end_idx = min(n - 1, end_page - 1)
        if start_idx > end_idx:
            return ExtractionResult([], 0, 0, 0)

        headings = build_heading_timeline(doc)

        records_with_words = list(extract_highlight_records(doc, start_idx, end_idx))

        buckets: dict[tuple[tuple[str, ...], str], list[HighlightRecord]] = {}
        path_lookup: dict[tuple[str, ...], list[HeadingRecord]] = {}

        for rec, words in records_with_words:
            path = assign_heading_path(rec, headings)
            key = paragraph_key_for_highlight(rec, words, path)
            path_ids = tuple(h.id for h in path)
            buckets.setdefault((path_ids, key), []).append(rec)
            path_lookup[path_ids] = path

        grouped: list[GroupedHighlight] = []
        for (path_ids, key), frags in buckets.items():
            merged = merge_fragments(frags)
            if not merged:
                continue
            grouped.append(
                GroupedHighlight(
                    heading_path=path_lookup[path_ids],
                    paragraph_key=key,
                    merged_text=merged,
                    page_index=min(f.page_index for f in frags),
                    fragments=frags,
                )
            )

        grouped.sort(key=lambda g: (g.page_index, g.fragments[0].union_bbox[1]))

        total = len(records_with_words)
        empty = sum(1 for r, _ in records_with_words if r.status == "empty")
        return ExtractionResult(
            grouped=grouped,
            total_annotations=total,
            empty_highlights=empty,
            headings_total=len(headings),
        )
    finally:
        doc.close()


# --- output formats -----------------------------------------------------------


def to_markdown_preview(result: ExtractionResult) -> str:
    if not result.grouped:
        return "_No highlights found in this page range._"
    lines: list[str] = []
    last_ids: list[str] = []
    for group in result.grouped:
        path = group.heading_path
        common = 0
        while common < min(len(path), len(last_ids)) and path[common].id == last_ids[common]:
            common += 1
        for h in path[common:]:
            level_marker = "#" * min(max(h.level, 1), 6)
            prefix = f"{h.number} " if h.number else ""
            lines.append(f"\n{level_marker} {prefix}{h.title}\n")
        lines.append(group.merged_text + "\n")
        last_ids = [h.id for h in path]
    return "\n".join(lines)


def to_docx_bytes(result: ExtractionResult, title: str) -> bytes:
    doc = Document()
    doc.add_heading(title, 0)

    last_ids: list[str] = []
    for group in result.grouped:
        path = group.heading_path
        common = 0
        while common < min(len(path), len(last_ids)) and path[common].id == last_ids[common]:
            common += 1
        for h in path[common:]:
            heading_text = f"{h.number} {h.title}" if h.number else h.title
            doc.add_heading(heading_text, level=min(h.level, 4))
        p = doc.add_paragraph(group.merged_text)
        p.paragraph_format.space_after = Pt(6)
        last_ids = [h.id for h in path]

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()
