"""Token accounting for every LLM call a run makes.

Nothing measured this before, so the cost of a conversion could only be
guessed at. The ledger is process-wide because the calls happen in three
unrelated places -- layer classification, wall adjudication, scale
verification -- and the question being asked ("what does this drawing cost?")
is about the run as a whole.

Fresh input and cache reads are kept apart on purpose: providers bill them at
different rates, so a report that folds them together cannot be turned into a
cost.
"""
from __future__ import annotations

import contextlib
import contextvars
from dataclasses import dataclass, field

#: Set by `purpose(...)` so a client deep in the stack can attribute its call
#: without every caller threading a parameter through.
_PURPOSE: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "archiagent_llm_purpose", default=None)

UNATTRIBUTED = "unattributed"


@dataclass(frozen=True)
class CallRecord:
    provider: str
    model: str
    purpose: str
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int
    vision: bool


@dataclass
class UsageLedger:
    calls: list[CallRecord] = field(default_factory=list)

    def record(self, *, provider: str, model: str, input_tokens: int,
               output_tokens: int, cached_input_tokens: int = 0,
               purpose: str | None = None, vision: bool = False) -> None:
        self.calls.append(CallRecord(
            provider=provider,
            model=model,
            # An untagged call is still a cost, so it is recorded under a name
            # rather than dropped -- a silently missing call is worse than an
            # unattributed one.
            purpose=purpose or _PURPOSE.get() or UNATTRIBUTED,
            input_tokens=int(input_tokens or 0),
            output_tokens=int(output_tokens or 0),
            cached_input_tokens=int(cached_input_tokens or 0),
            vision=bool(vision),
        ))

    def _sum(self, calls: list[CallRecord]) -> dict:
        return {
            "calls": len(calls),
            "text_calls": sum(1 for c in calls if not c.vision),
            "vision_calls": sum(1 for c in calls if c.vision),
            "input_tokens": sum(c.input_tokens for c in calls),
            "output_tokens": sum(c.output_tokens for c in calls),
            "cached_input_tokens": sum(c.cached_input_tokens for c in calls),
            "total_tokens": sum(c.input_tokens + c.output_tokens for c in calls),
        }

    def totals(self) -> dict:
        return self._sum(self.calls)

    def by_purpose(self) -> dict:
        names = sorted({c.purpose for c in self.calls})
        return {name: self._sum([c for c in self.calls if c.purpose == name])
                for name in names}

    def report(self) -> dict:
        """The whole ledger, JSON-serialisable.

        An empty ledger still reports zeros: a fully cached run makes no calls
        at all, and "no report" and "no calls" must not look the same.
        """
        return {
            "models": sorted({f"{c.provider}/{c.model}" for c in self.calls}),
            "totals": self.totals(),
            "by_purpose": self.by_purpose(),
            "calls": [
                {
                    "purpose": c.purpose,
                    "provider": c.provider,
                    "model": c.model,
                    "vision": c.vision,
                    "input_tokens": c.input_tokens,
                    "output_tokens": c.output_tokens,
                    "cached_input_tokens": c.cached_input_tokens,
                }
                for c in self.calls
            ],
        }


#: The run's ledger. A module-level singleton because the CLI needs one report
#: covering calls made by three unrelated subsystems.
_LEDGER = UsageLedger()


def current() -> UsageLedger:
    return _LEDGER


def reset() -> None:
    """For tests, and for a process that converts more than one drawing."""
    _LEDGER.calls.clear()


@contextlib.contextmanager
def purpose(name: str, *, ledger: UsageLedger | None = None):
    """Attributes every call made inside the block to `name`."""
    token = _PURPOSE.set(name)
    try:
        yield ledger or _LEDGER
    finally:
        _PURPOSE.reset(token)
