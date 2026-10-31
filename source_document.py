"""Read and render the supplied source without modifying it."""
from pathlib import Path

import pymupdf
import streamlit as st

SOURCE = Path(__file__).parent / "Northbridge_Broker_Submission.pdf"


def read_source(path: str | Path = SOURCE) -> bytes:
    path = Path(path)
    if not path.is_absolute():
        path = Path(__file__).parent / path
    return path.read_bytes()


@st.cache_data(max_entries=2, show_spinner=False)
def render_pages(pdf_bytes: bytes) -> list[bytes]:
    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as document:
        return [page.get_pixmap(matrix=pymupdf.Matrix(1.7, 1.7)).tobytes("png")
                for page in document]
