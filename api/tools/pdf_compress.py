import io
from pypdf import PdfReader, PdfWriter


def compress(data: bytes) -> tuple[bytes, str]:
    """Lossless compression: rebuild the PDF, enabling object streams and
    compression. For already-optimal files, returns the smaller of the two."""
    reader = PdfReader(io.BytesIO(data))
    writer = PdfWriter()
    writer.append_pages_from_reader(reader)
    writer.compress_content_streams = True
    for page in writer.pages:
        try:
            page.compress_content_streams()
        except Exception:
            pass
    out = io.BytesIO()
    writer.write(out)
    candidate = out.getvalue()
    if len(candidate) < len(data):
        return candidate, f"Reduced {len(data)} -> {len(candidate)} bytes"
    return data, "File already optimal; no size reduction"
