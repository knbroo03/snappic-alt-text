"""Download media and reduce it to a single still image for captioning.

Claude's vision API accepts still images. So:
  * photo / ai  -> use the image as-is (re-encoded to a supported format).
  * gif         -> pick a representative frame with Pillow.
  * video       -> grab a frame with ffmpeg if it's installed; otherwise the
                   caller falls back gracefully.

Returns JPEG or PNG bytes plus the media type string that Claude expects.
"""

from __future__ import annotations

import io
import shutil
import subprocess
import tempfile
from dataclasses import dataclass

import httpx
from PIL import Image

# Anthropic supports these image media types.
_SUPPORTED = {"image/jpeg", "image/png", "image/gif", "image/webp"}
_MAX_DIMENSION = 1568  # Claude downsamples above this; pre-shrink to save tokens.


class MediaError(Exception):
    pass


@dataclass
class PreparedImage:
    data: bytes
    media_type: str  # "image/jpeg" or "image/png"


def download(url: str, timeout: float = 30.0) -> bytes:
    with httpx.Client(follow_redirects=True, timeout=timeout) as client:
        resp = client.get(url)
        resp.raise_for_status()
        return resp.content


def _shrink(img: Image.Image) -> Image.Image:
    if max(img.size) <= _MAX_DIMENSION:
        return img
    ratio = _MAX_DIMENSION / max(img.size)
    new_size = (int(img.width * ratio), int(img.height * ratio))
    return img.resize(new_size, Image.LANCZOS)


def _encode(img: Image.Image) -> PreparedImage:
    img = _shrink(img)
    if img.mode in ("RGBA", "P", "LA"):
        # Flatten transparency onto white for a clean JPEG.
        background = Image.new("RGB", img.size, (255, 255, 255))
        img_rgba = img.convert("RGBA")
        background.paste(img_rgba, mask=img_rgba.split()[-1])
        img = background
    elif img.mode != "RGB":
        img = img.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=88)
    return PreparedImage(data=buf.getvalue(), media_type="image/jpeg")


def _frame_from_image_bytes(raw: bytes) -> PreparedImage:
    """Handles static images and animated GIFs (picks a middle frame)."""
    img = Image.open(io.BytesIO(raw))
    n_frames = getattr(img, "n_frames", 1)
    if n_frames > 1:
        img.seek(n_frames // 2)  # a middle frame is usually most representative
    return _encode(img)


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def _frame_from_video_bytes(raw: bytes) -> PreparedImage:
    if not ffmpeg_available():
        raise MediaError("ffmpeg not installed; cannot extract a video frame")
    with tempfile.TemporaryDirectory() as td:
        src = f"{td}/in"
        out = f"{td}/frame.jpg"
        with open(src, "wb") as fh:
            fh.write(raw)
        # Grab a frame ~1s in (avoids black intro frames).
        cmd = [
            "ffmpeg", "-y", "-ss", "1", "-i", src,
            "-frames:v", "1", "-q:v", "3", out,
        ]
        proc = subprocess.run(cmd, capture_output=True)
        if proc.returncode != 0:
            # Retry at the very start in case the clip is shorter than 1s.
            cmd[3] = "0"
            proc = subprocess.run(cmd, capture_output=True)
            if proc.returncode != 0:
                raise MediaError(
                    "ffmpeg failed: " + proc.stderr.decode("utf-8", "ignore")[:300]
                )
        with open(out, "rb") as fh:
            frame = fh.read()
    return _frame_from_image_bytes(frame)


def prepare_still(raw: bytes, media_type: str | None) -> PreparedImage:
    """Turn raw downloaded bytes into a single still image for Claude."""
    mt = (media_type or "").lower()
    if mt == "video":
        return _frame_from_video_bytes(raw)
    # photo, gif, ai, or unknown -> let Pillow figure it out.
    try:
        return _frame_from_image_bytes(raw)
    except Exception as exc:  # noqa: BLE001
        # Last resort: maybe it's actually a video with no/no-usable type hint.
        if ffmpeg_available():
            try:
                return _frame_from_video_bytes(raw)
            except Exception:
                pass
        raise MediaError(f"could not decode media: {exc}") from exc
