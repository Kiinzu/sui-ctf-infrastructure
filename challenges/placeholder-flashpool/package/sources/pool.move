/// Placeholder CTF challenge used to prove the infrastructure pipeline
/// (spawn -> deploy -> solve -> solve-check -> flag) end to end.
///
/// Theme mirrors the real "flash-pool" hard challenge: a shared `Pool` holds a
/// balance, and the win condition is draining it to zero. Here the "vulnerability"
/// is intentionally trivial (an open `drain`) so the bundled solver is simple and
/// the whole pipeline is testable. Swap this module out for the real challenge
/// later; only `challenge.yml` needs to point at the new package + solve-check.
module challenge::pool;

use sui::event;

/// The pool players must drain. Shared object, seeded at deploy time.
public struct Pool has key {
    id: UID,
    balance: u64,
}

/// Emitted by `claim` once the pool is drained. Demonstrates the on-chain
/// event path for the `event`-type solve-check (the `on_chain_claim` flag mode).
public struct FlagClaimed has copy, drop {
    who: address,
}

/// Deploy-time seed step: create the shared pool with `amount`.
/// Invoked by the orchestrator's deploy spec right after publish.
entry fun init_pool(amount: u64, ctx: &mut TxContext) {
    transfer::share_object(Pool { id: object::new(ctx), balance: amount });
}

/// Placeholder "flash-loan drain" — the intended (trivial) exploit path.
/// Sets the pool balance to zero. The real challenge would gate this behind an
/// actual vulnerability; here it is open so the pipeline is easy to exercise.
entry fun drain(pool: &mut Pool) {
    pool.balance = 0;
}

/// Optional on-chain claim path: only succeeds once the pool is drained, and
/// emits `FlagClaimed`. Lets a challenge use `solve.type: event` /
/// `flag_mode: on_chain_claim` instead of a pure state read.
entry fun claim(pool: &Pool, ctx: &TxContext) {
    assert!(pool.balance == 0, 0);
    event::emit(FlagClaimed { who: tx_context::sender(ctx) });
}

/// Read helper for the pool balance.
public fun balance(pool: &Pool): u64 {
    pool.balance
}
