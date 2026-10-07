# Odysseus Tauri Launcher - Windows build sandbox

A **disposable Windows VM** that builds the Odysseus Tauri desktop launcher from a pinned commit and lets you use the result without exposing your host.

**Core promise:** the `.exe` is built and runs **inside the VM** only. No synced folder means **nothing is written back to your host computer**.

---

## What this is, and what it is not

This is a **smoke-test harness**. It exists so a reviewer can build the launcher from a known commit and click through it on a real Windows desktop without trusting the binary on their own machine.

**It is not a release pipeline.** Concretely:

| | |
|---|---|
| **Does** | Build from an exact commit, with every tool and package version pinned and asserted before use (with the two exceptions below) |
| **Does** | Produce the portable `odysseus.exe` and the NSIS installer, and fail the build if either is missing |
| **Does** | Write a build receipt recording every input identity and every artefact digest |
| **Does not** | Code-sign or timestamp anything - both artefacts are unsigned and Windows SmartScreen will say so |
| **Does not** | Test upgrade, uninstall, or per-machine install paths |
| **Does not** | Exercise the launcher's Docker Compose path unless you install Docker Desktop in the VM yourself |
| **Does not** | Validate the app against anything but a clean, freshly provisioned Windows image |

Artefacts produced here are for interactive testing inside the VM. Do not ship them.

---

## What is pinned

Everything is declared once, at the top of the `Vagrantfile`. `vagrant/provision-build.ps1` asserts each pin inside the VM and throws if it does not hold, so a run either builds the inputs you declared or fails loudly.

| Input | How it is pinned | How it is asserted |
|---|---|---|
| VM image | `gusztavvargadr/windows-11` `2607.1.0`, `amd64`, `box_check_update = false` | Vagrant verifies the image against the published SHA-256 for that version on download; the digest is repeated in the `Vagrantfile` and recorded in the receipt |
| Source | Full 40-character commit SHA | Fetched by object id, then `git rev-parse HEAD` is compared against the pin |
| Chocolatey client | Exact version via `$env:chocolateyVersion` | Bootstrap script's Authenticode publisher checked, then `choco --version` compared |
| Chocolatey packages | Exact `--version=` per package, `--require-checksums` (see the exception below) | `choco list` compared against every pin after install |
| MSVC toolset | Not pinned - see below | `vswhere` must report `VC.Tools.x86.x64`; the version that landed is recorded in the receipt |
| rustup | Versioned archive URL, not `win.rustup.rs` | SHA-256 of the downloaded `rustup-init.exe` |
| Rust toolchain | Exact `--default-toolchain` | `rustc --version` compared |
| `tauri-cli` | Exact `--version`, `--locked` | `cargo tauri --version` compared |
| WebView2 | Evergreen bootstrapper (deliberately a moving target) | Authenticode publisher must be Microsoft Corporation |

A branch name is rejected as a source pin: it does not pin anything.

### Where the pinning stops

Two gaps that the table above would otherwise paper over:

- **The Visual Studio packages are exempt from `--require-checksums`.** Their install script fetches the release channel manifest from `https://aka.ms/vs/17/release/channel`, a live document that can never carry a static checksum, so Chocolatey fails the package outright under checksum enforcement. The exemption is by package name, keeps the HTTPS requirement (`--allow-empty-checksums-secure`, not `--allow-empty-checksums`), and is recorded per package in the receipt as `checksumPolicy`.
- **Pinning the Chocolatey package version does not pin the MSVC toolset.** `visualstudio2022buildtools` is a thin wrapper around Microsoft's bootstrapper, which installs whatever the current channel says. The same applies to the transitive packages Chocolatey pulls in along the way (`dotnetfx`, `visualstudio-installer`, the `chocolatey-*.extension` packages, and the KB packages behind `vcredist140`) - those install at whatever version is current. The harness cannot close that, so instead it asserts the toolset is actually usable via `vswhere` and records the version that landed in the receipt, which makes a run auditable after the fact rather than reproducible in advance.

### Build receipt

On success the provisioner writes `C:\OdysseusBuild\build-receipt.json` with the box identity, the repository and the commit actually checked out, every toolchain and package version, the frontend entry that was packaged, and the SHA-256 and size of each artefact. **No receipt means the build did not complete** - the script only writes it after every assertion has passed.

### Pointing the harness at a different commit

Edit the pinned-inputs block at the top of the `Vagrantfile` and re-provision:

```bash
vagrant provision --provision-with build
```

---

## Prerequisites

