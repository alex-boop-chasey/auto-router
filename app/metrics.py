"""
Prometheus metrics for auto-router.

Exposes /metrics with request counts, latency, tokens, cost,
cache hit rates, Jev decision distribution, rate limit enforcement.
"""
from __future__ import annotations

try:
    from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
    HAS_PROMETHEUS = True
except ImportError:
    HAS_PROMETHEUS = False

if HAS_PROMETHEUS:
    REQUEST_COUNT = Counter(
        "ar_requests_total",
        "Total requests processed",
        ["caller_id", "tier", "model", "success"],
    )
    REQUEST_LATENCY = Histogram(
        "ar_request_latency_seconds",
        "Request latency in seconds",
        ["tier", "model"],
        buckets=(0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0),
    )
    TOKENS_INPUT = Counter(
        "ar_tokens_input_total",
        "Total input tokens",
        ["caller_id", "tier", "model"],
    )
    TOKENS_OUTPUT = Counter(
        "ar_tokens_output_total",
        "Total output tokens",
        ["caller_id", "tier", "model"],
    )
    COST = Counter(
        "ar_cost_usd_total",
        "Total cost in USD",
        ["caller_id", "tier", "model"],
    )
    RATE_LIMIT_HITS = Counter(
        "ar_rate_limit_hits_total",
        "Rate limit enforcement hits",
        ["caller_id", "reason"],
    )
    CACHE_HITS = Counter(
        "ar_cache_hits_total", "Response cache hits", []
    )
    CACHE_MISSES = Counter(
        "ar_cache_misses_total", "Response cache misses", []
    )
    JEV_DECISIONS = Counter(
        "ar_jev_decisions_total",
        "Jev classification decisions",
        ["tier", "success"],
    )
    JEV_LATENCY = Histogram(
        "ar_jev_latency_seconds",
        "Jev classification latency",
        buckets=(0.1, 0.25, 0.5, 0.75, 1.0, 2.0, 5.0),
    )
    KEY_SPEND = Gauge(
        "ar_key_spend_usd",
        "Total spend per key",
        ["caller_id"],
    )

    def record_request(caller_id: str, tier: str, model: str, success: bool,
                       latency_s: float, input_tokens: int, output_tokens: int,
                       cost_usd: float) -> None:
        REQUEST_COUNT.labels(caller_id, tier, model, str(success)).inc()
        REQUEST_LATENCY.labels(tier, model).observe(latency_s)
        TOKENS_INPUT.labels(caller_id, tier, model).inc(input_tokens)
        TOKENS_OUTPUT.labels(caller_id, tier, model).inc(output_tokens)
        COST.labels(caller_id, tier, model).inc(cost_usd)

    def record_jev_decision(tier: str, success: bool, latency_s: float) -> None:
        JEV_DECISIONS.labels(tier, str(success)).inc()
        JEV_LATENCY.observe(latency_s)

    def record_rate_limit(caller_id: str, reason: str) -> None:
        RATE_LIMIT_HITS.labels(caller_id, reason).inc()

    def record_cache_hit() -> None:
        CACHE_HITS.inc()

    def record_cache_miss() -> None:
        CACHE_MISSES.inc()

    def set_key_spend(caller_id: str, spend: float) -> None:
        KEY_SPEND.labels(caller_id).set(spend)

    def metrics_response() -> tuple[bytes, str]:
        return generate_latest(), CONTENT_TYPE_LATEST

else:
    def record_request(*args, **kwargs) -> None: pass
    def record_jev_decision(*args, **kwargs) -> None: pass
    def record_rate_limit(*args, **kwargs) -> None: pass
    def record_cache_hit() -> None: pass
    def record_cache_miss() -> None: pass
    def set_key_spend(*args, **kwargs) -> None: pass
    def metrics_response() -> tuple[bytes, str]:
        return b"# prometheus_client not installed\n", "text/plain"
