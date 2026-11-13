"""Upload the image, metadata, and license, then optionally mint.

``DRY_RUN`` and the Upload only button both stop before ``nft_mint_nft``.
The service also refuses to mint when ``DRY_RUN`` is on, even if a caller
asks for a real mint.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from pathlib import Path

from config import Settings
from handlers.sessions import MintDraft, MintSession
from services.chia_minter import (
    ChiaMinter,
    build_mint_payload,
    extract_launcher_id,
    extract_nft_id,
)
from services.collection_store import CollectionStore
from services.filebase_uploader import FilebaseUploader, ipfs_uri
from services.metadata_generator import build_chip0007, license_document, metadata_document
from utils.errors import MintBotError, ValidationError
from utils.format import collection_image_key, collection_metadata_key, collection_object_key, format_xch
from utils.hash_utils import sha256_bytes

log = logging.getLogger(__name__)

_CONTENT_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}


@dataclass
class MintArtifacts:
    """CIDs and hashes collected while a mint runs. Fields stay empty until that step succeeds."""

    image_hash: str
    image_cid: str | None = None
    metadata_cid: str | None = None
    license_cid: str | None = None
    metadata_hash: str = ""
    license_hash: str | None = None
    nft_id: str | None = None
    launcher_id: str | None = None
    dry_run: bool = False
    offer_id: str | None = None
    offer_url: str | None = None
    sale_note: str = ""
    image_gateway_url: str = ""
    metadata_gateway_url: str = ""
    license_gateway_url: str = ""
    image_ipfs_uri: str = ""
    metadata_ipfs_uri: str = ""
    mint_number: int | None = None


@dataclass
class MintOutcome:
    """The result shown in Discord. ``message`` is safe to display."""

    ok: bool
    message: str
    artifacts: MintArtifacts


class MintService:
    """Upload metadata and submit wallet mints.

    One queue worker runs this pipeline. ``lock`` is held only around wallet
    spends (the readiness check, ``nft_mint_nft``, and later ``create_offer_for_ids``),
    so an offer can wait for confirmation without blocking the next mint.
    """

    def __init__(
        self,
        settings: Settings,
        uploader: FilebaseUploader,
        minter: ChiaMinter,
        collections: CollectionStore,
    ) -> None:
        self.settings = settings
        self.uploader = uploader
        self.minter = minter
        self.collections = collections
        self.lock = asyncio.Lock()

    async def upload_and_mint(
        self,
        session: MintSession,
        draft: MintDraft,
        *,
        dry_run: bool,
        on_stage: Callable[[str], Awaitable[None]] | None = None,
    ) -> MintOutcome:
        """Upload session files and mint unless this is a dry run.

        The collection number is claimed only after the wallet accepts the mint.
        The caller runs one mint at a time, so two uploads cannot take the same number.
        A sale price is recorded on the outcome and listed later, outside this call.
        """
        effective_dry_run = dry_run or self.settings.dry_run
        artifacts = MintArtifacts(image_hash=session.image_hash, dry_run=effective_dry_run)
        try:
            return await self._upload_and_mint(
                session,
                draft,
                artifacts,
                effective_dry_run=effective_dry_run,
                on_stage=on_stage,
            )
        except MintBotError as exc:
            log.warning("Mint pipeline stopped for user %s: %s", session.user_id, exc)
            return MintOutcome(ok=False, message=str(exc), artifacts=artifacts)

    async def _upload_and_mint(
        self,
        session: MintSession,
        draft: MintDraft,
        artifacts: MintArtifacts,
        *,
        effective_dry_run: bool,
        on_stage: Callable[[str], Awaitable[None]] | None,
    ) -> MintOutcome:
        """Upload and maybe mint. The wallet lock covers spends only."""
        collection = self.collections.get(draft.collection_id)
        if collection is None:
            raise ValidationError(
                "That collection is no longer saved. Start again after choosing one with /collection use."
            )
        extra_fee = self.settings.offer_fee_mojos if draft.sale_price_mojos and not effective_dry_run else 0
        if not effective_dry_run:
            if on_stage is not None:
                await on_stage("Checking the wallet.")
            async with self.lock:
                await asyncio.to_thread(self.minter.assert_ready_to_mint, extra_fee_mojos=extra_fee)
        if on_stage is not None:
            await on_stage("Uploading the image and metadata to IPFS.")
        number = self.collections.peek_next_number(collection.id)
        artifacts.mint_number = number
        if draft.automatic_name and draft.name != str(number):
            draft = replace(draft, name=str(number))
            session.draft = draft
        image = await asyncio.to_thread(self._verified_image, session)
        metadata = build_chip0007(
            name=draft.name,
            description=draft.description,
            collection_id=collection.id,
            collection_name=collection.name,
            minting_tool=self.settings.minting_tool,
            attributes=[
                {"trait_type": trait, "value": value} for trait, value in draft.attributes
            ],
            collection_attributes=collection.chip_attributes(),
        )
        meta_bytes = metadata_document(metadata)
        artifacts.metadata_hash = sha256_bytes(meta_bytes)
        license_bytes: bytes | None = None
        if self.settings.upload_license:
            if collection.license_text:
                license_bytes = collection.license_text.encode("utf-8")
            else:
                license_path = str(self.settings.license_file) if self.settings.license_file else None
                license_bytes = license_document(license_path)
            artifacts.license_hash = sha256_bytes(license_bytes)

        extension = _extension(session.filename, session.content_type)
        image_cid = await asyncio.to_thread(
            self.uploader.upload_bytes,
            image,
            collection_image_key(collection.name, collection.id, number, extension),
            _CONTENT_TYPES.get(extension, session.content_type or "application/octet-stream"),
        )
        self._remember_image(artifacts, image_cid)

        metadata_cid = await asyncio.to_thread(
            self.uploader.upload_bytes,
            meta_bytes,
            collection_metadata_key(collection.name, collection.id, number),
            "application/json",
        )
        self._remember_metadata(artifacts, metadata_cid)

        license_cid: str | None = None
        if license_bytes is not None and artifacts.license_hash:
            license_cid = await asyncio.to_thread(
                self.uploader.upload_bytes,
                license_bytes,
                collection_object_key(collection.name, collection.id, "license"),
                "text/plain",
            )
            self._remember_license(artifacts, license_cid)

        if effective_dry_run:
            log.info(
                "Dry run uploaded number %s image %s metadata %s for user %s",
                number,
                image_cid,
                metadata_cid,
                session.user_id,
            )
            if draft.sale_price_mojos:
                artifacts.sale_note = (
                    f"Sale price was {format_xch(draft.sale_price_mojos)} XCH. "
                    "Dry run does not create a Dexie offer."
                )
            return MintOutcome(ok=True, message="", artifacts=artifacts)

        payload = build_mint_payload(
            wallet_id=self.settings.chia_nft_wallet_id,
            uris=self._uris(image_cid),
            image_hash=session.image_hash,
            meta_uris=self._uris(metadata_cid),
            meta_hash=artifacts.metadata_hash,
            license_uris=self._uris(license_cid) if license_cid else None,
            license_hash=artifacts.license_hash,
            royalty_address=self.settings.chia_royalty_address,
            royalty_percentage=draft.royalty_hundredths,
            target_address=self.settings.chia_target_address,
            did_id=self.settings.chia_did_id,
            fee=self.settings.chia_fee_mojos,
        )
        session.state = "minting"
        if on_stage is not None:
            await on_stage("Submitting the mint to the Chia wallet. This can take a minute.")
        async with self.lock:
            response = await asyncio.to_thread(self.minter.mint_nft, payload)
            artifacts.nft_id = extract_nft_id(response)
            artifacts.launcher_id = extract_launcher_id(response)
            if not artifacts.nft_id:
                log.warning("Mint succeeded and the response had no nft_id. Fields: %s", sorted(response))
            claimed = self.collections.claim_next_number(collection.id)
        if claimed != number:
            log.error(
                "Collection %s number changed from %s to %s while the mint lock was held",
                collection.id,
                number,
                claimed,
            )
        if draft.sale_price_mojos:
            artifacts.sale_note = _pending_sale_note(draft.sale_price_mojos, artifacts.nft_id)
        log.info("Minted number %s for collection %s user %s", number, collection.id, session.user_id)
        return MintOutcome(ok=True, message="", artifacts=artifacts)

    def _remember_image(self, artifacts: MintArtifacts, cid: str) -> None:
        artifacts.image_cid = cid
        artifacts.image_ipfs_uri = ipfs_uri(cid)
        artifacts.image_gateway_url = self.uploader.gateway_url(cid)

    def _remember_metadata(self, artifacts: MintArtifacts, cid: str) -> None:
        artifacts.metadata_cid = cid
        artifacts.metadata_ipfs_uri = ipfs_uri(cid)
        artifacts.metadata_gateway_url = self.uploader.gateway_url(cid)

    def _remember_license(self, artifacts: MintArtifacts, cid: str) -> None:
        artifacts.license_cid = cid
        artifacts.license_gateway_url = self.uploader.gateway_url(cid)

    def _uris(self, cid: str) -> list[str]:
        uris = [ipfs_uri(cid)]
        if self.settings.include_gateway_uris:
            uris.append(self.uploader.gateway_url(cid))
        return uris

    def _verified_image(self, session: MintSession) -> bytes:
        if session.image_path is None:
            raise ValidationError("The temporary image is missing. React with the seedling again.")
        try:
            data = Path(session.image_path).read_bytes()
        except OSError as exc:
            raise ValidationError("The temporary image could not be read. React with the seedling again.") from exc
        if len(data) > self.settings.max_image_bytes:
            raise ValidationError("The image exceeds the configured size limit.")
        actual = sha256_bytes(data)
        if actual != session.image_hash:
            raise ValidationError("The temporary image no longer matches its hash. React with the seedling again.")
        return data


def _pending_sale_note(price_mojos: int, nft_id: str | None) -> str:
    """Tell Discord that the mint is done and the offer waits for the coin."""
    if not nft_id:
        return (
            "The mint was submitted, and the wallet did not return an NFT id, "
            "so no Dexie offer was created. List it later with /sell."
        )
    return (
        f"The sale offer for {format_xch(price_mojos)} XCH starts after this NFT is in the wallet. "
        "You can mint the next image now."
    )


def _extension(filename: str, content_type: str) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix == ".jpeg":
        return ".jpg"
    if suffix in _CONTENT_TYPES:
        return suffix
    normalized = (content_type or "").split(";", 1)[0].strip().lower()
    if normalized == "image/png":
        return ".png"
    if normalized in {"image/jpeg", "image/jpg"}:
        return ".jpg"
    if normalized == "image/webp":
        return ".webp"
    return ".img"
