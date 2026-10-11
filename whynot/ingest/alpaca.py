"""A read-only Alpaca client for history. Prices are always requested unadjusted.
The keys live in .env and never appear in an error, a log line or a repr."""

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

DATA = "https://data.alpaca.markets"
PAPER = "https://paper-api.alpaca.markets"


class AlpacaError(Exception):
    pass


def _http(url: str, params: dict, headers: dict) -> dict:
    full = url + ("?" + urllib.parse.urlencode(params) if params else "")
    for attempt in range(6):
        try:
            with urllib.request.urlopen(urllib.request.Request(full, headers=headers), timeout=60) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as err:
            if err.code in (429, 500, 502, 503, 504) and attempt < 5:
                time.sleep(2 + attempt * 4)                     # rate limit or a passing fault: wait and retry
                continue
            raise RuntimeError(f"HTTP {err.code} from {url}: {err.read()[:200]!r}") from None
        except (urllib.error.URLError, TimeoutError) as err:
            if attempt < 5:
                time.sleep(2 + attempt * 4)
                continue
            raise RuntimeError(f"no answer from {url}: {err}") from None
    raise RuntimeError(f"gave up on {url}")


class Alpaca:
    def __init__(self, key: str, secret: str, transport=None):
        self._key, self._secret = key, secret
        self._transport = transport or _http

    def __repr__(self):
        return "Alpaca(keys hidden)"

    @classmethod
    def from_env(cls, path=".env"):
        values = dict(line.strip().split("=", 1) for line in Path(path).read_text().splitlines()
                      if "=" in line and not line.lstrip().startswith("#"))
        key, secret = values.get("APCA_API_KEY_ID", "").strip(), values.get("APCA_API_SECRET_KEY", "").strip()
        if not key or not secret:
            raise AlpacaError(f"{path} has no Alpaca keys")
        return cls(key, secret)

    def get(self, url: str, params: dict | None = None):
        try:
            return self._transport(url, params or {}, {"APCA-API-KEY-ID": self._key, "APCA-API-SECRET-KEY": self._secret})
        except Exception as err:
            text = f"{type(err).__name__}: {err}".replace(self._secret, "***").replace(self._key, "***")
            raise AlpacaError(text) from None

    def request(self, method: str, url: str, body: dict | None = None):
        """A write to the PAPER trading API (orders). Refuses any other host."""
        if not url.startswith(PAPER + "/"):
            raise AlpacaError("writes are only ever sent to the paper trading endpoint")
        headers = {"APCA-API-KEY-ID": self._key, "APCA-API-SECRET-KEY": self._secret, "Content-Type": "application/json"}
        req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as err:
            text = f"HTTP {err.code}: {err.read()[:300]!r}".replace(self._secret, "***").replace(self._key, "***")
            raise AlpacaError(text) from None
        except Exception as err:
            raise AlpacaError(f"{type(err).__name__}: {err}".replace(self._secret, "***").replace(self._key, "***")) from None

    def _pages(self, url: str, params: dict, field: str):
        params = dict(params)
        while True:
            page = self.get(url, params)
            yield page.get(field) or []
            token = page.get("next_page_token")
            if not token:
                return
            params["page_token"] = token

    def stock_bars(self, symbol: str, timeframe: str, start: str, end: str, feed: str = "sip"):
        params = {"timeframe": timeframe, "start": start, "limit": 10000, "adjustment": "raw", "feed": feed}
        if end:
            params["end"] = end                                 # no end means "up to the latest this account may see"
        for bars in self._pages(f"{DATA}/v2/stocks/{symbol}/bars", params, "bars"):
            yield from bars

    def option_bars(self, symbols: list, timeframe: str, start: str, end: str):
        """Yields (contract, bar). A contract that never traded yields nothing."""
        params = {"symbols": ",".join(symbols), "timeframe": timeframe, "start": start, "limit": 10000}
        if end:
            params["end"] = end                                 # no end means "up to the latest this account may see"
        for by_contract in self._pages(f"{DATA}/v1beta1/options/bars", params, "bars"):
            for contract, bars in dict(by_contract).items():       # an empty page is an empty list
                for bar in bars:
                    yield contract, bar

    def option_contracts(self, underlying: str, status: str, expiration_from: str, expiration_to: str):
        params = {"underlying_symbols": underlying, "status": status, "limit": 10000,
                  "expiration_date_gte": expiration_from, "expiration_date_lte": expiration_to}
        for contracts in self._pages(f"{PAPER}/v2/options/contracts", params, "option_contracts"):
            yield from contracts

    def calendar(self, start: str, end: str) -> list:
        return self.get(f"{PAPER}/v2/calendar", {"start": start, "end": end})

    def corporate_actions(self, symbols: list, start: str, end: str):
        params = {"symbols": ",".join(symbols), "types": "forward_split,reverse_split,cash_dividend",
                  "start": start, "end": end, "limit": 1000}
        for groups in self._pages(f"{DATA}/v1/corporate-actions", params, "corporate_actions"):
            for kind, items in dict(groups).items():
                for item in items:
                    yield kind, item

    def status(self, url: str, params: dict) -> str:
        """'ok' or the provider's refusal, for checking what an account can reach."""
        try:
            self.get(url, params)
            return "ok"
        except AlpacaError as err:
            return str(err)[:120]
