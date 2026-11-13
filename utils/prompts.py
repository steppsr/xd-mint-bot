"""Pull a usable title and description out of a Midjourney message."""

from __future__ import annotations

import re
from pathlib import Path

from utils.format import truncate, truncate_words

# Midjourney wraps the prompt in bold and then appends the user mention.
_BOLD_PROMPT = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)
# Flags such as "--v 6.1", "--ar 1:1", and "--tile".
_PROMPT_FLAG = re.compile(r"(?:^|\s)--[A-Za-z][A-Za-z0-9_-]*(?:[ =]\S+)?")

DEFAULT_DESCRIPTION = "Minted with xd-mint-bot."
NAME_LIMIT = 80
DESCRIPTION_LIMIT = 1500


def extract_midjourney_prompt(content: str) -> str | None:
    """Return the bold prompt with parameter flags removed, or None."""
    if not content:
        return None
    match = _BOLD_PROMPT.search(content)
    if match is None:
        return None
    prompt = match.group(1).replace("\n", " ")
    prompt = _PROMPT_FLAG.sub(" ", prompt)
    prompt = re.sub(r"\s+", " ", prompt).strip(" -")
    return prompt or None


def name_from_filename(filename: str) -> str:
    """Turn an attachment file name into a readable fallback title."""
    stem = Path(filename).stem.replace("_", " ").replace("-", " ")
    stem = re.sub(r"\s+", " ", stem).strip()
    return stem or "Untitled NFT"


def default_nft_name(prompt: str | None, filename: str) -> str:
    """Prefer the parsed prompt, then the file name."""
    source = prompt if prompt else name_from_filename(filename)
    name = truncate_words(source, NAME_LIMIT)
    return name or "Untitled NFT"


def default_nft_description(prompt: str | None) -> str:
    """Use the parsed prompt when the message has one."""
    if prompt:
        return truncate(prompt, DESCRIPTION_LIMIT)
    return DEFAULT_DESCRIPTION
