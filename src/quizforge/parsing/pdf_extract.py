"""PDF 文本抽取：按页抽取，保留页码，标记疑似扫描页（字符画像）。"""

from __future__ import annotations

from dataclasses import dataclass

import pdfplumber

# 单页字符数低于该值视为疑似扫描页/图片页
EMPTY_PAGE_CHAR_THRESHOLD = 20


@dataclass
class PageText:
    page_no: int  # 1-based
    text: str


@dataclass
class ExtractedDoc:
    pages: list[PageText]
    empty_pages: list[int]

    @property
    def full_text(self) -> str:
        return "\n".join(p.text for p in self.pages)


def extract_pdf(pdf_path: str) -> ExtractedDoc:
    pages: list[PageText] = []
    empty: list[int] = []
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            pages.append(PageText(page_no=i, text=text))
            if len(text.strip()) < EMPTY_PAGE_CHAR_THRESHOLD:
                empty.append(i)
    return ExtractedDoc(pages=pages, empty_pages=empty)
