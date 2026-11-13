# Setup

This guide assumes you have used Discord as a person and have not built a bot before. It also assumes the Chia wallet and this bot will run on Linux. You can edit the code and run the tests on Windows or macOS. The process that calls `python main.py` has to run on the wallet machine, because the wallet RPC is on localhost.

Work through the sections in order. Leave `DRY_RUN=true` until the last section says to turn it off.

## 1. The two Discord screens

People mix these up, because both are called "developer" somewhere.

**Discord Developer Portal** is a website: <https://discord.com/developers/applications>. This is where the bot is created, where the token lives, and where the Message Content intent is turned on. Each application you create there is a separate bot. This project wants its own application and its own token. Two running programs cannot share one token. Discord disconnects the older session.

**Developer Mode** is a switch inside the Discord app you already use to chat. It adds **Copy User ID** and **Copy Server ID** to right-click menus. Turning it on does not create a bot. Creating a bot does not turn it on.

Sign in to the Developer Portal with the Discord account that should own the bot. That account should have two-factor authentication turned on. Discord requires it before some privileged intents can be used on large bots. A private bot in your own server can use Message Content Intent without an application review.

Read Discord's [Developer Terms of Service](https://discord.com/developers/docs/policies-and-agreements/developer-terms-of-service) and [Developer Policy](https://discord.com/developers/docs/policies-and-agreements/developer-policy) before you invite the bot. You are responsible for the bot you run.

## 2. Create the Discord application

1. Open <https://discord.com/developers/applications>.
2. Click **New Application**.
3. Name it. `xd-mint-bot` is fine. Accept the developer terms.
4. Open **Installation**. Leave **Require OAuth2 Code Grant** off. A private bot does not need a public install link.
5. On the **Bot** page, if you see **Public Bot**, leave it off unless strangers must be able to invite the bot. If the switch is somewhere else in the current portal layout, the same idea applies: do not publish an invite link you do not control.

## 3. Copy the bot token

1. Stay on the **Bot** page. If you only see **Add Bot**, click it. New applications usually create the bot user for you.
2. Click **Reset Token**, confirm, and copy the token immediately. Discord shows the full token once.
3. Put it somewhere temporary and private, such as a password manager. You will paste it into `.env` later. Do not put it in a Discord message, a screenshot, a GitHub issue, or a shell history you share.
4. If you lose it, reset it again. The old token stops working, including any copy saved in an older project.

## 4. Turn on Message Content Intent

On the **Bot** page, find **Privileged Gateway Intents**.

1. Turn **Message Content Intent** on.
2. Leave **Presence Intent** and **Server Members Intent** off. This bot does not use them.
3. Save the page if Discord shows a save button.

The bot asks Discord for message content when it logs in. With the switch off, Discord refuses the login and the process exits. With it on, the bot can see image attachments and the text of a message, which is how a Midjourney prompt can pre-fill the NFT description.

A bot that is only in your own server does not need Discord to approve this intent. A bot in 100 or more servers does. That limit is Discord's, and it can change. Check the notice on the Bot page.

## 5. Build the invite link

You need the **Manage Server** permission on the Discord server that will use the bot.

1. In the Developer Portal, open **OAuth2**, then **URL Generator**.
2. Under **Scopes**, check `bot` and `applications.commands`. The second scope is what registers slash commands such as `/collection`.
3. Under **Bot Permissions**, check:
   - View Channels
   - Read Message History
   - Send Messages
   - Embed Links
   - Attach Files
   - Add Reactions
   - Use External Emojis
4. Copy the generated URL, open it in a browser, choose your server, and authorize.

The same URL can be written by hand. `CLIENT_ID` is the **Application ID** on **General Information**. The permission number `379968` is the set of permissions above.

```text
https://discord.com/oauth2/authorize?client_id=CLIENT_ID&scope=bot%20applications.commands&permissions=379968
```

The bot appears in the member list as offline until `python main.py` is running.

## 6. Copy your user ID and the server ID

In the Discord app, right-click your own name in the server member list.

