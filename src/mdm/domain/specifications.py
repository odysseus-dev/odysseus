from src.mdm.domain.value_objects import iOSVersion


class Specification:
    def is_satisfied_by(self, device) -> bool:
        raise NotImplementedError


class iOSCompatSpec(Specification):
    def __init__(self, min_version: str):
        self.min = iOSVersion.parse(min_version) if min_version else None

    def is_satisfied_by(self, device) -> bool:
        if self.min is None:
            return True
        try:
            return iOSVersion.parse(device.ios_version) >= self.min
        except Exception:
            return False


class CapacitySpec(Specification):
    def __init__(self, min_free_go: float):
        self.min_free = min_free_go

    def is_satisfied_by(self, device) -> bool:
        return device.stockage_libre_go >= self.min_free


class ModelSpec(Specification):
    def __init__(self, allowed_models: list):
        self.models = [m.lower() for m in allowed_models]

    def is_satisfied_by(self, device) -> bool:
        return device.modele.lower() in self.models


class AndSpec(Specification):
    def __init__(self, *specs):
        self.specs = specs

    def is_satisfied_by(self, device) -> bool:
        return all(s.is_satisfied_by(device) for s in self.specs)


class OrSpec(Specification):
    def __init__(self, *specs):
        self.specs = specs

    def is_satisfied_by(self, device) -> bool:
        return any(s.is_satisfied_by(device) for s in self.specs)
