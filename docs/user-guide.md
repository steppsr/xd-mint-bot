# User guide

This is how to use xd-mint-bot after [setup](setup.md) and [Filebase](filebase.md) are done. The bot has to be online in your server. Slash commands are limited to the Discord user IDs in `AUTHORIZED_USER_IDS`.

## Ideas that matter before the first mint

**One active collection.** The bot has a single active collection, shared by every authorized user. The mint form copies that collection when you press **Enter metadata**. Changing the active collection later does not rewrite a form you already opened.

**Edition and number are different.** Every NFT is edition 1 of 1. `series_number` and `series_total` in the metadata stay 1. Separately, each collection has a counter starting at 1. That counter is the default name and the filename (`images/1.png`, `metadata/1.json`). It increases by one after a real mint. A dry run does not increase it.

**Dry run.** `DRY_RUN=true` uploads to IPFS and does not call the wallet. The **Upload only** button does the same thing even when dry run is off. Use one of those until the gateway links load.

**The queue is in memory.** **Mint now** and **Upload only** return as soon as the job is queued. You can react to the next image while earlier jobs run. Wallet spends still happen one at a time. Restarting the bot drops the queue, open forms, and an offer that has not been created yet. If the wallet already accepted the mint, do not mint that image again. Use `/sell` if you still need the offer.

**Two licenses.** The Apache 2.0 license applies to this program. The text pinned as the NFT license is whatever you upload with `/collection license`, or the built-in CC0 1.0 text when you do not. Those are unrelated.

**Royalties.** The Discord form asks for a normal percent. Type `10` for 10%. The value stored for Chia is hundredths of a percent, so 10% is sent to the wallet as `1000`.

## Create a collection

Run these in the server, in any channel where the bot can reply. Replies to slash commands are visible only to you.

1. `/collection create`
2. Fill in the form. Discord allows five fields:
   - **Name.** Required. This becomes the Filebase folder name.
   - **Description.** Optional. It is written into the collection metadata when it is not blank.
   - **Default royalty percent.** A number such as `10` or `3.5`. Each mint can still change it.
   - **X handle.** Optional. A username, with or without `@`. Profile URLs are accepted and reduced to the handle. 1 to 15 letters, numbers, or underscores.
   - **Website.** Optional. `https://` is added if you leave the scheme off.
3. Submit. The new collection becomes the active one. It gets a new id and starts at number 1.

`/collection list` shows every saved collection. `/collection use` picks the one the next mint form will use. Start typing in the collection option to search by name.

`/collection show` prints the id, whether it is active, the royalty, the next number, the X handle, the website, and the icon, banner, and license URLs.

`/collection edit` changes the name, description, royalty, X handle, and website. It does not change the icon, banner, or license. Renaming does not move files already stored under the old folder.

There is no command to delete a collection or to set the next number. To reuse a name, rename the old collection first. To resume numbering after a gap, the next mint uses whatever `/collection show` lists. Plan the first real mint so that number 1 is the number you want on the first NFT.

## Add the icon, banner, and license

Do this once per collection, after the collection exists, and before the mint you want those fields on. Later mints read whatever is saved at upload time.

| Command | What you attach | What gets stored |
| --- | --- | --- |
| `/collection icon` | PNG, JPG, WEBP, or GIF | Pinned as `<collection>/icon`. The gateway URL goes in the collection metadata. |
| `/collection banner` | PNG, JPG, WEBP, or GIF | Pinned as `<collection>/banner`. |
| `/collection license` | A UTF-8 text file, such as `license.md` | Pinned as `<collection>/license`. The original filename is only remembered for `/collection show`. |

The bot replies with the gateway URL. Open it. If it does not load, see [filebase.md](filebase.md). Uploading again replaces that object and stores the new CID for later mints.

`/collection clear` removes one field: icon, banner, X handle, website, description, or license. Clearing the license falls back to `LICENSE_FILE` in `.env`, or to the built-in CC0 text.

Icon, banner, description, X handle, and website are written into `collection.attributes` on each new NFT. Image values are HTTPS gateway URLs, which is what MintGarden fetches. Per-NFT traits from the mint form are a different list: each one has `trait_type` and `value`.

Leave the Filebase objects in place after a dry run if you want the real mint to use the same icon and banner. Deleting them in the Filebase console does not clear the URL in the bot. The URL then points at a CID you removed.

## Mint an image

1. Post a message that has one PNG, JPG, or WEBP image, or add the reaction to a message that already has one. Extra images are ignored aside from a count on the prompt.
2. React with 🌱. Any other emoji is ignored. A person who is not in `AUTHORIZED_USER_IDS` is ignored.
3. The bot replies with **Enter metadata**. Press it. If nothing happens, you are not the person who started that mint, or the session expired.
4. The form has five fields:
   - **Name.** Prefilled with the next collection number. Change it if you want a title. The filename still uses the number.
   - **Description.** Prefilled from a Midjourney-style prompt when the message has one. Leave it blank to keep that prompt. Type over it to replace it.
   - **Royalty percent.** Prefilled from the collection default.
   - **Attributes.** Optional. One trait per line, `Background: Blue` or `Background = Blue`. Up to 25 traits. A trait name can be 80 characters and a value 200.
   - **Sale price.** Optional XCH, such as `1.5`. Leave it blank to skip Dexie.
