"""Streamlit UI: upload a highlighted PDF, pick a page range, preview and download."""

import hashlib
import html

import fitz
import streamlit as st

import extractor


st.set_page_config(
    page_title="PDF Highlight Extractor",
    page_icon="📑",
    layout="wide",
    initial_sidebar_state="expanded",
)


CUSTOM_CSS = """
<style>
  :root {
    --paper: #fbfaf7;
    --ink: #1f2933;
    --muted: #6b7280;
    --accent: #d97706;
    --rule: #e5e3dd;
  }
  html, body, [class*="css"] {
    font-family: -apple-system, BlinkMacSystemFont, "Inter", "Segoe UI", sans-serif;
  }
  .app-hero {
    padding: 0.5rem 0 1.5rem 0;
    border-bottom: 1px solid var(--rule);
    margin-bottom: 1.5rem;
  }
  .app-hero h1 {
    font-size: 2.1rem;
    font-weight: 700;
    margin: 0;
    letter-spacing: -0.02em;
    color: var(--ink);
  }
  .app-hero p {
    color: var(--muted);
    margin: 0.35rem 0 0 0;
    font-size: 0.95rem;
  }
  .empty-state {
    text-align: center;
    padding: 4rem 2rem;
    border: 1px dashed var(--rule);
    border-radius: 12px;
    color: var(--muted);
    background: #fafaf9;
  }
  .empty-state .emoji { font-size: 2.4rem; display:block; margin-bottom: 0.6rem; }
  .doc-paper {
    background: var(--paper);
    border: 1px solid var(--rule);
    border-radius: 8px;
    padding: 3rem 3.5rem;
    box-shadow: 0 1px 2px rgba(0,0,0,0.04), 0 8px 24px rgba(0,0,0,0.04);
    font-family: "Iowan Old Style", "Charter", "Georgia", serif;
    color: var(--ink);
    line-height: 1.65;
    max-width: 820px;
    margin: 0 auto;
  }
  .doc-paper h1, .doc-paper h2, .doc-paper h3, .doc-paper h4 {
    font-family: -apple-system, BlinkMacSystemFont, "Inter", "Segoe UI", sans-serif;
    color: var(--ink);
    letter-spacing: -0.01em;
  }
  .doc-paper h1 {
    font-size: 1.6rem;
    border-bottom: 2px solid var(--accent);
    padding-bottom: 0.4rem;
    margin-top: 2rem;
  }
  .doc-paper h2 {
    font-size: 1.25rem;
    color: var(--accent);
    margin-top: 1.8rem;
  }
  .doc-paper h3 {
    font-size: 1.05rem;
    margin-top: 1.4rem;
    color: #374151;
  }
  .doc-paper p {
    margin: 0.7rem 0;
  }
  .doc-paper p:first-child { margin-top: 0; }
  .stat-row { display:flex; gap: 0.75rem; flex-wrap: wrap; margin-bottom: 1.25rem; }
  .stat {
    flex: 1 1 0;
    min-width: 130px;
    background: #ffffff;
    border: 1px solid var(--rule);
    border-radius: 10px;
    padding: 0.85rem 1rem;
  }
  .stat .label { color: var(--muted); font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.04em; }
  .stat .value { color: var(--ink); font-size: 1.5rem; font-weight: 600; line-height: 1.2; margin-top: 0.15rem; }
  .stat.warn .value { color: var(--accent); }
  div[data-testid="stSidebar"] { border-right: 1px solid var(--rule); }
  div[data-testid="stSidebar"] h2 { font-size: 1rem; }
  .filename {
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 0.8rem;
    color: var(--muted);
    background: #f4f3ef;
    padding: 0.25rem 0.5rem;
    border-radius: 4px;
    display: inline-block;
    margin-top: 0.4rem;
    word-break: break-all;
  }
</style>
"""

st.markdown(CUSTOM_CSS, unsafe_allow_html=True)

st.markdown(
    """
    <div class="app-hero">
      <h1>📑 PDF Highlight Extractor</h1>
      <p>Pull highlight annotations out of an annotated PDF, regrouped under the source document's chapters and sections.</p>
    </div>
    """,
    unsafe_allow_html=True,
)