- If the menu contains **Copy User ID**, Developer Mode is already on. Copy the ID.
- If that item is missing, turn Developer Mode on. Open **User Settings** (the gear next to your name) → **Advanced** → **Developer Mode**. On some clients the page is labeled **Developer** at the bottom of the settings sidebar. Close settings and right-click again. If the menu is still missing the ID, quit Discord completely and reopen it.

The value is a number of about 17 to 19 digits. That number goes in `AUTHORIZED_USER_IDS`. A username will not work. Add another person with a comma: `111111111111111111,222222222222222222`. Spaces around the commas are fine.

Right-click the server icon and choose **Copy Server ID**. That number goes in `DISCORD_GUILD_ID`. With it set, slash commands show up in that server as soon as the bot starts. Leave it empty and Discord's global command list can take up to an hour to update.

Only people listed in `AUTHORIZED_USER_IDS` can start a mint or run the slash commands. Everyone else's seedling reactions are ignored.

## 7. Install Chia

Install Chia from the current instructions at <https://docs.chia.net/>. Start the full node and the wallet, and wait until the wallet reports that it is synced.

```bash
chia version
chia wallet show
```

Create a DID, then an NFT wallet linked to that DID, if `chia wallet show` does not already list one:

```bash
chia wallet did create
chia wallet nft create -di <did_id> -n "NFT Wallet"
chia wallet show
```

Write down three values from that output:

- The NFT wallet id. It must be type NFT, linked to your DID. A pool wallet id will not mint.
- The DID, which looks like `did:chia:1...`.
- The `xch1...` address that should receive the NFT and royalties. On testnet the address starts with `txch1`.

The standard wallet, usually id `1`, pays the mint fee. It needs spendable XCH. The NFT wallet balance does not pay the fee.

The wallet RPC is HTTPS on port 9256 and uses a client certificate Chia generated:

```text
~/.chia/mainnet/config/ssl/wallet/private_wallet.crt
~/.chia/mainnet/config/ssl/wallet/private_wallet.key
```

A testnet install usually lives under `~/.chia/testnet11`. Leave `CHIA_SSL_CERT` and `CHIA_SSL_KEY` empty when the bot runs as the same user that installed Chia. The bot reads the certificate. It does not read the wallet mnemonic or the spend keys.

Keep port 9256 on localhost. Do not publish the wallet RPC to the internet.

`CHIA_RPC_MODE=subprocess` is the other option. The bot then runs `chia rpc wallet` instead of opening HTTPS itself. The Linux account that runs the bot must be able to run `chia`.

## 8. Install system packages

On Ubuntu or Debian:

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git
python3 --version
```

`python3 --version` needs to report 3.10 or newer. Ubuntu 22.04 has 3.10. Ubuntu 24.04 has 3.12.

On Windows, install Python 3.10 or newer from <https://www.python.org/downloads/> and confirm `py -3 --version`. Use that machine for tests. Run the live bot on the wallet machine.

## 9. Get the source and create a virtualenv

On the wallet machine:

```bash
git clone https://github.com/steppsr/xd-mint-bot.git
cd xd-mint-bot
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
chmod 600 .env
```

Activate the virtualenv again in every new terminal before you run the bot (`source venv/bin/activate`).

Create `.env` on the wallet machine. A `.env` written on another computer will have the wrong certificate paths. Do not copy `.env.example` over an existing `.env` later. That would wipe your token and keys.

On Windows, for tests only:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m unittest tests.test_core
```

## 10. Fill in `.env`

Open `.env` in an editor (`nano .env` on Linux). Replace every placeholder. The bot refuses to start while `DISCORD_BOT_TOKEN` or the Filebase keys are still the example text.