5. Submit. Read the confirmation. It shows the collection, royalty, traits, sale price, target address, DID, and fee. On mainnet it says so.
6. Choose one button:
   - **Mint now.** Queues a real mint. The label includes the fee from `.env`.
   - **Upload only.** Queues an IPFS upload and does not spend XCH.
   - **Edit.** Opens the form again.
   - **Cancel.** Stops before any upload.

After you confirm, the message says the job is queued. You can react to another image. The same image stays reserved until that job finishes. You can have one form open at a time. The reaction cooldown (`REACTION_COOLDOWN_SECONDS`, default 15) still applies between reactions.

The result message is edited in place: progress, then success or failure. A real mint adds ✅ to the source message when Discord allows it. A sale offer, if you set a price, is a second step. The success message says the offer starts after the NFT is in the wallet. When Dexie accepts it, the same message gains the Dexie link. If the offer fails, the NFT is still minted. The message says why.

Open the image and metadata links before you treat a dry run as finished. The JSON `format` is `CHIP-0007`. The on-chain hash is the SHA-256 of the exact bytes uploaded.

## Sale offers

A sale price asks the bot to create a Chia offer after the mint and post it to Dexie. The offer requests XCH and offers the NFT. `OFFER_FEE_MOJOS` should stay `0`. A fee on the creator side locks an XCH coin for the life of the offer. The taker can add a fee when they accept.

The bot waits until `nft_get_info` shows the NFT in the NFT wallet and the coin is not mid-spend. The default wait is 600 seconds (`OFFER_WAIT_SECONDS`, maximum 900). That wait does not block the next mint. The offer file is written to `data/offers/` before the Dexie post, so a Dexie outage still leaves you the `offer1...` file.

`/sell` lists an NFT you already hold:

```text
/sell nft_id:<nft1 id> price:1.5
```

Use it when the mint succeeded and the offer did not, or when you want to list an older NFT. If a previous attempt timed out, check the wallet first:

```bash
chia wallet get_offers -s
```

`PENDING_ACCEPT` means an offer already exists. Creating another one lists the NFT twice. The bot also skips creation when it can see an open offer for that NFT in the wallet.

Dexie mainnet is `https://api.dexie.space/v1`. Testnet is `https://api-testnet.dexie.space/v1`. Override either URL with `DEXIE_API_URL` and `DEXIE_OFFER_URL` if you need to.

## Slash commands

All of these are guild commands. They reply ephemerally. They refuse anyone who is not in `AUTHORIZED_USER_IDS`.

| Command | What it does |
| --- | --- |
| `/nftstatus` | Sync, wallet ids, spendable balance, and the Filebase bucket. |
| `/collection create` | Name, description, default royalty, X handle, and website. Becomes active. |
| `/collection edit` | Change those text fields. Icon, banner, and license stay. |
| `/collection list` | Show every saved collection. |
| `/collection use` | Choose the collection for the next mint form. |
| `/collection show` | Ids, social links, image URLs, license, and the next number. |
| `/collection icon` | Pin a PNG, JPG, WEBP, or GIF as `icon`. |
| `/collection banner` | Pin a PNG, JPG, WEBP, or GIF as `banner`. |
| `/collection license` | Pin a UTF-8 text file as `license`. |
| `/collection clear` | Remove one of icon, banner, X handle, website, description, or license. |
| `/sell` | Offer an `nft1` id you already hold, at a price in XCH. |

## What is stored where

| Place | Contents |
| --- | --- |
| `data/collections.json` | Collection profiles, URLs, license text, and the next number. Back this up. |
| `data/offers/` | Offer files written after the wallet creates them. |
| Filebase | The pinned bytes. The CID is the public address of those bytes. |
| Chia wallet | The NFT coin and any offer. The bot cannot undo a mint. |
| Discord | The progress and result messages. |
| Bot memory | The queue and open forms. Lost on restart. |

`logs/xd-mint-bot.log` is the place to look when a Discord message is short. The log redacts the bot token and the Filebase keys when a line would contain them.

## Limits worth knowing

- One open metadata form per person. Queued and in-progress mints do not block a new image.
- The same source message cannot start a second mint until the first job finishes.
- Sessions expire. The default is 15 minutes (`SESSION_TIMEOUT_SECONDS`). Queued work does not expire that way.
- Images must be within `MAX_IMAGE_BYTES` (default 25 MB, hard cap 100 MB).
- Attributes are limited to 25 traits.
- Collection license text is capped around 200 KB.
- There is one license file per collection, not a different license on each NFT.
- A fee above 0.01 XCH is refused unless `ALLOW_HIGH_FEE=true`. The same cap applies to `OFFER_FEE_MOJOS`.
- The bot submits `nft_mint_nft` once. A timeout means you should look in the wallet before you try again.
- MintGarden can take a few minutes to show a new NFT after the transaction confirms.
