# Contributing

Contributions are welcome under the [Apache License, Version 2.0](LICENSE). By submitting a patch you agree that it can be distributed under that license.

## Before you open a pull request

- Run `python -m unittest tests.test_core` on Python 3.10 or newer.
- Match the style of the file you are editing. Keep comments for constraints that the code itself does not show.
- Do not add a real `.env`, a wallet certificate, a token, or a Filebase key.
- The guides in `docs/` describe the program as it ships.
- User-facing text should say what the bot did. Skip contrast frames such as "X, not Y" unless the distinction is the point of the sentence.

## Tests

The unit tests stay offline. They must not call Discord, Filebase, Chia, or Dexie. Cover a behavior with a pure function or a fake when you can.

GitHub Actions runs `tests.test_core` on Ubuntu and Windows. See `.github/workflows/tests.yml`.

## Reporting bugs

Include the bot version from the log, `CHIA_NETWORK`, whether `DRY_RUN` was on, and the wording of the Discord error. Redact addresses if you want, but never paste `.env`.

A mint that reached the wallet and then timed out may already be in the mempool. Say whether `chia wallet nft list` shows the new NFT before you describe it as lost.
