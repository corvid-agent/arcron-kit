#!/usr/bin/env python3
"""Arcron cookbook recipe: register an upkeep on the keeper app, raw SDK.

Complete py-algorand-sdk register flow for keeper app 769891898 (TestNet),
with every trap commented inline at the line it guards. No algokit-utils,
no generated client — this is the from-scratch version you can port.

Run (real):   BANK_MNEMONIC="25 words..." python3 recipes/register.py \
                  --target-app 770734249 --interval 224000 --funding 500000
Run (dry):    python3 recipes/register.py --target-app 770734249 --interval 224000 \
                  --funding 500000 --dry-run

Dry run reads live keeper state and builds the full group without signing or
sending anything. The mnemonic comes from BANK_MNEMONIC, never from source.
"""
import argparse
import base64
import os
import sys
from hashlib import new as _new

# LOCAL TRAP: recipes/selectors.py shadows the stdlib `selectors` module for
# any script run from this directory, which breaks subprocess -> cffi inside
# py-algorand-sdk. Drop the script dir from sys.path before importing it.
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path = [p for p in sys.path if os.path.abspath(p or ".") != _SCRIPT_DIR]

from algosdk import encoding, mnemonic, transaction
from algosdk.abi import ArrayDynamicType, ByteType
from algosdk.logic import get_application_address
from algosdk.v2client import algod

KEEPER_APP_ID = 769891898
ALGOD_URL = os.environ.get("ALGOD_URL", "https://testnet-api.algonode.cloud")

# TRAP 10 — the register signature, verbatim. `target_app` is declared
# `Application` in the Puya source but Puya lowers it to uint64 in the ARC-4
# signature (same lowering as TRAP 4). Transcribing `application` from the
# source hashes to 0x7291d904, matches no method, and fails without saying why.
REGISTER_SIG = (
    "register(pay,pay,uint64,byte[][],uint64,uint64,uint64,uint64,uint64,uint64)uint64"
)
REGISTER_SELECTOR = _new("sha512_256", REGISTER_SIG.encode()).digest()[:4]
assert REGISTER_SELECTOR.hex() == "3636cfc6"  # verified against Keeper.arc56.json

# From smart_contracts/keeper/contract.py (CorvidLabs/arcron):
MIN_INTERVAL_ROUNDS = 10
MAX_INTERVAL_ROUNDS = 1_000_000_000
MIN_UPKEEP_FEE = 4_000          # TRAP 9a — below this, register asserts "Fee below minimum"
MAX_UPKEEP_FEE = 1_000_000_000
BOX_MBR_FIXED = 2_500 + 400 * 139   # 58,100 µALGO: 2,500 per box + 400/byte of name+head
MAX_CALL_ARGS = 3
MAX_CALL_DATA = 1_024
CATCH_UP = 0                    # TRAP 6 — the trap. Replays every missed interval.
SKIP_AHEAD = 1                  #           what you almost always want.
REGISTER_APP_CALL_FEE = 3_000   # TRAP 9b — the app-call txn carries a 3,000 µALGO
                                # flat fee; the keeper's inner calls spend it.

KEEPER_ADDRESS = get_application_address(KEEPER_APP_ID)
# == M4YFP33L5VIFRF53X53WUMQWBOWSLYQNBSSAJV2SORGF43L36XBY7OREUA — derive it,
# never paste it (and never pay the creator account; both payments go to the
# APP account or the contract asserts).


def encode_call_args(target_selector: bytes) -> bytes:
    """TRAP 1 — ARC-4 `byte[][]` carries its own offset header.

    A bare 4-byte selector hand-rolled as call_args fails `register` with a
    bare assert: the contract reads `call_args.bytes`, and a DynamicArray of
    DynamicBytes encodes as u16 count + u16 offsets + u16 lengths + data.
    For one 4-byte selector that is `0001 0002 0004 <selector>` — 10 bytes,
    not 4. Let the SDK encode it; never concatenate by hand.
    """
    t = ArrayDynamicType(ArrayDynamicType(ByteType()))
    return t.encode([target_selector])


def box_mbr(encoded_call_args: bytes) -> int:
    """TRAP 2 — MBR = 2,500 + 400 * (139 + len(encoded call_args)) µALGO.

    62,100 µALGO for the 10-byte bare-selector encoding. Underpaying fails
    "MBR payment too small"; the MBR is refunded in full on `cancel`.
    """
    return BOX_MBR_FIXED + 400 * len(encoded_call_args)


