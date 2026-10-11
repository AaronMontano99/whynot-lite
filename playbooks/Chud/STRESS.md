# Agent Chud in STRESS

BOT_3, IWM, strategy version chud-v1. Focus: Volatility and regime transitions.

- **Entry:** Buy (calls) on the day the regime leaves STRESS or CRASH for a calmer state. Sell short (puts) on the day the regime moves from a calm state into STRESS, or when the chance of stress or crash tomorrow first rises above 0.25.
- **Exit:** Longs exit if the regime returns to STRESS or CRASH. Shorts exit when the regime is calm and that chance is under 0.15. Time stop after 10 trading days. Also closed if the state changes to one with a different playbook, or to a flat or uncertain state.
- **Stop:** 4% from the entry price.
- **Take profit:** None. The exit rule takes the profit.
- **Max size:** 50% of this agent's allocation (the state allows 50%, this playbook 100%), cut to half early if stress looks likely, and never above the central risk limits.
- **Invalidation:** The regime reverses the move that opened the trade.

A starting hypothesis, not a proven strategy. The code decides; this page describes it.
