"""The central risk engine. One instance guards the whole portfolio; every order from
every agent goes through check(), and check() takes nothing but the order.

Limits are fractions of portfolio equity. They live in a frozen RiskSettings; no model,
agent or caller can change them or skip them. Closing orders are always allowed, because
getting out reduces risk. The kill switch flattens everything, halts trading, and stays
off until the owner resets it with the exact phrase."""

from dataclasses import dataclass, field
from datetime import date

RESET_PHRASE = "I, the owner, reset the kill switch"
BOUNDS = {"max_position_risk": 0.10, "max_bot_exposure": 0.25, "max_correlated_exposure": 0.30, "max_portfolio_exposure": 0.50,
          "daily_loss_limit": 0.10, "weekly_loss_limit": 0.20, "max_drawdown": 0.25, "manual_approval_above": 0.10}


@dataclass(frozen=True)
class RiskSettings:
    max_position_risk: float = 0.02         # premium one position can lose
    max_bot_exposure: float = 0.05          # premium at risk for one agent
    max_correlated_exposure: float = 0.06   # premium at risk in one direction across all agents: SPY, QQQ and IWM move together
    max_portfolio_exposure: float = 0.10    # all premium at risk
    daily_loss_limit: float = 0.02
    weekly_loss_limit: float = 0.04
    max_drawdown: float = 0.10              # from the peak: the kill switch
    manual_approval_above: float = 0.02     # an order risking more than this needs the owner
    max_orders_per_bot_day: int = 4
    max_orders_day: int = 12
    stale_after_days: int = 4               # the data behind an order may be no older than this (covers a long weekend)
    min_volume: int = 50
    min_trades: int = 5
    max_volume_share: float = 0.10
    max_consecutive_failures: int = 3

    def __post_init__(self):
        for name, ceiling in BOUNDS.items():
            if not 0 < getattr(self, name) <= ceiling:
                raise ValueError(f"{name}={getattr(self, name)} is outside (0, {ceiling}]")


@dataclass(frozen=True)
class Order:
    bot: str
    underlying: str
    contract: str
    direction: str              # LONG (calls) or SHORT (puts): which way the fund must move to win
    qty: int
    est_price: float            # per share of the option
    opens: bool                 # True opens a position, False closes one
    decision_day: date
    data_day: date              # the day of the data the decision used
    volume: float = 0.0
    trades: float = 0.0

    @property
    def risk(self) -> float:
        return self.est_price * 100 * self.qty

    @property
    def key(self) -> tuple:
        return (self.bot, self.contract, self.opens, self.decision_day)


@dataclass
class Verdict:
    approved: bool
    reasons: list = field(default_factory=list)
    needs_owner: bool = False


