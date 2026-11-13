"""Discord UI for metadata, confirmation, and the mint result.

A raw reaction is not an interaction, and Discord only opens a modal from an
interaction. The reaction therefore posts a button, and that button opens the
modal. Minting starts only after a second confirmation click. That click queues
the mint so the user can start another image while this one runs.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any

import discord

from handlers.sessions import MintDraft, MintSession, remove_temp_file
from services.metadata_generator import validate_mint_fields
from services.mint_service import MintArtifacts
from services.sale_service import SaleResult, publish_sale_async
from utils.errors import ValidationError
from utils.format import format_percent, format_xch, truncate

log = logging.getLogger(__name__)

COLOR_OPEN = 0x3BA55D
COLOR_CONFIRM = 0xFEE75C
COLOR_PROGRESS = 0x5865F2
COLOR_OK = 0x3BA55D
COLOR_DRY = 0xF0B232
COLOR_ERROR = 0xED4245


def build_start_embed(bot: Any, session: MintSession) -> discord.Embed:
    """Explain the next step before any upload or spend."""
    draft = session.defaults
    embed = discord.Embed(
        title="Seedling mint",
        description=(
            "Press **Enter metadata**, review the mint, then confirm. "
            "Nothing is uploaded and no XCH is spent until you confirm."
        ),
        color=COLOR_OPEN,
    )
    embed.add_field(name="File", value=truncate(session.filename, 256), inline=False)
    embed.add_field(name="SHA-256", value=f"`{session.image_hash}`", inline=False)
    embed.add_field(name="Collection", value=truncate(draft.collection_name, 256), inline=False)
    embed.add_field(
        name="Before you continue",
        value=(
            "The form uses whichever collection is active when you press **Enter metadata**. "
            "The name starts as that collection's next number. Type a different name if you want one. "
            "Change the collection with `/collection use` first if this is the wrong one."
        ),
        inline=False,
    )
    embed.add_field(name="Name", value=truncate(draft.name, 256), inline=True)
    if session.extra_image_count:
        embed.add_field(
            name="Images",
            value=(
                f"Using the first image. {session.extra_image_count} other image(s) "
                "on that message stay where they are."
            ),
            inline=False,
        )
    if session.content_was_empty:
        embed.add_field(
            name="Prompt",
            value=(
                "Discord did not return the message text. Enable the Message Content Intent "
                "if you want the Midjourney prompt prefilled."
            ),
            inline=False,
        )
    if session.source_image_url:
        embed.set_image(url=session.source_image_url)
    _stamp(embed, bot.settings.chia_network)
    return embed


def build_confirm_embed(bot: Any, session: MintSession) -> discord.Embed:
    """Show every value that will be written on chain."""
    draft = session.current_draft()
    settings = bot.settings
    fee = format_xch(settings.chia_fee_mojos)
    if settings.dry_run:
        lead = "Dry run is on. **Upload only** pins the files to IPFS and does not spend XCH."
    else:
        lead = (
            f"**Mint now** spends **{fee} XCH** ({settings.chia_fee_mojos} mojos) "
            f"from fee wallet {settings.chia_fee_wallet_id}. "
            "Confirming queues the work, so you can start the next image while this one runs."
        )
        if settings.chia_network == "mainnet":
            lead = "This is mainnet. " + lead
    embed = discord.Embed(
        title=truncate(draft.name, 256),
        description=f"{lead}\n\n{truncate(draft.description, 800)}",
        color=COLOR_CONFIRM,
    )
    embed.add_field(name="Collection", value=f"{draft.collection_name}\n`{draft.collection_id}`", inline=False)
    profile = _collection_profile(bot, draft.collection_id)
    if profile is not None:
        files = []
        files.append("icon set" if profile.icon_uri else "no icon")
        files.append("banner set" if profile.banner_uri else "no banner")
        if profile.twitter:
            files.append(f"X @{profile.twitter}")
        if profile.website:
            files.append(profile.website)
        if profile.license_filename:
            files.append(f"license {profile.license_filename}")
        else:
            files.append("default license")
        embed.add_field(name="Collection profile", value=truncate(" · ".join(files), 1024), inline=False)
    if draft.attributes:
        lines = [f"**{truncate(trait, 80)}**: {truncate(value, 200)}" for trait, value in draft.attributes[:25]]
        embed.add_field(name="Attributes", value=truncate("\n".join(lines), 1024), inline=False)
    else:
        embed.add_field(name="Attributes", value="None", inline=True)
    if draft.sale_price_mojos:
        sale = f"{format_xch(draft.sale_price_mojos)} XCH on Dexie"
        offer_fee = getattr(settings, "offer_fee_mojos", 0)
        if not settings.dry_run and offer_fee:
            sale += f"\nOffer fee {format_xch(offer_fee)} XCH"
    else:
        sale = "No Dexie offer"
    embed.add_field(name="Sale", value=sale, inline=True)
    embed.add_field(
        name="Royalty",
        value=f"{format_percent(draft.royalty_hundredths)}% ({draft.royalty_hundredths})",
        inline=True,
    )
    embed.add_field(name="Edition", value="1 of 1", inline=True)
    if profile is not None:
        number_line = str(profile.next_number)
        if draft.automatic_name:
            number_line += "\nName uses this number."
        else:
            number_line += "\nCustom name is kept."
        embed.add_field(name="Number", value=number_line, inline=True)
    embed.add_field(name="Fee", value=f"{fee} XCH", inline=True)
    embed.add_field(name="Target", value=f"`{settings.chia_target_address}`", inline=False)
    embed.add_field(name="Royalty address", value=f"`{settings.chia_royalty_address}`", inline=False)
    embed.add_field(name="DID", value=f"`{settings.chia_did_id}`", inline=False)
    embed.add_field(name="Image SHA-256", value=f"`{session.image_hash}`", inline=False)
    if session.source_image_url:
        embed.set_thumbnail(url=session.source_image_url)
    _stamp(embed, settings.chia_network)
    return embed


def build_success_embed(bot: Any, session: MintSession, artifacts: MintArtifacts) -> discord.Embed:
    """Report a completed upload or a submitted mint."""
    draft = session.current_draft()
    settings = bot.settings
    lines: list[str] = []
    if artifacts.dry_run:
        title = "Uploaded (dry run)"
        color = COLOR_DRY
        lines.append("The files are on IPFS. No NFT was minted and no XCH was spent.")
        if artifacts.mint_number:
            lines.append(
                f"These files use number {artifacts.mint_number}. "
                "A dry run leaves the counter here, so the next upload uses this number again."
            )
    else:
        title = "NFT minted"
        color = COLOR_OK
        lines.append(f"**{draft.name}** was submitted to the Chia wallet.")
        if artifacts.nft_id and artifacts.nft_id.startswith("nft"):
            mintgarden = settings.mintgarden_nft_url.format(nft_id=artifacts.nft_id)
            spacescan = settings.spacescan_nft_url.format(nft_id=artifacts.nft_id)
            lines.append(f"[MintGarden]({mintgarden}) · [Spacescan]({spacescan})")
        elif not artifacts.nft_id:
            lines.append(
                "The wallet accepted the mint and did not return an NFT id. "
                "Check the NFT wallet for the new coin."
            )
        lines.append("MintGarden can take a few minutes to show the NFT after the transaction confirms.")
    if artifacts.sale_note:
        lines.append(artifacts.sale_note)
    if artifacts.offer_url:
        lines.append(f"[Dexie offer]({artifacts.offer_url})")
    if artifacts.image_gateway_url:
        lines.append(f"[Image gateway]({artifacts.image_gateway_url})")
    if artifacts.metadata_gateway_url:
        lines.append(f"[Metadata gateway]({artifacts.metadata_gateway_url})")
    embed = discord.Embed(title=title, description="\n".join(lines), color=color)
    if artifacts.mint_number:
        embed.add_field(name="Number", value=str(artifacts.mint_number), inline=True)
    if artifacts.nft_id:
        embed.add_field(name="NFT ID", value=f"`{truncate(artifacts.nft_id, 200)}`", inline=False)
    if artifacts.launcher_id and artifacts.launcher_id != artifacts.nft_id:
        embed.add_field(name="Launcher ID", value=f"`{truncate(artifacts.launcher_id, 200)}`", inline=False)
    if artifacts.image_ipfs_uri:
        embed.add_field(name="Image", value=f"`{artifacts.image_ipfs_uri}`", inline=False)
    if artifacts.metadata_ipfs_uri:
        embed.add_field(name="Metadata", value=f"`{artifacts.metadata_ipfs_uri}`", inline=False)
    embed.add_field(name="Image SHA-256", value=f"`{artifacts.image_hash}`", inline=False)
    if artifacts.metadata_hash:
        embed.add_field(name="Metadata SHA-256", value=f"`{artifacts.metadata_hash}`", inline=False)
    if not artifacts.dry_run:
        embed.add_field(name="Fee", value=f"{format_xch(settings.chia_fee_mojos)} XCH", inline=True)
        embed.add_field(name="Royalty", value=f"{format_percent(draft.royalty_hundredths)}%", inline=True)
    if artifacts.image_gateway_url:
        embed.set_image(url=artifacts.image_gateway_url)
    _stamp(embed, settings.chia_network)
    return embed


def build_error_embed(message: str, artifacts: MintArtifacts | None) -> discord.Embed:
    """Show the failure and any CIDs that were uploaded before it."""
    embed = discord.Embed(title="Mint failed", description=truncate(message, 1800), color=COLOR_ERROR)
    if artifacts and artifacts.image_ipfs_uri:
        embed.add_field(name="Image CID", value=f"`{artifacts.image_ipfs_uri}`", inline=False)
    if artifacts and artifacts.metadata_ipfs_uri:
        embed.add_field(name="Metadata CID", value=f"`{artifacts.metadata_ipfs_uri}`", inline=False)
    if artifacts and (artifacts.image_cid or artifacts.metadata_cid):
        embed.add_field(
            name="Uploaded files",
            value=(
                "These CIDs were uploaded before the failure. "
                "Check the wallet before starting again, so the same image is not minted twice."
            ),
            inline=False,
        )
    embed.set_footer(text="xd-mint-bot")
    embed.timestamp = datetime.now(timezone.utc)
    return embed


def build_progress_embed(text: str) -> discord.Embed:
    embed = discord.Embed(title="Mint in progress", description=text, color=COLOR_PROGRESS)
    embed.set_footer(text="xd-mint-bot")
    return embed


def build_simple_embed(title: str, description: str, *, color: int = COLOR_ERROR) -> discord.Embed:
    embed = discord.Embed(title=title, description=description, color=color)
    embed.set_footer(text="xd-mint-bot")
    embed.timestamp = datetime.now(timezone.utc)
    return embed


class PromptView(discord.ui.View):
    """Buttons posted in reply to the seedling reaction."""

    def __init__(self, bot: Any, session_key: str) -> None:
        super().__init__(timeout=bot.settings.session_timeout_seconds)
        self.bot = bot
        self.session_key = session_key

    @discord.ui.button(label="Enter metadata", style=discord.ButtonStyle.primary)
    async def enter(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        session = self.bot.sessions.get(self.session_key)
        denial = _denial(self.bot, session, interaction)
        if denial or session is None:
            await _reject(interaction, denial or "This mint session expired. React with the seedling again.")
            return
        async with session.lock:
            waiting = session.state != "awaiting_metadata"
        if waiting:
            await _reject(interaction, "This mint has already moved on.")
            return
        await interaction.response.send_modal(MetadataModal(self.bot, self.session_key))

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        session = self.bot.sessions.get(self.session_key)
        if session is None:
            await _reject(interaction, "This mint session expired. React with the seedling again.")
            return
        await cancel_session(self.bot, session, interaction)

    async def on_timeout(self) -> None:
        session = self.bot.sessions.get(self.session_key)
        if session is not None:
            await expire_session(
                self.bot,
                session,
                view=self,
                reason="The mint session timed out. React with the seedling again to start over.",
            )


class MetadataModal(discord.ui.Modal, title="Chia NFT details"):
    """Five fields, which is Discord's modal limit.

    Collection id and name come from the active collection. Description may be
    left blank and then keeps the prefilled prompt.
    """

    def __init__(self, bot: Any, session_key: str) -> None:
        super().__init__(timeout=600)
        self.bot = bot
        self.session_key = session_key
        session = bot.sessions.get(session_key)
        draft = session.current_draft() if session is not None else None
        # Defaults must already satisfy Discord's required fields, even if the
        # session disappeared between the click and this constructor.
        offered_number_name = _offered_number_name(bot, session)
        self.offered_number_name = offered_number_name
        name = offered_number_name if offered_number_name is not None else (draft.name if draft else "Untitled NFT")
        description = draft.description if draft else "Minted with xd-mint-bot."
        royalty = format_percent(draft.royalty_hundredths) if draft else "3"
        attribute_text = ""
        sale_text = ""
        if draft is not None and session is not None and session.draft is not None:
            attribute_text = "\n".join(f"{trait}: {value}" for trait, value in draft.attributes)
            if draft.sale_price_mojos:
                sale_text = format_xch(draft.sale_price_mojos)
            collection = _collection_profile(bot, draft.collection_id)
            if collection is not None:
                royalty = format_percent(draft.royalty_hundredths)
        elif session is not None:
            active = _active_collection(bot)
            if active is not None:
                royalty = format_percent(active.royalty_hundredths)
        self.name_input = discord.ui.TextInput(
            label="Name",
            default=truncate(name, 100),
            max_length=100,
            min_length=1,
            required=True,
            style=discord.TextStyle.short,
        )
        self.description_input = discord.ui.TextInput(
            label="Description",
            default=truncate(description, 1500),
            max_length=1500,
            required=False,
            placeholder="Leave blank to keep the prefilled prompt.",
            style=discord.TextStyle.paragraph,
        )
        self.royalty_input = discord.ui.TextInput(
            label="Royalty percent",
            default=royalty,
            placeholder="3 means 3 percent",
            max_length=6,
            min_length=1,
            required=True,
            style=discord.TextStyle.short,
        )
        self.attributes_input = discord.ui.TextInput(
            label="Attributes",
            default=truncate(attribute_text, 1000) or None,
            max_length=1000,
            required=False,
            placeholder="Optional. One per line, for example Background: Blue",
            style=discord.TextStyle.paragraph,
        )
        self.sale_input = discord.ui.TextInput(
            label="Sale price (XCH)",
            default=sale_text or None,
            max_length=20,
            required=False,
            placeholder="Optional. 1.5 lists it on Dexie. Blank skips the offer.",
            style=discord.TextStyle.short,
        )
        self.add_item(self.name_input)
        self.add_item(self.description_input)
        self.add_item(self.royalty_input)
        self.add_item(self.attributes_input)
        self.add_item(self.sale_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        session = self.bot.sessions.get(self.session_key)
        denial = _denial(self.bot, session, interaction)
        if denial or session is None:
            await _reject(interaction, denial or "This mint session expired. React with the seedling again.")
            return
        if session.draft is None:
            collection = _active_collection(self.bot)
        else:
            collection = _collection_profile(self.bot, session.draft.collection_id) or _active_collection(self.bot)
        if collection is None:
            await _reject(interaction, "No collection is active. Use /collection create or /collection use, then try again.")
            return
        try:
            fields = validate_mint_fields(
                name=str(self.name_input.value),
                description=str(self.description_input.value),
                royalty=str(self.royalty_input.value),
                attributes=str(self.attributes_input.value or ""),
                sale_price=str(self.sale_input.value or ""),
                fallback_description=session.defaults.description,
            )
        except ValidationError as exc:
            await interaction.response.send_message(
                f"{exc}\nPress Enter metadata or Edit and try again.",
                ephemeral=True,
            )
            return
        draft = MintDraft(
            name=fields["name"],
            description=fields["description"],
            collection_id=collection.id,
            collection_name=collection.name,
            royalty_hundredths=fields["royalty_hundredths"],
            attributes=fields["attributes"],
            sale_price_mojos=fields["sale_price_mojos"],
            automatic_name=self.offered_number_name is not None and fields["name"] == self.offered_number_name,
        )
        # Hold the lock across the message edit so a Mint click cannot start
        # on the previous confirmation while this one is being published.
        async with session.lock:
            if session.state not in {"awaiting_metadata", "confirming"}:
                await _reject(interaction, "This mint has already moved on.")
                return
            session.draft = draft
            session.state = "confirming"
            session.created_at = time.monotonic()
            view = ConfirmView(self.bot, self.session_key)
            previous = session.active_view
            session.active_view = view
            if previous is not None and previous is not view and hasattr(previous, "stop"):
                previous.stop()
            try:
                await interaction.response.edit_message(
                    content=f"<@{session.user_id}> review this mint before anything is spent.",
                    embed=build_confirm_embed(self.bot, session),
                    view=view,
                )
            except discord.HTTPException:
                log.exception("Could not publish the confirmation for %s", session.key)
                session.state = "cancelled"
                session.active_view = None
                self.bot.sessions.pop(session.key)
                remove_temp_file(session.image_path)
                await _reject(interaction, "Could not show the confirmation. React with the seedling again.")
                return
        log.info("User %s confirmed metadata for %s (%s)", session.user_id, draft.name, session.key)


class ConfirmView(discord.ui.View):
    """Mint, dry-run upload, edit, or cancel."""

    def __init__(self, bot: Any, session_key: str) -> None:
        super().__init__(timeout=bot.settings.session_timeout_seconds)
        self.bot = bot
        self.session_key = session_key
        if not bot.settings.dry_run:
            fee_label = f"Mint now ({format_xch(bot.settings.chia_fee_mojos)} XCH)"
            if len(fee_label) > 80:
                fee_label = "Mint now"
            self.add_item(_ActionButton(label=fee_label, style=discord.ButtonStyle.success, action="mint"))
        upload_style = discord.ButtonStyle.primary if bot.settings.dry_run else discord.ButtonStyle.secondary
        self.add_item(_ActionButton(label="Upload only", style=upload_style, action="dry_run"))
        self.add_item(_ActionButton(label="Edit", style=discord.ButtonStyle.secondary, action="edit"))
        self.add_item(_ActionButton(label="Cancel", style=discord.ButtonStyle.danger, action="cancel"))

    async def handle(self, interaction: discord.Interaction, action: str) -> None:
        session = self.bot.sessions.get(self.session_key)
        denial = _denial(self.bot, session, interaction)
        if denial or session is None:
            await _reject(interaction, denial or "This mint session expired. React with the seedling again.")
            return
        if action == "cancel":
            await cancel_session(self.bot, session, interaction)
            return
        if action == "edit":
            async with session.lock:
                editable = session.state == "confirming"
            if not editable:
                await _reject(interaction, "This mint has already moved on.")
                return
            await interaction.response.send_modal(MetadataModal(self.bot, self.session_key))
            return

        async with session.lock:
            if session.state != "confirming":
                ready = False
            else:
                session.state = "queued"
                ready = True
                self.stop()
        if not ready:
            await _reject(interaction, "This mint has already moved on.")
            return
        dry_run = action == "dry_run" or self.bot.settings.dry_run
        try:
            await interaction.response.defer()
        except discord.HTTPException:
            log.exception("Could not acknowledge the mint confirmation for %s", session.key)
            await _finish_session(self.bot, session, keep_file=False)
            return
        message = interaction.message
        if message is None:
            await _finish_session(self.bot, session, keep_file=False)
            return
        await _safe_message_edit(
            message,
            content=f"<@{session.user_id}> this mint is queued. You can start the next image.",
            embed=build_progress_embed(
                "Queued. This image uploads in the background. "
                "A sale offer waits until the NFT is in the wallet."
            ),
            view=None,
        )
        queue = getattr(self.bot, "mint_jobs", None)
        if queue is None:
            await run_confirmed_mint(self.bot, session, message, dry_run=dry_run)
            return
        queue.put_nowait((session.key, message, dry_run))

    async def on_timeout(self) -> None:
        session = self.bot.sessions.get(self.session_key)
        if session is not None:
            await expire_session(
                self.bot,
                session,
                view=self,
                reason="The mint session timed out. React with the seedling again to start over.",
            )


class _ActionButton(discord.ui.Button):
    def __init__(self, *, label: str, style: discord.ButtonStyle, action: str) -> None:
        super().__init__(label=label, style=style)
        self.action = action

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, ConfirmView):
            await _reject(interaction, "This button is no longer active.")
            return
        await view.handle(interaction, self.action)


async def cancel_session(bot: Any, session: MintSession, interaction: discord.Interaction) -> None:
    """Stop a mint that has not uploaded yet."""
    denial = _denial(bot, session, interaction)
    if denial:
        await _reject(interaction, denial)
        return
    async with session.lock:
        if session.state not in {"awaiting_metadata", "confirming"}:
            blocked = True
            view = None
        else:
            blocked = False
            session.state = "cancelled"
            view = session.active_view
            session.active_view = None
            bot.sessions.pop(session.key)
    if blocked:
        await _reject(interaction, "This mint can no longer be cancelled.")
        return
    _stop_view(view)
    remove_temp_file(session.image_path)
    log.info("User %s cancelled mint %s", session.user_id, session.key)
    try:
        await interaction.response.edit_message(
            content="Mint cancelled.",
            embed=build_simple_embed("Mint cancelled", "No files were uploaded and no XCH was spent."),
            view=None,
        )
    except discord.HTTPException:
        log.warning("Could not show the cancellation message for %s", session.key, exc_info=True)


async def expire_session(bot: Any, session: MintSession, *, view: object | None, reason: str) -> None:
    """Drop a session whose buttons timed out. In-flight mints are left alone."""
    async with session.lock:
        if session.active_view is not view:
            return
        if session.state not in {"awaiting_metadata", "confirming"}:
            return
        if bot.sessions.get(session.key) is not session:
            return
        session.state = "cancelled"
        session.active_view = None
        bot.sessions.pop(session.key)
    _stop_view(view)
    remove_temp_file(session.image_path)
    log.info("Mint session %s expired", session.key)
    await _edit_stored_message(
        bot,
        session,
        content="Mint session expired.",
        embed=build_simple_embed("Mint session expired", reason),
        view=None,
    )


async def notify_queue_dropped(bot: Any, session: MintSession) -> None:
    """Tell Discord that a restart dropped a mint that had not finished."""
    await _edit_stored_message(
        bot,
        session,
        content="This mint was still queued when the bot stopped.",
        embed=build_simple_embed(
            "Mint stopped",
            "The bot restarted before this mint finished. React with the seedling again to start over. "
            "If the wallet already accepted it, do not mint the same image twice.",
        ),
        view=None,
    )


async def run_confirmed_mint(bot: Any, session: MintSession, message: discord.Message, *, dry_run: bool) -> None:
    """Upload, maybe mint, then edit the same Discord message with the result.

    A real mint with a sale price schedules the offer after this session is
    finished. The offer wait does not hold the mint, and the temp image is
    already deleted.
    """
    async with session.lock:
        if session.state != "queued":
            log.info("Skipping mint %s in state %s", session.key, session.state)
            return
        session.state = "uploading"
    succeeded = False
    offer_price: int | None = None
    artifacts: MintArtifacts | None = None
    try:
        async def on_stage(text: str) -> None:
            await _safe_message_edit(
                message,
                content="Working on the mint…",
                embed=build_progress_embed(text),
                view=None,
            )

        outcome = await bot.mints.upload_and_mint(
            session,
            session.current_draft(),
            dry_run=dry_run,
            on_stage=on_stage,
        )
        succeeded = outcome.ok
        if outcome.ok:
            verb = "uploaded" if outcome.artifacts.dry_run else "minted"
            await _safe_message_edit(
                message,
                content=f"<@{session.user_id}> {verb} **{session.current_draft().name}**.",
                embed=build_success_embed(bot, session, outcome.artifacts),
                view=None,
            )
            if not outcome.artifacts.dry_run:
                await _react_checkmark(bot, session)
            price = session.current_draft().sale_price_mojos
            if price and outcome.artifacts.nft_id and not outcome.artifacts.dry_run:
                offer_price = price
                artifacts = outcome.artifacts
            log.info(
                "Mint flow finished for user %s nft_id=%s dry_run=%s",
                session.user_id,
                outcome.artifacts.nft_id,
                outcome.artifacts.dry_run,
            )
        else:
            await _safe_message_edit(
                message,
                content=f"<@{session.user_id}> the mint did not complete.",
                embed=build_error_embed(outcome.message, outcome.artifacts),
                view=None,
            )
    except Exception:
        log.exception("Mint flow failed for session %s", session.key)
        await _safe_message_edit(
            message,
            content=f"<@{session.user_id}> the mint did not complete.",
            embed=build_error_embed(
                "Something unexpected went wrong. The details are in the bot log.",
                None,
            ),
            view=None,
        )
    finally:
        keep = bot.settings.keep_temp_on_error and not succeeded
        await _finish_session(bot, session, keep_file=keep)
    if offer_price is not None and artifacts is not None:
        _schedule_sale_offer(bot, session, message, artifacts, offer_price)


def _schedule_sale_offer(
    bot: Any,
    session: MintSession,
    message: discord.Message,
    artifacts: MintArtifacts,
    price_mojos: int,
) -> None:
    """List the NFT after the mint session has been released."""
    task = asyncio.create_task(
        _finish_sale_offer(bot, session, message, artifacts, price_mojos),
        name=f"sale-offer-{artifacts.nft_id}",
    )
    tasks = getattr(bot, "offer_tasks", None)
    if isinstance(tasks, set):
        tasks.add(task)
        task.add_done_callback(tasks.discard)


async def _finish_sale_offer(
    bot: Any,
    session: MintSession,
    message: discord.Message,
    artifacts: MintArtifacts,
    price_mojos: int,
) -> None:
    """Wait for the NFT, create the offer, and update the mint message."""
    nft_id = artifacts.nft_id or ""
    try:
        result = await publish_sale_async(
            bot.settings,
            bot.minter,
            bot.dexie,
            bot.mints.lock,
            nft_id=nft_id,
            price_mojos=price_mojos,
        )
    except Exception:
        log.exception("Sale offer failed for %s", nft_id)
        result = SaleResult(
            ok=False,
            message="The sale offer failed. The details are in the bot log. You can try /sell.",
        )
    artifacts.offer_id = result.offer_id
    artifacts.offer_url = result.offer_url
    artifacts.sale_note = result.message
    name = session.current_draft().name
    await _safe_message_edit(
        message,
        content=f"<@{session.user_id}> minted **{name}**.",
        embed=build_success_embed(bot, session, artifacts),
        view=None,
    )
    if result.ok:
        log.info(
            "Sale offer posted for user %s nft_id=%s offer=%s",
            session.user_id,
            nft_id,
            result.offer_id,
        )
    else:
        log.warning("Mint succeeded and the sale offer did not: %s", result.message)


async def _finish_session(bot: Any, session: MintSession, *, keep_file: bool) -> None:
    session.state = "done"
    bot.sessions.pop(session.key)
    if keep_file and session.image_path is not None:
        log.warning("Kept temporary image for debugging: %s", session.image_path)
        return
    remove_temp_file(session.image_path)


async def _edit_stored_message(bot: Any, session: MintSession, **kwargs: Any) -> None:
    if session.prompt_message_id is None:
        return
    try:
        channel = bot.get_channel(session.channel_id) or await bot.fetch_channel(session.channel_id)
        message = await channel.fetch_message(session.prompt_message_id)
        await message.edit(**kwargs)
    except discord.HTTPException:
        log.warning("Could not edit mint message %s", session.prompt_message_id, exc_info=True)


async def _safe_message_edit(message: discord.Message, **kwargs: Any) -> None:
    try:
        await message.edit(**kwargs)
    except discord.HTTPException:
        log.warning("Could not edit message %s", message.id, exc_info=True)


async def _react_checkmark(bot: Any, session: MintSession) -> None:
    try:
        channel = bot.get_channel(session.channel_id) or await bot.fetch_channel(session.channel_id)
        source = await channel.fetch_message(session.source_message_id)
        await source.add_reaction("✅")
    except discord.HTTPException:
        log.warning("Could not add the checkmark reaction on message %s", session.source_message_id, exc_info=True)


async def _reject(interaction: discord.Interaction, text: str) -> None:
    try:
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)
    except discord.HTTPException:
        log.warning("Could not send an interaction rejection", exc_info=True)


def _denial(bot: Any, session: MintSession | None, interaction: discord.Interaction) -> str | None:
    if session is None:
        return "This mint session expired. React with the seedling again."
    user = interaction.user
    if user is None or user.id != session.user_id:
        return "Only the person who reacted can continue this mint."
    if user.id not in bot.settings.authorized_user_ids:
        return "You are not allowed to mint."
    return None


def _stop_view(view: object | None) -> None:
    if view is not None and hasattr(view, "stop"):
        view.stop()


def _offered_number_name(bot: Any, session: MintSession | None) -> str | None:
    """Return the number prefilled into the name field, or None when the user already set a custom name."""
    if session is None:
        return None
    if session.draft is not None and not session.draft.automatic_name:
        return None
    if session.draft is None:
        collection = _active_collection(bot)
    else:
        collection = _collection_profile(bot, session.draft.collection_id) or _active_collection(bot)
    if collection is not None:
        return str(collection.next_number)
    if session.current_draft().automatic_name:
        return session.current_draft().name
    return None


def _active_collection(bot: Any):
    store = getattr(bot, "collections", None)
    if store is None:
        return None
    return store.active()


def _collection_profile(bot: Any, collection_id: str):
    store = getattr(bot, "collections", None)
    if store is None:
        return None
    return store.get(collection_id)


def _stamp(embed: discord.Embed, network: str) -> None:
    embed.set_footer(text=f"xd-mint-bot • {network}")
    embed.timestamp = datetime.now(timezone.utc)
