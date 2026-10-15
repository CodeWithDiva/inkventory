"""Product images: uploaded files (kept in storage) or external URLs, prepared for vision models."""

import mimetypes

from app.services.llm import ImageInput
from app.services.storage import read_file

STORAGE_PREFIX = "storage:"
ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
MAX_IMAGES_PER_REQUEST = 3


def is_url(ref: str) -> bool:
    return ref.startswith("http://") or ref.startswith("https://")


def media_type_for(ref: str) -> str:
    guessed = mimetypes.guess_type(ref.split("?")[0])[0]
    return guessed if guessed in ALLOWED_TYPES else "image/jpeg"


def load(refs: list[str], limit: int = MAX_IMAGES_PER_REQUEST) -> list[ImageInput]:
    """Turn stored image references into model inputs. Unreadable files are skipped."""
    images: list[ImageInput] = []
    for ref in refs[:limit]:
        if is_url(ref):
            images.append(ImageInput(url=ref, media_type=media_type_for(ref)))
        elif ref.startswith(STORAGE_PREFIX):
            key = ref[len(STORAGE_PREFIX):]
            try:
                images.append(ImageInput(data=read_file(key), media_type=media_type_for(key)))
            except OSError:
                continue
    return images