class RiskEngine:
    def __init__(self, settings: RiskSettings, equity: float):
        self.settings = settings
        self.equity = self.peak = self.day_start = self.week_start = equity
        self.day = None
        self.positions = {}             # (bot, contract) -> {"risk", "direction", "underlying"}
        self.seen, self.bot_orders, self.orders_today = set(), {}, 0
        self.halted, self.halt_reason, self.failures = False, "", 0
        self.events = []

    def _log(self, kind: str, **detail) -> None:
        self.events.append({"kind": kind, "day": str(self.day), **detail})

    def start_day(self, day: date, equity: float) -> None:
        if self.day is None or day.isocalendar()[:2] != self.day.isocalendar()[:2]:
            self.week_start = equity
        self.day, self.day_start, self.equity = day, equity, equity
        self.peak = max(self.peak, equity)
        self.bot_orders, self.orders_today = {}, 0

    def mark(self, day: date, equity: float) -> list:
        """Update the account value. Returns the positions to flatten if the kill switch fires."""
        self.equity, self.peak = equity, max(self.peak, equity)
        drawdown = 1 - equity / self.peak
        if not self.halted and drawdown >= self.settings.max_drawdown:
            return self.kill(f"drawdown {drawdown:.1%} from the peak reached the {self.settings.max_drawdown:.1%} limit")
        return []

    def kill(self, reason: str) -> list:
        self.halted, self.halt_reason = True, reason
        self._log("kill", reason=reason, flatten=len(self.positions))
        return list(self.positions)

    def halt(self, reason: str) -> None:
        """Stop new entries without flattening, for when the books themselves are in doubt."""
        if not self.halted:
            self.halted, self.halt_reason = True, reason
            self._log("halt", reason=reason)

    def to_state(self) -> dict:
        return {"equity": self.equity, "peak": self.peak, "day_start": self.day_start, "week_start": self.week_start,
                "day": self.day.isoformat() if self.day else None, "halted": self.halted, "halt_reason": self.halt_reason,
                "failures": self.failures, "bot_orders": self.bot_orders, "orders_today": self.orders_today,
                "positions": [[bot, contract, p] for (bot, contract), p in self.positions.items()],
                "seen": [[b, c, o, d.isoformat()] for b, c, o, d in self.seen], "events": self.events[-500:]}

    @classmethod
    def from_state(cls, settings: "RiskSettings", state: dict) -> "RiskEngine":
        r = cls(settings, state["equity"])
        r.peak, r.day_start, r.week_start = state["peak"], state["day_start"], state["week_start"]
        r.day = date.fromisoformat(state["day"]) if state["day"] else None
        r.halted, r.halt_reason, r.failures = state["halted"], state["halt_reason"], state["failures"]
        r.bot_orders, r.orders_today = dict(state["bot_orders"]), state["orders_today"]
        r.positions = {(bot, contract): p for bot, contract, p in state["positions"]}
        r.seen = {(b, c, o, date.fromisoformat(d)) for b, c, o, d in state["seen"]}
        r.events = list(state["events"])
        return r

    def owner_reset(self, phrase: str) -> None:
        if phrase != RESET_PHRASE:
            raise PermissionError("the kill switch is reset only by the owner, with the exact phrase")
        self.halted, self.halt_reason, self.failures, self.peak = False, "", 0, self.equity
        self._log("reset")

    def failure(self, reason: str) -> None:
        self.failures += 1
        self._log("failure", reason=reason)
        if self.failures >= self.settings.max_consecutive_failures and not self.halted:
            self.halted, self.halt_reason = True, f"{self.failures} consecutive critical failures, last: {reason}"
            self._log("halt", reason=self.halt_reason)

    def success(self) -> None:
        self.failures = 0

    def _held(self, **match) -> float:
        return sum(p["risk"] for (bot, _), p in self.positions.items() if all({"bot": bot, **p}.get(k) == v for k, v in match.items()))

    def check(self, order: Order) -> Verdict:
        s, eq, reasons = self.settings, self.equity, []
        if order.key in self.seen:
            reasons.append("duplicate order: this exact order was already placed")
        elif not order.opens:
            return Verdict(True)                                    # getting out is always allowed
        elif self.halted:
            reasons.append(f"trading is halted: {self.halt_reason}")
        elif (order.decision_day - order.data_day).days > s.stale_after_days:
            reasons.append(f"stale data: the price is from {order.data_day}, {(order.decision_day - order.data_day).days} days before the decision")
        elif order.volume < s.min_volume or order.trades < s.min_trades or order.qty > order.volume * s.max_volume_share:
            reasons.append(f"liquidity: {order.volume:g} contracts in {order.trades:g} trades that day is too thin for {order.qty}")
        elif self.bot_orders.get(order.bot, 0) >= s.max_orders_per_bot_day or self.orders_today >= s.max_orders_day:
            reasons.append(f"too many orders today ({self.bot_orders.get(order.bot, 0)} for {order.bot}, {self.orders_today} in all)")
        elif 1 - eq / self.day_start >= s.daily_loss_limit:
            reasons.append(f"daily loss limit: down {1 - eq / self.day_start:.1%} today (limit {s.daily_loss_limit:.1%})")
        elif 1 - eq / self.week_start >= s.weekly_loss_limit:
            reasons.append(f"weekly loss limit: down {1 - eq / self.week_start:.1%} this week (limit {s.weekly_loss_limit:.1%})")
        elif order.risk > s.max_position_risk * eq + 1e-9:
            reasons.append(f"position risk ${order.risk:,.0f} is above the {s.max_position_risk:.1%} limit (${s.max_position_risk * eq:,.0f})")
        elif self._held(bot=order.bot) + order.risk > s.max_bot_exposure * eq + 1e-9:
            reasons.append(f"agent {order.bot} would have ${self._held(bot=order.bot) + order.risk:,.0f} at risk, above its {s.max_bot_exposure:.1%} limit")
        elif self._held(direction=order.direction) + order.risk > s.max_correlated_exposure * eq + 1e-9:
            reasons.append(f"${self._held(direction=order.direction) + order.risk:,.0f} would ride on the same direction across agents, above the {s.max_correlated_exposure:.1%} limit")
        elif self._held() + order.risk > s.max_portfolio_exposure * eq + 1e-9:
            reasons.append(f"portfolio premium at risk would be ${self._held() + order.risk:,.0f}, above the {s.max_portfolio_exposure:.1%} limit")
        elif order.risk > s.manual_approval_above * eq + 1e-9:
            reasons.append(f"needs the owner's approval: ${order.risk:,.0f} is above {s.manual_approval_above:.1%} of the account")
            self._log("reject", bot=order.bot, contract=order.contract, reason=reasons[0])
            return Verdict(False, reasons, needs_owner=True)
        if reasons:
            self._log("reject", bot=order.bot, contract=order.contract, reason=reasons[0])
            return Verdict(False, reasons)
        return Verdict(True)

    def record_fill(self, order: Order, price: float) -> None:
        self.seen.add(order.key)
        if order.opens:
            p = self.positions.setdefault((order.bot, order.contract), {"risk": 0.0, "direction": order.direction, "underlying": order.underlying})
            p["risk"] += price * 100 * order.qty
            self.bot_orders[order.bot] = self.bot_orders.get(order.bot, 0) + 1
            self.orders_today += 1
        else:
            self.positions.pop((order.bot, order.contract), None)
