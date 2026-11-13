"""Post a Chia offer file to Dexie.

Dexie's public offers endpoint takes the raw offer string. ``drop_only`` asks
for the id without waiting while Dexie fetches NFT metadata.
"""

from __future__ import annotations

import logging
from typing import Any

import requests

from config import Settings
from utils.errors import DexieError

log = logging.getLogger(__name__)


class DexieClient:
    """HTTP client for the Dexie offers API."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def post_offer(self, offer: str) -> str:
        """Upload an offer and return Dexie's offer id."""
        url = self.settings.dexie_api_url.rstrip("/") + "/offers"
        try:
            response = requests.post(
                url,
                json={"offer": offer, "drop_only": True},
                headers={"User-Agent": "xd-mint-bot", "Accept": "application/json"},
                timeout=(10, 90),
            )
        except requests.Timeout as exc:
            raise DexieError("Dexie timed out while accepting the offer. The offer file was saved locally.") from exc
        except requests.RequestException as exc:
            raise DexieError("Could not reach Dexie. The offer file was saved locally.") from exc
        try:
            body: Any = response.json()
        except ValueError as exc:
            raise DexieError(f"Dexie returned a response that was not JSON (HTTP {response.status_code}).") from exc
        if not isinstance(body, dict):
            raise DexieError("Dexie returned an unexpected response.")
        if response.status_code >= 400 or body.get("success") is False:
            detail = body.get("error_message") or body.get("error") or f"HTTP {response.status_code}"
            raise DexieError(f"Dexie rejected the offer: {str(detail)[:300]}")
        offer_id = body.get("id")
        nested = body.get("offer")
        if not isinstance(offer_id, str) or not offer_id.strip():
            if isinstance(nested, dict) and isinstance(nested.get("id"), str):
                offer_id = nested["id"]
        if not isinstance(offer_id, str) or not offer_id.strip():
            raise DexieError("Dexie accepted the request and did not return an offer id.")
        log.info("Dexie accepted offer %s", offer_id.strip())
        return offer_id.strip()


def offer_page_url(template: str, offer_id: str) -> str:
    """Fill the configured Dexie offer page template."""
    return template.format(offer_id=offer_id)
