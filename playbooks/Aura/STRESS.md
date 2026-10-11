# Agent Aura in STRESS

BOT_5, QQQ, strategy version aura-v1. Focus: Adaptive quantitative strategy research.

- **Entry:** Buy (calls) when the 2-day RSI is under 10 and price is above its 200-day average. Sell short (puts) when the 2-day RSI is over 90 and price is below its 200-day average.
- **Exit:** Exit when price closes back through its 5-day average. Time stop after 5 trading days. Also closed if the state changes to one with a different playbook, or to a flat or uncertain state.
- **Stop:** 4% from the entry price.
- **Take profit:** None. The exit rule takes the profit.
- **Max size:** 50% of this agent's allocation (the state allows 50%, this playbook 100%), cut to half early if stress looks likely, and never above the central risk limits.
- **Invalidation:** No snap-back within the time stop, or the stop is hit: the dip was not a dip.

A starting hypothesis, not a proven strategy. The code decides; this page describes it.
