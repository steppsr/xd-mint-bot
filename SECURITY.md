# Security

## Report a vulnerability

Open a private GitHub security advisory on this repository once it is published. Do not include a live bot token, Filebase secret, wallet certificate, or mnemonic in a public issue, pull request, or Discord message.

If the repository does not have advisories enabled yet, contact the maintainer out of band and describe the bug without pasting secrets.

## What this bot is allowed to touch

The process needs:

- The Discord bot token
- The Filebase access key and secret
- Read access to the Chia wallet client certificate and key
- Localhost access to the wallet RPC, or permission to run `chia rpc wallet`

It does not need the wallet mnemonic, the farmer key, or the pool key. Do not put those in `.env`.

`.env`, `data/`, `logs/`, and `temp/` are gitignored. Keep them that way. `chmod 600 .env` on the wallet machine.

## Running it safely

- Bind the wallet RPC to localhost. Do not forward port 9256 to the internet.
- Run one copy of the bot. A second process with the same token will fight the first, and it will not see the first process's queue.
- Run the systemd service as the wallet user, or as another user that can read only the wallet certificate and key. Do not run it as root.
- Leave `OFFER_FEE_MOJOS` at 0 unless you intend to lock an XCH coin inside each offer.
- Leave `ALLOW_HIGH_FEE` false unless you have a reason to permit a fee above 0.01 XCH.
- Prefer `DRY_RUN=true` and a testnet wallet before the first mainnet mint.
- Restrict `AUTHORIZED_USER_IDS` to the people who may spend the fee wallet.
- Set `ALLOWED_GUILD_IDS` if the bot is invited to more than one server.
- IPFS CIDs are public. Anyone who has the CID can fetch the file. Do not pin private images.

## If a secret leaks

1. Reset the Discord token on the Bot page. Update `.env` and restart.
2. Rotate the Filebase key in the Filebase console. Update `.env` and restart.
3. If a wallet certificate was copied to a place you do not control, follow Chia's current guidance for regenerating the SSL files. The certificate is not the mnemonic, and rotating it is still worth doing.
4. If the mnemonic leaked, move the funds with Chia's documented key-rotation process. Treat that as a full compromise.
