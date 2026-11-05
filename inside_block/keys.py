from __future__ import annotations

import json
import os
from pathlib import Path

from solders.keypair import Keypair


def save_keypair(kp: Keypair, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(list(bytes(kp))))
    os.chmod(path, 0o600)
    return path


def load_keypair(path: str | Path) -> Keypair:
    raw = json.loads(Path(path).read_text())
    return Keypair.from_bytes(bytes(raw))


def probe_keypair_path(keys_dir: str | Path, index: int) -> Path:
    return Path(keys_dir) / f"probe_{index}.json"


def load_or_create_probe_keypairs(keys_dir: str | Path, count: int) -> list[Keypair]:
    """Probe accounts are dedicated experiment accounts. Files live under
    keys/ which is gitignored; keys are only ever loaded from disk/env."""
    out: list[Keypair] = []
    for i in range(count):
        p = probe_keypair_path(keys_dir, i)
        if p.exists():
            out.append(load_keypair(p))
        else:
            kp = Keypair()
            save_keypair(kp, p)
            out.append(kp)
    return out


def load_main_keypair() -> Keypair:
    """Main experiment wallet, ONLY via env INSIDE_BLOCK_KEYPAIR_PATH.
    Never hardcode a path and never commit this file."""
    path = os.environ.get("INSIDE_BLOCK_KEYPAIR_PATH")
    if not path:
        raise RuntimeError(
            "INSIDE_BLOCK_KEYPAIR_PATH is not set; the main keypair is only "
            "provided via env/volume, never from the repo."
        )
    return load_keypair(path)
