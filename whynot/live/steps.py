"""The two daily steps and the reconciliation that guards them.

close_step  after the close: mark the account, let each agent decide, choose contracts
            from what really traded today, ask the risk engine, queue orders.
open_step   at the next open: check each queued order against a live quote and the risk
            engine again, send it as a limit order at the quote, and book what fills.
            What has not filled by the deadline is cancelled.
Both are safe to run twice."""

from datetime import date, datetime, time as clock, timedelta

from ..ingest.timeutil import ET, occ_parse
from ..opt.engine import pick
from ..riskengine import Order

PROBE = 0.01                    # of the account per trade, as in the backtest, until fills prove the probabilities
# This account's option quotes arrive 15 minutes late (real-time needs a paid data agreement), so the open step
# runs at 09:52 and accepts a quote up to 20 minutes old: the first one made after the opening bell.
QUOTE_MAX_AGE = timedelta(minutes=20)
FILL_DEADLINE = clock(10, 30)   # an order still open at 10:30 New York time is cancelled


def close_step(day: date, symbols: dict, decide, data, spots: dict, broker, db, risk, rule, rate: float = 0.0) -> list:
    """symbols: agent -> fund. decide(agent) -> the agent's decision at today's close
    (action, direction, state, fraction, reason, probs, stopped). Returns the orders queued."""
    if (db.checkpoint("close") or "") >= day.isoformat():
        return []
    equity = broker.account()["equity"]
    if risk.day != day:
        risk.start_day(day, risk.equity)
    flatten = set(risk.mark(day, equity))
    queued = []
    waiting = {(i["agent"], i["side"]) for i in db.intents(status="pending")}
    decided = {agent: decide(agent) for agent in symbols}
    db.record_decisions(day, decided)
    for agent, symbol in symbols.items():
        d, pos = decided[agent], db.position(agent)
        if pos:
            expiry = date.fromisoformat(occ_parse(pos["contract"])[1])
            why = ("kill switch" if (agent, pos["contract"]) in flatten or risk.halted and "drawdown" in risk.halt_reason
                   else "signal stop" if getattr(d, "stopped", False)
                   else f"signal exit: {d.reason}" if d.action == "EXIT"
                   else "expiry near" if (expiry - day).days <= rule.exit_dte + 3 else None)
            if why and (agent, "SELL") not in waiting:
                queued.append(db.add_intent(agent=agent, underlying=symbol, side="SELL", contract=pos["contract"], qty=pos["qty"],
                                            direction=pos["direction"], state=pos["state"], decided_day=day, status="pending", reason=why))
            continue
        if d.action != "ENTER" or (agent, "BUY") in waiting:
            continue
        base = dict(agent=agent, underlying=symbol, side="BUY", direction=d.direction, state=d.state, decided_day=day)
        cap = risk.settings.max_position_risk * equity
        choice, why, note = pick(data, symbol, d.direction, day, spots[symbol], PROBE * d.fraction * equity, cap, rule, rate)
        if choice is None:
            db.add_intent(**base, status="refused", reason=f"selection: {why}")
            continue
        verdict = risk.check(Order(agent, symbol, choice.contract, d.direction, choice.qty, choice.ref_price, True, day, day, choice.volume, choice.trades))
        if not verdict.approved:
            db.add_intent(**base, contract=choice.contract, qty=choice.qty, est_price=choice.ref_price, status="refused", reason=f"risk: {verdict.reasons[0]}")
            continue
        queued.append(db.add_intent(**base, contract=choice.contract, qty=choice.qty, est_price=choice.ref_price, volume=choice.volume,
                                    trades=choice.trades, status="pending", reason=f"{d.reason}; {why}; probe size{note}"))
    db.note("close step", day=day, equity=equity, queued=len(queued), halted=risk.halted)
    db.set_checkpoint("close", day.isoformat())
    db.save_risk(risk)
    return queued


