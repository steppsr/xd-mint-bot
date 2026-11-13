"""Upload bytes to a Filebase IPFS bucket and read back the CID.

Filebase returns the CID in the ``x-amz-meta-cid`` header. The header is
sometimes absent from the PUT response and present on a later HEAD, so the
uploader polls ``head_object`` before giving up.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Mapping

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from config import Settings
from utils.errors import UploadError

log = logging.getLogger(__name__)

_RETRYABLE_CODES = {500, 502, 503, 504}
_CID_KEYS = ("x-amz-meta-cid", "cid", "ipfs-cid")


def gateway_url_for(gateway: str, cid: str) -> str:
    """Join a gateway origin and a CID. A gateway that already ends in ``/ipfs`` is accepted."""
    base = gateway.rstrip("/")
    if base.endswith("/ipfs"):
        base = base[: -len("/ipfs")]
    return f"{base}/ipfs/{cid}"


def ipfs_uri(cid: str) -> str:
    """Return the canonical IPFS URI for a CID."""
    return f"ipfs://{cid}"


def cid_from_mapping(mapping: Mapping[str, object] | None) -> str | None:
    """Read a CID from response headers or boto3 metadata, if one is present."""
    if not mapping:
        return None
    normalized = {str(key).lower(): str(value).strip().strip('"') for key, value in mapping.items()}
    for key in _CID_KEYS:
        value = normalized.get(key)
        if value:
            return value
    return None


def normalize_cid(value: str) -> str:
    """Accept a token-like CID and reject empty or padded values."""
    cid = value.strip().strip('"')
    if not re.fullmatch(r"[A-Za-z0-9]+", cid) or len(cid) < 20:
        raise UploadError("Filebase returned an invalid IPFS CID.")
    return cid


class FilebaseUploader:
    """S3 client pointed at Filebase."""

    def __init__(self, settings: Settings) -> None:
        self._bucket = settings.filebase_bucket
        self._gateway = settings.filebase_gateway
        self._client = boto3.client(
            "s3",
            endpoint_url=settings.filebase_endpoint,
            region_name=settings.filebase_region,
            aws_access_key_id=settings.filebase_access_key,
            aws_secret_access_key=settings.filebase_secret_key,
            config=Config(
                signature_version="s3v4",
                retries={"max_attempts": 4, "mode": "standard"},
                connect_timeout=10,
                read_timeout=120,
                user_agent_extra="xd-mint-bot",
            ),
        )

    def gateway_url(self, cid: str) -> str:
        return gateway_url_for(self._gateway, cid)

    def check_bucket(self) -> str:
        """Confirm the configured bucket exists and the keys are accepted."""
        try:
            self._client.head_bucket(Bucket=self._bucket)
        except ClientError as exc:
            raise UploadError(self._public_error(exc)) from exc
        except BotoCoreError as exc:
            raise UploadError("Could not reach Filebase. Check FILEBASE_ENDPOINT and the network.") from exc
        return f"Bucket {self._bucket} is reachable."

    def upload_bytes(self, data: bytes, key: str, content_type: str) -> str:
        """Upload ``data`` and return its IPFS CID.

        Retries cover connection failures and 5xx responses. A later PUT to
        the same key replaces that object.
        """
        if not data:
            raise UploadError("Refusing to upload an empty file.")
        last_error: Exception | None = None
        for attempt in range(1, 4):
            try:
                response = self._client.put_object(
                    Bucket=self._bucket,
                    Key=key,
                    Body=data,
                    ContentType=content_type,
                )
                cid = cid_from_mapping(response.get("ResponseMetadata", {}).get("HTTPHeaders"))
                if cid:
                    return normalize_cid(cid)
                return self._wait_for_cid(key)
            except UploadError:
                raise
            except ClientError as exc:
                last_error = exc
                status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0)
                if status not in _RETRYABLE_CODES:
                    raise UploadError(self._public_error(exc)) from exc
                log.warning("Filebase PUT %s failed with HTTP %s (attempt %s/3)", key, status, attempt)
            except BotoCoreError as exc:
                last_error = exc
                log.warning("Filebase PUT %s failed (attempt %s/3): %s", key, attempt, exc.__class__.__name__)
            if attempt < 3:
                time.sleep(attempt)
        raise UploadError("Filebase upload failed after retries.") from last_error

    def _wait_for_cid(self, key: str) -> str:
        """Poll HEAD until Filebase attaches the CID metadata."""
        for attempt in range(1, 9):
            try:
                head = self._client.head_object(Bucket=self._bucket, Key=key)
            except ClientError as exc:
                status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0)
                if status not in _RETRYABLE_CODES and status != 404:
                    raise UploadError(self._public_error(exc)) from exc
                head = {}
            except BotoCoreError as exc:
                log.warning("Filebase HEAD %s failed: %s", key, exc.__class__.__name__)
                head = {}
            cid = cid_from_mapping(head.get("Metadata")) or cid_from_mapping(
                head.get("ResponseMetadata", {}).get("HTTPHeaders")
            )
            if cid:
                return normalize_cid(cid)
            time.sleep(0.4 * attempt)
        raise UploadError(
            "Filebase accepted the upload and did not return an IPFS CID. Confirm the bucket is an IPFS bucket."
        )

    @staticmethod
    def _public_error(exc: ClientError) -> str:
        code = exc.response.get("Error", {}).get("Code", "Unknown")
        log.warning("Filebase error code: %s", code)
        hints = {
            "InvalidAccessKeyId": "Filebase rejected the access key.",
            "SignatureDoesNotMatch": "Filebase rejected the secret key.",
            "NoSuchBucket": "The Filebase bucket was not found. Check FILEBASE_BUCKET.",
            "AccessDenied": "Filebase refused access to that bucket.",
        }
        return hints.get(code, f"Filebase upload failed ({code}).")
