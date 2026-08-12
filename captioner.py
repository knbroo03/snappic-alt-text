"""Generate screen-reader-quality alt-text with Claude's vision API.

The prompt follows accessibility best practice for alt-text: concise, concrete,
no "image of" preamble, no guessing at people's names or identities.
"""

from __future__ import annotations

import base64

from anthropic import Anthropic

from .config import Settings
from .media import PreparedImage

_SYSTEM = (
    "You write alt-text descriptions of photo-booth pictures for blind and "
    "low-vision guests, which their phone's screen reader will read aloud. "
    "Follow these rules strictly:\n"
    "- Write ONE natural sentence, ideally under 30 words.\n"
    "- Describe the people (how many, what they're doing, expressions), the "
    "setting, and any obvious props, costumes, or overlay text.\n"
    "- Be concrete and specific, but do not invent details you cannot see.\n"
    "- Do NOT start with 'image of', 'photo of', 'a picture of', or similar.\n"
    "- Do NOT guess anyone's name, identity, age, race, or relationships.\n"
    "- If readable text or signage appears in the image, include it in quotes.\n"
    "- Warm, plain language. No emojis. Output ONLY the description sentence."
)

_MEDIA_HINTS = {
    "gif": "This is one frame from a short animated GIF or boomerang.",
    "video": "This is one frame taken from a short video clip.",
    "ai": "This is an AI-generated image from the booth.",
}


class CaptionError(Exception):
    pass


class Captioner:
    """Thin wrapper around the Anthropic client (easy to mock in tests)."""

    def __init__(self, settings: Settings, client: Anthropic | None = None):
        self.settings = settings
        self._client = client or Anthropic(api_key=settings.anthropic_api_key)

    def describe(self, image: PreparedImage, media_type: str | None = None) -> str:
        hint = _MEDIA_HINTS.get((media_type or "").lower(), "")
        text_prompt = (
            "Write the alt-text description for this photo-booth image."
        )
        if hint:
            text_prompt = f"{hint} {text_prompt}"

        b64 = base64.standard_b64encode(image.data).decode("ascii")
        try:
            resp = self._client.messages.create(
                model=self.settings.anthropic_model,
                max_tokens=150,
                system=_SYSTEM,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": image.media_type,
                                    "data": b64,
                                },
                            },
                            {"type": "text", "text": text_prompt},
                        ],
                    }
                ],
            )
        except Exception as exc:  # noqa: BLE001
            raise CaptionError(str(exc)) from exc

        parts = [
            block.text
            for block in resp.content
            if getattr(block, "type", None) == "text"
        ]
        description = " ".join(p.strip() for p in parts).strip()
        if not description:
            raise CaptionError("Claude returned an empty description")
        return _tidy(description)


def _tidy(text: str) -> str:
    """Strip stray quotes/preambles a model might add despite instructions."""
    text = text.strip().strip('"').strip()
    lowered = text.lower()
    for prefix in ("image of ", "photo of ", "a photo of ", "a picture of ",
                   "picture of ", "an image of "):
        if lowered.startswith(prefix):
            text = text[len(prefix):]
            text = text[:1].upper() + text[1:]
            break
    return text
