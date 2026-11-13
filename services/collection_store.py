"""Saved NFT collections.

The active collection is shared by this bot process. Icon, banner, and license
files are uploaded once and reused by later mints. The JSON file is local
state, so a second bot process would not see updates written here.
"""

from __future__ import annotations

import json
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from config import Settings
from utils.errors import ConfigError, ValidationError

_LICENSE_LIMIT = 200_000


def _next_number(value: object) -> int:
    """Accept a saved counter. Missing values start at 1."""
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ConfigError("A saved collection has an invalid next number.")
    try:
        number = int(value)
    except ValueError as exc:
        raise ConfigError("A saved collection has an invalid next number.") from exc
    if number < 1:
        raise ConfigError("A saved collection has an invalid next number.")
    return number


@dataclass
class NftCollection:
    """One CHIP-0007 collection profile."""

    id: str
    name: str
    royalty_hundredths: int
    description: str = ""
    twitter: str = ""
    website: str = ""
    icon_uri: str = ""
    icon_ipfs: str = ""
    banner_uri: str = ""
    banner_ipfs: str = ""
    license_text: str = ""
    license_filename: str = ""
    license_uri: str = ""
    license_ipfs: str = ""
    next_number: int = 1

    def chip_attributes(self) -> list[dict[str, str]]:
        """Collection attributes in the order MintGarden displays them.

        Collection attributes use ``type``. Per-NFT traits use ``trait_type``.
        Icon and banner values are gateway URLs so explorers can fetch the image.
        """
        pairs = (
            ("description", self.description),
            ("icon", self.icon_uri),
            ("banner", self.banner_uri),
            ("twitter", self.twitter),
            ("website", self.website),
        )
        return [{"type": key, "value": value} for key, value in pairs if value]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "royalty_hundredths": self.royalty_hundredths,
            "description": self.description,
            "twitter": self.twitter,
            "website": self.website,
            "icon_uri": self.icon_uri,
            "icon_ipfs": self.icon_ipfs,
            "banner_uri": self.banner_uri,
            "banner_ipfs": self.banner_ipfs,
            "license_text": self.license_text,
            "license_filename": self.license_filename,
            "license_uri": self.license_uri,
            "license_ipfs": self.license_ipfs,
            "next_number": self.next_number,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> NftCollection:
        try:
            parsed = uuid.UUID(str(raw["id"]))
        except (KeyError, ValueError, TypeError) as exc:
            raise ConfigError("A saved collection has an invalid id.") from exc
        name = str(raw.get("name") or "").strip()
        if not name:
            raise ConfigError("A saved collection is missing its name.")
        return cls(
            id=str(parsed),
            name=name,
            royalty_hundredths=int(raw.get("royalty_hundredths") or 0),
            description=str(raw.get("description") or ""),
            twitter=str(raw.get("twitter") or ""),
            website=str(raw.get("website") or ""),
            icon_uri=str(raw.get("icon_uri") or ""),
            icon_ipfs=str(raw.get("icon_ipfs") or ""),
            banner_uri=str(raw.get("banner_uri") or ""),
            banner_ipfs=str(raw.get("banner_ipfs") or ""),
            license_text=str(raw.get("license_text") or ""),
            license_filename=str(raw.get("license_filename") or ""),
            license_uri=str(raw.get("license_uri") or ""),
            license_ipfs=str(raw.get("license_ipfs") or ""),
            next_number=_next_number(raw.get("next_number", 1)),
        )


