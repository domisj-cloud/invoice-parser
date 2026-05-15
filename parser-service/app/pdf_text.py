from pathlib import Path

from pypdf import PdfReader


def extract_pdf_text(path: Path) -> str:
    reader = PdfReader(str(path))
    page_texts: list[str] = []
    for index, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        page_texts.append(f"\n--- page {index} ---\n{text}")
    return "\n".join(page_texts)
