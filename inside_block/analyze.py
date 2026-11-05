from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path


def block_key(row: dict):
    """Total order of a landed transaction: (slot, index in block)."""
    if row.get("landed_slot") is None or row.get("block_index") is None:
        return None
    return (row["landed_slot"], row["block_index"])


def kendall_tau(send_order: list[int], block_order: list[int]) -> float | None:
    """Kendall tau between send ranks and block positions for paired items.
    send_order/block_order are aligned lists (item i has send_order[i], block_order[i]).
    Returns None when fewer than 2 items or no comparable pairs."""
    n = len(send_order)
    if n < 2:
        return None
    concordant = discordant = 0
    for i in range(n):
        for j in range(i + 1, n):
            s = (send_order[j] - send_order[i])
            b = (block_order[j] - block_order[i])
            if s == 0 or b == 0:
                continue
            if (s > 0) == (b > 0):
                concordant += 1
            else:
                discordant += 1
    total = concordant + discordant
    if total == 0:
        return None
    return (concordant - discordant) / total


def analyze_rows(rows: list[dict]) -> dict:
    by_validator: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_validator[r["target_leader_node"]].append(r)

    per_validator: dict[str, dict] = {}
    total_fee_pairs = 0
    total_reversals = 0
    for node, vrows in by_validator.items():
        landed = [r for r in vrows if block_key(r) is not None]
        # Fee pairs and arrival tau are only meaningful WITHIN one batch
        # (probes from different batches never share a block).
        pairs = reversals = 0
        batch_taus: list[float] = []
        landed_by_batch: dict[str, list[dict]] = defaultdict(list)
        for r in landed:
            landed_by_batch[r["batch_id"]].append(r)
        for batch_rows in landed_by_batch.values():
            fee = sorted([r for r in batch_rows if r["role"] == "fee"], key=lambda r: r["send_rank"])
            for i in range(len(fee)):
                for j in range(i + 1, len(fee)):
                    if fee[j]["fee_price_micro"] <= fee[i]["fee_price_micro"]:
                        continue
                    pairs += 1
                    if block_key(fee[j]) < block_key(fee[i]):
                        reversals += 1
            arrival = sorted([r for r in batch_rows if r["role"] == "arrival"], key=lambda r: r["send_rank"])
            if len(arrival) >= 2:
                order = sorted(range(len(arrival)), key=lambda k: block_key(arrival[k]))
                block_rank = [0] * len(arrival)
                for rank, k in enumerate(order):
                    block_rank[k] = rank
                t = kendall_tau([r["send_rank"] for r in arrival], block_rank)
                if t is not None:
                    batch_taus.append(t)
        total_fee_pairs += pairs
        total_reversals += reversals
        tau = (sum(batch_taus) / len(batch_taus)) if batch_taus else None
        # conflict pairs: exactly the low-first / high-second pair per batch
        conflict_outcomes: list[str] = []
        by_batch: dict[str, list[dict]] = defaultdict(list)
        for r in landed:
            if r["role"] == "conflict":
                by_batch[r["batch_id"]].append(r)
        for batch_rows in by_batch.values():
            if len(batch_rows) == 2:
                lo, hi = sorted(batch_rows, key=lambda r: r["send_rank"])
                conflict_outcomes.append(
                    "high_fee_first" if block_key(hi) < block_key(lo) else "low_fee_first"
                )
        sent = len(vrows)
        in_window = sum(1 for r in vrows if r["status"] == "in_window")
        late = sum(1 for r in vrows if r["status"] == "late")
        dropped = sum(1 for r in vrows if r["status"] == "dropped")
        per_validator[node] = {
            "client": vrows[0].get("target_client", ""),
            "sent": sent,
            "landed": len(landed),
            "in_window": in_window,
            "late": late,
            "dropped": dropped,
            "inclusion_rate": (len(landed) / sent) if sent else None,
            "fee_pairs": pairs,
            "fee_reversals": reversals,
            "fee_reversal_rate": (reversals / pairs) if pairs else None,
            "arrival_kendall_tau": tau,
            "conflict_outcomes": conflict_outcomes,
        }

    overall_rate = (total_reversals / total_fee_pairs) if total_fee_pairs else None
    if total_fee_pairs < 8:
        conclusion = "INSUFFICIENT_DATA"
        conclusion_text = (
            "Too few landed fee-tier pairs to distinguish a rule from noise. "
            "'No rule' cannot be claimed either; the honest result is insufficient data."
        )
    elif overall_rate >= 0.7:
        conclusion = "PRIORITY_DOMINANT"
        conclusion_text = "Higher-fee transactions systematically land earlier despite being sent later: ordering is priority-driven, not arrival-driven."
    elif overall_rate <= 0.3:
        conclusion = "ARRIVAL_DOMINANT"
        conclusion_text = "Block order mostly follows send order even against fee differences: ordering is arrival-driven within measurement noise."
    else:
        conclusion = "NO_CLEAR_RULE_NOISE"
        conclusion_text = "Reversal rate is near chance: no stable ordering rule is distinguishable from noise in this data."
    return {
        "per_validator": per_validator,
        "overall": {
            "fee_pairs": total_fee_pairs,
            "fee_reversals": total_reversals,
            "fee_reversal_rate": overall_rate,
            "conclusion": conclusion,
            "conclusion_text": conclusion_text,
        },
    }


def render_tables(analysis: dict) -> str:
    lines = [
        "| Validator (node) | Client | Sent | Landed | In-window | Late | Dropped | Fee pairs | Reversals | Reversal rate | Arrival tau | Conflict outcomes |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for node, v in analysis["per_validator"].items():
        rate = "n/a" if v["fee_reversal_rate"] is None else f"{v['fee_reversal_rate']:.2f}"
        tau = "n/a" if v["arrival_kendall_tau"] is None else f"{v['arrival_kendall_tau']:.2f}"
        lines.append(
            f"| {node} | {v['client']} | {v['sent']} | {v['landed']} | {v['in_window']} | {v['late']} | {v['dropped']} | "
            f"{v['fee_pairs']} | {v['fee_reversals']} | {rate} | {tau} | {', '.join(v['conflict_outcomes']) or 'n/a'} |"
        )
    o = analysis["overall"]
    lines += [
        "",
        f"Overall fee-pair reversal rate: {o['fee_reversal_rate']} "
        f"({o['fee_reversals']}/{o['fee_pairs']} pairs). Conclusion: **{o['conclusion']}** — {o['conclusion_text']}",
    ]
    return "\n".join(lines) + "\n"


def run_analysis(data_dir: Path) -> dict:
    rows = json.loads((data_dir / "summary.json").read_text()) if (data_dir / "summary.json").exists() else []
    analysis = analyze_rows(rows)
    (data_dir / "analysis.json").write_text(json.dumps(analysis, indent=1))
    (data_dir / "tables.md").write_text(render_tables(analysis))
    return analysis
