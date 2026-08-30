import io
from pypdf import PdfReader


def extract(data: bytes) -> str:
    reader = PdfReader(io.BytesIO(data))
    chunks = []
    for i, page in enumerate(reader.pages):
        chunks.append(f"--- Page {i + 1} ---\n")
        chunks.append(page.extract_text() or "")
    return "\n".join(chunks).strip()