- [Vagrant](https://developer.hashicorp.com/vagrant/downloads) 2.4 or newer (`box_architecture` is required)
- [VirtualBox](https://www.virtualbox.org/wiki/Downloads) 7.0 or newer (Windows 11 needs its TPM/EFI support)
- An **x86-64 host**. The pinned box is `amd64`; Apple Silicon cannot run it under VirtualBox.
- ~35 GB free disk space
- 8 GB RAM (12 GB recommended)

> Nested VT-x is enabled so you can optionally install Docker Desktop inside the VM later if you want to test the full launcher installer path (`git clone` + `docker-compose`). If your CPU does not support it, comment out `--nested-hw-virt on` in the `Vagrantfile`.

---

## Quick start

```bash
# 1. Create the VM and build the binary (first run downloads the Windows image)
vagrant up

# 2. Save a clean snapshot before you start using the app
vagrant snapshot save clean

# 3. RDP into the VM
vagrant rdp
#   Login: OdysseusUser
#   Password: Odysseus123!
#
#   Then double-click the "Odysseus" icon on the Desktop and use the launcher normally.
```

Check what you got before you trust it:

```bash
vagrant winrm -c "Get-Content C:\OdysseusBuild\build-receipt.json"
```

When finished (or if something goes wrong):

```bash
# Reset the VM to the pristine state - all changes the app made are erased
vagrant snapshot restore clean
```

To destroy the VM entirely:

```bash
vagrant destroy -f
```

---

## If you already have a pre-built `.exe`

If you want to skip building and just test an existing executable (e.g., downloaded from a CI artefact):

1. Place the `.exe` in the same folder as this `Vagrantfile`.
2. Uncomment the `file` provisioner block in the `Vagrantfile` (see the commented section at the bottom of the file) and point it to your `.exe`.
3. `vagrant up`
4. The file will be copied into `C:\OdysseusBuild` inside the VM, and a desktop shortcut created automatically.

*(If you do this, nothing in the VM can write back to the folder on your host because the default synced folder is disabled.)*

**An imported binary has no build receipt.** The VM cannot tell you what commit or toolchain produced it. Use this path to test a binary you already trust, never to establish that one is trustworthy.

---

## How it works

### Isolation

The `Vagrantfile` disables the default synced folder and turns off host/guest channels:

- **Clipboard** - disabled
- **Drag-and-drop** - disabled
- **USB passthrough** - disabled
- **Inbound networking** - no forwarded ports into your host

This means the guest does not have a writable path to your host filesystem and cannot leak data through the usual virtualisation side channels.

### Users

| Account | Role | Password |
|---------|------|----------|
| `vagrant` | Admin (provisioning only) | `vagrant` |
| `OdysseusUser` | Standard user (you use this one) | `Odysseus123!` |

The launcher runs as `OdysseusUser`, so any UAC prompts or privilege-escalation attempts are blocked by Windows unless you explicitly approve them.

### Firewall

- **Inbound:** blocked (the VM is not exposed to your LAN).
- **Outbound:** allowed (so the launcher can reach the internet for updates, etc.).
- **Logging:** blocked packets are logged to `C:\Windows\system32\LogFiles\Firewall\pfirewall.log` inside the VM.

### Bundling

`src-tauri/tauri.conf.json` declares no `bundle` object, and Tauri v2 defaults `bundle.active` to `false`. The provisioner therefore passes `--bundles nsis` explicitly, which makes the CLI bundle regardless, and then **requires** an installer executable in `target\x86_64-pc-windows-msvc\release\bundle\nsis`. If none is produced, provisioning fails with the bundler output rather than reporting a complete build that shipped only the portable `.exe`.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `vagrant up` hangs | The Windows image is large (~7 GB download) and initial boot can take 20-30 min on first run. |
| `The box ... could not be found` / no matching provider | Check your Vagrant is 2.4+ and your host is x86-64. The pinned box publishes an `amd64` VirtualBox image only. |
| Provisioning fails with "does not match its pin" | A pinned version has been yanked or replaced upstream. Update the value in the `Vagrantfile`; do not remove the assertion. |
| Provisioning fails with "MSVC x64 build tools are not usable" | The toolset install did not complete. Usually a pending reboot: `vagrant reload`, then `vagrant provision --provision-with build`. |
| A package fails with "does not yet have package checksums" | Upstream stopped publishing a checksum for something the package downloads. Add the package to `$checksumExempt` in `provision-build.ps1` only if the download is over HTTPS, and note it in the section above. |
| Provisioning fails with "produced no installer executable" | The NSIS bundling step failed. Read the `cargo tauri build` output above the error - it is the actual failure. |
| Provisioning fails with "build.frontendDist points at ..." | The commit you pinned does not carry the frontend assets its release config references. That is a defect in the source, not in the harness; it is reported rather than patched over. |
| WebView2 window is blank / black | Ensure the VM has at least 128 MB video RAM (set in the `Vagrantfile`) and the VirtualBox display adapter is VBoxSVGA or VMSVGA. |
| RDP connection fails | Use `vagrant rdp` - it automatically forwards to the correct host port. If you prefer a manual client, run `vagrant port rdp` to find the mapped port. |
| Need to test Docker Desktop path | Install Docker Desktop inside the VM manually or via Chocolatey (`choco install docker-desktop`). Note: this requires nested VT-x and extra RAM. |

---

## Files

| File | Purpose |
|------|---------|
| `Vagrantfile` | VM definition and the single place every pinned input is declared |
| `vagrant/provision-isolation.ps1` | Create standard user, firewall block inbound |
| `vagrant/provision-build.ps1` | Assert every pin, build from the pinned commit, require both artefacts, write the build receipt |
