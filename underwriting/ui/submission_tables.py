"""Escaped, content-sized Submission tables and narrowly scoped presentation CSS."""
from html import escape
from pathlib import Path

import streamlit as st


def apply_styles():
    st.html(Path(__file__).parent / "styles" / "submission.css")


def table_html(headers, rows, *, label, kind="fields"):
    """Only markup is trusted; every header, label and cell is HTML-escaped."""
    widths = {"fields": [26, 56, 18], "checks": [24, 76], "losses": [13, 29, 14, 14, 30]}
    if kind not in widths or len(headers) != len(widths[kind]):
        raise ValueError("Invalid Submission table definition.")
    def text(value):
        return escape("—" if value is None else str(value), quote=True)
    columns = "".join(f'<col style="width:{width}%">' for width in widths[kind])
    heading = "".join(f'<th scope="col">{text(header)}</th>' for header in headers)
    body = []
    for row in rows:
        if len(row) != len(headers):
            raise ValueError("Submission row does not match table columns.")
        cells = "".join(f'<td data-label="{text(header)}">{text(value)}</td>' for header, value in zip(headers, row))
        body.append("<tr>" + cells + "</tr>")
    return (f'<table class="submission-table submission-{kind}-table" aria-label="{text(label)}">'
            f'<colgroup>{columns}</colgroup><thead><tr>{heading}</tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table>')


def show_table(headers, rows, *, label, kind="fields"):
    st.html(table_html(headers, rows, label=label, kind=kind))
