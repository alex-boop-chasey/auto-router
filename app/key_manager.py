"""
Key management with budgets, rate limits, and spend tracking.

Per-key: max_budget (USD), budget_duration (1d/30d), rpm_limit, tpm_limit.
Auto-enforces budgets on each request, rejects over-budget keys.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Any
import asyncpg

@dataclass
class KeyBudget:
    caller_id: str
    max_budget: float | None = None
    budget_duration: str | None = None  # "1d", "30d", None = never reset
    rpm_limit: int | None = None
    tpm_limit: int | None = None
    spend_tracked: float = 0.0
    budget_reset_at: datetime | None = None
    is_active: bool = True

    @property
    def is_over_budget(self) -> bool:
        if self.max_budget is None or self.max_budget <= 0:
            return False
        return self.spend_tracked >= self.max_budget

    @property
    def remaining_budget(self) -> float | None:
        if self.max_budget is None:
            return None
        return max(0.0, self.max_budget - self.spend_tracked)

    def needs_reset(self, now: datetime | None = None) -> bool:
        if not self.budget_reset_at or not self.budget_duration:
            return False
        now = now or datetime.now(timezone.utc)
        return now >= self.budget_reset_at

    def next_reset_at(self, now: datetime | None = None) -> datetime | None:
        if not self.budget_duration:
            return None
        now = now or datetime.now(timezone.utc)
        dur = _parse_duration(self.budget_duration)
        if dur is None:
            return None
        return now + dur


def _parse_duration(d: str) -> timedelta | None:
    """Parse budget_duration strings like '1d', '30d', '1h'."""
    if d.endswith("d"):
        try:
            return timedelta(days=int(d[:-1]))
        except ValueError:
            return None
    if d.endswith("h"):
        try:
            return timedelta(hours=int(d[:-1]))
        except ValueError:
            return None
    if d.endswith("m"):
        try:
            return timedelta(minutes=int(d[:-1]))
        except ValueError:
            return None
    return None


async def ensure_budget_columns(pool: asyncpg.Pool) -> None:
    """Add budget columns to keys table if they don't exist."""
    async with pool.acquire() as conn:
        await conn.execute("""
            ALTER TABLE keys
            ADD COLUMN IF NOT EXISTS max_budget NUMERIC(12,6) DEFAULT NULL,
            ADD COLUMN IF NOT EXISTS budget_duration TEXT DEFAULT NULL,
            ADD COLUMN IF NOT EXISTS rpm_limit INTEGER DEFAULT NULL,
            ADD COLUMN IF NOT EXISTS tpm_limit INTEGER DEFAULT NULL,
            ADD COLUMN IF NOT EXISTS spend_tracked NUMERIC(12,6) DEFAULT 0,
            ADD COLUMN IF NOT EXISTS budget_reset_at TIMESTAMPTZ DEFAULT NULL,
            ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE
        """)


async def get_key_budget(pool: asyncpg.Pool, caller_id: str) -> KeyBudget | None:
    """Fetch budget + rate limit info for a key. Returns None if key not found."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT caller_id, max_budget, budget_duration, rpm_limit, tpm_limit,
                      spend_tracked, budget_reset_at, is_active
               FROM keys WHERE caller_id = $1""",
            caller_id,
        )
    if not row:
        return None

    budget = KeyBudget(
        caller_id=row["caller_id"],
        max_budget=float(row["max_budget"]) if row["max_budget"] is not None else None,
        budget_duration=row["budget_duration"],
        rpm_limit=row["rpm_limit"],
        tpm_limit=row["tpm_limit"],
        spend_tracked=float(row["spend_tracked"]) if row["spend_tracked"] else 0.0,
        budget_reset_at=row["budget_reset_at"],
        is_active=row["is_active"],
    )

    # Check if budget needs resetting
    if budget.needs_reset():
        budget.spend_tracked = 0.0
        budget.budget_reset_at = budget.next_reset_at()
        # Persist the reset
        async with pool.acquire() as conn:
            await conn.execute(
                """UPDATE keys SET spend_tracked = 0,
                   budget_reset_at = $2 WHERE caller_id = $1""",
                caller_id, budget.budget_reset_at,
            )

    return budget


