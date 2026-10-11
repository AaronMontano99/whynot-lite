# Agent Era in STRESS

BOT_4, QQQ, strategy version era-v1. Focus: Intraday momentum and opening range.

- **Entry:** After the first 30 minutes, buy (calls) on a 5-minute close above the opening range's high, or sell short (puts) on a 5-minute close below its low. One trade a day, none after 15:00, none if the range is under 0.1% or over 1.5% of price.
- **Exit:** Flat by the end of the session, always. Same day only. Also closed if the state changes to one with a different playbook, or to a flat or uncertain state.
- **Stop:** The far side of the opening range.
- **Take profit:** None. The exit rule takes the profit.
- **Max size:** 50% of this agent's allocation (the state allows 50%, this playbook 100%), cut to half early if stress looks likely, and never above the central risk limits.
- **Invalidation:** Price crosses back through the other side of the opening range.

A starting hypothesis, not a proven strategy. The code decides; this page describes it.
