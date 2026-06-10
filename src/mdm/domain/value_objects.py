from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class iOSVersion:
    major: int
    minor: int = 0
    patch: int = 0

    @classmethod
    def parse(cls, version_str: str) -> "iOSVersion":
        parts = version_str.split(".")
        major = int(parts[0]) if len(parts) > 0 else 0
        minor = int(parts[1]) if len(parts) > 1 else 0
        patch = int(parts[2]) if len(parts) > 2 else 0
        return cls(major, minor, patch)

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"

    def __ge__(self, other: "iOSVersion") -> bool:
        return (self.major, self.minor, self.patch) >= (other.major, other.minor, other.patch)

    def __le__(self, other: "iOSVersion") -> bool:
        return (self.major, self.minor, self.patch) <= (other.major, other.minor, other.patch)


@dataclass(frozen=True)
class Capacity:
    total_go: float
    utilise_go: float

    @property
    def libre_go(self) -> float:
        return self.total_go - self.utilise_go

    @property
    def taux_utilisation(self) -> float:
        if self.total_go == 0:
            return 0
        return round((self.utilise_go / self.total_go) * 100, 1)


@dataclass(frozen=True)
class MatchScore:
    score: float

    def __post_init__(self):
        object.__setattr__(self, "score", max(0.0, min(1.0, self.score)))

    @classmethod
    def from_raw(cls, raw: float) -> "MatchScore":
        clamped = max(0.0, min(1.0, raw))
        return cls(clamped)

    @property
    def label(self) -> str:
        if self.score >= 0.9:
            return "Excellent"
        elif self.score >= 0.7:
            return "Bon"
        elif self.score >= 0.5:
            return "Moyen"
        elif self.score >= 0.3:
            return "Faible"
        return "Incompatible"
