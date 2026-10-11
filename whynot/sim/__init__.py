"""The five agents and the engine that walks them through time.

One decision function per timeframe serves the backtest, the replay and (later) live
trading: it sees only what was known at that moment, and its output is a pure function
of its input. This package measures the SIGNALS on the underlying funds. Option
contracts and the portfolio risk engine sit on top of it in the next milestone."""