with st.sidebar:
    st.markdown("## Source PDF")
    uploaded = st.file_uploader("PDF file", type="pdf", label_visibility="collapsed")

    if uploaded:
        pdf_bytes = uploaded.getvalue()
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        n_pages = doc.page_count
        doc.close()

        st.markdown(
            f'<span class="filename">{html.escape(uploaded.name)}</span>'
            f'<div style="color:#6b7280; font-size:0.8rem; margin-top:0.4rem;">{n_pages} pages</div>',
            unsafe_allow_html=True,
        )

        st.markdown("## Page range")
        col1, col2 = st.columns(2)
        start = col1.number_input("From", min_value=1, max_value=n_pages, value=1, step=1)
        end = col2.number_input("To", min_value=1, max_value=n_pages, value=n_pages, step=1)

        if start > end:
            st.error("`From` must be ≤ `To`.")
            extract_clicked = False
        else:
            st.markdown("")
            extract_clicked = st.button("Extract highlights", type="primary", use_container_width=True)
    else:
        pdf_bytes = None
        extract_clicked = False
        start = end = 1


if not uploaded:
    st.markdown(
        """
        <div class="empty-state">
          <span class="emoji">⬅️</span>
          <div><strong>Upload a PDF in the sidebar to begin.</strong></div>
          <div style="margin-top: 0.4rem; font-size: 0.85rem;">Works best with digitally generated PDFs containing highlight annotations.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.stop()


@st.cache_data(show_spinner=False)
def _run(pdf_hash: str, pdf_bytes: bytes, start: int, end: int, title: str):
    result = extractor.extract(pdf_bytes, start, end)
    md = extractor.to_markdown_preview(result)
    docx = extractor.to_docx_bytes(result, title=title)
    return result, md, docx


if not extract_clicked:
    st.markdown(
        f"""
        <div class="empty-state">
          <span class="emoji">📑</span>
          <div><strong>Ready to extract.</strong></div>
          <div style="margin-top:0.4rem; font-size:0.85rem;">Pages {start}–{end} selected. Click <em>Extract highlights</em> in the sidebar.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.stop()


pdf_hash = hashlib.sha1(pdf_bytes).hexdigest()
title = uploaded.name.rsplit(".", 1)[0]
with st.spinner("Extracting highlights…"):
    result, md, docx_bytes = _run(pdf_hash, pdf_bytes, int(start), int(end), title)


def _stat(label: str, value, warn: bool = False) -> str:
    cls = "stat warn" if warn else "stat"
    return f'<div class="{cls}"><div class="label">{label}</div><div class="value">{value}</div></div>'


stats_html = '<div class="stat-row">' + "".join([
    _stat("Annotations", result.total_annotations),
    _stat("Grouped paragraphs", len(result.grouped)),
    _stat("Headings detected", result.headings_total),
    _stat("Empty highlights", result.empty_highlights, warn=result.empty_highlights > 0),
]) + '</div>'
st.markdown(stats_html, unsafe_allow_html=True)


out_name = title + "_highlights.docx"
dl_col, _ = st.columns([1, 3])
with dl_col:
    st.download_button(
        label=f"⬇  Download {out_name}",
        data=docx_bytes,
        file_name=out_name,
        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        use_container_width=True,
    )

st.write("")


def _md_to_html(md_text: str) -> str:
    """Tiny markdown → HTML converter for the preview card. Handles only what
    `to_markdown_preview` emits: ATX headings (#..######) and paragraph lines."""
    out: list[str] = []
    for raw in md_text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            level = 0
            while level < len(line) and line[level] == "#":
                level += 1
            level = min(max(level, 1), 6)
            text = html.escape(line[level:].strip())
            out.append(f"<h{level}>{text}</h{level}>")
        elif line.startswith("_") and line.endswith("_"):
            out.append(f'<p style="color:#6b7280; font-style:italic;">{html.escape(line.strip("_"))}</p>')
        else:
            out.append(f"<p>{html.escape(line)}</p>")
    return "\n".join(out)


st.markdown(
    f'<div class="doc-paper">{_md_to_html(md)}</div>',
    unsafe_allow_html=True,
)
