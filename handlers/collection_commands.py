"""Slash commands for collections and for listing an existing NFT on Dexie."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

import discord
from discord import app_commands

from services.filebase_uploader import ipfs_uri
from services.metadata_generator import (
    clean_description,
    clean_single_line,
    normalize_twitter,
    normalize_website,
    parse_royalty_percent,
    parse_sale_price,
)
from services.sale_service import publish_sale_async
from utils.errors import MintBotError, ValidationError
from utils.format import collection_object_key, format_percent, format_xch, truncate

log = logging.getLogger(__name__)

_IMAGE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}

collection = app_commands.Group(
    name="collection",
    description="Create NFT collections and choose the one new mints use.",
)


async def _complete_collection(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    bot: Any = interaction.client
    store = getattr(bot, "collections", None)
    if store is None:
        return []
    folded = current.casefold()
    active_id = store.active_id()
    choices: list[app_commands.Choice[str]] = []
    for item in store.list():
        if folded and folded not in item.name.casefold():
            continue
        label = item.name if item.id != active_id else f"{item.name} (active)"
        choices.append(app_commands.Choice(name=label[:100], value=item.id))
        if len(choices) == 25:
            break
    return choices


async def _denied(interaction: discord.Interaction) -> bool:
    """Reply and return True when this user cannot manage the minter."""
    bot: Any = interaction.client
    settings = bot.settings
    if interaction.guild_id is None:
        await interaction.response.send_message("Use this command in a server.", ephemeral=True)
        return True
    if settings.allowed_guild_ids and interaction.guild_id not in settings.allowed_guild_ids:
        await interaction.response.send_message("This server is not allowed to use the minter.", ephemeral=True)
        return True
    if interaction.user.id not in settings.authorized_user_ids:
        await interaction.response.send_message("You are not allowed to use this command.", ephemeral=True)
        return True
    return False


def _resolve_collection(bot: Any, collection_id: str | None):
    store = bot.collections
    if collection_id:
        item = store.get(collection_id)
        if item is None:
            raise ValidationError("That collection is not saved. Use /collection list.")
        return item
    item = store.active()
    if item is None:
        raise ValidationError("No collection is active. Create one with /collection create.")
    return item


class CreateCollectionModal(discord.ui.Modal, title="New NFT collection"):
    """Text profile for a collection. Icon, banner, and license are separate commands."""

    def __init__(self, royalty: str = "3") -> None:
        super().__init__(timeout=600)
        self.name_input = discord.ui.TextInput(
            label="Collection name",
            max_length=100,
            min_length=1,
            required=True,
        )
        self.description_input = discord.ui.TextInput(
            label="Collection description",
            required=False,
            max_length=1000,
            style=discord.TextStyle.paragraph,
            placeholder="Optional. Explorers show this with the collection.",
        )
        self.royalty_input = discord.ui.TextInput(
            label="Default royalty percent",
            default=royalty,
            max_length=6,
            min_length=1,
            required=True,
            placeholder="3 means 3 percent",
        )
        self.twitter_input = discord.ui.TextInput(
            label="X handle",
            required=False,
            max_length=80,
            placeholder="ExampleHandle",
        )
        self.website_input = discord.ui.TextInput(
            label="Website",
            required=False,
            max_length=300,
            placeholder="https://example.com",
        )
        self.add_item(self.name_input)
        self.add_item(self.description_input)
        self.add_item(self.royalty_input)
        self.add_item(self.twitter_input)
        self.add_item(self.website_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        bot: Any = interaction.client
        try:
            item = bot.collections.create(
                name=clean_single_line(str(self.name_input.value), field="Collection name", limit=100),
                royalty_hundredths=parse_royalty_percent(str(self.royalty_input.value)),
                description=clean_description(str(self.description_input.value), limit=1000, required=False),
                twitter=normalize_twitter(str(self.twitter_input.value)),
                website=normalize_website(str(self.website_input.value)),
            )
        except MintBotError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        log.info("User %s created collection %s (%s)", interaction.user.id, item.name, item.id)
        await interaction.response.send_message(
            (
                f"**{item.name}** is now the active collection.\n"
                "Add files with `/collection icon`, `/collection banner`, and `/collection license` "
                "before the next mint if you want them in the metadata."
            ),
            ephemeral=True,
        )


class EditCollectionModal(discord.ui.Modal, title="Edit NFT collection"):
    """Replace the text profile. Uploaded icon, banner, and license stay put."""

    def __init__(self, bot: Any, collection_id: str) -> None:
        super().__init__(timeout=600)
        self.collection_id = collection_id
        item = bot.collections.get(collection_id)
        name = item.name if item else "Collection"
        description = item.description if item else ""
        royalty = format_percent(item.royalty_hundredths) if item else "3"
        twitter = item.twitter if item else ""
        website = item.website if item else ""
        self.name_input = discord.ui.TextInput(
            label="Collection name",
            default=truncate(name, 100),
            max_length=100,
            min_length=1,
            required=True,
        )
        self.description_input = discord.ui.TextInput(
            label="Collection description",
            default=truncate(description, 1000) or None,
            required=False,
            max_length=1000,
            style=discord.TextStyle.paragraph,
        )
        self.royalty_input = discord.ui.TextInput(
            label="Default royalty percent",
            default=royalty,
            max_length=6,
            min_length=1,
            required=True,
        )
        self.twitter_input = discord.ui.TextInput(
            label="X handle",
            default=twitter or None,
            required=False,
            max_length=80,
        )
        self.website_input = discord.ui.TextInput(
            label="Website",
            default=website or None,
            required=False,
            max_length=300,
        )
        self.add_item(self.name_input)
        self.add_item(self.description_input)
        self.add_item(self.royalty_input)
        self.add_item(self.twitter_input)
        self.add_item(self.website_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        bot: Any = interaction.client
        try:
            item = bot.collections.update_profile(
                self.collection_id,
                name=clean_single_line(str(self.name_input.value), field="Collection name", limit=100),
                royalty_hundredths=parse_royalty_percent(str(self.royalty_input.value)),
                description=clean_description(str(self.description_input.value), limit=1000, required=False),
                twitter=normalize_twitter(str(self.twitter_input.value)),
                website=normalize_website(str(self.website_input.value)),
            )
        except MintBotError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        log.info("User %s edited collection %s", interaction.user.id, item.id)
        await interaction.response.send_message(
            f"Updated **{item.name}**. Icon, banner, and license were left as they are.",
            ephemeral=True,
        )


@collection.command(name="create", description="Create a collection and make it the active one.")
@app_commands.guild_only()
async def collection_create(interaction: discord.Interaction) -> None:
    if await _denied(interaction):
        return
    royalty = format_percent(interaction.client.settings.default_royalty_percentage)  # type: ignore[attr-defined]
    await interaction.response.send_modal(CreateCollectionModal(royalty))


@collection.command(name="edit", description="Edit the name, royalty, description, X handle, or website.")
@app_commands.describe(collection="Collection to edit. Defaults to the active one.")
@app_commands.autocomplete(collection=_complete_collection)
@app_commands.guild_only()
async def collection_edit(interaction: discord.Interaction, collection: str | None = None) -> None:
    if await _denied(interaction):
        return
    try:
        item = _resolve_collection(interaction.client, collection)
    except ValidationError as exc:
        await interaction.response.send_message(str(exc), ephemeral=True)
        return
    await interaction.response.send_modal(EditCollectionModal(interaction.client, item.id))


@collection.command(name="list", description="List saved collections.")
@app_commands.guild_only()
async def collection_list(interaction: discord.Interaction) -> None:
    if await _denied(interaction):
        return
    bot: Any = interaction.client
    active_id = bot.collections.active_id()
    lines = []
    for item in bot.collections.list():
        marker = "active" if item.id == active_id else "saved"
        lines.append(f"**{item.name}** ({marker})\n`{item.id}`")
    text = "\n".join(lines) if lines else "No collections are saved yet."
    await interaction.response.send_message(truncate(text, 1800), ephemeral=True)


@collection.command(name="use", description="Choose the collection used the next time you open the mint form.")
@app_commands.describe(collection="Saved collection")
@app_commands.autocomplete(collection=_complete_collection)
@app_commands.guild_only()
async def collection_use(interaction: discord.Interaction, collection: str) -> None:
    if await _denied(interaction):
        return
    try:
        item = interaction.client.collections.set_active(collection)  # type: ignore[attr-defined]
    except MintBotError as exc:
        await interaction.response.send_message(str(exc), ephemeral=True)
        return
    log.info("User %s set active collection %s", interaction.user.id, item.id)
    await interaction.response.send_message(
        f"**{item.name}** is the active collection. The next mint form will use it.",
        ephemeral=True,
    )


@collection.command(name="show", description="Show one collection, including icon, banner, and license.")
@app_commands.describe(collection="Collection to show. Defaults to the active one.")
@app_commands.autocomplete(collection=_complete_collection)
@app_commands.guild_only()
async def collection_show(interaction: discord.Interaction, collection: str | None = None) -> None:
    if await _denied(interaction):
        return
    try:
        item = _resolve_collection(interaction.client, collection)
    except ValidationError as exc:
        await interaction.response.send_message(str(exc), ephemeral=True)
        return
    bot: Any = interaction.client
    active = "Yes" if item.id == bot.collections.active_id() else "No"
    embed = discord.Embed(title=item.name, description=truncate(item.description, 1000) or "No collection description.")
    embed.add_field(name="Id", value=f"`{item.id}`", inline=False)
    embed.add_field(name="Active", value=active, inline=True)
    embed.add_field(name="Royalty", value=f"{format_percent(item.royalty_hundredths)}%", inline=True)
    embed.add_field(name="Next number", value=str(item.next_number), inline=True)
    embed.add_field(name="X", value=item.twitter or "Not set", inline=True)
    embed.add_field(name="Website", value=item.website or "Not set", inline=False)
    embed.add_field(name="Icon", value=item.icon_uri or "Not set", inline=False)
    embed.add_field(name="Banner", value=item.banner_uri or "Not set", inline=False)
    if item.license_filename:
        license_line = f"Custom file: {item.license_filename}"
        if item.license_uri:
            license_line = f"{license_line}\n{item.license_uri}"
    else:
        license_line = "The mint uses LICENSE_FILE, or the built-in CC0 text."
    embed.add_field(name="License", value=license_line, inline=False)
    if item.icon_uri:
        embed.set_thumbnail(url=item.icon_uri)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@collection.command(name="icon", description="Upload the collection icon to Filebase and store it on the collection.")
@app_commands.describe(image="PNG, JPG, WEBP, or GIF", collection="Collection to update. Defaults to the active one.")
@app_commands.autocomplete(collection=_complete_collection)
@app_commands.guild_only()
async def collection_icon(
    interaction: discord.Interaction,
    image: discord.Attachment,
    collection: str | None = None,
) -> None:
    await _upload_image(interaction, image, kind="icon", collection_id=collection)


@collection.command(name="banner", description="Upload the collection banner to Filebase and store it on the collection.")
@app_commands.describe(image="PNG, JPG, WEBP, or GIF", collection="Collection to update. Defaults to the active one.")
@app_commands.autocomplete(collection=_complete_collection)
@app_commands.guild_only()
async def collection_banner(
    interaction: discord.Interaction,
    image: discord.Attachment,
    collection: str | None = None,
) -> None:
    await _upload_image(interaction, image, kind="banner", collection_id=collection)


@collection.command(name="license", description="Upload a license file to Filebase for this collection.")
@app_commands.describe(
    file="UTF-8 text file. It is pinned as license in the collection folder.",
    collection="Collection to update. Defaults to the active one.",
)
@app_commands.autocomplete(collection=_complete_collection)
@app_commands.guild_only()
async def collection_license(
    interaction: discord.Interaction,
    file: discord.Attachment,
    collection: str | None = None,
) -> None:
    if await _denied(interaction):
        return
    await interaction.response.defer(ephemeral=True)
    try:
        item = _resolve_collection(interaction.client, collection)
        data = await file.read()
        if b"\x00" in data:
            raise ValidationError("The license file must be plain text.")
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValidationError("The license file must be UTF-8 text.") from exc
        bot: Any = interaction.client
        updated = bot.collections.set_license(
            item.id,
            text=text,
            filename=Path(file.filename or "license.txt").name,
        )
        cid = await asyncio.to_thread(
            bot.uploader.upload_bytes,
            updated.license_text.encode("utf-8"),
            collection_object_key(updated.name, updated.id, "license"),
            "text/plain",
        )
        updated = bot.collections.set_license_pin(
            updated.id,
            gateway_url=bot.uploader.gateway_url(cid),
            ipfs_uri=ipfs_uri(cid),
        )
    except MintBotError as exc:
        await interaction.followup.send(str(exc), ephemeral=True)
        return
    log.info("User %s set license for collection %s cid=%s", interaction.user.id, updated.id, cid)
    await interaction.followup.send(
        (
            f"Uploaded the license for **{updated.name}**.\n{updated.license_uri}\n"
            "The Filebase object is named `license`."
        ),
        ephemeral=True,
    )


@collection.command(name="clear", description="Remove an icon, banner, social link, description, or custom license.")
@app_commands.describe(field="What to remove", collection="Collection to update. Defaults to the active one.")
@app_commands.choices(
    field=[
        app_commands.Choice(name="Icon", value="icon"),
        app_commands.Choice(name="Banner", value="banner"),
        app_commands.Choice(name="X handle", value="twitter"),
        app_commands.Choice(name="Website", value="website"),
        app_commands.Choice(name="Description", value="description"),
        app_commands.Choice(name="License", value="license"),
    ]
)
@app_commands.autocomplete(collection=_complete_collection)
@app_commands.guild_only()
async def collection_clear(
    interaction: discord.Interaction,
    field: app_commands.Choice[str],
    collection: str | None = None,
) -> None:
    if await _denied(interaction):
        return
    try:
        item = _resolve_collection(interaction.client, collection)
        updated = interaction.client.collections.clear_field(item.id, field.value)  # type: ignore[attr-defined]
    except MintBotError as exc:
        await interaction.response.send_message(str(exc), ephemeral=True)
        return
    await interaction.response.send_message(
        f"Cleared {field.name.lower()} on **{updated.name}**.",
        ephemeral=True,
    )


@app_commands.command(name="sell", description="Offer an NFT you already hold for sale on Dexie.")
@app_commands.describe(nft_id="The nft1 id to sell", price="Price in XCH, for example 1.5")
@app_commands.guild_only()
async def sell(interaction: discord.Interaction, nft_id: str, price: str) -> None:
    """Create a sale offer for an NFT that is already in the wallet."""
    if await _denied(interaction):
        return
    await interaction.response.defer(ephemeral=True)
    bot: Any = interaction.client
    try:
        mojos = parse_sale_price(price)
        if mojos is None:
            raise ValidationError("Enter a sale price in XCH.")
    except ValidationError as exc:
        await interaction.followup.send(str(exc), ephemeral=True)
        return
    fee_note = (
        f"The offer fee is {format_xch(bot.settings.offer_fee_mojos)} XCH. "
        if bot.settings.offer_fee_mojos
        else "The creator fee is 0 XCH. "
    )
    await interaction.followup.send(
        (
            f"Creating an offer for **{format_xch(mojos)} XCH**. "
            f"{fee_note}"
            "This can take a few minutes if the NFT is still confirming."
        ),
        ephemeral=True,
    )
    result = await publish_sale_async(
        bot.settings,
        bot.minter,
        bot.dexie,
        bot.mints.lock,
        nft_id=nft_id.strip(),
        price_mojos=mojos,
    )
    if result.ok and result.offer_url:
        await interaction.followup.send(f"Listed on Dexie: {result.offer_url}", ephemeral=True)
        return
    await interaction.followup.send(result.message, ephemeral=True)


async def _upload_image(
    interaction: discord.Interaction,
    image: discord.Attachment,
    *,
    kind: str,
    collection_id: str | None,
) -> None:
    if await _denied(interaction):
        return
    await interaction.response.defer(ephemeral=True)
    bot: Any = interaction.client
    try:
        item = _resolve_collection(bot, collection_id)
        filename = image.filename or f"{kind}.png"
        extension = Path(filename).suffix.lower()
        if extension not in _IMAGE_TYPES:
            raise ValidationError("Use a PNG, JPG, WEBP, or GIF file.")
        if image.size and image.size > bot.settings.max_image_bytes:
            raise ValidationError("That image is larger than MAX_IMAGE_BYTES.")
        data = await image.read()
        if len(data) > bot.settings.max_image_bytes:
            raise ValidationError("That image is larger than MAX_IMAGE_BYTES.")
        cid = await asyncio.to_thread(
            bot.uploader.upload_bytes,
            data,
            collection_object_key(item.name, item.id, kind),
            _IMAGE_TYPES[extension],
        )
        updated = bot.collections.set_image(
            item.id,
            kind=kind,
            gateway_url=bot.uploader.gateway_url(cid),
            ipfs_uri=ipfs_uri(cid),
        )
    except MintBotError as exc:
        await interaction.followup.send(str(exc), ephemeral=True)
        return
    uri = updated.icon_uri if kind == "icon" else updated.banner_uri
    log.info("User %s set %s for collection %s cid=%s", interaction.user.id, kind, updated.id, cid)
    await interaction.followup.send(
        f"Uploaded the {kind} for **{updated.name}**.\n{uri}\nNew mints of this collection include it in the metadata.",
        ephemeral=True,
    )
