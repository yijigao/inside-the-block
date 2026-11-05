from __future__ import annotations

import json
from pathlib import Path

LAMPORTS_PER_SOL = 1_000_000_000


class SpendCapExceeded(RuntimeError):
    pass


class SpendLedger:
    """Persistent ledger of fees spent (lamports). The probe refuses to send
    any batch whose estimated fees would push cumulative spend over the cap."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.spent_lamports: int = 0
        self.entries: list[dict] = []
        if self.path.exists():
            raw = json.loads(self.path.read_text())
            self.spent_lamports = int(raw.get("spent_lamports", 0))
            self.entries = raw.get("entries", [])

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({"spent_lamports": self.spent_lamports, "entries": self.entries}, indent=1)
        )

    def would_exceed(self, additional_lamports: int, cap_lamports: int) -> bool:
        return self.spent_lamports + additional_lamports > cap_lamports

    def check(self, additional_lamports: int, cap_lamports: int) -> None:
        if self.would_exceed(additional_lamports, cap_lamports):
            raise SpendCapExceeded(
                f"spend cap: already spent {self.spent_lamports} lamports, "
                f"next batch needs {additional_lamports}, cap is {cap_lamports}"
            )

    def record(self, lamports: int, kind: str, ref: str) -> None:
        if lamports < 0:
            raise ValueError("cannot record negative spend")
        self.spent_lamports += lamports
        self.entries.append({"lamports": lamports, "kind": kind, "ref": ref})
        self._save()

    def remaining(self, cap_lamports: int) -> int:
        return cap_lamports - self.spent_lamports


def sol_to_lamports(sol: float) -> int:
    return int(round(sol * LAMPORTS_PER_SOL))
