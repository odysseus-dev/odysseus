from src.mdm.domain.config_builder import ConfigBuilder


class ConfigWriter:
    @staticmethod
    def export_mobileconfig(device: dict, profile: dict) -> str:
        builder = ConfigBuilder()
        builder.set_metadata("device_udid", device.get("udid", ""))
        builder.set_metadata("profile_nom", profile.get("nom", ""))
        builder.set_metadata("payload_type", "Configuration")
        if profile.get("payload_json"):
            builder.set_payload(profile["payload_json"])
        return builder.to_plist_xml()

    @staticmethod
    def export_json(device: dict, profile: dict) -> str:
        builder = ConfigBuilder()
        builder.set_metadata("device_udid", device.get("udid", ""))
        builder.set_metadata("profile_nom", profile.get("nom", ""))
        if profile.get("payload_json"):
            builder.set_payload(profile["payload_json"])
        return builder.to_json()

    @staticmethod
    def validate_config(profile: dict) -> list:
        return ConfigBuilder.validate(profile.get("payload_json", {}))
