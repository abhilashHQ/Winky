import asyncio
import base64
import binascii
import hmac
import logging
import os
from pathlib import Path
from random import SystemRandom

_DEFAULT_ENTROPY = 32
_sysrand = SystemRandom()


def randbelow(exclusive_upper_bound: int) -> int:
    if exclusive_upper_bound <= 0:
        raise ValueError("Upper bound must be positive.")
    return _sysrand.randrange(exclusive_upper_bound)


def choice(sequence):
    return _sysrand.choice(sequence)


def token_bytes(nbytes: int | None = None) -> bytes:
    if nbytes is None:
        nbytes = _DEFAULT_ENTROPY
    return os.urandom(nbytes)


def token_hex(nbytes: int | None = None) -> str:
    return binascii.hexlify(token_bytes(nbytes)).decode("ascii")


def token_urlsafe(nbytes: int | None = None) -> str:
    token = base64.urlsafe_b64encode(token_bytes(nbytes)).rstrip(b"=")
    return token.decode("ascii")


compare_digest = hmac.compare_digest

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

logger = logging.getLogger(__name__)

_secrets: dict[str, str] = {}


async def fetch_secrets() -> None:
    """Call once on harness startup. Populates in-memory _secrets dict."""
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            await _fetch_secrets_once()
            return
        except Exception as exc:
            last_error = exc
            if attempt == 0:
                await asyncio.sleep(1)

    if last_error is not None:
        raise last_error


async def _fetch_secrets_once() -> None:
    worker_url = os.environ["WORKER_URL"].rstrip("/")
    worker_key = os.environ["WORKER_API_KEY"]

    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{worker_url}/secrets",
            headers={"Authorization": f"Bearer {worker_key}"},
            timeout=10.0,
        )
        response.raise_for_status()
        global _secrets
        _secrets = response.json()

    logger.info("Fetched %s secrets from Worker", len(_secrets))


def get(key: str) -> str:
    """Get a secret by key. Raises if not loaded or key missing."""
    if not _secrets:
        raise RuntimeError("Secrets not loaded. Call fetch_secrets() on startup.")
    if key not in _secrets:
        raise KeyError(f"Secret '{key}' not found in Worker response.")
    return _secrets[key]


def get_all() -> dict[str, str]:
    """Get the in-memory secrets dict. Raises if secrets are not loaded."""
    if not _secrets:
        raise RuntimeError("Secrets not loaded. Call fetch_secrets() on startup.")
    return _secrets


async def store_oauth_token(key: str, token: str) -> None:
    """Called after first-run OAuth to persist token to Worker."""
    worker_url = os.environ["WORKER_URL"].rstrip("/")
    worker_key = os.environ["WORKER_API_KEY"]

    async with httpx.AsyncClient() as client:
        response = await client.put(
            f"{worker_url}/secrets/{key}",
            headers={"Authorization": f"Bearer {worker_key}"},
            json={"value": token},
            timeout=10.0,
        )
        response.raise_for_status()

    _secrets[key] = token
