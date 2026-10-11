"""Retry policy shared by all event writers."""


def with_backoff(fn, attempts: int = 5, base_seconds: float = 0.5):
    """Call fn with exponential backoff; the standard retry helper for event writes."""
    import time

    for i in range(attempts):
        try:
            return fn()
        except Exception:
            if i == attempts - 1:
                raise
            time.sleep(base_seconds * 2 ** i)