def next_upkeep_id(client: algod.AlgodClient) -> int:
    """TRAP 3 — the box reference is the FUTURE box, named by an id the
    contract assigns at register time. Read `next_upkeep_id` from keeper
    global state FRESH each attempt: if anyone registers between your read
    and your submit, the reference is stale and the group fails. Re-read and
    resubmit; it is a race, not a bug.
    """
    gs = client.application_info(KEEPER_APP_ID)["params"]["global-state"]
    for kv in gs:
        if base64.b64decode(kv["key"]) == b"next_upkeep_id":
            return int(kv["value"]["uint"])
    raise RuntimeError("keeper global state has no next_upkeep_id")


def build_register_group(
    client: algod.AlgodClient,
    sender: str,
    target_app: int,
    target_selector: bytes,
    interval: int,
    fee: int,
    funding: int,
    policy: int,
    fee_cap: int = 0,
    fee_asset: int = 0,
    asset_fee: int = 0,
) -> list[transaction.Transaction]:
    """Assemble [mbr_payment, funding_payment, app_call] — order is load-bearing."""
    # Client-side copies of the contract's own asserts, so a dry run fails
    # here instead of at validation:
    assert MIN_INTERVAL_ROUNDS <= interval <= MAX_INTERVAL_ROUNDS, "interval out of bounds"
    assert MIN_UPKEEP_FEE <= fee <= MAX_UPKEEP_FEE, "fee out of bounds"
    assert policy in (CATCH_UP, SKIP_AHEAD), "unknown policy"
    assert fee_cap == 0 or fee_cap >= fee, "fee cap below the fee"
    assert fee_asset == 0 or asset_fee > 0, "asset fee must be positive"
    call_args = encode_call_args(target_selector)
    assert 0 < len(call_args) <= MAX_CALL_DATA, "call data too large"
    # funding must cover one execution at the price the upkeep can actually
    # be charged (the cap, if one is set):
    required_funding = fee_cap if fee_cap > fee else fee
    assert funding >= required_funding, "funding must cover at least one execution"

    upkeep_id = next_upkeep_id(client)  # TRAP 3: fresh read, every attempt
    mbr = box_mbr(call_args)            # TRAP 2
    sp = client.suggested_params()

    # TRAP 2 (cont.) — BOTH payments go to the keeper APP ADDRESS, from the
    # SAME sender as the app call, and FIRST mbr THEN funding: the contract
    # checks receivers, senders, and relative group positions. No rekey, no
    # close_remainder_to (the contract refuses both).
    mbr_payment = transaction.PaymentTxn(
        sender=sender, sp=sp, receiver=KEEPER_ADDRESS, amt=mbr,
    )
    funding_payment = transaction.PaymentTxn(
        sender=sender, sp=sp, receiver=KEEPER_ADDRESS, amt=funding,
    )

    # ARC-4 args: selector first, then the non-reference args in order.
    # The two `pay` args are reference types — they live in the group, NOT in
    # app_args. itob = 8-byte big-endian.
    itob = lambda n: n.to_bytes(8, "big")
    app_args = [
        REGISTER_SELECTOR,
        itob(target_app),
        call_args,
        itob(interval),
        itob(fee),
        itob(policy),
        itob(fee_cap),
        itob(fee_asset),
        itob(asset_fee),
    ]

    app_sp = client.suggested_params()
    app_sp.flat_fee = True
    app_sp.fee = REGISTER_APP_CALL_FEE   # TRAP 9b — 3,000 µALGO flat on the app call

    app_call = transaction.ApplicationNoOpTxn(
        sender=sender,
        sp=app_sp,
        index=KEEPER_APP_ID,
        app_args=app_args,
        # TRAP 3 — the box being CREATED: b"u" || itob(the id register will assign).
        boxes=[(KEEPER_APP_ID, b"u" + itob(upkeep_id))],
    )

    group = [mbr_payment, funding_payment, app_call]
    transaction.assign_group_id(group)
    return group


