"""Public count-only input and output contracts for statistical detection."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Mapping


@dataclass(frozen=True)
class CountObservation:
    """One timestamped count vector; deliberately contains no text or RCA evidence."""

    window_index: int
    window_start: str
    window_end: str
    category_counts: Mapping[str, int]

    def __post_init__(self) -> None:
        if self.window_index < 0:
            raise ValueError("window_index cannot be negative")
        if not self.window_start or not self.window_end:
            raise ValueError("window bounds are required")
        if any(not isinstance(value, int) or isinstance(value, bool)
               for value in self.category_counts.values()):
            raise TypeError("category counts must be integers")
        if any(value < 0 for value in self.category_counts.values()):
            raise ValueError("category counts cannot be negative")


@dataclass(frozen=True)
class Score:
    detector: str
    score_name: str
    score: float
    threshold: float
    expected: float
    observed: int
    anomalous: bool
    detail: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class CategoryDetection:
    category: str
    observed: int
    expected: float | None
    scores: tuple[Score, ...]
    alerted: bool

    def to_dict(self) -> dict:
        return {"observed": self.observed, "expected": self.expected,
                "scores": [score.to_dict() for score in self.scores], "alerted": self.alerted}


@dataclass(frozen=True)
class DetectionResult:
    """Statistical result for a single count-only observation."""

    window_index: int
    window_start: str
    window_end: str
    categories: Mapping[str, CategoryDetection]

    def to_dict(self) -> dict:
        return {"schema_version": 1, "window_index": self.window_index,
                "window_start": self.window_start, "window_end": self.window_end,
                "categories": {key: result.to_dict() for key, result in self.categories.items()}}
