# Agent Kip in STRESS

BOT_1, QQQ, strategy version kip-v1. Focus: Momentum and trend following.

- **Entry:** Sell short (puts) when the close is below its 50-day average and below where it was 20 days ago.
- **Exit:** Cover when the close rises above the 50-day average. No time stop. Also closed if the state changes to one with a different playbook, or to a flat or uncertain state.
- **Stop:** 5% from the entry price.
- **Take profit:** None. The exit rule takes the profit.
- **Max size:** 50% of this agent's allocation (the state allows 50%, this playbook 100%), cut to half early if stress looks likely, and never above the central risk limits.
- **Invalidation:** A close above the 50-day average.

A starting hypothesis, not a proven strategy. The code decides; this page describes it.
