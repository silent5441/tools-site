import io
import secrets

from pypdf import PdfReader, PdfWriter


class AlreadyEncryptedError(Exception):
    pass


def protect(data: bytes, password: str) -> bytes:
    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted:
        raise AlreadyEncryptedError(
            "This PDF is already password protected. Remove its password first."
        )
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    owner = secrets.token_urlsafe(24)
    writer.encrypt(
        user_password=password,
        owner_password=owner,
        use_128bit=True,
    )
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()
