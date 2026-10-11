"""Option data read from the catalog: real traded daily bars, as of a day."""

from datetime import date, datetime

from ..ingest.timeutil import ET, epoch
from ..partitions import HOLDOUT_END, VALIDATION_END
from ..sim.daily import HoldoutLocked
from .engine import Quote


class CatalogOptionData:
    def __init__(self, cat):
        self.cat, self._exp, self._contracts = cat, {}, {}

    @staticmethod
    def _ts(day: date) -> int:
        if VALIDATION_END < day <= HOLDOUT_END:
            raise HoldoutLocked(f"option data for {day} is inside the locked holdout")
        return epoch(datetime(day.year, day.month, day.day, tzinfo=ET))

    def expirations(self, underlying: str) -> list:
        """Expirations that have stored bars (the monthly third Fridays)."""
        if underlying not in self._exp:
            rows = self.cat.db.execute("SELECT DISTINCT c.expiration FROM contract c WHERE c.underlying=? AND EXISTS "
                                       "(SELECT 1 FROM option_bar b WHERE b.contract=c.symbol)", (underlying,))
            self._exp[underlying] = sorted(date.fromisoformat(r[0]) for r in rows)
        return self._exp[underlying]

    def chain(self, underlying: str, kind: str, expiration: date, day: date) -> list:
        key = (underlying, kind, expiration)
        if key not in self._contracts:
            self._contracts[key] = {c["symbol"]: c["strike"] for c in self.cat.contracts(underlying, expiration.isoformat(), expiration.isoformat())
                                    if c["kind"] == kind}
        strikes = self._contracts[key]
        if not strikes:
            return []
        marks = ",".join("?" * len(strikes))
        rows = self.cat.db.execute(f"SELECT * FROM option_bar WHERE timeframe='1Day' AND ts=? AND contract IN ({marks})", (self._ts(day), *strikes))
        return [Quote(r["contract"], kind, strikes[r["contract"]], expiration, r["open"], r["high"], r["low"], r["close"], r["volume"] or 0, r["trades"] or 0)
                for r in rows]

    def bar(self, contract: str, day: date):
        r = self.cat.db.execute("SELECT * FROM option_bar WHERE contract=? AND timeframe='1Day' AND ts=?", (contract, self._ts(day))).fetchone()
        if r is None:
            return None
        from ..ingest.timeutil import occ_parse
        _, exp, kind, strike = occ_parse(contract)
        return Quote(contract, kind, strike, date.fromisoformat(exp), r["open"], r["high"], r["low"], r["close"], r["volume"] or 0, r["trades"] or 0)
