# Inside the Block — How Solana Validators Order Transactions

**Measurement date:** 2026-11-05 (all data collected after the Alpenglow upgrade window of 2026-10-21; no pre-upgrade data is mixed in)
**Epoch:** 1077 · **Sender:** one externally owned account set (1 funding wallet + 9 probe accounts) in a single location, submitting through public RPC endpoints
**Status:** FINAL — all numbers below are computed from `data/analysis.json` / `data/summary.json` after the last collection round (2026-11-05 ~14:25 CST). Total fees consumed: **0.00356 SOL** (spend ledger, 630 entries), against a hard cap of 0.05 SOL. Of the 0.1 SOL funding, 0.0964 SOL remains in the experiment's own accounts (0.081955 in the main wallet, 0.014442 across the 9 probe accounts); the only value destroyed was transaction fees.

## Question

When several transactions compete for the same block, what decides their order inside it: the priority fee they pay, the order in which they arrive at the leader, or something else — and does the answer differ by validator client?

## Design in one paragraph

Every probe batch is 10 self-transactions fired at one leader's 4-slot window: 4 *fee probes* (different accounts, priority-fee prices 0 / 1k / 10k / 100k µlamports per CU, deliberately sent lowest-fee first, highest-fee last), 4 *arrival probes* (same fee, known send order), and 2 *conflict probes* (same account twice: low fee first, high fee second). Each transaction carries an SPL Memo (`IB1|<batch>|<role>|<rank>|<fee>`) so anyone can find and verify the probes on-chain. If block order follows fees, the fee probes must come out reversed relative to send order; if it follows arrival, they must come out in send order. Full design rationale, confounders, and reproduction steps are in the README.

## Targets

Eight validators were aimed at, spanning six distinct client version strings (4.4.0, 4.4.0-rc.1, 4.3.2, 26.10.6, 26.10.5, 0.1.1), all of them running Jito according to the Jito Kobe validator registry at selection time (2026-11-05 ~13:18 CST). See `config.json` for the exact vote/node pubkeys.

## What went wrong (read this first)

1. **Public-RPC forwarding delay.** Transactions submitted through public RPC endpoints (api.mainnet-beta.solana.com, raced with solana-rpc.publicnode.com) reached blocks a **median of 51 slots (~20 s) after the targeted leader window** (mean 51.6, range −7…+107), whichever endpoint won the race. Only 1 of 630 probes landed inside its aimed window. Sequential submission was worse: a 10-transaction batch smeared across ~75 slots. Concurrent submission compressed each batch into a median span of 1 slot (most batches landed inside 1–2 consecutive blocks), but the systematic delay did not move. Consequence: batches are *attributed* to the leader that actually included them (recorded per transaction in `data/summary.json`), and the aimed-at leader is reported alongside. The ordering question is answered for the including leader, which is the only leader whose decision is observable anyway.
2. **Leader-schedule API trap.** `getLeaderSchedule(epoch)` silently returns the schedule for the epoch containing *slot* `epoch` if the epoch number is passed as the slot parameter; the first live run found "no windows" for this reason.
3. **Memo compute cost.** The SPL Memo instruction in our transaction shape consumes ~10.5k CU on current mainnet, not the ~300 CU of older documentation; at a 5,000 CU limit every probe failed with out-of-compute. Limit is now 30,000 CU.

## Findings

- **Scale:** 63 live batches fired · 630 probes sent · **630 landed (100% inclusion) · 0 dropped**, across 177 distinct blocks. Batches were aimed at 7 of the 8 target validators (the 4.4.0-rc.1 target's windows never came up first in the campaign loop; its client family still appears below as an *including* leader). The 630 probes were included by **68 distinct leaders** spanning 6 client families: 4.4.0 (415 probes), 26.10.6 (104), 26.10.5 (53), 4.4.0-rc.1 (37), 4.3.2 (20), 0.1.1 (1).
- **Fee ordering — no rule distinguishable from noise.** 378 within-batch fee pairs (a pair = two fee probes of one batch; a *reversal* = the higher-fee probe placed before a lower-fee probe that was sent earlier). **155 reversals → reversal rate 0.410**, i.e. *below* the 0.5 expected if block order ignored fees entirely, and far from the ~1.0 that fee-priority ordering would produce. Analyzer conclusion: `NO_CLEAR_RULE_NOISE`. Paying 100× the priority fee did not buy earlier placement for these small, uncontended transfers arriving via public RPC.
- **Arrival ordering — also essentially absent.** Per-batch Kendall tau between send order and block order for the equal-fee arrival probes: **mean 0.069, median 0.0** across 63 batches. Whatever scrambled the batches between our sender and the including leader erased the send order almost completely — consistent with the ~51-slot forwarding delay above: by inclusion time, the batch is re-queued, not replayed in arrival sequence.
- **Conflict pairs (same account, low fee sent first, high fee second): 63 pairs → high-fee-first 35, low-fee-first 28.** A coin flip with a slight high-fee lean; nowhere near decisive. Priority fees do not reliably jump a same-account write-lock queue in this setup either.
- **Per aimed validator** (full tables in `data/tables.md` and `data/analysis.json`):

  | Aimed validator | Client | Batches | Fee pairs | Reversal rate | Arrival tau |
  |---|---|---|---|---|---|
  | everstake | agave-4.4.0+jito | 25 | 150 | 0.360 | 0.133 |
  | helius | agave-4.4.0+jito | 16 | 96 | 0.385 | −0.146 |
  | jupiter | v26.10.6+jito | 13 | 78 | 0.474 | 0.282 |
  | fd | v26.10.5+jito | 4 | 24 | 0.500 | 0.417 |
  | agave-a | agave-4.3.2+jito | 3 | 18 | 0.500 | −0.222 |
  | chorus | agave-4.3.2+jito | 1 | 6 | 0.500 | −0.333 |
  | v0.1.1 | v0.1.1+jito | 1 | 6 | 0.500 | −1.000 |

  The two validators with real sample sizes (everstake, helius — same client build) agree with the overall picture; the small-n rows are shown for completeness and support no per-validator claim on their own. No client family shows fee-priority ordering for this traffic class.

- **Bottom line:** for small independent transfers submitted through public RPC, neither the priority fee (0 / 1k / 10k / 100k µlamports/CU) nor the arrival order measurably decides intra-block order in our data. The dominant measurable effect is the public-RPC path itself: a ~51-slot median delay and a near-complete reshuffle before inclusion. "Validators order by fee" may well hold for bundles, staked connections, or contended state — none of which this experiment measures (see scope note below).

## What this does and does not show

The probes measure how a leader orders *small, independent, low-contention transfers that arrive together through public RPC*. They do not measure Jito bundle ordering (no bundles were sent and bundle/tip mechanics are explicitly out of scope), private order flow, stake-weighted QoS lanes from staked connections, or arbitrage transactions competing for the same state — all of which can follow different rules. "No clear rule" is reported as-is where the data cannot separate a rule from noise.

## Raw data

- `data/sends.jsonl` — every probe sent: signature, batch, role, fee tier, send timestamps, target window.
- `data/summary.json` / `data/summary.csv` — every probe located on-chain: landed slot, index within the block, target leader, actual including leader and its client version.
- `data/raw/block_<slot>.json` — the full ordered signature list of every block a probe landed in.
- `data/analysis.json` — all computed statistics (per aimed validator and per including leader).
- `data/spend_ledger.json` was intentionally not committed (it contains no secrets, only fee accounting; totals are stated above).
