# Project Instructions

## Android APK Handoff

- Do not hand off `android/app/build/outputs/apk/debug/app-debug.apk` to the user as an installable APK.
- For any APK the user should install on a phone, build the signed sideload release from the repo root:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build-android-sideload.ps1
```

- Send the APK from `release-assets/android/Odysseus-Simple-Signal-<version>-sideload.apk`, plus its `.sha256` file if useful.
- Before building a user-facing APK that contains new code, bump `versionCode` and `versionName` in `android/app/build.gradle`.
- Keep `android/keystore.properties` and keystore files private. Do not print passwords, key passwords, or private keystore contents.
- The sideload script verifies signing and refuses debug-signed APKs. If it fails, fix signing/build configuration instead of falling back to a debug APK.
