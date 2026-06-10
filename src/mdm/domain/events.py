from dataclasses import dataclass, field
from datetime import datetime, timezone


class DomainEvent:
    pass


@dataclass
class DeviceAdded(DomainEvent):
    device_id: str
    udid: str
    modele: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class DeviceDeleted(DomainEvent):
    device_id: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class ProfileMatched(DomainEvent):
    device_id: str
    profile_id: str
    score: float
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class ConfigApplied(DomainEvent):
    device_id: str
    profile_id: str
    statut: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class CapacityChanged(DomainEvent):
    total_devices: int
    capacite_go: float
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
