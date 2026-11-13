"""Pure tests for config, metadata, sessions, and the Discord form constructors.

Run from the project root:

    python -m unittest tests.test_core
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import Settings, log_startup  # noqa: E402
from handlers.collection_commands import CreateCollectionModal  # noqa: E402
from handlers.metadata_flow import (  # noqa: E402
    ConfirmView,
    MetadataModal,
    PromptView,
    build_confirm_embed,
    build_success_embed,
)
from handlers.sessions import MintDraft, MintSession, SessionStore  # noqa: E402
from services.chia_minter import (  # noqa: E402
    _record_offers_launcher,
    build_mint_payload,
    extract_nft_id,
    friendly_chia_error,
    parse_cli_json,
)
from services.collection_store import CollectionStore  # noqa: E402
from services.dexie_client import offer_page_url  # noqa: E402
from services.filebase_uploader import cid_from_mapping, gateway_url_for, normalize_cid  # noqa: E402
from services.metadata_generator import (  # noqa: E402
    build_chip0007,
    metadata_document,
    normalize_twitter,
    parse_royalty_percent,
    parse_sale_price,
    validate_mint_fields,
)
from services.mint_service import MintArtifacts  # noqa: E402
from services.sale_service import (  # noqa: E402
    nft_info_is_spendable,
    offer_error_is_temporary,
    publish_sale_async,
)

from utils.bech32m import encode_nft_id, nft_id_to_launcher_hex  # noqa: E402
from utils.format import (  # noqa: E402
    collection_image_key,
    collection_metadata_key,
    collection_object_key,
    collection_object_prefix,
)
from utils.errors import ChiaMintError, ConfigError, UploadError, ValidationError  # noqa: E402
from utils.hash_utils import sha256_bytes  # noqa: E402
from utils.prompts import default_nft_name, extract_midjourney_prompt  # noqa: E402

_BODY = "q" * 58
_XCH = f"xch1{_BODY}"
_TXCH = f"txch1{_BODY}"
_DID = f"did:chia:1{_BODY}"
_UUID = "6f1b0c3a-8e4d-4f1a-9c2b-1a2b3c4d5e6f"


def base_env(**overrides: str) -> dict[str, str]:
    env = {
        "DISCORD_BOT_TOKEN": "discord-token-for-tests-0001",
        "AUTHORIZED_USER_IDS": "42,84",
        "CHIA_NETWORK": "mainnet",
        "CHIA_NFT_WALLET_ID": "4",
        "CHIA_DID_ID": _DID,
        "CHIA_ROYALTY_ADDRESS": _XCH,
        "CHIA_TARGET_ADDRESS": _XCH,
        "CHIA_FEE_MOJOS": "615000000",
        "FILEBASE_ACCESS_KEY": "AKIATESTACCESSKEY0001",
        "FILEBASE_SECRET_KEY": "filebase-secret-for-tests",
        "FILEBASE_BUCKET": "ipfs-bucket",
        "FILEBASE_ENDPOINT": "https://s3.filebase.com",
        "FILEBASE_GATEWAY": "https://ipfs.filebase.io",
        "DEFAULT_COLLECTION_ID": _UUID,
        "DEFAULT_COLLECTION_NAME": "Seedlings",
        "DEFAULT_ROYALTY_PERCENTAGE": "300",
    }
    env.update(overrides)
    return env


def _isolated_env(**overrides: str) -> dict[str, str]:
    """Build a clean environment that still knows where the user home is."""
    env = base_env(**overrides)
    for key in ("HOME", "USERPROFILE", "HOMEDRIVE", "HOMEPATH"):
        value = os.environ.get(key)
        if value:
            env[key] = value
    return env


def load_settings(**overrides: str) -> Settings:
    with mock.patch.dict(os.environ, _isolated_env(**overrides), clear=True):
        return Settings.load(dotenv=False)


def make_session(**overrides: object) -> MintSession:
    draft = MintDraft(
        name="Rain seedling",
        description="A seedling in the rain",
        collection_id=_UUID,
        collection_name="Seedlings",
        royalty_hundredths=300,
    )
    session = MintSession(
        key="1:2",
        user_id=42,
        guild_id=7,
        channel_id=1,
        source_message_id=2,
        filename="seedling.png",
        content_type="image/png",
        source_image_url="https://cdn.example/seedling.png",
        defaults=draft,
        image_hash="ab" * 32,
    )
    for key, value in overrides.items():
        setattr(session, key, value)
    return session


def fake_bot(session: MintSession | None = None) -> SimpleNamespace:
    current = session or make_session()
    settings = SimpleNamespace(
        chia_network="mainnet",
        session_timeout_seconds=60,
        dry_run=False,
        chia_fee_mojos=615_000_000,
        chia_fee_wallet_id=1,
        chia_target_address=_XCH,
        chia_royalty_address=_XCH,
        chia_did_id=_DID,
        mintgarden_nft_url="https://mintgarden.io/nfts/{nft_id}",
        spacescan_nft_url="https://www.spacescan.io/nft/{nft_id}",
    )
    return SimpleNamespace(settings=settings, sessions=SimpleNamespace(get=lambda key: current))


class SettingsTests(unittest.TestCase):
    def test_loads_a_valid_environment(self) -> None:
        settings = load_settings()
        self.assertEqual(settings.chia_nft_wallet_id, 4)
        self.assertEqual(settings.authorized_user_ids, frozenset({42, 84}))
        self.assertEqual(settings.default_collection_id, _UUID)
        self.assertEqual(settings.default_royalty_percentage, 300)
        self.assertEqual(settings.offer_fee_mojos, 0)
        self.assertEqual(settings.offer_wait_seconds, 600)
        self.assertTrue(settings.dry_run is False)
        self.assertNotIn("discord-token", repr(settings))

    def test_rejects_a_placeholder_token(self) -> None:
        with self.assertRaises(ConfigError):
            load_settings(DISCORD_BOT_TOKEN="your_token_here")

    def test_rejects_an_empty_allow_list(self) -> None:
        with self.assertRaises(ConfigError):
            load_settings(AUTHORIZED_USER_IDS="")

    def test_rejects_a_testnet_address_on_mainnet(self) -> None:
        with self.assertRaises(ConfigError):
            load_settings(CHIA_TARGET_ADDRESS=_TXCH)

    def test_accepts_a_testnet_address(self) -> None:
        settings = load_settings(
            CHIA_NETWORK="testnet",
            CHIA_TARGET_ADDRESS=_TXCH,
            CHIA_ROYALTY_ADDRESS=_TXCH,
        )
        self.assertEqual(settings.chia_target_address, _TXCH)
        self.assertIn("testnet11.mintgarden.io", settings.mintgarden_nft_url)
        self.assertIn("api-testnet.dexie.space", settings.dexie_api_url)

    def test_rejects_a_fee_above_the_safety_cap(self) -> None:
        with self.assertRaises(ConfigError):
            load_settings(CHIA_FEE_MOJOS="10000000001")

    def test_allows_a_high_fee_when_asked(self) -> None:
        settings = load_settings(CHIA_FEE_MOJOS="10000000001", ALLOW_HIGH_FEE="true")
        self.assertEqual(settings.chia_fee_mojos, 10_000_000_001)

    def test_rejects_a_bad_collection_id(self) -> None:
        with self.assertRaises(ConfigError):
            load_settings(DEFAULT_COLLECTION_ID="not-a-uuid")

    def test_small_royalty_is_logged_in_chia_units(self) -> None:
        with mock.patch.dict(os.environ, _isolated_env(DEFAULT_ROYALTY_PERCENTAGE="3"), clear=True):
            settings = Settings.load(dotenv=False)
        with self.assertLogs("xd_mint_bot", level="WARNING") as captured:
            log_startup(settings)
        self.assertTrue(any("0.03" in line for line in captured.output))


class MetadataTests(unittest.TestCase):
    def test_chip0007_document_is_stable(self) -> None:
        metadata = build_chip0007(
            name="Rain seedling",
            description="A seedling in the rain",
            collection_id=_UUID,
            collection_name="Seedlings",
            minting_tool="xd-mint-bot Discord Bot",
        )
        first = metadata_document(metadata)
        second = metadata_document(metadata)
        self.assertEqual(first, second)
        parsed = __import__("json").loads(first)
        self.assertEqual(parsed["format"], "CHIP-0007")
        self.assertEqual(parsed["collection"]["id"], _UUID)
        self.assertEqual(parsed["attributes"], [])
        self.assertNotIn("attributes", parsed["collection"])
        self.assertEqual(parsed["series_number"], 1)
        with_profile = build_chip0007(
            name="Rain seedling",
            description="A seedling in the rain",
            collection_id=_UUID,
            collection_name="Seedlings",
            minting_tool="xd-mint-bot Discord Bot",
            attributes=[{"trait_type": "Background", "value": "Blue"}],
            collection_attributes=[
                {"type": "icon", "value": "https://ipfs.filebase.io/ipfs/bafyicon"},
                {"type": "twitter", "value": "ExampleHandle"},
            ],
        )
        profile = __import__("json").loads(metadata_document(with_profile))
        self.assertEqual(profile["attributes"][0]["trait_type"], "Background")
        self.assertEqual(profile["collection"]["attributes"][0]["type"], "icon")
        self.assertEqual(sha256_bytes(first), sha256_bytes(second))

    def test_royalty_percent_converts_to_chia_hundredths(self) -> None:
        self.assertEqual(parse_royalty_percent("3"), 300)
        self.assertEqual(parse_royalty_percent("3%"), 300)
        self.assertEqual(parse_royalty_percent("3.5"), 350)
        self.assertEqual(parse_royalty_percent("0.01"), 1)
        self.assertEqual(parse_royalty_percent("100"), 10_000)
        with self.assertRaises(ValidationError):
            parse_royalty_percent("101")
        with self.assertRaises(ValidationError):
            parse_royalty_percent("3.555")

    def test_modal_fields_are_normalized(self) -> None:
        fields = validate_mint_fields(
            name="  Rain   seedling ",
            description="Line one\r\nLine two",
            royalty="3.5",
            attributes="Background: Blue\nHat = Crown",
            sale_price="1.5",
            fallback_description="Prompt text",
        )
        self.assertEqual(fields["name"], "Rain seedling")
        self.assertEqual(fields["description"], "Line one\nLine two")
        self.assertEqual(fields["royalty_hundredths"], 350)
        self.assertEqual(fields["attributes"], (("Background", "Blue"), ("Hat", "Crown")))
        self.assertEqual(fields["sale_price_mojos"], 1_500_000_000_000)
        blank = validate_mint_fields(
            name="Rain seedling",
            description="  ",
            royalty="3",
            fallback_description="Prompt text",
        )
        self.assertEqual(blank["description"], "Prompt text")
        self.assertIsNone(blank["sale_price_mojos"])

    def test_prompt_flags_are_removed(self) -> None:
        content = "**a seedling in the rain --v 6.1 --ar 1:1 --tile** - <@1> (fast)"
        prompt = extract_midjourney_prompt(content)
        self.assertEqual(prompt, "a seedling in the rain")
        self.assertEqual(default_nft_name(prompt, "ignored.png"), "a seedling in the rain")

    def test_mint_payload_matches_the_wallet_fields(self) -> None:
        payload = build_mint_payload(
            wallet_id=4,
            uris=["ipfs://bafyimage"],
            image_hash="ab" * 32,
            meta_uris=["ipfs://bafymeta"],
            meta_hash="cd" * 32,
            license_uris=["ipfs://bafylicense"],
            license_hash="ef" * 32,
            royalty_address=_XCH,
            royalty_percentage=300,
            target_address=_XCH,
            did_id=_DID,
            fee=615_000_000,
        )
        self.assertEqual(payload["edition_number"], 1)
        self.assertEqual(payload["edition_total"], 1)
        self.assertIsInstance(payload["fee"], int)
        self.assertEqual(payload["royalty_percentage"], 300)
        self.assertEqual(payload["license_hash"], "ef" * 32)
        bare = build_mint_payload(
            wallet_id=4,
            uris=["ipfs://bafyimage"],
            image_hash="ab" * 32,
            meta_uris=["ipfs://bafymeta"],
            meta_hash="cd" * 32,
            license_uris=None,
            license_hash=None,
            royalty_address=_XCH,
            royalty_percentage=0,
            target_address=_XCH,
            did_id=_DID,
            fee=0,
        )
        self.assertNotIn("license_uris", bare)

    def test_cli_json_and_nft_id_parsing(self) -> None:
        parsed = parse_cli_json('wallet ready\n{"success": true, "nft_id": "nft1abc"}')
        self.assertEqual(extract_nft_id(parsed), "nft1abc")
        self.assertIn("enough spendable", friendly_chia_error("insufficient funds for fee").lower())


class FilebaseTests(unittest.TestCase):
    def test_gateway_and_cid_helpers(self) -> None:
        cid = "Qm" + "a" * 44
        self.assertEqual(gateway_url_for("https://ipfs.filebase.io", cid), f"https://ipfs.filebase.io/ipfs/{cid}")
        self.assertEqual(gateway_url_for("https://ipfs.filebase.io/ipfs/", cid), f"https://ipfs.filebase.io/ipfs/{cid}")
        self.assertEqual(normalize_cid(f' "{cid}" '), cid)
        self.assertEqual(cid_from_mapping({"X-Amz-Meta-Cid": cid}), cid)
        with self.assertRaises(UploadError):
            normalize_cid("nope")


class SessionTests(unittest.TestCase):
    def test_one_open_mint_and_cooldown(self) -> None:
        store = SessionStore()
        first = make_session()
        self.assertIsNone(store.reserve(first, cooldown=15, now=0))
        second = make_session(key="1:3", source_message_id=3)
        self.assertIn("already", (store.reserve(second, cooldown=15, now=1) or "").lower())
        store.pop(first.key)
        self.assertIn("wait", (store.reserve(second, cooldown=15, now=1) or "").lower())
        self.assertIsNone(store.reserve(second, cooldown=15, now=16))

    def test_same_message_reports_the_open_prompt(self) -> None:
        store = SessionStore()
        first = make_session(prompt_message_id=99)
        store.reserve(first, cooldown=0, now=0)
        duplicate = make_session()
        reason = store.reserve(duplicate, cooldown=0, now=1)
        self.assertIsNotNone(reason)
        assert reason is not None
        self.assertIn("/99", reason)

    def test_expiry_ignores_an_in_flight_mint(self) -> None:
        store = SessionStore()
        session = make_session()
        store.reserve(session, cooldown=0, now=0)
        session.state = "uploading"
        session.created_at = 0
        self.assertEqual(store.expired(10, now=100), [])
        session.state = "queued"
        self.assertEqual(store.expired(10, now=100), [])
        session.state = "confirming"
        self.assertEqual(store.expired(10, now=100), [session])

    def test_queued_mint_allows_another_image(self) -> None:
        store = SessionStore()
        first = make_session()
        self.assertIsNone(store.reserve(first, cooldown=0, now=0))
        first.state = "queued"
        second = make_session(key="1:3", source_message_id=3)
        self.assertIsNone(store.reserve(second, cooldown=0, now=1))
        duplicate = make_session()
        reason = store.reserve(duplicate, cooldown=0, now=1)
        self.assertIsNotNone(reason)
        assert reason is not None
        self.assertIn("already", reason.lower())


class DiscordFormTests(unittest.TestCase):
    def test_views_and_embeds_build(self) -> None:
        session = make_session(state="confirming")
        bot = fake_bot(session)
        prompt = PromptView(bot, session.key)
        self.assertEqual(len(prompt.children), 2)
        confirm = ConfirmView(bot, session.key)
        labels = [child.label for child in confirm.children]
        self.assertIn("Upload only", labels)
        self.assertTrue(any(label and label.startswith("Mint now") for label in labels))
        modal = MetadataModal(bot, session.key)
        self.assertEqual(len(modal.children), 5)
        self.assertFalse(modal.description_input.required)
        self.assertFalse(modal.attributes_input.required)
        self.assertFalse(modal.sale_input.required)
        embed = build_confirm_embed(bot, session)
        self.assertIn("0.000615 XCH", embed.description or "")
        self.assertIn("No Dexie offer", str(embed.fields))
        self.assertIn(_XCH, str(embed.fields))
        created = CreateCollectionModal("10")
        self.assertEqual(len(created.children), 5)
        artifacts = MintArtifacts(
            image_hash=session.image_hash,
            image_cid="bafyimagecidvalue0000000000",
            metadata_cid="bafymetadatacidvalue000000",
            image_ipfs_uri="ipfs://bafyimagecidvalue0000000000",
            metadata_ipfs_uri="ipfs://bafymetadatacidvalue000000",
            image_gateway_url="https://ipfs.filebase.io/ipfs/bafyimagecidvalue0000000000",
            metadata_gateway_url="https://ipfs.filebase.io/ipfs/bafymetadatacidvalue000000",
            nft_id="nft1example",
            metadata_hash="cd" * 32,
        )
        success = build_success_embed(bot, session, artifacts)
        self.assertIn("https://mintgarden.io/nfts/nft1example", success.description or "")
        self.assertEqual(uuid.UUID(_UUID).version, 4)


class CollectionTests(unittest.TestCase):
    def test_seed_create_and_profile_attributes(self) -> None:
        settings = load_settings()
        with tempfile.TemporaryDirectory() as tmp:
            store = CollectionStore(Path(tmp) / "collections.json", settings)
            seeded = store.active()
            assert seeded is not None
            self.assertEqual(seeded.name, "Seedlings")
            self.assertEqual(seeded.royalty_hundredths, 300)
            created = store.create(name="Knights", royalty_hundredths=1000, twitter="sample_art", website="https://example.com")
            self.assertEqual(store.active_id(), created.id)
            with self.assertRaises(ValidationError):
                store.create(name="knights", royalty_hundredths=300)
            updated = store.set_image(
                created.id,
                kind="icon",
                gateway_url="https://ipfs.filebase.io/ipfs/bafyicon",
                ipfs_uri="ipfs://bafyicon",
            )
            self.assertEqual(updated.chip_attributes()[0]["type"], "icon")
            types = [item["type"] for item in updated.chip_attributes()]
            self.assertEqual(types, ["icon", "twitter", "website"])
            licensed = store.set_license(created.id, text="Custom license", filename="LICENSE.txt")
            self.assertEqual(licensed.license_filename, "LICENSE.txt")
            self.assertEqual(licensed.license_uri, "")
            pinned = store.set_license_pin(
                created.id,
                gateway_url="https://ipfs.filebase.io/ipfs/bafylicense",
                ipfs_uri="ipfs://bafylicense",
            )
            self.assertEqual(pinned.license_uri, "https://ipfs.filebase.io/ipfs/bafylicense")
            cleared = store.clear_field(created.id, "icon")
            self.assertEqual(cleared.icon_uri, "")
            reloaded = CollectionStore(Path(tmp) / "collections.json", settings)
            self.assertEqual(reloaded.active_id(), created.id)
            self.assertEqual(reloaded.active().name, "Knights")  # type: ignore[union-attr]

    def test_collection_folder_uses_the_collection_name(self) -> None:
        self.assertEqual(collection_object_prefix("SampleCollection", _UUID), "SampleCollection")
        self.assertEqual(collection_object_prefix("My Collection", _UUID), "My Collection")
        self.assertEqual(collection_object_prefix("a/b\\c", _UUID), "a b c")
        self.assertEqual(collection_object_prefix("   ", _UUID), _UUID)
        self.assertEqual(collection_object_key("SampleCollection", _UUID, "icon"), "SampleCollection/icon")
        self.assertEqual(collection_object_key("SampleCollection", _UUID, "banner"), "SampleCollection/banner")
        self.assertEqual(collection_object_key("SampleCollection", _UUID, "license"), "SampleCollection/license")
        self.assertEqual(collection_image_key("SampleCollection", _UUID, 1, ".png"), "SampleCollection/images/1.png")
        self.assertEqual(collection_image_key("SampleCollection", _UUID, 2, ".jpg"), "SampleCollection/images/2.jpg")
        self.assertEqual(collection_metadata_key("SampleCollection", _UUID, 1), "SampleCollection/metadata/1.json")

    def test_next_number_starts_at_one_and_advances_when_claimed(self) -> None:
        settings = load_settings()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "collections.json"
            store = CollectionStore(path, settings)
            active = store.active()
            assert active is not None
            self.assertEqual(active.next_number, 1)
            saved = json.loads(path.read_text(encoding="utf-8"))
            del saved["collections"][0]["next_number"]
            path.write_text(json.dumps(saved), encoding="utf-8")
            reloaded = CollectionStore(path, settings)
            loaded = reloaded.active()
            assert loaded is not None
            self.assertEqual(loaded.next_number, 1)
            self.assertEqual(reloaded.claim_next_number(loaded.id), 1)
            self.assertEqual(reloaded.peek_next_number(loaded.id), 2)
            persisted = CollectionStore(path, settings).active()
            assert persisted is not None
            self.assertEqual(persisted.next_number, 2)
            saved["collections"][0]["next_number"] = 0
            path.write_text(json.dumps(saved), encoding="utf-8")
            with self.assertRaises(ConfigError):
                CollectionStore(path, settings)
            session = make_session()
            session.defaults = MintDraft(
                name="1",
                description="A seedling in the rain",
                collection_id=loaded.id,
                collection_name=loaded.name,
                royalty_hundredths=loaded.royalty_hundredths,
                automatic_name=True,
            )
            fresh = CollectionStore(Path(tmp) / "other.json", settings)
            bot = fake_bot(session)
            bot.collections = fresh
            modal = MetadataModal(bot, session.key)
            self.assertEqual(modal.offered_number_name, "1")
            self.assertEqual(modal.name_input.default, "1")

    def test_handle_and_price_and_launcher_round_trip(self) -> None:
        self.assertEqual(normalize_twitter("https://x.com/Example_Handle"), "Example_Handle")
        self.assertEqual(parse_sale_price(""), None)
        self.assertEqual(parse_sale_price("0.000615"), 615_000_000)
        launcher = bytes(range(32))
        nft_id = encode_nft_id(launcher)
        self.assertEqual(nft_id_to_launcher_hex(nft_id), launcher.hex())
        self.assertEqual(
            offer_page_url("https://dexie.space/offers/{offer_id}", "abc"),
            "https://dexie.space/offers/abc",
        )


class SaleWaitTests(unittest.IsolatedAsyncioTestCase):
    def test_blank_offer_error_is_temporary(self) -> None:
        self.assertTrue(
            offer_error_is_temporary("Chia wallet RPC rejected the sale offer: Error creating offer:")
        )
        self.assertTrue(offer_error_is_temporary("Error creating offer: None"))
        self.assertTrue(offer_error_is_temporary("Error creating offer: null"))
        self.assertFalse(offer_error_is_temporary("Error creating offer: insufficient funds"))
        self.assertFalse(offer_error_is_temporary("The Chia wallet RPC timed out."))
        self.assertFalse(offer_error_is_temporary("not enough spendable balance"))

    def test_nft_info_spendable(self) -> None:
        self.assertFalse(nft_info_is_spendable(None))
        self.assertFalse(nft_info_is_spendable("missing"))
        self.assertFalse(nft_info_is_spendable({"pending_transaction": True}))
        self.assertTrue(nft_info_is_spendable({"pending_transaction": False}))
        self.assertTrue(nft_info_is_spendable({"nft_coin_id": "0xabc"}))

    def test_open_offer_matches_the_launcher_in_the_summary(self) -> None:
        launcher = "ab" * 32
        self.assertTrue(
            _record_offers_launcher(
                {"summary": {"offered": {launcher: -1}, "requested": {"xch": 1}}},
                launcher,
            )
        )
        self.assertFalse(_record_offers_launcher({"summary": {"offered": {"xch": 1}}}, launcher))
        self.assertFalse(_record_offers_launcher({"trade_id": launcher}, launcher))

    async def test_ready_nft_retries_a_blank_offer_error_then_posts(self) -> None:
        nft_id = encode_nft_id(b"\x11" * 32)
        lock = asyncio.Lock()
        calls = {"create": 0}

        class Minter:
            def nft_get_info(self, nft_id: str) -> dict[str, bool]:
                self.wait_locked = lock.locked()
                return {"pending_transaction": False}

            def find_open_offer_launcher(self, launcher_hex: str) -> bool:
                return False

            def create_sale_offer(self, *, launcher_hex: str, price_mojos: int) -> str:
                calls["create"] += 1
                self.create_locked = lock.locked()
                if calls["create"] == 1:
                    raise ChiaMintError("Chia wallet RPC rejected the sale offer: Error creating offer:")
                return "offer1testoffer"

        class Dexie:
            def post_offer(self, offer: str) -> str:
                return "dexie-id"

        minter = Minter()
        with tempfile.TemporaryDirectory() as tmp:
            settings = SimpleNamespace(
                offer_wait_seconds=30,
                dexie_offer_url="https://dexie.space/offers/{offer_id}",
                offers_dir=Path(tmp),
            )
            with mock.patch("services.sale_service._POLL_SECONDS", 0):
                result = await publish_sale_async(
                    settings,
                    minter,
                    Dexie(),
                    lock,
                    nft_id=nft_id,
                    price_mojos=1_000_000_000_000,
                )
            self.assertTrue((Path(tmp) / f"{nft_id}.offer").is_file())
        self.assertTrue(result.ok)
        self.assertEqual(result.offer_id, "dexie-id")
        self.assertEqual(result.offer_url, "https://dexie.space/offers/dexie-id")
        self.assertEqual(calls["create"], 2)
        self.assertFalse(minter.wait_locked)
        self.assertTrue(minter.create_locked)

    async def test_missing_nft_does_not_create_an_offer_when_the_wait_is_zero(self) -> None:
        class Minter:
            def nft_get_info(self, nft_id: str) -> None:
                return None

            def create_sale_offer(self, *, launcher_hex: str, price_mojos: int) -> str:
                raise AssertionError("should not create an offer")

        result = await publish_sale_async(
            SimpleNamespace(
                offer_wait_seconds=0,
                dexie_offer_url="https://dexie.space/offers/{offer_id}",
                offers_dir=Path("."),
            ),
            Minter(),
            SimpleNamespace(),
            asyncio.Lock(),
            nft_id=encode_nft_id(b"\x22" * 32),
            price_mojos=1,
        )
        self.assertFalse(result.ok)
        self.assertIn("/sell", result.message)

    async def test_timeout_stops_without_an_offer(self) -> None:
        class Minter:
            def nft_get_info(self, nft_id: str) -> None:
                raise ChiaMintError("The Chia wallet RPC timed out.")

            def create_sale_offer(self, *, launcher_hex: str, price_mojos: int) -> str:
                raise AssertionError("should not create an offer")

        result = await publish_sale_async(
            SimpleNamespace(
                offer_wait_seconds=30,
                dexie_offer_url="https://dexie.space/offers/{offer_id}",
                offers_dir=Path("."),
            ),
            Minter(),
            SimpleNamespace(),
            asyncio.Lock(),
            nft_id=encode_nft_id(b"\x33" * 32),
            price_mojos=1,
        )
        self.assertFalse(result.ok)
        self.assertIn("timed out", result.message.lower())

    async def test_an_open_offer_is_not_created_again(self) -> None:
        class Minter:
            def nft_get_info(self, nft_id: str) -> dict[str, bool]:
                return {"pending_transaction": False}

            def find_open_offer_launcher(self, launcher_hex: str) -> bool:
                return True

            def create_sale_offer(self, *, launcher_hex: str, price_mojos: int) -> str:
                raise AssertionError("should not create a second offer")

        result = await publish_sale_async(
            SimpleNamespace(
                offer_wait_seconds=30,
                dexie_offer_url="https://dexie.space/offers/{offer_id}",
                offers_dir=Path("."),
            ),
            Minter(),
            SimpleNamespace(),
            asyncio.Lock(),
            nft_id=encode_nft_id(b"\x44" * 32),
            price_mojos=1,
        )
        self.assertFalse(result.ok)
        self.assertIn("already", result.message.lower())

    async def test_unreadable_nft_info_still_tries_the_offer(self) -> None:
        class Minter:
            def nft_get_info(self, nft_id: str) -> None:
                raise ChiaMintError("Chia wallet RPC rejected the mint: the info call failed.")

            def find_open_offer_launcher(self, launcher_hex: str) -> bool:
                return False

            def create_sale_offer(self, *, launcher_hex: str, price_mojos: int) -> str:
                return "offer1fallback"

        class Dexie:
            def post_offer(self, offer: str) -> str:
                return "dexie-id"

        with tempfile.TemporaryDirectory() as tmp:
            result = await publish_sale_async(
                SimpleNamespace(
                    offer_wait_seconds=30,
                    dexie_offer_url="https://dexie.space/offers/{offer_id}",
                    offers_dir=Path(tmp),
                ),
                Minter(),
                Dexie(),
                asyncio.Lock(),
                nft_id=encode_nft_id(b"\x55" * 32),
                price_mojos=1,
            )
        self.assertTrue(result.ok)
        self.assertEqual(result.offer_id, "dexie-id")


if __name__ == "__main__":
    unittest.main()
