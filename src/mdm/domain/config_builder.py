import json
import xml.etree.ElementTree as ET
from xml.dom import minidom
from typing import Optional


_PASSWORD_KEYS = frozenset({
    "allowSimple", "forcePIN", "maxFailedAttempts", "maxInactivity",
    "maxPINAgeInDays", "minComplexChars", "minLength", "pinHistory",
    "requireAlphanumeric", "maxGracePeriod", "autoDismissKeyboardDelay",
    "minimumLength", "maximumFailedAttempts", "maximumInactivity",
})

_ICLOUD_KEYS = frozenset({
    "allowCloudSync", "allowCloudBackup", "allowCloudKeychainSync",
    "allowCloudDocumentSync", "allowFindMyDevice", "allowFindMyFriends",
    "allowManagedAppsCloudSync", "allowCloudSubscriptionSync",
    "allowCloudKeychain", "allowCloudDocumentSync",
})

_IOS17_COMPAT_RESTRICTIONS = {
    "allowAppStoreAppModifications": None,
    "allowAutomaticAppDownloads": None,
    "allowBookstore": None,
    "allowBookstoreErotica": None,
    "allowCamera": None,
    "allowCellularDataModifications": None,
    "allowDiagnosticSubmission": None,
    "allowEnterpriseAppTrust": None,
    "allowGameCenter": None,
    "allowLockScreenControlCenter": None,
    "allowLockScreenNotificationsView": None,
    "allowLockScreenTodayView": None,
    "allowModifications": None,
    "allowMultiplayerGaming": None,
    "allowOpenFromManagedToUnmanaged": None,
    "allowOpenFromUnmanagedToManaged": None,
    "allowPassbookWhileLocked": None,
    "allowPhotoStream": None,
    "allowSafari": None,
    "allowScreenShot": None,
    "allowSharedStream": None,
    "allowUIConfigurationProfileInstallation": None,
    "allowUntrustedTlsPrompt": None,
    "allowVoiceDialing": None,
    "allowYouTube": None,
    "allowiTunes": None,
    "forceAppStorePasswordPrompt": None,
    "forceClassroomAutomaticallyJoinClasses": None,
    "forceClassroomRequestPermissionToLeaveClasses": None,
    "forceClassroomUnpromptedScreenObservation": None,
    "forceDeviceForUserActivityEnrollment": None,
    "forceEncryptedBackup": None,
    "forceLimitAdTracking": None,
    "forceManagedBackup": None,
    "forceManagedRestrictions": None,
    "forceWiFiPowerOn": None,
}


class ConfigBuilder:
    def __init__(self):
        self._payload = {}
        self._metadata = {}

    def set_metadata(self, key: str, value: str):
        self._metadata[key] = value
        return self

    def set_payload(self, payload: dict):
        self._payload = payload
        return self

    def add_restriction(self, key: str, value):
        self._payload.setdefault("restrictions", {})[key] = value
        return self

    def add_wifi(self, ssid: str, password: str = None, is_hidden: bool = False):
        wifi = {"SSID": ssid, "AutoJoin": True}
        if password:
            wifi["Password"] = password
        if is_hidden:
            wifi["HIDDEN_NETWORK"] = True
        self._payload.setdefault("wifi", []).append(wifi)
        return self

    def add_email_account(self, email: str, host: str, port: int, ssl: bool = True):
        acct = {
            "Email": email,
            "Host": host,
            "Port": port,
            "SSL": ssl,
        }
        self._payload.setdefault("email_accounts", []).append(acct)
        return self

    def add_calendar(self, url: str, username: str, password: str = None):
        cal = {
            "URL": url,
            "Username": username,
        }
        if password:
            cal["Password"] = password
        self._payload.setdefault("calendars", []).append(cal)
        return self

    def add_app_config(self, bundle_id: str, config: dict):
        self._payload.setdefault("app_config", []).append({
            "BundleID": bundle_id,
            "Config": config,
        })
        return self

    def strip_password_policies(self):
        """Supprime toutes les politiques de mot de passe du payload."""
        restrictions = self._payload.get("restrictions", {})
        for k in _PASSWORD_KEYS:
            restrictions.pop(k, None)
        for rp_key in list(self._payload.keys()):
            if "passcode" in rp_key.lower() or "password" in rp_key.lower():
                if isinstance(self._payload[rp_key], dict):
                    for k in _PASSWORD_KEYS:
                        self._payload[rp_key].pop(k, None)
        return self

    def strip_icloud(self):
        """Supprime toutes les restrictions iCloud/Cloud du payload."""
        restrictions = self._payload.get("restrictions", {})
        for k in _ICLOUD_KEYS:
            restrictions.pop(k, None)
        for rp_key in list(self._payload.keys()):
            if "cloud" in rp_key.lower() or "backup" in rp_key.lower():
                self._payload.pop(rp_key, None)
        return self

    def normalize_ios17(self):
        """Normalise le profil pour iOS 17 : supprime mots de passe, iCloud,
        et nettoie les restrictions obsolètes. Compatible iPhone et iPad."""
        self.strip_password_policies()
        self.strip_icloud()
        restrictions = self._payload.setdefault("restrictions", {})
        for k in _IOS17_COMPAT_RESTRICTIONS:
            restrictions.pop(k, None)
        self._payload["ios_compat"] = "17.0"
        return self

    def build(self) -> dict:
        return {
            "metadata": self._metadata,
            "payload": self._payload,
        }

    def to_json(self) -> str:
        return json.dumps(self.build(), indent=2, default=str)

    def to_plist_xml(self) -> str:
        root = ET.Element("plist", version="1.0")
        def _dict_to_et(d, parent):
            d_elem = ET.SubElement(parent, "dict")
            for k, v in d.items():
                ET.SubElement(d_elem, "key").text = str(k)
                if isinstance(v, dict):
                    _dict_to_et(v, d_elem)
                elif isinstance(v, list):
                    a = ET.SubElement(d_elem, "array")
                    for item in v:
                        if isinstance(item, dict):
                            _dict_to_et(item, a)
                        elif isinstance(item, bool):
                            ET.SubElement(a, "true" if item else "false")
                        else:
                            ET.SubElement(a, "string").text = str(item)
                elif isinstance(v, bool):
                    ET.SubElement(d_elem, "true" if v else "false")
                elif isinstance(v, (int, float)):
                    ET.SubElement(d_elem, "integer").text = str(int(v))
                else:
                    ET.SubElement(d_elem, "string").text = str(v)
        _dict_to_et(self.build(), root)
        return minidom.parseString(ET.tostring(root, encoding="utf-8")).toprettyxml(indent="  ")

    @staticmethod
    def validate(config: dict) -> list:
        warnings = []
        if not config.get("metadata"):
            warnings.append("Aucun metadata défini")
        if not config.get("payload"):
            warnings.append("Payload vide")
        return warnings
