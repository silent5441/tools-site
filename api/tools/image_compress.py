import io
from PIL import Image

_MIME = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}


def _load(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data))


def convert(data: bytes, fmt: str = "JPEG", quality: int = 90) -> tuple[bytes, str]:
    fmt = (fmt or "JPEG").upper()
    if fmt not in _MIME:
        fmt = "JPEG"
    img = _load(data)
    out = io.BytesIO()
    if fmt == "JPEG":
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        img.save(out, format="JPEG", quality=max(1, min(95, quality)), optimize=True)
    elif fmt == "PNG":
        if img.mode == "P":
            img = img.convert("RGBA")
        img.save(out, format="PNG", optimize=True)
    else:
        if img.mode == "P":
            img = img.convert("RGBA")
        img.save(out, format="WEBP", quality=max(1, min(100, quality)))
    return out.getvalue(), _MIME[fmt]


def compress(data: bytes, quality: int = 70) -> tuple[bytes, str]:
    return convert(data, "JPEG", quality)


def resize(data: bytes, width: int = -1, height: int = -1) -> tuple[bytes, str]:
    img = _load(data)
    w, h = img.size
    if width > 0 and height > 0:
        img = img.resize((width, height), Image.LANCZOS)
    elif width > 0:
        ratio = width / w
        img = img.resize((width, int(h * ratio)), Image.LANCZOS)
    elif height > 0:
        ratio = height / h
        img = img.resize((int(w * ratio), height), Image.LANCZOS)
    out = io.BytesIO()
    if img.mode in ("RGBA", "P"):
        img.save(out, format="PNG")
        return out.getvalue(), "image/png"
    img.save(out, format="JPEG", quality=85, optimize=True)
    return out.getvalue(), "image/jpeg"
