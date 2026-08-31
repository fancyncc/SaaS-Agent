from __future__ import annotations

from collections import defaultdict, deque
from time import monotonic

from fastapi import HTTPException

try:
    from redis.asyncio import Redis
except ImportError:  # Local fallback; production images install redis from pyproject.toml.
    Redis = None  # type: ignore[assignment,misc]

from backend.config import get_settings

_redis = None
_fallback: dict[str, deque[float]] = defaultdict(deque)


async def enforce_rate_limit(key: str, limit: int, window_seconds: int) -> None:
    """Redis-backed limiter with an in-process fallback for local development."""
    global _redis
    try:
        if Redis is None:
            raise RuntimeError("redis client unavailable")
        if _redis is None:
            _redis = Redis.from_url(get_settings().redis_url, decode_responses=True)
        count = await _redis.incr(key)
        if count == 1:
            await _redis.expire(key, window_seconds)
        if count > limit:
            raise HTTPException(429, "请求过于频繁，请稍后重试")
        return
    except HTTPException:
        raise
    except Exception:
        now = monotonic()
        bucket = _fallback[key]
        while bucket and bucket[0] <= now - window_seconds:
            bucket.popleft()
        if len(bucket) >= limit:
            raise HTTPException(429, "请求过于频繁，请稍后重试") from None
        bucket.append(now)


async def clear_rate_limit(key: str) -> None:
    _fallback.pop(key, None)
    if _redis is not None:
        try:
            await _redis.delete(key)
        except Exception:
            pass
