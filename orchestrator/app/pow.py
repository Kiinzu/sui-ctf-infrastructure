"""Proof-of-work gate for instance spawning.

Scheme (self-contained, no external solver needed): the server issues a random
`challenge` string and a difficulty `D` (leading zero *bits*). The player must
find any integer `nonce` such that:

    sha256(challenge + str(nonce))  has >= D leading zero bits.

The player pastes back the nonce; the server re-hashes and checks. Difficulty is
tunable via POW_DIFFICULTY_BITS (0 disables the gate entirely).
"""
from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass


def _leading_zero_bits(digest: bytes) -> int:
    v = int.from_bytes(digest, "big")
    # 256-bit digest; count how many top bits are zero.
    return 256 - v.bit_length() if v else 256


def _hash(challenge: str, nonce: str) -> bytes:
    return hashlib.sha256((challenge + nonce).encode()).digest()


@dataclass(frozen=True)
class PowChallenge:
    challenge: str
    difficulty: int

    def verify(self, nonce: str) -> bool:
        if self.difficulty <= 0:
            return True
        nonce = (nonce or "").strip()
        if not nonce:
            return False
        return _leading_zero_bits(_hash(self.challenge, nonce)) >= self.difficulty

    def solver_command(self) -> str:
        """A copy-pasteable one-liner the player can run to find a nonce."""
        c, d = self.challenge, self.difficulty
        return (
            "python3 -c \""
            "import hashlib,itertools;"
            f"c='{c}';d={d};"
            "print(next(n for n in itertools.count() "
            "if int.from_bytes(hashlib.sha256((c+str(n)).encode()).digest(),'big')"
            ">>(256-d)==0))\""
        )


def new_challenge(difficulty: int) -> PowChallenge:
    return PowChallenge(challenge=secrets.token_hex(12), difficulty=max(0, difficulty))
