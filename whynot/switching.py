"""Layer 3 of the owner's philosophy: the code decides when to switch playbooks.

The six rules, in the owner's words, with the numbers this build uses:
  1. A new state must exceed 0.70 filtered probability to take over.
  2. It must hold that lead for 3 consecutive candles.
  3. Cooldown of 3 candles after any switch.
  4. If the probability of moving into a stress or crash state next candle exceeds
     0.25, cut size to half early.
  5. If the top two states are within 0.15 of each other, treat as uncertain and
     size to zero.
  6. If the model errors or data is stale, go flat. Log every switch with
     probabilities and reason.
The model only supplies probabilities. Nothing in here can be overridden by it."""

from dataclasses import dataclass, field

from .regime import DANGER, POLICY

TAKEOVER = 0.70
CONFIRM = 3
COOLDOWN = 3
EARLY_CUT_AT, EARLY_CUT_TO = 0.25, 0.5
UNCERTAIN_GAP = 0.15


@dataclass
class Decision:
    state: str | None            # the active state, None until one has earned it
    size: float                  # multiplier on position size: 0, 0.5 or 1
    reason: str
    switched: bool = False
    probs: dict = field(default_factory=dict)
    next_probs: dict = field(default_factory=dict)

    @property
    def flat(self) -> bool:
        return self.size <= 0


@dataclass
class Switcher:
    active: str | None = None
    candidate: str | None = None
    streak: int = 0
    cooldown: int = 0

    def flat(self, reason: str) -> Decision:
        """Rule 6. The hysteresis state is left alone so a blip cannot cause a switch."""
        return Decision(self.active, 0.0, reason)

    def step(self, labels: list, probs, next_probs) -> Decision:
        """One closed candle in, one decision out."""
        p = {s: float(x) for s, x in zip(labels, probs)}
        nxt = {s: float(x) for s, x in zip(labels, next_probs)}
        ranked = sorted(p, key=p.get, reverse=True)
        top = ranked[0]
        gap = p[top] - (p[ranked[1]] if len(ranked) > 1 else 0.0)
        cooling = self.cooldown > 0
        if cooling:
            self.cooldown -= 1

        switched, note = False, ""
        if top != self.active and p[top] > TAKEOVER:                      # rule 1
            self.streak = self.streak + 1 if top == self.candidate else 1
            self.candidate = top
            if self.streak >= CONFIRM and not cooling:                    # rules 2 and 3
                note = (f"switch {self.active or 'none'} -> {top}: {top} held "
                        f"{p[top]:.2f} > {TAKEOVER} for {self.streak} candles")
                self.active, self.candidate, self.streak = top, None, 0
                self.cooldown, switched = COOLDOWN, True
        else:
            self.candidate, self.streak = None, 0

        def out(size, reason):
            return Decision(self.active, size, note or reason, switched, p, nxt)

        if self.active is None:
            return out(0.0, "no state has held the lead long enough yet")
        if gap < UNCERTAIN_GAP:                                            # rule 5
            return out(0.0, f"uncertain: top two states within {gap:.2f} (< {UNCERTAIN_GAP})")
        size = POLICY[self.active]
        if size <= 0:
            return out(0.0, f"{self.active}: flat")
        danger = sum(v for s, v in nxt.items() if s in DANGER)
        if self.active not in DANGER and danger > EARLY_CUT_AT:            # rule 4
            return out(min(size, EARLY_CUT_TO),
                       f"{self.active}, cut to half: {danger:.2f} chance of stress or crash next candle")
        return out(size, f"{self.active} at {p[self.active]:.2f}")
