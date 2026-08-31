#!/usr/bin/env python3
"""Arcron cookbook recipe: decode every upkeep box on the keeper app.

Reads box storage straight from algod (default: public AlgoNode TestNet API).
Read-only — no keys, no transactions.

Upkeep box name:  b"u" || itob(upkeep_id)   (9 bytes, first byte 0x75)
Upkeep struct:    140 bytes, big-endian, verified against keeper 769891898:
    [0:32]  creator address
    [32]    target_app u64            [42] interval_rounds u64
    [50]    next_execution_round u64  [58] fee u64
    [66]    balance u64               [74] times_executed u64
    [82]    policy u64 (0 = CATCH_UP — TRAP 6, 1 = SKIP_AHEAD — what you want)

Run:  python3 recipes/read_upkeeps.py [--json]
"""
import argparse
import base64
import json
import os
import sys
from dataclasses import asdict, dataclass

# LOCAL TRAP: recipes/selectors.py shadows the stdlib `selectors` module for any
# script run from this directory (script dir lands at sys.path[0]), which breaks
# subprocess -> cffi -> pycryptodome inside py-algorand-sdk. Drop it before
# importing third-party packages.
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path = [p for p in sys.path if os.path.abspath(p or ".") != _SCRIPT_DIR]

from algosdk import encoding
from algosdk.v2client import algod

KEEPER_APP_ID = 769891898
ALGOD_URL = os.environ.get("ALGOD_URL", "https://testnet-api.algonode.cloud")
ROUND_SECONDS = 2.8  # TestNet round time, for ETA labels only

POLICY = {0: "CATCH_UP", 1: "SKIP_AHEAD"}


@dataclass
class Upkeep:
    id: int
    creator: str
    target_app: int
    interval: int
    next_round: int
    fee: int
    balance: int
    times_executed: int
    policy: int


def decode_upkeep(upkeep_id: int, raw: bytes) -> Upkeep:
    if len(raw) < 90:
        raise ValueError(f"short upkeep box {upkeep_id}: {len(raw)} bytes")
    u64 = lambda off: int.from_bytes(raw[off : off + 8], "big")
    return Upkeep(
        id=upkeep_id,
        creator=encoding.encode_address(raw[0:32]),
        target_app=u64(32),
        interval=u64(42),
        next_round=u64(50),
        fee=u64(58),
        balance=u64(66),
        times_executed=u64(74),
        policy=u64(82),
    )


def fetch_upkeeps(client: algod.AlgodClient) -> list[Upkeep]:
    boxes = client.application_boxes(KEEPER_APP_ID)["boxes"]
    upkeeps = []
    for b in boxes:
        name = base64.b64decode(b["name"])
        # upkeep boxes are exactly b"u" || itob(id); skip anything else
        if len(name) != 9 or name[0:1] != b"u":
            continue
        upkeep_id = int.from_bytes(name[1:], "big")
        value = base64.b64decode(client.application_box_by_name(KEEPER_APP_ID, name)["value"])
        upkeeps.append(decode_upkeep(upkeep_id, value))
    upkeeps.sort(key=lambda u: (u.next_round, u.id))
    return upkeeps


def eta_label(delta_rounds: int) -> str:
    if delta_rounds <= 0:
        return "DUE NOW"
    sec = delta_rounds * ROUND_SECONDS
    if sec < 3600:
        return f"~{round(sec / 60)}m"
    if sec < 86400:
        return f"~{sec / 3600:.1f}h"
    return f"~{sec / 86400:.1f}d"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = ap.parse_args()

    client = algod.AlgodClient("", ALGOD_URL)
    last_round = client.status()["last-round"]
    upkeeps = fetch_upkeeps(client)

    if args.json:
        print(json.dumps({
            "keeper_app": KEEPER_APP_ID,
            "last_round": last_round,
            "upkeeps": [asdict(u) for u in upkeeps],
        }, indent=2))
        return 0

    print(f"keeper app {KEEPER_APP_ID} @ {ALGOD_URL}")
    print(f"last round {last_round} · {len(upkeeps)} upkeep(s) registered\n")
    hdr = f"{'ID':>4}  {'TARGET':>10}  {'CREATOR':>12}  {'INTERVAL':>9}  {'NEXT ROUND':>11}  {'ETA':>8}  {'FEE':>6}  {'BALANCE':>9}  {'EXECS':>5}  POLICY"
    print(hdr)
    print("-" * len(hdr))
    for u in upkeeps:
        print(
            f"{u.id:>4}  {u.target_app:>10}  "
            f"{u.creator[:6]}…{u.creator[-4:]}  "
            f"{u.interval:>8}r  {u.next_round:>11}  {eta_label(u.next_round - last_round):>8}  "
            f"{u.fee:>6}  {u.balance:>9}  {u.times_executed:>5}  "
            f"{POLICY.get(u.policy, 'POL_' + str(u.policy))}"
        )
    escrow = sum(u.balance for u in upkeeps)
    print(f"\ntotal escrowed: {escrow} µALGO ({escrow / 1e6:.4f} ALGO)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
