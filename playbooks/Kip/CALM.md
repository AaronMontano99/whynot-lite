# Agent Kip in CALM

BOT_1, QQQ, strategy version kip-v1. Focus: Momentum and trend following.

- **Entry:** Buy (calls) when the close is above its 50-day average and above where it was 20 days ago.
- **Exit:** Sell when the close falls below the 50-day average. No time stop. Also closed if the state changes to one with a different playbook, or to a flat or uncertain state.
- **Stop:** 5% from the entry price.
- **Take profit:** None. The exit rule takes the profit.
- **Max size:** 100% of this agent's allocation (the state allows 100%, this playbook 100%), cut to half early if stress looks likely, and never above the central risk limits.
- **Invalidation:** A close below the 50-day average: the trend this trade relied on is gone.

A starting hypothesis, not a proven strategy. The code decides; this page describes it.
