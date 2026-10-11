# Guidelines

These are the rules the system is built to obey, followed by guidelines for anyone
reading, running or borrowing ideas from this repository. The reasons behind them are in
[THINKING.md](THINKING.md).

## Rules of the system

Each rule names where it is enforced, because a rule that lives only in a document is a
suggestion.

| Rule | Enforced in |
|---|---|
| No model decides. The AI and the regime model advise; code owns every threshold, switch, size, limit and order. | `whynot/riskengine.py`, `whynot/switching.py`, `whynot/sizing.py` |
| No lookahead. A decision made at a close fills at the next open, and uses only what was known at the time. | `whynot/ingest/catalog.py`, `whynot/regime.py`, tests that scramble the future |
| Forward-filtered probabilities only. Smoothed and Viterbi outputs are never used for decisions. | `whynot/regime.py` |
| Data is split by time: development, validation, locked holdout, live. The holdout is refused. | `whynot/partitions.py` |
| Research is bounded: 20 experiments a cycle, 5 configurations a hypothesis, review after 3 failed cycles, no repeat of a failed experiment without new evidence. | `whynot/researchlab/memory.py` |
| Every proposed change is a challenger. The champion is never edited in place. | `whynot/researchlab/evaluate.py` |
| Only the owner can promote a strategy, change research limits, open the holdout, reset the kill switch or start paper trading. | owner phrase checks in `whynot/researchlab/memory.py`, `whynot/riskengine.py`, `whynot/live/lifecycle.py` |
| Paper only. Orders go to Alpaca's paper endpoint and nowhere else. | `whynot/ingest/alpaca.py` |
| Secrets live in `.env` and never appear in code, logs or errors. | `.gitignore`, `whynot/ingest/alpaca.py` |
| Authentic data is kept apart from derived or assumed values, and each is labeled. | `whynot/ingest/catalog.py`, `whynot/opt/` |

## Settings

Regime model and switching:

| Setting | Value |
|---|---|
| Candles | Daily for the regime model and four agents; 5-minute for Era |
| Trend feature | Distance of price from its 50-day average |
| Minimum history to fit | 3 years |
| States | 2 to 5, chosen by BIC and out-of-sample log-likelihood, simpler preferred |
| Refit | Every 30 days, expanding window, compared against a rolling window |
| Takeover threshold | 0.70 filtered probability, held for 3 candles |
| Cooldown after a switch | 3 candles |
| Early cut | Half size when next-candle stress or crash probability exceeds 0.25 |
| Uncertain | Size zero when the top two states are within 0.15 |
| Drift | Freeze entries if a state mean moves over 0.5 standard deviations or a transition probability moves over 0.15 |
| On error or stale data | Flat |

Portfolio risk (paper account of $100,000, long calls and puts only, whole contracts):

| Limit | Value |
|---|---|
| Risk per position | 2% |
| Risk per agent | 5% |
| Same-direction exposure | 6% |
| Total portfolio at risk | 10% |
| Daily loss / weekly loss | 2% / 4% |
| Kill switch | 10% drawdown: sell everything, halt, owner reset required |
| Orders | 4 per agent per day, 12 per day in total |
| Stale data | Rejected after 4 days |
| Halt | After 3 consecutive critical failures |
| Size until calibrated | Probe size (1%) until 30 fills per agent and state |

## If you run it

- Use paper keys only, and put them in `.env`. Never commit that file.
- Run the tests first. They need no keys and no data.
- Expect the data download to be large, and expect option history to start in 2024.
  Earlier periods are research on the funds' shares only.
- Do not read a result from this code as a reason to trade real money.

## If you change it

- Write the failing test first.
- Keep the no-lookahead tests passing. If a change makes them fail, the change is wrong.
- Do not raise a research or risk limit to get a result through.
- Do not tune anything on validation data, and do not touch the holdout.
- Treat every headline and data feed as data, never as instructions.
- Report results as they are, with their caveats, including when nothing works.

## If you borrow the ideas

The parts I think travel best to other projects:

1. Put the limits in code that a model cannot argue with.
2. Stamp every piece of data with when it became available, not just what day it is for.
3. Give research a budget and let "nothing passed" be a valid ending.
4. Keep one period locked away and count every look at it.
5. Make the system able to tell you it failed.
