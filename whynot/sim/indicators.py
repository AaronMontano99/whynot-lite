"""Small numeric indicators. Each takes a list that ends at "now" and looks only backward."""


def sma(values: list, n: int) -> float | None:
    return sum(values[-n:]) / n if len(values) >= n else None


def rsi(closes: list, n: int = 2) -> float | None:
    """Share of the last n moves that were up, by size, 0 to 100."""
    if len(closes) < n + 1:
        return None
    moves = [b - a for a, b in zip(closes[-n - 1:-1], closes[-n:])]
    up, down = sum(m for m in moves if m > 0), -sum(m for m in moves if m < 0)
    return 50.0 if up + down == 0 else 100.0 * up / (up + down)


def highest(values: list, n: int) -> float | None:
    """The highest of the n values before the last one."""
    return max(values[-n - 1:-1]) if len(values) >= n + 1 else None


def lowest(values: list, n: int) -> float | None:
    return min(values[-n - 1:-1]) if len(values) >= n + 1 else None
