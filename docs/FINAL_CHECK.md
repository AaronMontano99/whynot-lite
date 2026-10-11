# Final check before live money

My specification requires these questions to be answered before any real money, and
requires a refusal to go live until every answer is clean. As of 2026-10-09 they are
not clean. **The answer to "go live?" is no.** No real-money trading is built or authorized.

| Question | Answer | Clean? |
|---|---|---|
| Does paper match the backtest? | Unknown. Paper trading began 2026-10-08 and has no finished trades yet. There is also no approved strategy to compare. | No |
| Is every decision using filtered probabilities only? | Yes. Probabilities come from the forward filter; hmmlearn's smoothed and Viterbi outputs are never called. Tests scramble the future at the regime, signal and option layers and require the past to be identical. | Yes |
| Did the kill switch fire in testing? | Yes: it fired, queued the sale of every position, refused new entries, and stayed off until reset with the owner's phrase. It has not fired in paper trading because the drawdown has not reached 10%. | Yes |
| Is any hard limit delegated to a model? | No. Limits live in a frozen settings object; `check()` takes only the order; there is no override. Claude's nightly review is stored as text and changes nothing. | Yes |
| Does the refit keep state labels stable? | Mostly. Labels are matched to the old states and re-ranked by volatility when they drift out of order, which is treated as drift and freezes entries. That re-ranking was needed once each for QQQ and IWM in 14 years. The number of states is fixed from 2005 and never reconsidered. | Partly |
| What market would break this? | See below. | No |

## WHAT COULD BLOW UP THIS ACCOUNT?

1. **There is no demonstrated edge.** No strategy cleared the gates on development, and
   the one option-level year (2024) made 0.9% and turns negative if the assumed trading
   cost is doubled. Trading real money on these strategies would be paying costs to hold
   coin flips with time decay against them.
2. **Fill assumptions.** Historical option bid/ask does not exist in our data, so every
   backtest fill is a real traded price plus an assumed cost. Real spreads on the days
   that matter (stress) are wider. Paper fills use quotes 15 minutes old.
3. **Long options decay to zero.** A run of wrong signals loses the whole premium each
   time. The limits cap this at 2% a position and 10% of the account at risk at once,
   with the kill switch at a 10% drawdown, but the kill switch acts at the next open: a
   gap can take the loss past 10% before anything is sold.
4. **The three funds move together.** Five agents are closer to one bet than five. On
   2026-10-09 two agents held the same contract. The same-direction limit (6%) bounds it;
   it does not diversify it.
5. **The regime model reads volatility, not direction.** It did not improve returns over
   the same rules without it. A fast crash from a calm state (as in February 2020) is
   exactly the case it sees late.
6. **One machine.** Everything runs on a Mac that must be awake. If it sleeps through an
   open, exits wait a day. There is no second host and no broker-side stop.
7. **Data dependence.** A late or wrong daily candle means no decision that day (by
   design), and Alpaca's own daily candles were wrong on several stress days in 2020.
8. **Whole contracts on a small account.** Sizing rounds to one contract, so position
   size cannot actually follow confidence until the account or the edge is larger.

Until the first row and item 1 change, by independent forward evidence and not by a
better-looking backtest, the system should stay on paper.
