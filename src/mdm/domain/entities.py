from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


@dataclass
class DeviceEntity:
    udid: str
    modele: str
    ios_version: str
    capacite_go: int
    stockage_utilise_go: float = 0
    proprietaire: Optional[str] = None
    type_appareil: str = "iPad"
    notes: Optional[str] = None
    est_actif: bool = True
    derniere_sync: Optional[datetime] = None
    id: Optional[str] = None
    created_at: datetime = field(default_factory=_now)
    updated_at: datetime = field(default_factory=_now)

    @property
    def stockage_libre_go(self) -> float:
        return float(self.capacite_go) - self.stockage_utilise_go

    @property
    def taux_utilisation(self) -> float:
        if self.capacite_go == 0:
            return 0
        return round((self.stockage_utilise_go / self.capacite_go) * 100, 1)


@dataclass
class ProfileEntity:
    nom: str
    description: Optional[str] = None
    payload_json: dict = field(default_factory=dict)
    ios_min_version: Optional[str] = None
    categorie: Optional[str] = None
    est_actif: bool = True
    id: Optional[str] = None
    created_at: datetime = field(default_factory=_now)
    updated_at: datetime = field(default_factory=_now)


@dataclass
class EnrollmentEntity:
    device_id: str
    profile_id: str
    statut: str = "pending"
    applied_at: Optional[datetime] = None
    id: Optional[str] = None


@dataclass
class MatchResultEntity:
    device_id: str
    profile_id: str
    score: float = 0
    strategy: str = "cantique"
    est_applique: bool = False
    id: Optional[str] = None
    matched_at: datetime = field(default_factory=_now)
