"""
Structured JSON logger for auto-router.

Outputs one JSON object per line to stdout (Uvicorn/Docker captures this).
Log levels: INFO (requests), WARN (fallbacks/escalations), ERROR (failures).

Usage:
    from .logger import logger
    logger.request(caller_id="hermes-ar", tier="simple", ...)
    logger.fallback(caller_id="hermes-ar", reason="Jev timeout", ...)
    logger.error(caller_id="hermes-ar", error="OpenRouter 502", ...)
"""

from __future__ import annotations

import json
import sys
import time
import uuid
from typing import Any


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S.", time.gmtime()) + f"{time.time() % 1:.6f}"[2:8] + "Z"


def _emit(level: str, **fields: Any) -> None:
    record = {
        "ts": _now_iso(),
        "level": level,
        **{k: v for k, v in fields.items() if v is not None},
    }
    print(json.dumps(record, default=str), file=sys.stdout, flush=True)


class Logger:
    """JSON structured logger. All methods accept arbitrary kwargs."""

    @staticmethod
    def request(
        *,
        request_id: str | None = None,
        caller_id: str,
        tier: str,
        model: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cost_usd: float = 0.0,
        latency_ms: int = 0,
        stream: bool = False,
        used_fallback: bool = False,
        escalation_fired: bool = False,
        confidence: float | None = None,
        probability_gap: float | None = None,
    ) -> str:
        """Log a successful request. Returns the request_id."""
        rid = request_id or str(uuid.uuid4())[:8]
        _emit(
            "INFO",
            event="request",
            request_id=rid,
            caller_id=caller_id,
            tier=tier,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=round(cost_usd, 8),
            latency_ms=latency_ms,
            stream=stream,
            used_fallback=used_fallback,
            escalation_fired=escalation_fired,
            confidence=round(confidence, 4) if confidence is not None else None,
            probability_gap=round(probability_gap, 4) if probability_gap is not None else None,
        )
        return rid

    @staticmethod
    def fallback(
        caller_id: str,
        reason: str,
        tier: str = "medium",
    ) -> None:
        _emit("WARN", event="fallback", caller_id=caller_id, reason=reason, tier=tier)

    @staticmethod
    def escalation(
        caller_id: str,
        original_tier: str,
        escalated_tier: str,
        confidence: float,
        gap: float,
    ) -> None:
        _emit(
            "WARN",
            event="escalation",
            caller_id=caller_id,
            original_tier=original_tier,
            escalated_tier=escalated_tier,
            confidence=round(confidence, 4),
            probability_gap=round(gap, 4),
        )

    @staticmethod
    def error(
        caller_id: str,
        error: str,
        tier: str | None = None,
        model: str | None = None,
        latency_ms: int = 0,
        request_id: str | None = None,
    ) -> None:
        _emit(
            "ERROR",
            event="error",
            request_id=request_id,
            caller_id=caller_id,
            error=error,
            tier=tier,
            model=model,
            latency_ms=latency_ms,
        )

    @staticmethod
    def jev_decision(
        caller_id: str,
        tier: str,
        confidence: float | None,
        gap: float | None,
        latency_ms: int,
        success: bool,
        error: str | None = None,
    ) -> None:
        level = "INFO" if success else "WARN"
        _emit(
            level,
            event="jev_decision",
            caller_id=caller_id,
            tier=tier,
            confidence=round(confidence, 4) if confidence is not None else None,
            probability_gap=round(gap, 4) if gap is not None else None,
            jev_latency_ms=latency_ms,
            jev_success=success,
            jev_error=error,
        )


logger = Logger()