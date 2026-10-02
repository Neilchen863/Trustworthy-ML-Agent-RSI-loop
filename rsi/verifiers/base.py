"""Verifier interface.

A verifier reads one finished run and returns one Result.  Rewards follow one convention so the vector reads
the same everywhere:

    official_score   the raw official metric (train tasks only)
    every other one  in [-1, 0]; 0 = no problem found, -1 = worst

`value` is the raw measured quantity (a rate, a gap, a count) and `evidence` is what the improver reads to
understand why.  A verifier that cannot check something says applicable=False instead of returning 0.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class Result:
    name: str
    reward: float | None
    value: float | None = None
    applicable: bool = True
    summary: str = ""
    evidence: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def not_applicable(name: str, why: str) -> Result:
    return Result(name=name, reward=None, applicable=False, summary=why)
