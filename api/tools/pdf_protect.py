import io
from pypdf import PdfReader, PdfWriter


def protect(data: bytes, password: str) -> bytes:
    reader = PdfReader(io.BytesIO(data))
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    writer.encrypt(user_password=password, owner_password=password, use_128bit=True)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()
