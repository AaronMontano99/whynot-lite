# Agent Aura in CALM

BOT_5, QQQ, strategy version aura-v1. Focus: Adaptive quantitative strategy research.

- **Entry:** Buy (calls) when the close is above the highest high of the previous 20 days.
- **Exit:** Sell when the close is below the lowest low of the previous 10 days. No time stop. Also closed if the state changes to one with a different playbook, or to a flat or uncertain state.
- **Stop:** 5% from the entry price.
- **Take profit:** None. The exit rule takes the profit.
- **Max size:** 100% of this agent's allocation (the state allows 100%, this playbook 100%), cut to half early if stress looks likely, and never above the central risk limits.
- **Invalidation:** A close below the 10-day low: the breakout failed.

A starting hypothesis, not a proven strategy. The code decides; this page describes it.