def describe(group, target_app, interval, fee, funding, policy) -> None:
    mbr_payment, funding_payment, app_call = group
    box_name = bytes(app_call.boxes[0].name)
    upkeep_id = int.from_bytes(box_name[1:], "big")
    gid = base64.b64encode(app_call.group).decode() if app_call.group else "—"
    print(f"register group (3 txns, group id {gid})")
    print(f"  [0] pay   {mbr_payment.amt:>7} µALGO -> {mbr_payment.receiver}")
    print(f"            (box MBR: 58,100 + 400*len(encoded call_args))")
    print(f"  [1] pay   {funding_payment.amt:>7} µALGO -> {funding_payment.receiver}")
    print(f"            (escrow: {funding / fee:.0f} executions at {fee} µALGO)")
    print(f"  [2] appl  register(...) on {app_call.index}, flat fee {app_call.fee} µALGO")
    print(f"            selector {app_call.app_args[0].hex()} (expect 3636cfc6)")
    print(f"            call_args {app_call.app_args[2].hex()} "
          f"({len(app_call.app_args[2])} bytes, expect 0001 0002 0004 <selector>)")
    print(f"            box ref \"u\"+itob({upkeep_id}) = {box_name.hex()}  <- FUTURE box")
    print(f"            target_app={target_app} interval={interval}r fee={fee} "
          f"policy={policy} ({'SKIP_AHEAD' if policy == 1 else 'CATCH_UP — trap!'})")
    print(f"  sender    {app_call.sender} (same on all three)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--target-app", type=int, required=True, help="app id the upkeep calls")
    ap.add_argument("--selector", default="4d4d5f0b",
                    help="hex selector of the target method (default: 4d4d5f0b = tick()uint64)")
    ap.add_argument("--interval", type=int, required=True, help="cadence in rounds (min 10)")
    ap.add_argument("--fee", type=int, default=MIN_UPKEEP_FEE,
                    help="µALGO per execution (min 4,000)")
    ap.add_argument("--funding", type=int, required=True, help="µALGO escrow deposit")
    ap.add_argument("--policy", type=int, default=SKIP_AHEAD, choices=(CATCH_UP, SKIP_AHEAD),
                    help="TRAP 6: 0=CATCH_UP (replays missed intervals — usually wrong), "
                         "1=SKIP_AHEAD (default, what you want)")
    ap.add_argument("--fee-cap", type=int, default=0, help="escalation ceiling, 0 = off")
    ap.add_argument("--dry-run", action="store_true",
                    help="build and print the group against live state; sign and send nothing")
    args = ap.parse_args()

    if args.policy == CATCH_UP:
        print("WARNING: policy CATCH_UP replays every missed interval; one due upkeep can "
              "demand many back-to-back executions and drain its escrow. See TRAP 6.",
              file=sys.stderr)

    client = algod.AlgodClient("", ALGOD_URL)

    mn = os.environ.get("BANK_MNEMONIC")   # never hardcoded; env only
    if mn:
        sender = mnemonic.to_public_key(mn)
    elif args.dry_run:
        sender = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAY5HFKQ"
        print("dry run: BANK_MNEMONIC unset, using the zero address as sender\n")
    else:
        print("BANK_MNEMONIC is not set; refusing to send. Use --dry-run to build only.",
              file=sys.stderr)
        return 2

    target_selector = bytes.fromhex(args.selector)
    assert len(target_selector) == 4, "a method selector is exactly 4 bytes"

    group = build_register_group(
        client, sender, args.target_app, target_selector,
        args.interval, args.fee, args.funding, args.policy, args.fee_cap,
    )
    describe(group, args.target_app, args.interval, args.fee, args.funding, args.policy)

    if args.dry_run:
        print("\ndry run: group built against live keeper state; nothing signed or sent.")
        print("re-run without --dry-run (BANK_MNEMONIC set) to submit — the box ref is")
        print("re-read fresh inside build_register_group on every attempt.")
        return 0

    signed = [txn.sign(mnemonic.to_private_key(mn)) for txn in group]
    txid = client.send_transactions(signed)
    result = transaction.wait_for_confirmation(client, txid, 4)
    # ARC-4 return log: 0x151f7c75 || itob(upkeep_id)
    upkeep_id = None
    for log in result.get("logs") or []:
        raw = base64.b64decode(log)
        if raw[:4].hex() == "151f7c75":
            upkeep_id = int.from_bytes(raw[4:], "big")
    print(f"\nconfirmed in round {result['confirmed-round']}: tx {txid}")
    print(f"registered upkeep id: {upkeep_id} "
          f"(box b\"u\"+itob({upkeep_id}) now exists; verify with recipes/read_upkeeps.py)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
