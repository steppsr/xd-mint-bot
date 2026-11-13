# Filebase

xd-mint-bot pins NFT images, metadata, and license files to IPFS through [Filebase](https://filebase.com/). Chia stores the IPFS URI on the coin. Wallets and MintGarden then fetch the bytes from a gateway.

Filebase has two storage products, and they use different endpoints. This bot needs the **IPFS** product.

| Product | Endpoint | Region | CIDs |
| --- | --- | --- | --- |
| IPFS storage | `https://s3.filebase.com` | `us-east-1` | Yes. This is what `.env.example` uses. |
| S3-compatible object storage | `https://s3.filebase.io` | `auto` | No. The bot cannot mint from this tier. |

Official references:

- Pricing: <https://filebase.com/pricing>
- Free tier: <https://filebase.com/free/>
- IPFS overview: <https://filebase.com/docs/ipfs/overview>
- Access keys: <https://filebase.com/docs/get-started/access-keys>
- Pinning: <https://filebase.com/docs/ipfs/pinning/pinning-files>
- Dedicated gateways: <https://filebase.com/docs/ipfs/gateways/managing-dedicated-gateways>

Limits change. Read the pricing page again when you sign up. The numbers below are from Filebase's pricing page as checked in November 2026.

## Free tier

New accounts can start without a paid plan. The free IPFS plan published at that time included:

| Item | Free |
| --- | --- |
| Price | $0 / month |
| Storage | 5 GB, pooled |
| Pinned files | 500 |
| Buckets | 1 |
| IPFS egress | 10 GB / month |
| Dedicated gateways | 1 |
| IPNS names | 1 |
| Custom domains | Not included |
| Bucket-level CID | Not included |

The bot does not need a bucket-level CID. It reads the CID Filebase returns for each object (`x-amz-meta-cid`). That is a per-file pin, which the IPFS S3 API provides.

A practical limit: each real mint adds an image object and a metadata object. Icon, banner, and license reuse the same object names, so uploading them again replaces the pin instead of adding a new file. Five hundred pins is on the order of a couple of hundred NFTs plus the collection files, depending on image size. The 5 GB cap and the 10 GB monthly IPFS egress cap matter too. Gateway views, including MintGarden fetching your metadata, count as IPFS egress. When a cap is hit, downloads can fail until the next month or until you upgrade.

Paid plans start at the price on the pricing page (Pro was listed at $7.50 / month, with more storage, more pins, and more gateways). Confirm the current price before you rely on it.

## 1. Create an account

1. Open <https://filebase.com/> and create an account. The console is <https://console.filebase.com/>.
2. Confirm the email if Filebase asks you to.
3. Turn on two-factor authentication in the account settings. The free plan includes 2FA.

One Filebase account has one S3 access-key pair. That pair can read and write every bucket on the account. Treat the secret like a password.

## 2. Create an IPFS bucket

1. In the console, open **Buckets**.
2. Click **Create Bucket**.
3. Choose a name. Filebase requires 3 to 63 characters, lowercase letters, numbers, and dashes, and the name must be unique across Filebase.
4. Choose the **IPFS** storage network. If the form offers object storage, S3, or another network, do not pick those for this bot.
5. Create the bucket.

The name you chose is `FILEBASE_BUCKET`.

IPFS content is public to anyone who knows the CID, even when the bucket's S3 listing is private. Do not pin files you need to keep secret.

## 3. Create the access keys

1. Open **Access Keys** in the console. The guide is <https://filebase.com/docs/get-started/access-keys>.
2. Copy the Access Key ID into `FILEBASE_ACCESS_KEY`.
3. Copy the Secret Access Key into `FILEBASE_SECRET_KEY`.

If you suspect the secret leaked, rotate it in the console and update `.env`. Restart the bot after you save `.env`.

These are the values for `.env`:

```text
FILEBASE_ACCESS_KEY=...
FILEBASE_SECRET_KEY=...
FILEBASE_BUCKET=your-ipfs-bucket
FILEBASE_ENDPOINT=https://s3.filebase.com
FILEBASE_REGION=us-east-1
```

`FILEBASE_REGION=us-east-1` matches the IPFS endpoint. `auto` belongs to the other endpoint. Leave the endpoint and region at the example values unless Filebase's IPFS docs change them.

## 4. Choose a gateway

A gateway turns a CID into an HTTPS URL that a browser or MintGarden can fetch. The bot stores both `ipfs://<cid>` and a gateway URL when `INCLUDE_GATEWAY_URIS=true`.

Set `FILEBASE_GATEWAY` to the origin only. The bot adds `/ipfs/<cid>`. If the value already ends in `/ipfs`, that suffix is removed.

**Public gateway**, for a first test:

```text
FILEBASE_GATEWAY=https://ipfs.filebase.io
```

Filebase documents this gateway as light use, with a rate limit around 200 requests per minute. It is a poor long-term home for a collection other people will open.

**Dedicated gateway**, for anything you want others to view:

1. In the console, open **Gateways** and create a gateway. The free plan includes one.
2. Pick a name. The hostname is `https://<that-name>.myfilebase.com`.
3. A public gateway can serve CIDs in general. A private or scoped gateway serves only CIDs pinned to your account or to one bucket. Scoped is a good fit for a collection, as long as MintGarden can fetch the URL without logging in.
4. Set:

```text
FILEBASE_GATEWAY=https://your-name.myfilebase.com
```

Do not put `/ipfs` on the end. A scoped gateway returns 404 for a CID that is not in its bucket. If you delete the Filebase object, that CID stops being the current pin and a scoped gateway can stop serving it. Metadata you already minted still points at the old CID.

Custom domains are a paid feature. Filebase asks you not to put another CDN, such as Cloudflare's proxy, in front of the gateway.

## 5. What the bot uploads

Collection files use stable names, so a new upload replaces that object:

```text
<collection name>/icon
<collection name>/banner
<collection name>/license
```

There is no file extension on those three names. The Content-Type is still an image type or `text/plain`. Replacing the object gives the new bytes a new CID. Later mints use the new CID. NFTs that were already minted keep the CID written into their metadata.

Each NFT uses its collection number:

```text
SampleCollection/images/1.png
SampleCollection/metadata/1.json
```

JPEG files are stored as `.jpg`. Numbers start at 1. `/collection show` lists the next number. A real mint advances the counter. **Upload only** and a dry run write that number and leave the counter where it is, so the next upload uses it again.

The folder name is the collection name, with slashes and control characters turned into spaces. Renaming a collection changes the folder used by later uploads. Files already pinned under the old name stay where they are.

Icon and banner are uploaded when you run `/collection icon` and `/collection banner`, not on every mint. If you delete those objects in the Filebase console, the URLs saved in `data/collections.json` still point at CIDs that are no longer pinned. Run the command again.

`/collection license` pins immediately. A later mint uploads the same license bytes to the same `license` key.

## 6. Check that it works

On the wallet machine, with `.env` filled in:

```bash
python main.py --check
```

The Filebase line should be OK. Then do one **Upload only** mint and open the gateway links in the Discord result. You should also see the objects in the bucket in the Filebase console, with a CID on each one.

If `--check` says the keys were rejected, rotate them and confirm the bucket name. If the upload succeeds in the console but the bot says there is no CID, the bucket is not an IPFS bucket or the endpoint is `s3.filebase.io`.
