"""In-memory request counters (custom metric requirement)."""
from collections import defaultdict

request_counts: dict = defaultdict(int)
error_counts: dict = defaultdict(int)


def track(endpoint: str, error: bool = False):
    request_counts[endpoint] += 1
    if error:
        error_counts[endpoint] += 1


def snapshot() -> dict:
    return {
        "request_counts": dict(request_counts),
        "error_counts": dict(error_counts),
    }
