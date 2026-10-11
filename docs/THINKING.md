# My thinking

This is the reasoning behind Project WHYNOT. The lines in quotation marks are taken
word for word from the specification I wrote for it. The full specification stays in my
private copy; the ideas that shaped the system are all here.

## The question

Most trading projects start from "make this profitable". I started from a different
question: does a reproducible edge exist, and does it generalize?

> "The goal is not to produce a profitable-looking backtest. The goal is to determine
> whether a reproducible trading edge exists and whether it generalizes to unseen
> conditions."

A system that can only ever report success is not measuring anything. So the first
requirement was that this one must be able to fail in public:

> "The system must be able to report that no profitable strategy was discovered."

> "Do not manufacture success."

## Risk first, returns second

The role I set was an engineer who cares "about risk first and returns second" and who
never lets "a model override a hard limit". Everything else follows from that order.

## Three layers that never overlap

> "The models advise, the code decides."

1. The AI researches, writes playbooks and reviews results.
2. The Hidden Markov model reads the market state and outputs probabilities.
3. Plain deterministic code owns thresholds, switching, sizing, risk vetoes and every
   order.

An AI is useful for ideas and for auditing. It should not hold the keys. Limits live in
code that takes only the order as input, so there is nothing for a model to argue with.

## The backtest must not lie

The easiest way to fool yourself is to let the past see the future.

> "Use only information timestamped strictly before each decision, or the backtest will
> lie."

> "Never fit a model using the entire historical dataset and then pretend it existed in
> the past."

So the regime model uses forward-filtered probabilities only, every stored price carries
the time it actually became available, and the tests scramble the future and require the
past to stay identical.

## Keep something untouched

Data is split by time into development, validation, a locked holdout and live paper
trading. The holdout is refused by the code, not by good intentions.

> "Do not repeatedly optimize against the locked holdout."

> "Never repeatedly inspect the locked holdout until a favorable result appears."

Once you have looked at a test set and changed something because of it, it is no longer
a test set.

## Research has a budget

This is the idea I care about most. If you let an optimizer run until something looks
good, something will look good, and it will mean nothing.

> "Never keep training, tuning, or backtesting indefinitely until a profitable strategy
> appears. Profitability is not a stopping condition."

> "These limits must be enforced in code, not merely suggested to the AI."

Each research cycle gets at most 20 experiments and 5 configurations per hypothesis.
Three failed cycles in a row stop the research until I review it. A cycle that produces
nothing is fine:

> "A completed research cycle may produce zero successful challengers. That is an
> acceptable result."

## One trade proves nothing

> "A winning trade does not prove a strategy is correct. A losing trade does not prove a
> strategy is incorrect."

A loss is recorded as a lesson or a hypothesis. It does not automatically become a new
rule, because rules added after every loss are just overfitting done slowly.

> "A system that makes no strategy changes for several months may be operating
> correctly."

## The AI never grades itself

Every change the AI proposes is a challenger. The approved strategy, the champion, stays
exactly as it is until a challenger passes independent tests and I approve it.

> "Human approval is mandatory before promotion. The AI may propose changes but never
> approve its own strategy."

## Targets are not goals to chase

The acceptance gates (Sharpe above 1.5, drawdown under 15%, hit rate above 55%,
t-statistic above 2.0, beat both baselines) are targets to measure against.

> "These targets are not instructions to optimize indefinitely until they pass."

> "Do not assume passing these thresholds proves future profitability."

## Five agents are not five bets

QQQ, SPY and IWM move together.

> "Never assume five bots automatically provide diversification."

That is why there is one shared risk engine with limits on total and same-direction
exposure, instead of five separate ones.

## Never invent data

> "Never fabricate historical options contracts, prices, bid/ask spreads, or fills."

Where real option prices did not exist, that period is used only for research on the
funds themselves, and anything derived or assumed is labeled as such.

## Why a city

I wanted to be able to look at the system and see what it is doing: which agent is
active, what state the market is in, what was decided and why. A tower per agent, each
with its own character, is easier to read and more fun to build than a table. The
dashboard is read-only, and it always labels what is simulated and what came from the
broker.

## Where it stands

No strategy has passed. The regime model did not improve returns. The system is paper
trading at the smallest size to gather evidence that no backtest can give. I would
rather show a framework that tells the truth than a chart that looks good.

> "Build a real, reproducible quantitative research platform, not a backtest optimized
> to produce impressive numbers."
