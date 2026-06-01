# Patches Directory

Patches are organized by package family first, then by target inside each
family. Base source stays clean. Build-specific fixes belong in patch files
under the matching target directory.

## Patch Tree

```text
patches/
|-- common/
|-- appimage/
|-- deb/
|-- rpm/
|-- apk/
|-- flatpak/
|-- snap/
|-- windows/
`-- aur/
```

## Naming

- `common/*.patch` for changes every packaged target needs
- `deb/<target>.patch` for Debian/Ubuntu target-specific fixes
- `rpm/<target>.patch` for Fedora/EL/openSUSE target-specific fixes
- `apk/<target>.patch` for Alpine target-specific fixes
- `flatpak/<runtime>.patch` for Flatpak runtime-specific fixes
- `snap/<base>.patch` for Snap base-specific fixes
- `windows/<target>.patch` for Windows/PyInstaller-specific fixes
- `appimage/<target>.patch` for AppImage runtime fixes
- `aur/<target>.patch` for Arch/AUR-specific fixes

## Rules

1. Runtime or distro-specific compatibility work goes in patches, not in the
   clean upstream source tree, unless the change is truly universal.
2. One target should own one patch file.
3. Common patches are for behavior every packaged target needs.
4. Package workflows should apply `common/` first, then the target-specific
   patch for the build being produced.
