# xd-mint-bot

Discord bot that mints a 1-of-1 Chia NFT from an image message.

An authorized person reacts with 🌱. The bot asks for [CHIP-0007](https://github.com/Chia-Network/chips/blob/main/CHIPs/chip-0007.md) metadata, pins the image, metadata, and license to a Filebase IPFS bucket, then submits `nft_mint_nft` to a Chia wallet on the same machine. An optional sale price creates a Chia offer and posts it to [Dexie](https://dexie.space/).

The bot spends XCH only after **Mint now**. **Upload only** and `DRY_RUN=true` pin the files and leave the wallet alone.

Copyright 2026 Steve Stepp. Licensed under the [Apache License, Version 2.0](LICENSE).

Source: <https://github.com/steppsr/xd-mint-bot>

The Apache license covers this program. It does not set the license of the NFTs you mint. NFT license text is separate and is described in the [user guide](docs/user-guide.md).

## Start here

| Guide | Who it is for |
| --- | --- |
| [Setup](docs/setup.md) | First install: Discord application, Chia wallet, Python, configuration, and systemd. |
| [Filebase](docs/filebase.md) | Create a free account, an IPFS bucket, keys, and a gateway. |
| [User guide](docs/user-guide.md) | Day-to-day use: collections, uploads, minting, offers, and every slash command. |
| [Security](SECURITY.md) | What to keep off GitHub, and how to report a vulnerability. |

## What you need

- Python 3.10 or newer
- A synced Chia wallet on the same machine as the bot, with a DID and an NFT wallet
- Spendable XCH in the standard wallet for the mint fee
- A Discord application with the Message Content intent
- A Filebase IPFS bucket (the free tier is enough to start)

Run the bot on the computer that holds the Chia wallet. The wallet RPC listens on localhost. A second computer is fine for editing code and running tests.

## Quick start

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# edit .env, then:
python main.py --check
python main.py
```

Leave `DRY_RUN=true` until `--check` passes and an **Upload only** mint looks right. The full walkthrough is in [docs/setup.md](docs/setup.md).

## What a mint does

1. An authorized user reacts to an image with the seedling emoji.
2. The bot posts a button. Discord cannot open a form directly from a reaction, so **Enter metadata** opens the form.
3. The user confirms. The job joins an in-memory queue, and they can start the next image.
4. The bot uploads the image, CHIP-0007 JSON, and license to Filebase.
5. A real mint calls the wallet once. The collection number advances only after the wallet accepts it.
6. If a sale price was set, the bot waits until the NFT is in the wallet, creates one offer, and posts it to Dexie.

Wallet spends run one at a time. A restart drops the queue and any offer that has not been created yet.

## Project layout

```text
xd-mint-bot/
├── main.py                 # Discord client and --check
├── config.py               # Environment loading
├── requirements.txt
├── .env.example
├── handlers/               # Reactions, forms, slash commands
├── services/               # Filebase, Chia, collections, offers
├── utils/
├── tests/test_core.py
├── deploy/xd-mint-bot.service
├── docs/
├── data/                   # Created at runtime. Not in git.
├── logs/                   # Created at runtime. Not in git.
└── temp/                   # Downloaded images. Not in git.
```

## Tests

```bash
python -m unittest tests.test_core
```

The tests do not contact Discord, Filebase, or Chia.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Please do not open an issue that contains a bot token, a Filebase key, a wallet certificate, or a mnemonic.
