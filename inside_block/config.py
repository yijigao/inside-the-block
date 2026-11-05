from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_RPC_URL = "https://api.mainnet-beta.solana.com"

_B58 = set("123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz")


def is_placeholder(pubkey: str | None) -> bool:
    """True if the value is not a plausible base58 32-byte pubkey string."""
    if not pubkey:
        return True
    if pubkey.startswith("PLACEHOLDER"):
        return True
    if len(pubkey) < 32 or len(pubkey) > 44:
        return True
    return any(c not in _B58 for c in pubkey)


@dataclass
class ValidatorTarget:
    name: str
    vote_pubkey: str
    node_pubkey: str
    client: str

    @property
    def configured(self) -> bool:
        return not is_placeholder(self.node_pubkey)


@dataclass
class BatchConfig:
    cu_limit: int = 30000
    stagger_ms: int = 75
    arm_ahead_slots: int = 8
    funding_lamports_per_probe: int = 2_000_000
    probe_account_count: int = 9
    transfer_lamports: int = 1


@dataclass
class ProbeConfig:
    validators: list[ValidatorTarget] = field(default_factory=list)
    batch: BatchConfig = field(default_factory=BatchConfig)
    rpc_url: str = DEFAULT_RPC_URL

    def configured_validators(self) -> list[ValidatorTarget]:
        return [v for v in self.validators if v.configured]


def load_config(path: str | Path) -> ProbeConfig:
    raw = json.loads(Path(path).read_text())
    validators = [ValidatorTarget(**v) for v in raw.get("validators", [])]
    batch = BatchConfig(**raw.get("batch", {}))
    rpc_url = os.environ.get("INSIDE_BLOCK_RPC_URL", DEFAULT_RPC_URL)
    return ProbeConfig(validators=validators, batch=batch, rpc_url=rpc_url)
