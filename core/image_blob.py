"""Image helpers shared by the chat UI and the agent's vision paths.

Used for Quick Action snips (Alt -> Screen -> drag a region) and for file
attachments, so a captured screenshot can be shown in the chat *and* sent to
the model as an inline image.
"""

from __future__ import annotations

import io
from pathlib import Path

IMAGE_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tif", ".tiff",
}

_MIME_BY_EXT = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".webp": "image/webp",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
}

#: Vision models handle the long edge best at/below ~1568px; bigger snips are
#: downscaled so requests stay fast and inside API payload limits.
MAX_EDGE = 1568
JPEG_QUALITY = 85
MAX_BYTES = 6 * 1024 * 1024


def is_image_path(path) -> bool:
    try:
        return Path(str(path or "")).suffix.lower() in IMAGE_EXTENSIONS
    except Exception:
        return False


def mime_for_path(path) -> str:
    try:
        suffix = Path(str(path or "")).suffix.lower()
    except Exception:
        suffix = ""
    return _MIME_BY_EXT.get(suffix, "image/png")


def _resample():
    from PIL import Image
    return getattr(Image, "Resampling", Image).LANCZOS


def prepare_image_blob(path, max_edge: int = MAX_EDGE) -> tuple[bytes, str] | None:
    """Return ``(bytes, mime_type)`` ready for an inline image part.

    Large screenshots are re-encoded as downscaled JPEG; if Pillow is missing
    the original bytes are returned untouched.
    """
    p = Path(str(path or ""))
    if not p.is_file():
        return None
    try:
        raw = p.read_bytes()
    except Exception:
        return None
    if not raw:
        return None
    mime = mime_for_path(p)

    try:
        from PIL import Image
    except Exception:
        return (raw, mime) if len(raw) <= MAX_BYTES else None

    try:
        with Image.open(io.BytesIO(raw)) as im:
            im = im.convert("RGB")
            width, height = im.size
            scale = min(1.0, float(max_edge) / float(max(width, height) or 1))
            if scale < 1.0:
                im = im.resize(
                    (max(1, int(width * scale)), max(1, int(height * scale))),
                    _resample(),
                )
            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=True)
            data = buf.getvalue()
        if data and len(data) <= MAX_BYTES:
            return data, "image/jpeg"
    except Exception:
        pass

    if len(raw) <= MAX_BYTES:
        return raw, mime
    return None


def attachment_dict(path) -> dict:
    """Attachment payload used by the chat feed and the agent turn."""
    p = Path(str(path or ""))
    return {
        "name": p.name or "attachment",
        "path": str(p),
        "type": "image" if is_image_path(p) else "file",
    }