| Setting | What to put |
| --- | --- |
| `DISCORD_BOT_TOKEN` | The token from the Bot page. |
| `AUTHORIZED_USER_IDS` | Your numeric user ID. Commas for more than one person. |
| `DISCORD_GUILD_ID` | Your server ID, so slash commands appear immediately. |
| `ALLOWED_GUILD_IDS` | Leave empty to accept every server the bot is in, or set one or more server IDs. |
| `SEEDLING_EMOJI` | `🌱`, or a custom emoji name without colons. |
| `CHIA_NETWORK` | `mainnet` or `testnet`. Use testnet until you have minted once on purpose. |
| `CHIA_NFT_WALLET_ID` | The NFT wallet id from `chia wallet show`. |
| `CHIA_FEE_WALLET_ID` | Usually `1`, the standard wallet that pays the fee. |
| `CHIA_DID_ID` | The `did:chia:1...` id. |
| `CHIA_ROYALTY_ADDRESS` | The `xch1` or `txch1` address that receives royalties. |
| `CHIA_TARGET_ADDRESS` | The address that receives the NFT. Often the same as the royalty address. |
| `CHIA_FEE_MOJOS` | Mint fee in mojos. `615000000` is 0.000615 XCH. A fee above 0.01 XCH is refused unless `ALLOW_HIGH_FEE=true`. |
| `CHIA_RPC_MODE` | `https` unless you want the `chia` CLI to make the call. |
| `DRY_RUN` | `true` for the first runs. |
| `DEFAULT_COLLECTION_ID` | A new UUID. Generate one with the command below. |
| `DEFAULT_COLLECTION_NAME` | The name of the first collection. You can rename it later in Discord. |
| `DEFAULT_ROYALTY_PERCENTAGE` | Chia hundredths of a percent. `300` means 3%. `1000` means 10%. The Discord form asks for a normal percent (`10`), and the bot converts it. |
| Filebase keys | See [filebase.md](filebase.md). |
| `OFFER_FEE_MOJOS` | Leave at `0`. A creator fee locks an XCH coin until the offer is taken or cancelled. The buyer can add a fee. |

```bash
python -c "import uuid; print(uuid.uuid4())"
```

Royalty in `.env` and royalty in the Discord form are different units. The environment and the saved collection use hundredths (`1000` = 10%). The form asks for `10`.

Leave `CHIA_SSL_CERT`, `CHIA_SSL_KEY`, and `CHIA_ROOT` empty when the bot user owns the Chia install. Set `CHIA_ROOT` if the wallet lives somewhere other than `~/.chia/mainnet`.

`OFFER_WAIT_SECONDS` can stay commented. The default is 600 seconds, and a ready NFT returns as soon as the wallet lists it.

## 11. Check the wallet and Filebase

With the virtualenv active, on the wallet machine:

```bash
python main.py --check
```

Every line should read `OK` before you let the bot spend XCH. A missing certificate, a wallet that is still syncing, a pool wallet id in `CHIA_NFT_WALLET_ID`, a low XCH balance, or a rejected Filebase key fails this check. `--check` does not log in to Discord and does not create `data/collections.json`.

## 12. Run it in the foreground

```bash
python main.py
```

The log says when the bot has logged in, and the member list shows it online. Logs also go to `logs/xd-mint-bot.log`. Stop this process with Ctrl+C before you start the systemd service, so only one copy is running.

Then, still with `DRY_RUN=true`:

1. In Discord, run `/nftstatus`. It should report sync, the wallet ids, the fee balance, and the Filebase bucket.
2. Run `/collection show`. The first start saves `DEFAULT_COLLECTION_*` as the active collection in `data/collections.json`.
3. Follow the [user guide](user-guide.md) to add an icon, banner, and license if you want them, then react with 🌱 and press **Upload only**.
4. Open the gateway links in the result. The image and the JSON should load.
5. Stop the bot. Set `DRY_RUN=false` only when you intend to spend XCH. Restart, and prefer a testnet mint before mainnet.

A missing `data/collections.json` is created on the next start from `DEFAULT_COLLECTION_*`. An empty or broken file stops the bot. Move a file aside when you want a new one. Do not leave an empty file in its place.

## 13. Run it as a service

The example unit is `deploy/xd-mint-bot.service`. The user `chia` and the path `/home/chia/xd-mint-bot` are placeholders. Edit them before you install the unit.

```bash
nano deploy/xd-mint-bot.service
```

Set `User` and `Group` to the account that can read the wallet certificate. Set `WorkingDirectory`, `EnvironmentFile`, and `ExecStart` to that account's checkout. `ExecStart` must use the virtualenv Python, for example `/home/youruser/xd-mint-bot/venv/bin/python`.

Do not run the service as root.

