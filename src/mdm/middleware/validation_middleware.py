class ValidationError(Exception):
    def __init__(self, field: str, message: str):
        self.field = field
        self.message = message
        super().__init__(f"{field}: {message}")


class ValidationMiddleware:
    @staticmethod
    def validate_device(data: dict) -> list[str]:
        errors = []
        if not data.get("udid"):
            errors.append("udid: requis")
        if not data.get("modele"):
            errors.append("modele: requis")
        if not data.get("ios_version"):
            errors.append("ios_version: requis")
        if data.get("capacite_go") is None:
            errors.append("capacite_go: requis")
        elif not isinstance(data.get("capacite_go"), (int, float)) or data["capacite_go"] <= 0:
            errors.append("capacite_go: doit être un nombre positif")
        if data.get("stockage_utilise_go") is not None:
            if not isinstance(data["stockage_utilise_go"], (int, float)) or data["stockage_utilise_go"] < 0:
                errors.append("stockage_utilise_go: doit être un nombre positif ou zéro")
        return errors

    @staticmethod
    def validate_profile(data: dict) -> list[str]:
        errors = []
        if not data.get("nom"):
            errors.append("nom: requis")
        if data.get("payload_json") and not isinstance(data["payload_json"], dict):
            errors.append("payload_json: doit être un objet JSON")
        return errors

    @staticmethod
    def sanitize(data: dict, allowed_fields: set) -> dict:
        return {k: v for k, v in data.items() if k in allowed_fields and v is not None}

    DEVICE_FIELDS = {"udid", "modele", "ios_version", "capacite_go", "stockage_utilise_go", "proprietaire", "type_appareil", "notes", "est_actif"}
    PROFILE_FIELDS = {"nom", "description", "payload_json", "ios_min_version", "categorie", "est_actif"}
