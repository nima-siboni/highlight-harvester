# Highlight Harvester

Turn an annotated PDF into a structured Word document.

Upload a PDF that has highlight annotations, pick a page range, and download a `.docx` where each highlighted passage sits under the chapter, section, or subsection it came from. Highlights that originally belonged to the same paragraph are merged together with `...`, so the result reads like organized study notes rather than a raw annotation dump.

## What it does

- Extracts every PDF highlight annotation using its precise quadrilateral geometry — not a sloppy bounding-box that picks up extra lines.
- Rebuilds the source document's heading hierarchy from the PDF outline, falling back to numeric heading detection in the body text (works with patterns like `1`, `1.1`, `1.1.1`).
- Assigns each highlight to the deepest heading active at the highlight's position in the document.
- Coalesces fragments inside the same source paragraph into a single passage joined by an ellipsis.
- Normalizes common PDF artifacts: soft hyphens, line-break hyphenation (`Inter-\ngruppen` → `Intergruppen`), ligatures (`ﬁ`, `ﬂ`), and stray whitespace.
- Writes a `.docx` with native Word heading styles (Heading 1/2/3), so the result is navigable in Word's outline pane.

## Run locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

Opens at <http://localhost:8501>.

Python 3.10+ is required. Dependencies: [PyMuPDF](https://pymupdf.readthedocs.io) for PDF parsing, [python-docx](https://python-docx.readthedocs.io) for the Word output, [Streamlit](https://streamlit.io) for the UI.

## How it works

The pipeline is one module (`extractor.py`) and runs entirely in memory. No file is written to disk; the uploaded PDF lives in a `BytesIO` buffer for the duration of the session and disappears when the tab closes.

1. **Annotation enumeration** — walk every page's annotation chain, keep `Highlight` types.
2. **Geometry extraction** — read the annotation's quadpoints (line-level rectangles), fall back to `quads()` and then `rect` if the PDF stores them differently.
3. **Word-level filter** — pull all words on the page with `page.get_text("words")`, keep those whose bounding box overlaps the highlight geometry by at least 15%. This avoids grabbing the line above or below.
4. **Text reconstruction** — group surviving words by `(block_no, line_no)`, sort within each line by x, normalize the joined text.
5. **Heading timeline** — combine `doc.get_toc()` entries with regex-detected numeric headings in the body text, sorted by `(page, y, level)`.
6. **Heading assignment** — for each highlight, walk the heading list in document order, maintaining a stack of currently-active levels; the deepest active heading wins.
7. **Paragraph grouping** — use the dominant `block_no` of the highlight's words as a paragraph key; fragments sharing the same key merge with ` ... `.
8. **DOCX emit** — write semantic Word headings only when they change between consecutive groups; merged text goes into normal paragraphs.

## Limitations

- Designed for **digitally generated, searchable PDFs**. Scanned-image PDFs typically have no text layer; highlights on them will produce empty records.
- Heading detection is tuned for **numeric heading schemes** (`1`, `1.1`, `1.1.1`). Books with decorative or non-numeric headings will fall back to whatever the PDF outline provides.
- Highlights over images or tables can't recover text and are reported as empty; there's no OCR fallback in this build.

## Deploy

This repo is laid out for [Streamlit Community Cloud](https://share.streamlit.io): point the deploy form at `app.py` on the `main` branch and it works out of the box. Theme configuration lives in `.streamlit/config.toml`.

## Project layout

```
app.py                  # Streamlit UI: sidebar (upload + page range), main pane (preview + download)
extractor.py            # Pipeline: annotations -> text -> headings -> grouped paragraphs -> DOCX
requirements.txt
.streamlit/config.toml  # Amber primary color and warm background
```