class CollectionStore:
    """Load and save ``data/collections.json``."""

    def __init__(self, path: Path, settings: Settings) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._active_id = ""
        self._items: dict[str, NftCollection] = {}
        with self._lock:
            if path.is_file():
                self._load_unlocked()
            else:
                self._seed_unlocked(settings)

    def list(self) -> list[NftCollection]:
        with self._lock:
            return list(self._items.values())

    def get(self, collection_id: str) -> NftCollection | None:
        with self._lock:
            item = self._items.get(collection_id)
            return None if item is None else NftCollection(**item.__dict__)

    def active(self) -> NftCollection | None:
        with self._lock:
            item = self._items.get(self._active_id)
            return None if item is None else NftCollection(**item.__dict__)

    def active_id(self) -> str:
        with self._lock:
            return self._active_id

    def create(
        self,
        *,
        name: str,
        royalty_hundredths: int,
        description: str = "",
        twitter: str = "",
        website: str = "",
    ) -> NftCollection:
        """Add a collection and make it the active one."""
        with self._lock:
            self._reject_duplicate_unlocked(name, ignore_id=None)
            item = NftCollection(
                id=str(uuid.uuid4()),
                name=name,
                royalty_hundredths=royalty_hundredths,
                description=description,
                twitter=twitter,
                website=website,
            )
            self._items[item.id] = item
            self._active_id = item.id
            self._save_unlocked()
            return NftCollection(**item.__dict__)

    def update_profile(
        self,
        collection_id: str,
        *,
        name: str,
        royalty_hundredths: int,
        description: str,
        twitter: str,
        website: str,
    ) -> NftCollection:
        with self._lock:
            item = self._require_unlocked(collection_id)
            self._reject_duplicate_unlocked(name, ignore_id=item.id)
            item.name = name
            item.royalty_hundredths = royalty_hundredths
            item.description = description
            item.twitter = twitter
            item.website = website
            self._save_unlocked()
            return NftCollection(**item.__dict__)

    def set_active(self, collection_id: str) -> NftCollection:
        with self._lock:
            item = self._require_unlocked(collection_id)
            self._active_id = item.id
            self._save_unlocked()
            return NftCollection(**item.__dict__)

    def set_image(
        self,
        collection_id: str,
        *,
        kind: str,
        gateway_url: str,
        ipfs_uri: str,
    ) -> NftCollection:
        if kind not in {"icon", "banner"}:
            raise ValidationError("Choose an icon or a banner.")
        with self._lock:
            item = self._require_unlocked(collection_id)
            if kind == "icon":
                item.icon_uri = gateway_url
                item.icon_ipfs = ipfs_uri
            else:
                item.banner_uri = gateway_url
                item.banner_ipfs = ipfs_uri
            self._save_unlocked()
            return NftCollection(**item.__dict__)

    def set_license(self, collection_id: str, *, text: str, filename: str) -> NftCollection:
        cleaned = text.replace("\r\n", "\n").replace("\r", "\n").strip()
        if not cleaned:
            raise ValidationError("The license file is empty.")
        if len(cleaned.encode("utf-8")) > _LICENSE_LIMIT:
            raise ValidationError("License file must be 200 KB or smaller.")
        with self._lock:
            item = self._require_unlocked(collection_id)
            item.license_text = cleaned + "\n"
            item.license_filename = filename
            item.license_uri = ""
            item.license_ipfs = ""
            self._save_unlocked()
            return NftCollection(**item.__dict__)

    def set_license_pin(self, collection_id: str, *, gateway_url: str, ipfs_uri: str) -> NftCollection:
        """Store the Filebase URL for a license that was just uploaded."""
        with self._lock:
            item = self._require_unlocked(collection_id)
            if not item.license_text:
                raise ValidationError("Save a license before pinning it.")
            item.license_uri = gateway_url
            item.license_ipfs = ipfs_uri
            self._save_unlocked()
            return NftCollection(**item.__dict__)

    def peek_next_number(self, collection_id: str) -> int:
        """Return the number the next mint will use. This does not reserve it."""
        with self._lock:
            return self._require_unlocked(collection_id).next_number

    def claim_next_number(self, collection_id: str) -> int:
        """Reserve the next mint number and return it. The saved counter then points at the one after it."""
        with self._lock:
            item = self._require_unlocked(collection_id)
            number = item.next_number
            item.next_number = number + 1
            self._save_unlocked()
            return number

    def clear_field(self, collection_id: str, field: str) -> NftCollection:
        allowed = {"icon", "banner", "twitter", "website", "license", "description"}
        if field not in allowed:
            raise ValidationError("That collection field cannot be cleared.")
        with self._lock:
            item = self._require_unlocked(collection_id)
            if field == "icon":
                item.icon_uri = ""
                item.icon_ipfs = ""
            elif field == "banner":
                item.banner_uri = ""
                item.banner_ipfs = ""
            elif field == "license":
                item.license_text = ""
                item.license_filename = ""
                item.license_uri = ""
                item.license_ipfs = ""
            else:
                setattr(item, field, "")
            self._save_unlocked()
            return NftCollection(**item.__dict__)

    def _require_unlocked(self, collection_id: str) -> NftCollection:
        item = self._items.get(collection_id)
        if item is None:
            raise ValidationError("That collection is not saved. Use /collection list to see the choices.")
        return item

    def _reject_duplicate_unlocked(self, name: str, *, ignore_id: str | None) -> None:
        folded = name.casefold()
        for item in self._items.values():
            if item.id != ignore_id and item.name.casefold() == folded:
                raise ValidationError(f"A collection named {item.name} already exists.")

    def _seed_unlocked(self, settings: Settings) -> None:
        item = NftCollection(
            id=settings.default_collection_id,
            name=settings.default_collection_name,
            royalty_hundredths=settings.default_royalty_percentage,
        )
        self._items = {item.id: item}
        self._active_id = item.id
        self._save_unlocked()

    def _load_unlocked(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigError(f"Could not read collections from {self.path}.") from exc
        items = raw.get("collections") if isinstance(raw, dict) else None
        if not isinstance(items, list) or not items:
            raise ConfigError(f"{self.path} does not contain a collection.")
        loaded: dict[str, NftCollection] = {}
        for entry in items:
            if not isinstance(entry, dict):
                raise ConfigError(f"{self.path} contains a collection that is not an object.")
            item = NftCollection.from_dict(entry)
            loaded[item.id] = item
        active = str(raw.get("active_id") or "")
        if active not in loaded:
            active = next(iter(loaded))
        self._items = loaded
        self._active_id = active

    def _save_unlocked(self) -> None:
        payload = {
            "active_id": self._active_id,
            "collections": [item.to_dict() for item in self._items.values()],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        temporary.replace(self.path)
