from __future__ import annotations

import os
import time
from typing import Any

import httpx


def _client(timeout: float) -> httpx.Client:
    # Build the client explicitly: httpx 0.28 crashes parsing bracketed IPv6
    # entries in NO_PROXY env vars, so take the proxy from *_PROXY ourselves
    # and disable env trust. In a clean container no proxy env exists -> direct.
    proxy = (
        os.environ.get("HTTPS_PROXY")
        or os.environ.get("https_proxy")
        or os.environ.get("ALL_PROXY")
        or os.environ.get("all_proxy")
        or None
    )
    return httpx.Client(timeout=timeout, proxy=proxy, trust_env=False)


class RpcError(RuntimeError):
    pass


class RpcClient:
    """Minimal Solana JSON-RPC client (httpx). Retries 429/transient errors."""

    def __init__(self, url: str, timeout: float = 30.0, max_retries: int = 3):
        self.url = url
        self.timeout = timeout
        self.max_retries = max_retries
        self._id = 0

    def call(self, method: str, params: list | None = None) -> Any:
        self._id += 1
        payload = {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params or []}
        last_exc: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                with _client(self.timeout) as client:
                    resp = client.post(self.url, json=payload)
                if resp.status_code == 429 or resp.status_code >= 500:
                    raise RpcError(f"HTTP {resp.status_code}")
                resp.raise_for_status()
                body = resp.json()
                if "error" in body:
                    raise RpcError(f"{method}: {body['error']}")
                return body.get("result")
            except (RpcError, httpx.HTTPError) as exc:  # retry transient failures
                last_exc = exc
                if attempt < self.max_retries - 1:
                    time.sleep(1.5 * (attempt + 1))
        raise RpcError(f"{method} failed after {self.max_retries} tries: {last_exc}")

    # --- convenience wrappers -------------------------------------------------
    def send_transaction_race(self, tx_b64: str, extra_urls: list[str]) -> str:
        """Send the same signed transaction through several endpoints at once;
        first success wins. Safe: identical signature, the network dedupes."""
        import threading

        if not extra_urls:
            return self.send_transaction(tx_b64)
        results: dict[str, str] = {}
        errors: list[str] = []

        def worker(url: str) -> None:
            try:
                results[url] = RpcClient(url, timeout=self.timeout, max_retries=1).send_transaction(tx_b64)
            except Exception as exc:  # noqa: BLE001 - collected and reported below
                errors.append(f"{url}: {exc}")

        threads = [threading.Thread(target=worker, args=(u,), daemon=True) for u in [self.url, *extra_urls]]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=self.timeout + 5)
        for url in [self.url, *extra_urls]:
            if url in results:
                return results[url]
        raise RpcError(f"all endpoints failed: {errors}")

    def get_slot(self) -> int:
        return int(self.call("getSlot"))

    def get_epoch_info(self) -> dict:
        return self.call("getEpochInfo")

    def get_latest_blockhash(self) -> str:
        return self.call("getLatestBlockhash")["value"]["blockhash"]

    def get_leader_schedule(self, epoch: int) -> dict[str, list[int]]:
        result = self.call("getLeaderSchedule", [None, {"epoch": epoch}])
        return result or {}

    def get_slot_leaders(self, start_slot: int, limit: int) -> list[str]:
        return self.call("getSlotLeaders", [start_slot, limit])

    def get_block_signatures(self, slot: int) -> list[str] | None:
        """Ordered transaction signatures for a slot, or None if slot was skipped."""
        result = self.call(
            "getBlock",
            [
                slot,
                {
                    "encoding": "json",
                    "transactionDetails": "signatures",
                    "rewards": False,
                    "maxSupportedTransactionVersion": 0,
                },
            ],
        )
        if result is None:
            return None
        return list(result.get("signatures") or [])

    def get_signature_statuses(self, signatures: list[str]) -> list[dict | None]:
        # Public RPC endpoints reject very large getSignatureStatuses payloads
        # (HTTP 413 once a campaign passes a few hundred signatures), so query
        # in chunks and stitch the per-chunk "value" lists back together.
        values: list[dict | None] = []
        chunk = 100
        for i in range(0, len(signatures), chunk):
            result = self.call(
                "getSignatureStatuses",
                [signatures[i : i + chunk], {"searchTransactionHistory": True}],
            )
            values.extend(result["value"])
        return values

    def simulate_transaction(self, tx_b64: str) -> dict:
        result = self.call(
            "simulateTransaction",
            [tx_b64, {"sigVerify": False, "replaceRecentBlockhash": False, "encoding": "base64"}],
        )
        return result["value"]

    def send_transaction(self, tx_b64: str) -> str:
        return self.call(
            "sendTransaction",
            [tx_b64, {"skipPreflight": False, "encoding": "base64", "maxRetries": 3}],
        )

    def get_balance(self, pubkey: str) -> int:
        return int(self.call("getBalance", [pubkey])["value"])