def open_step(day: date, broker, db, risk, rule, now: datetime) -> list:
    """Send what was queued before today, then book or cancel what was sent. Returns today's orders."""
    if risk.day != day:
        risk.start_day(day, broker.account()["equity"])
    pending = [i for i in db.intents(status="pending") if i["decided_day"] < day.isoformat()]
    for i in sorted(pending, key=lambda i: i["side"] != "SELL"):               # get out before getting in
        q = broker.quote(i["contract"])
        stale = q is None or now - q["ts"] > QUOTE_MAX_AGE
        if i["side"] == "SELL":
            if stale:
                db.update_intent(i["id"], for_day=day.isoformat(), reason=(i["reason"] or "") + " | no fresh quote yet; will try again")
                continue
            limit = q["bid"]
        else:
            if stale:
                db.update_intent(i["id"], for_day=day.isoformat(), status="refused",
                                 reason=f"stale quote: {'none' if q is None else 'last one at ' + q['ts'].astimezone(ET).strftime('%Y-%m-%d %H:%M')}")
                continue
            limit = q["ask"]
            verdict = risk.check(Order(i["agent"], i["underlying"], i["contract"], i["direction"], i["qty"], limit, True, day, day,
                                       i["volume"] or 0, i["trades"] or 0))
            if not verdict.approved:
                db.update_intent(i["id"], for_day=day.isoformat(), status="refused", reason=f"risk at the open: {verdict.reasons[0]}")
                continue
        cid = f"{i['agent']}-{i['contract']}-{day}-{i['side']}"
        try:
            o = broker.submit(i["contract"], i["qty"], i["side"], limit, cid)
        except Exception as err:
            risk.failure(f"order rejected by the broker: {err}")
            db.update_intent(i["id"], for_day=day.isoformat(), status="refused" if i["side"] == "BUY" else "pending", reason=f"broker refused: {str(err)[:160]}")
            continue
        risk.success()
        db.update_intent(i["id"], for_day=day.isoformat(), status="submitted", client_order_id=cid, order_id=o["id"], est_price=limit,
                         simulated=int(broker.simulated))

    for i in db.intents(status="submitted"):
        o = broker.order(i["order_id"])
        if o["status"] == "filled":
            price = o["filled_price"]
            order = Order(i["agent"], i["underlying"], i["contract"], i["direction"], i["qty"], price, i["side"] == "BUY", day, day)
            if i["side"] == "BUY":
                db.open_position(agent=i["agent"], underlying=i["underlying"], contract=i["contract"], qty=i["qty"], direction=i["direction"],
                                 state=i["state"], entry_day=day, entry_price=price, reason=i["reason"], simulated=broker.simulated)
            else:
                db.close_position(i["agent"], day, price, i["reason"], broker.label)
            risk.record_fill(order, price)
            db.update_intent(i["id"], status="filled", fill_price=price)
        elif o["status"] in ("canceled", "rejected"):
            db.update_intent(i["id"], status="cancelled" if i["side"] == "BUY" else "pending", reason=(i["reason"] or "") + f" | broker: {o['status']}")
        elif now.astimezone(ET).time() >= FILL_DEADLINE:
            broker.cancel(i["order_id"])
            db.update_intent(i["id"], status="cancelled" if i["side"] == "BUY" else "pending",
                             reason=(i["reason"] or "") + " | not filled by 10:30, cancelled" + ("" if i["side"] == "BUY" else "; will try again"))
    db.note("open step", day=day, at=now.isoformat())
    db.save_risk(risk)
    return [i for i in db.intents() if i["for_day"] == day.isoformat()]


def reconcile(broker, db, risk) -> list:
    """Compare our books with the broker's. Any difference halts new entries and is reported."""
    theirs, ours = broker.positions(), {}
    for p in db.positions():                                   # two agents may hold the same contract; the broker sees one position
        ours[p["contract"]] = ours.get(p["contract"], 0) + p["qty"]
    problems = [f"the broker holds {q} x {c}, which we have no record of buying" for c, q in theirs.items() if c not in ours]
    problems += [f"our books hold {q} x {c}, which the broker does not have" for c, q in ours.items() if c not in theirs]
    problems += [f"quantity differs on {c}: broker {theirs[c]}, ours {q}" for c, q in ours.items() if c in theirs and theirs[c] != q]
    known = {i["client_order_id"] for i in db.intents() if i["client_order_id"]}
    problems += [f"the broker has an open order we did not place: {o['client_order_id']}" for o in broker.orders(open_only=True)
                 if o["client_order_id"] not in known]
    if problems:
        risk.halt("books disagree with the broker: " + "; ".join(problems))
        db.note("reconcile failed", problems=problems)
        db.save_risk(risk)
    return problems