async def track_spend(
    pool: asyncpg.Pool, caller_id: str, cost_usd: float
) -> KeyBudget | None:
    """Add cost to a key's tracked spend. Returns updated budget or None."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """UPDATE keys SET spend_tracked = spend_tracked + $2
               WHERE caller_id = $1
               RETURNING caller_id, max_budget, budget_duration, rpm_limit, tpm_limit,
                         spend_tracked, budget_reset_at, is_active""",
            caller_id, cost_usd,
        )
    if not row:
        return None
    return KeyBudget(
        caller_id=row["caller_id"],
        max_budget=float(row["max_budget"]) if row["max_budget"] is not None else None,
        budget_duration=row["budget_duration"],
        rpm_limit=row["rpm_limit"],
        tpm_limit=row["tpm_limit"],
        spend_tracked=float(row["spend_tracked"]) if row["spend_tracked"] else 0.0,
        budget_reset_at=row["budget_reset_at"],
        is_active=row["is_active"],
    )


async def update_key_budget(
    pool: asyncpg.Pool,
    caller_id: str,
    max_budget: float | None = None,
    budget_duration: str | None = None,
    rpm_limit: int | None = None,
    tpm_limit: int | None = None,
    is_active: bool | None = None,
) -> KeyBudget | None:
    """Update budget/rate-limit fields on a key."""
    sets: list[str] = []
    vals: list[Any] = []
    idx = 1

    if max_budget is not None:
        sets.append(f"max_budget = ${idx}")
        vals.append(max_budget)
        idx += 1
    if budget_duration is not None:
        sets.append(f"budget_duration = ${idx}")
        vals.append(budget_duration)
        idx += 1
    if rpm_limit is not None:
        sets.append(f"rpm_limit = ${idx}")
        vals.append(rpm_limit)
        idx += 1
    if tpm_limit is not None:
        sets.append(f"tpm_limit = ${idx}")
        vals.append(tpm_limit)
        idx += 1
    if is_active is not None:
        sets.append(f"is_active = ${idx}")
        vals.append(is_active)
        idx += 1

    if not sets:
        return await get_key_budget(pool, caller_id)

    vals.append(caller_id)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"UPDATE keys SET {', '.join(sets)} WHERE caller_id = ${idx} "
            "RETURNING caller_id, max_budget, budget_duration, rpm_limit, tpm_limit, "
            "spend_tracked, budget_reset_at, is_active",
            *vals,
        )
    if not row:
        return None
    return KeyBudget(
        caller_id=row["caller_id"],
        max_budget=float(row["max_budget"]) if row["max_budget"] is not None else None,
        budget_duration=row["budget_duration"],
        rpm_limit=row["rpm_limit"],
        tpm_limit=row["tpm_limit"],
        spend_tracked=float(row["spend_tracked"]) if row["spend_tracked"] else 0.0,
        budget_reset_at=row["budget_reset_at"],
        is_active=row["is_active"],
    )


async def list_all_key_budgets(pool: asyncpg.Pool) -> list[KeyBudget]:
    """Return all keys with their budget info."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT caller_id, max_budget, budget_duration, rpm_limit, tpm_limit,
                      spend_tracked, budget_reset_at, is_active FROM keys"""
        )
    return [
        KeyBudget(
            caller_id=r["caller_id"],
            max_budget=float(r["max_budget"]) if r["max_budget"] is not None else None,
            budget_duration=r["budget_duration"],
            rpm_limit=r["rpm_limit"],
            tpm_limit=r["tpm_limit"],
            spend_tracked=float(r["spend_tracked"]) if r["spend_tracked"] else 0.0,
            budget_reset_at=r["budget_reset_at"],
            is_active=r["is_active"],
        )
        for r in rows
    ]


# --- Convenience wrappers that auto-resolve the pool ---
_pool: "asyncpg.Pool | None" = None

def set_budget_pool(pool: "asyncpg.Pool") -> None:
    global _pool
    _pool = pool

def _get_pool() -> "asyncpg.Pool":
    if _pool is None:
        raise RuntimeError("Budget pool not initialized - call set_budget_pool() at startup")
    return _pool

async def check_key_budget(caller_id: str) -> tuple[bool, float]:
    """Check if caller has remaining budget. Returns (ok, remaining_usd)."""
    budget = await get_key_budget(_get_pool(), caller_id)
    if budget is None:
        return True, 0.0  # No budget configured = unlimited
    if not budget.is_active:
        return False, 0.0
    if budget.max_budget is None:
        return True, 0.0  # Unlimited budget
    remaining = budget.max_budget - budget.spend_tracked
    return remaining > 0, remaining

async def record_spend(caller_id: str, cost_usd: float) -> None:
    """Track spend against a key's budget."""
    await track_spend(_get_pool(), caller_id, cost_usd)
