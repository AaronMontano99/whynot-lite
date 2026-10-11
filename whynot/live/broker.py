"""Two brokers with one shape: Alpaca's PAPER endpoint, and a local simulator for tests
and for anything Alpaca's paper account cannot do. Each says which it is, and every fill
is stored with that label, so a simulated fill is never mistaken for a broker execution."""

from datetime import datetime, timezone

from ..ingest.alpaca import DATA, PAPER


class SimBroker:
    label, simulated = "SIMULATED (local, not a broker)", True

    def __init__(self, equity: float = 100_000.0):
        self.equity, self.fill_orders = equity, True
        self.positions_held, self._orders, self._quotes = {}, [], {}

    def account(self) -> dict:
        return {"equity": self.equity, "status": "ACTIVE"}

    def positions(self) -> dict:
        return dict(self.positions_held)

    def set_quote(self, contract, bid, ask, ts) -> None:
        self._quotes[contract] = {"bid": bid, "ask": ask, "ts": ts}

    def quote(self, contract) -> dict | None:
        return self._quotes.get(contract)

    def orders(self, open_only: bool = False) -> list:
        return [dict(o) for o in self._orders if not open_only or o["status"] == "new"]

    def order(self, order_id) -> dict:
        return dict(next(o for o in self._orders if o["id"] == order_id))

    def submit(self, contract, qty, side, limit_price, client_order_id) -> dict:
        for o in self._orders:
            if o["client_order_id"] == client_order_id:
                return dict(o)                                  # the same order twice is the same order
        o = {"id": f"sim-{len(self._orders) + 1}", "client_order_id": client_order_id, "contract": contract, "qty": qty, "side": side,
             "limit_price": limit_price, "status": "new", "filled_price": None}
        q = self._quotes.get(contract)
        if self.fill_orders and q and ((side == "BUY" and limit_price >= q["ask"]) or (side == "SELL" and limit_price <= q["bid"])):
            o["status"], o["filled_price"] = "filled", limit_price
            held = self.positions_held.get(contract, 0) + (qty if side == "BUY" else -qty)
            self.positions_held.pop(contract, None) if held == 0 else self.positions_held.__setitem__(contract, held)
        self._orders.append(o)
        return dict(o)

    def cancel(self, order_id) -> None:
        for o in self._orders:
            if o["id"] == order_id and o["status"] == "new":
                o["status"] = "canceled"


class AlpacaPaperBroker:
    """Alpaca paper trading. The base URL is the paper endpoint and is not configurable."""

    label, simulated = "Alpaca paper account", False

    def __init__(self, client):
        self.client = client

    def account(self) -> dict:
        a = self.client.get(f"{PAPER}/v2/account")
        return {"equity": float(a["equity"]), "status": a["status"], "options_level": a.get("options_trading_level"),
                "blocked": bool(a.get("trading_blocked") or a.get("account_blocked"))}

    def positions(self) -> dict:
        return {p["symbol"]: int(float(p["qty"])) for p in self.client.get(f"{PAPER}/v2/positions")}

    def snapshot(self) -> dict:
        """Everything the dashboard needs from the broker in one read-only call pair."""
        a = self.client.get(f"{PAPER}/v2/account")
        held = {p["symbol"]: {"qty": int(float(p["qty"])), "price": float(p["current_price"] or 0), "unrealized": float(p["unrealized_pl"] or 0),
                              "unrealized_today": float(p["unrealized_intraday_pl"] or 0)} for p in self.client.get(f"{PAPER}/v2/positions")}
        return {"ok": True, "label": self.label, "equity": float(a["equity"]), "last_equity": float(a["last_equity"]), "positions": held}

    def quote(self, contract) -> dict | None:
        q = (self.client.get(f"{DATA}/v1beta1/options/quotes/latest", {"symbols": contract}).get("quotes") or {}).get(contract)
        if not q or not q.get("ap") or not q.get("bp"):
            return None
        return {"bid": float(q["bp"]), "ask": float(q["ap"]), "ts": datetime.fromisoformat(q["t"][:19] + "+00:00").astimezone(timezone.utc)}

    @staticmethod
    def _shape(o: dict) -> dict:
        status = {"filled": "filled", "canceled": "canceled", "expired": "canceled", "rejected": "rejected"}.get(o["status"], "new")
        return {"id": o["id"], "client_order_id": o["client_order_id"], "contract": o["symbol"], "qty": int(float(o["qty"])),
                "side": o["side"].upper(), "limit_price": float(o["limit_price"]) if o.get("limit_price") else None, "status": status,
                "filled_price": float(o["filled_avg_price"]) if o.get("filled_avg_price") else None, "raw_status": o["status"]}

    def orders(self, open_only: bool = False) -> list:
        return [self._shape(o) for o in self.client.get(f"{PAPER}/v2/orders", {"status": "open" if open_only else "all", "limit": 200})]

    def order(self, order_id) -> dict:
        return self._shape(self.client.get(f"{PAPER}/v2/orders/{order_id}"))

    def submit(self, contract, qty, side, limit_price, client_order_id) -> dict:
        body = {"symbol": contract, "qty": str(qty), "side": side.lower(), "type": "limit", "limit_price": f"{limit_price:.2f}",
                "time_in_force": "day", "client_order_id": client_order_id}
        return self._shape(self.client.request("POST", f"{PAPER}/v2/orders", body=body))

    def cancel(self, order_id) -> None:
        self.client.request("DELETE", f"{PAPER}/v2/orders/{order_id}")

    def clock(self) -> dict:
        return self.client.get(f"{PAPER}/v2/clock")
