import hashlib
import itertools

from app.pow import new_challenge


def _solve(challenge: str, difficulty: int) -> str:
    for n in itertools.count():
        h = hashlib.sha256((challenge + str(n)).encode()).digest()
        if int.from_bytes(h, "big") >> (256 - difficulty) == 0:
            return str(n)
    raise AssertionError("unreachable")


def test_roundtrip():
    pc = new_challenge(12)
    nonce = _solve(pc.challenge, pc.difficulty)
    assert pc.verify(nonce)
    # any earlier integer is, by construction, not a valid solution
    if int(nonce) > 0:
        assert not pc.verify(str(int(nonce) - 1))


def test_disabled_difficulty_accepts_anything():
    pc = new_challenge(0)
    assert pc.verify("whatever")
    assert pc.verify("")


def test_empty_and_garbage_rejected_when_enabled():
    pc = new_challenge(16)
    assert not pc.verify("")
    assert not pc.verify(None)  # type: ignore[arg-type]


def test_solver_command_is_runnable_and_correct():
    import subprocess
    import sys

    pc = new_challenge(14)
    cmd = pc.solver_command().replace("python3", sys.executable, 1)
    # strip the surrounding `python3 -c "..."` to run via -c safely
    out = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=60)
    nonce = out.stdout.strip()
    assert nonce != "", out.stderr
    assert pc.verify(nonce)
