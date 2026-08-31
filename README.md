# arcron-kit

The integration cookbook for the [Arcron](https://github.com/CorvidLabs/arcron) keeper
network: every recipe and every trap, copy-paste runnable. All ten traps below were hit
and fixed for real on Algorand TestNet keeper app **769891898** while deploying the four
reference contracts.

**Cookbook site (GitHub Pages):** `docs/` — the ten traps as split-flap cards, the
register recipe annotated, the live upkeep board decoded in-browser, and a committed
`snapshot.json` fallback. Enable Pages from `/docs` to serve it.

## Quickstart

```bash
pip install py-algorand-sdk

# 1. Verify your selector math against known vectors (self-checking, exits 1 on mismatch)
python3 recipes/selectors.py

# 2. Read every upkeep on the keeper, decoded from box storage (read-only)
python3 recipes/read_upkeeps.py

# 3. Build a register group against live keeper state — nothing signed, nothing sent
python3 recipes/register.py --target-app <your_app_id> --interval 30857 \
    --funding 500000 --dry-run

# 4. Register for real (TestNet). Mnemonic comes from the environment, never source.
BANK_MNEMONIC="twenty five words ..." python3 recipes/register.py \
    --target-app <your_app_id> --interval 30857 --funding 500000
```

| Recipe | What it does |
|---|---|
| `recipes/selectors.py` | sha512_256 ARC-4 selector derivation + self-verifying known vectors |
| `recipes/register.py` | Full register flow on keeper 769891898, raw py-algorand-sdk, traps commented inline at the line each guards. `--dry-run` builds the group against live state without signing |
| `recipes/read_upkeeps.py` | Decode every upkeep box via algod (`--json` for machine output) |

## The ten traps

1. **ARC-4 `byte[][]` needs its offset header** — a bare 4-byte selector hand-rolled as
   call_args fails `register` with a bare assert. Correct encoding:
   `0001 0002 0004 <selector>` (10 bytes) —
   `ArrayDynamicType(ArrayDynamicType(ByteType())).encode([selector])`.
2. **MBR formula** — `2,500 + 400 × (139 + len(encoded call_args))` µALGO → **62,100** for
   the bare-selector encoding. Both payments go to the keeper **app address**
   (`algosdk.logic.get_application_address(769891898)` =
   `M4YFP33L5VIFRF53X53WUMQWBOWSLYQNBSSAJV2SORGF43L36XBY7OREUA`), from the same sender as
   the app call, group order `[mbr_payment, funding_payment, app_call]`.
3. **The box reference is the FUTURE box** — `b"u" + itob(next_upkeep_id)`, read from
   keeper global state fresh each time; a stale id fails the group. It's a race, not a bug.
4. **Puya lowers `Application` params to `uint64`** — `set_keeper(uint64)void` =
   `0xc4c1d8f7`, not `set_keeper(application)void`. A selector mismatch dies with
   "err opcode".
5. **`app_params_get` needs the app available** — hand-TEAL `set_keeper` reading
   `AppCreator` needs the keeper app in `foreign_apps` ("unavailable App" otherwise).
   Keeper-auth reads inside hooks (`Application(keeper).address`) work **without** naming
   it: keeper bots simulate first and auto-attach resources
   (`_resolve_execute_references` in `CorvidLabs/arcron` `scripts/keeper_bot.py`).
6. **Policy 0 (`CATCH_UP`) is the trap; `SKIP_AHEAD = 1` is what you want** — catch-up
   replays every missed interval; one due upkeep can demand many back-to-back executions
   and drain its escrow.
7. **Zero uint64 create-args** — a create-arg mapped to the keeper id left at zero freezes
   cadence for ~68 years of rounds.
8. **Fail-soft hooks** — asserts after keeper auth get exponentially backed off by keeper
   bots (1, 2, 4, 8 intervals, capped near 1,286 rounds, persisted across restarts) and
   burn escrow. Return, don't assert.
9. **Fee floor** — `MIN_UPKEEP_FEE = 4,000` µALGO; the app-call txn in the register group
   wants a **3,000 µALGO flat fee**.
10. **Register signature, verbatim** —
    `register(pay,pay,uint64,byte[][],uint64,uint64,uint64,uint64,uint64,uint64)uint64`,
    selector `sha512_256(sig)[:4]` = `0x3636cfc6`. Transcribing `application` from the
    Puya source hashes to `0x7291d904` and matches nothing.

## Live reference contracts

| Contract | App id | Upkeep | Interval | Hook |
|---|---|---|---|---|
| [corvid-agent/plod](https://github.com/corvid-agent/plod) | 770734249 | 110 | 224,000 r (~weekly) | `tick()uint64` |
| [corvid-agent/waddle](https://github.com/corvid-agent/waddle) | 770742373 | 111 | 30,857 r (~daily) | `tick()uint64` |
| [corvid-agent/arcron-beacon](https://github.com/corvid-agent/arcron-beacon) | 770742777 | 112 | 1,700 r | `tick()uint64` |
| [corvid-agent/epitaph](https://github.com/corvid-agent/epitaph) | 770748282 | 114 | 7,200 r | `publish()uint64` |

## Protocol docs

- [CorvidLabs/arcron · docs/integrating.md](https://github.com/CorvidLabs/arcron/blob/main/docs/integrating.md) —
  the protocol's own integration guide (hook shape, authorization, the pull pattern,
  funding math).
- [corvid-agent/arcron-hub](https://github.com/corvid-agent/arcron-hub) — live network
  observer this cookbook's board is built from.

## Notes

- TestNet only. Arcron is unaudited experimental software.
- `recipes/selectors.py` shadows the stdlib `selectors` module for any script run from
  inside `recipes/` (script dir lands at `sys.path[0]`), which breaks
  subprocess → cffi → pycryptodome inside py-algorand-sdk. The other two recipes drop the
  script dir from `sys.path` before importing; if you add scripts there, copy that guard.
- The Pages site fetches boxes live from `https://testnet-api.algonode.cloud`
  (`?name=b64:<base64>` per box) and falls back to the committed `docs/snapshot.json`.
