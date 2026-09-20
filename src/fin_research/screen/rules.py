"""Declarative screens: what must be true, and what to rank the survivors by.

Conditions are structured rather than written as expressions. An expression language would need a parser and
a sandbox to keep a config file from executing arbitrary code, and screens are simple enough that the
structure costs nothing: a factor, a floor, a ceiling.

Ranking combines factors by percentile rather than by raw value. The factors are on incompatible scales -- a
yield near 0.05 against a margin near 0.4 -- so a score built from raw numbers is decided entirely by
whichever happens to be largest.
"""

import logging
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from fin_research.screen.factors import FACTOR_DESCRIPTIONS, rank_percentile

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Condition:
    """A factor must sit within a range for a company to survive."""

    factor: str
    minimum: float | None = None
    maximum: float | None = None
    # Whether a company missing this factor passes. Default is no: an unknown is not a pass.
    allow_missing: bool = False

    def describe(self) -> str:
        """The condition in words."""
        if self.minimum is not None and self.maximum is not None:
            return f'{self.factor} between {self.minimum:g} and {self.maximum:g}'
        if self.minimum is not None:
            return f'{self.factor} at least {self.minimum:g}'
        return f'{self.factor} at most {self.maximum:g}'

    def mask(self, frame: pd.DataFrame) -> pd.Series:
        """Which rows satisfy this condition."""
        values = frame[self.factor]
        keep = pd.Series(True, index=frame.index)
        if self.minimum is not None:
            keep &= values >= self.minimum
        if self.maximum is not None:
            keep &= values <= self.maximum
        return keep | values.isna() if self.allow_missing else keep & values.notna()


@dataclass(frozen=True)
class Weight:
    """A factor contributing to the ranking score."""

    factor: str
    weight: float = 1.0
    # True where a lower reading is better, such as leverage or accruals.
    lower_is_better: bool = False


@dataclass(frozen=True)
class Screen:
    """One named screen."""

    name: str
    description: str = ''
    require: tuple[Condition, ...] = ()
    rank: tuple[Weight, ...] = ()
    exclude_sectors: tuple[str, ...] = ()
    top: int = 40
    notes: str = ''

    def explain(self) -> str:
        """The screen in words, for the command line and for the record kept with its results."""
        lines = [f'{self.name}: {self.description}']
        if self.exclude_sectors:
            lines.append(f'  excludes {", ".join(self.exclude_sectors)}')
        lines += [f'  requires {condition.describe()}' for condition in self.require]
        if self.rank:
            ordered = ', '.join(f'{w.factor}{"" if not w.lower_is_better else " (lower better)"} x{w.weight:g}'
                                for w in self.rank)
            lines.append(f'  ranks by {ordered}')
        return '\n'.join(lines)


@dataclass
class Result:
    """What a screen produced, and how much of the universe it removed."""

    screen: Screen
    survivors: pd.DataFrame
    considered: int
    dropped: dict[str, int] = field(default_factory=dict)

    def summary(self) -> str:
        """Where the universe went, condition by condition.

        Reported because a screen returning nothing is otherwise indistinguishable from a screen that is
        broken, and the single condition responsible is usually obvious once the counts are visible.
        """
        lines = [f'{self.considered:,} companies considered']
        for label, removed in self.dropped.items():
            lines.append(f'  -{removed:,} {label}')
        lines.append(f'  = {len(self.survivors):,} survivors')
        return '\n'.join(lines)


def parse(name: str, raw: dict) -> Screen:
    """Build a screen from its configuration."""
    unknown = [c['factor'] for c in raw.get('require', []) + raw.get('rank', [])
               if c['factor'] not in FACTOR_DESCRIPTIONS]
    if unknown:
        raise KeyError(f'screen {name!r} uses unknown factor(s): {sorted(set(unknown))}')

    return Screen(
        name=name,
        description=raw.get('description', ''),
        require=tuple(Condition(factor=c['factor'], minimum=c.get('min'), maximum=c.get('max'),
                                allow_missing=c.get('allow_missing', False))
                      for c in raw.get('require', [])),
        rank=tuple(Weight(factor=w['factor'], weight=w.get('weight', 1.0),
                          lower_is_better=w.get('lower_is_better', False))
                   for w in raw.get('rank', [])),
        exclude_sectors=tuple(raw.get('exclude_sectors', ())),
        top=raw.get('top', 40),
        notes=raw.get('notes', ''))


def load(path: Path) -> dict[str, Screen]:
    """Read every screen defined in a TOML file."""
    raw = tomllib.loads(path.read_text())
    return {name: parse(name, body) for name, body in raw.get('screens', {}).items()}


def apply(screen: Screen, frame: pd.DataFrame) -> Result:
    """Run a screen over the factor table, returning the ranked survivors."""
    considered = len(frame)
    dropped: dict[str, int] = {}
    working = frame

    if screen.exclude_sectors:
        before = len(working)
        working = working[~working['sector'].isin(screen.exclude_sectors)]
        dropped[f'in {", ".join(screen.exclude_sectors)}'] = before - len(working)

    for condition in screen.require:
        before = len(working)
        working = working[condition.mask(working)]
        dropped[f'failing {condition.describe()}'] = before - len(working)

    working = working.copy()
    if screen.rank and len(working):
        total = sum(abs(w.weight) for w in screen.rank) or 1.0
        score = sum(rank_percentile(working, w.factor, ascending=w.lower_is_better).fillna(0.5) * w.weight
                    for w in screen.rank)
        working['score'] = score / total
        working = working.sort_values('score', ascending=False)

    return Result(screen=screen, survivors=working.head(screen.top), considered=considered, dropped=dropped)
