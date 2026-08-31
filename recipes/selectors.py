#!/usr/bin/env python3
"""Arcron cookbook recipe: ARC-4 method selector derivation.

ARC-4 selector = sha512_256("<method_signature>")[:4]

Run:  python3 recipes/selectors.py
Exits non-zero if any known vector fails to match. These vectors are the
ground truth for every trap in this cookbook — if your selector is wrong,
the keeper app (or your own app) dies with a bare assert or "err opcode".
"""
from hashlib import new as _new

def selector(signature: str) -> bytes:
    """First 4 bytes of the SHA-512/256 digest of the ARC-4 signature."""
    return _new("sha512_256", signature.encode()).digest()[:4]

# Known vectors, verified against live TestNet contracts.
#   tick()uint64      — the canonical upkeep target method (plod/waddle/beacon/epitaph)
#   set_keeper(uint64)void — TRAP 4: Puya lowers Application params to uint64.
#                        set_keeper(application)void is a DIFFERENT selector and
#                        dies with "err opcode" on a Puya-compiled app.
#   register(...)     — TRAP 10: keeper app 769891898 register signature.
#   publish()uint64   — epitaph's upkeep target method.
#   create()void      — bare create entrypoint used by the reference contracts.
KNOWN_VECTORS = {
    "tick()uint64": "4d4d5f0b",
    "set_keeper(uint64)void": "c4c1d8f7",
    "register(pay,pay,uint64,byte[][],uint64,uint64,uint64,uint64,uint64,uint64)uint64": "3636cfc6",
    "publish()uint64": "be0b2922",
    "create()void": "4c5c61ba",
}


def main() -> int:
    width = max(len(s) for s in KNOWN_VECTORS)
    failed = 0
    for sig, expected in KNOWN_VECTORS.items():
        got = selector(sig).hex()
        ok = got == expected
        failed += 0 if ok else 1
        mark = "PASS" if ok else f"FAIL (expected {expected})"
        print(f"{got}  {mark}  {sig}")
    # TRAP 4 demonstrated: the wrong type in the signature gives the wrong selector.
    wrong = selector("set_keeper(application)void").hex()
    print(f"\ntrap 4 demo: set_keeper(application)void -> {wrong} "
          f"(!= {KNOWN_VECTORS['set_keeper(uint64)void']} — Puya lowers Application to uint64)")
    if failed:
        print(f"\n{failed} vector(s) FAILED")
        return 1
    print(f"\nall {len(KNOWN_VECTORS)} selector vectors verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