```bash
sudo cp deploy/xd-mint-bot.service /etc/systemd/system/xd-mint-bot.service
sudo systemctl daemon-reload
sudo systemctl enable --now xd-mint-bot
journalctl -u xd-mint-bot -f
```

Useful commands later:

```bash
sudo systemctl status xd-mint-bot
sudo systemctl restart xd-mint-bot
sudo systemctl stop xd-mint-bot
```

Run a single instance. Session state is in memory, and a second process can mint the same image again. One Discord token can only stay online in one process.

Restart the service after you change `.env` or pull new code. With `DISCORD_GUILD_ID` set, slash commands refresh in that server when the bot logs in again.

If the service user is not the Chia user, that account still needs read access to `private_wallet.crt` and `private_wallet.key`. Membership in the Chia user's group, plus files that are group-readable, is the usual approach. Do not copy the wallet mnemonic onto the bot account.

## 14. Update an existing checkout

Stop the bot first (`sudo systemctl stop xd-mint-bot`, or Ctrl+C if it is in the foreground).

```bash
cd ~/xd-mint-bot
git pull
source venv/bin/activate
pip install -r requirements.txt
python main.py --check
sudo systemctl start xd-mint-bot
```

`git pull` does not touch `.env`, `venv/`, `logs/`, or `data/` when those paths are gitignored. Do not copy `.env.example` over `.env`.

If you copy the tree with `tar` or `scp` instead of git, exclude `.venv`, `venv`, `__pycache__`, `.env`, `logs`, `.git`, and `data`. Extracting that archive into an existing checkout leaves `.env` and `data/collections.json` in place.

## 15. Moving from a test collection to a real one

`/collection create` makes a new collection and makes it active. Names must be unique, ignoring case. There is no delete command. If the seed collection from `.env` already uses the name you want, run `/collection edit` and rename the seed, then create the real one.

Empty the matching folder in the Filebase bucket before the first real mint if earlier dry runs uploaded test files you do not want mixed in. Deleting a Filebase object drops that pin. Metadata you already published will still point at the old CID.

## 16. Troubleshooting

| Symptom | What to do |
| --- | --- |
| Process exits on login | Turn on Message Content Intent. Check `DISCORD_BOT_TOKEN`. Restart. |
| Right-click menu has no Copy User ID | Turn on Developer Mode in this Discord app, then quit and reopen Discord if the menu does not update. |
| `--check` cannot see the wallet | Run it on the wallet machine. Port 9256 and the certificates are local. |
| `--check` cannot find certificates | Leave the SSL paths empty for the Chia user, or set `CHIA_SSL_CERT` and `CHIA_SSL_KEY`, or set `CHIA_RPC_MODE=subprocess`. |
| Wallet id is a PoolWallet | `CHIA_NFT_WALLET_ID` is a pool wallet. Use the NFT wallet id from `chia wallet show`. |
| Wallet is not synced | Wait until `chia wallet show` says synced, then run `--check` again. |
| Spendable balance is below the fee | Put XCH in the fee wallet (`CHIA_FEE_WALLET_ID`, usually 1). |
| `InvalidAccessKeyId` or `SignatureDoesNotMatch` | Replace the Filebase keys. The bucket must be an IPFS bucket. |
| Filebase returns no CID | The bucket is an object-storage bucket, or `FILEBASE_ENDPOINT` is `s3.filebase.io`. Use an IPFS bucket and `https://s3.filebase.com`. |
| Slash commands do not appear | Restart the bot. Set `DISCORD_GUILD_ID` so they sync to that server immediately. |
| Modal never appears | Press **Enter metadata** on the bot's reply. A reaction cannot open a modal. |
| Sale offer was not posted | The mint can still have succeeded. The offer waits until the NFT is in the wallet. Check wallet offers before `/sell`. |
| Queued mint disappeared after a restart | The queue is in memory. React again. If the wallet already shows the NFT, use `/sell` instead of minting it again. |
| Prompt field is empty | The message had no bold Midjourney prompt. Type a description. Message Content Intent is still required for login. |
| Mint timed out | Look in the NFT wallet before reacting again. The transaction may already be in the mempool. |
| Buttons die after a restart | React with 🌱 again. Open forms are not stored on disk. |
