# -*- mode: ruby -*-
# vi: set ft=ruby :

# Disposable Windows sandbox that builds and smoke-tests the Odysseus Tauri
# launcher from a pinned commit.
# ================================================================
# This is a smoke-test harness, not a release pipeline. It produces an
# unsigned executable and an unsigned NSIS installer for interactive testing
# inside the VM. It does not sign, notarise, or publish anything.
# See vagrant/README.vagrant.md for what it does and does not validate.
#
# Quick start:
#   vagrant up
#   vagrant snapshot save clean
#   vagrant rdp
#     login: OdysseusUser
#     pass:  Odysseus123!
#   # double-click "Odysseus" on the desktop and use it normally
#
# Reset after use:
#   vagrant snapshot restore clean
# ================================================================

# -----------------------------------------------------------------
# Pinned inputs
# -----------------------------------------------------------------
# Every identity the build depends on is declared here and asserted inside the
# VM by vagrant/provision-build.ps1 before it is used. A mismatch fails the
# provisioner instead of quietly staging a binary from different inputs.
# This block is the only place to edit when moving the harness to a new commit
# or a newer toolchain.

# VM image. peru/windows-10-enterprise-x64-eval was used previously but no
# longer publishes a virtualbox provider (libvirt only, as of 20240201.01), so
# `vagrant up` could not succeed on VirtualBox at all.
BOX_NAME         = "gusztavvargadr/windows-11"
BOX_VERSION      = "2607.1.0"
BOX_ARCHITECTURE = "amd64"
# SHA-256 published for the virtualbox/amd64 image of BOX_VERSION. Vagrant
# verifies the image against Vagrant Cloud metadata on download; the digest is
# repeated here and written into the build receipt so a run can be audited
# after the fact.
BOX_SHA256 = "a85862d5d7541785e9ace7ff33498865f2ea035b79c5cce72aabf980c7c26153"

# Source under test. A full 40-character commit SHA is required; a branch name
# is rejected by the provisioner because it does not pin anything.
# This names the most recent commit that changes buildable source. Commits that
# only touch this file or vagrant/ sit on top of it without changing what gets
# compiled, so the pin does not need to chase them.
SOURCE_REPO   = "https://github.com/bitboody/odysseus.git"
SOURCE_COMMIT = "df2adab248b78a12c5eb25db79dbd8b63274a907"

# Rust toolchain. RUSTUP_SHA256 is the digest published alongside the installer
# at static.rust-lang.org/rustup/archive/<version>/.
RUSTUP_VERSION    = "1.29.0"
RUSTUP_SHA256     = "86478e53f769379d7f0ebfa7c9aa97cb76ca92233f79aa2cc0dbee2efaac73c7"
RUST_TOOLCHAIN    = "1.98.0-x86_64-pc-windows-msvc"
TAURI_CLI_VERSION = "2.11.4"

# Chocolatey client and packages. nodejs was dropped: there is no frontend
# build step, so it was install surface nothing consumed.
CHOCOLATEY_VERSION  = "2.7.4"
CHOCOLATEY_PACKAGES = {
  "git"                               => "2.55.0.5",
  "nsis"                              => "3.12.0",
  "vcredist140"                       => "14.51.36247",
  "visualstudio2022buildtools"        => "117.14.37",
  "visualstudio2022-workload-vctools" => "1.0.0",
}

# Bundle target the harness requires. Passed to `cargo tauri build --bundles`,
# which makes the CLI bundle even though src-tauri/tauri.conf.json declares no
# `bundle` object and Tauri v2 defaults bundle.active to false. A build that
# does not produce this installer fails the provisioner.
BUNDLE_TARGET = "nsis"

Vagrant.configure("2") do |config|
  config.vm.box              = BOX_NAME
  config.vm.box_version      = BOX_VERSION
  config.vm.box_architecture = BOX_ARCHITECTURE
  # Never silently move to a newer image than the one pinned above.
  config.vm.box_check_update = false
  config.vm.hostname = "odysseus-sandbox"

  config.vm.communicator = "winrm"
  config.winrm.username  = "vagrant"
  config.winrm.password  = "vagrant"
  config.winrm.retry_limit = 30
  config.winrm.retry_delay = 10

  # No synced folders to the host project directory.
  # This guarantees the guest CANNOT write anything to your host filesystem.
  config.vm.synced_folder ".", "/vagrant", disabled: true

  # RDP forward (Vagrant auto-corrects the host port if 3389 is taken)
  config.vm.network "forwarded_port", guest: 3389, host: 3389, id: "rdp", auto_correct: true

  config.vm.provider "virtualbox" do |vb|
    vb.name = "odysseus-sandbox"
    vb.cpus = 4
    vb.memory = "8192"
    # Set to true if you want a native VM window (useful if RDP is unavailable)
    vb.gui = false

    # Video RAM required for WebView2 rendering
    vb.customize ["modifyvm", :id, "--vram", "128"]

    # Isolate host <-> guest channels
    vb.customize ["modifyvm", :id, "--clipboard-mode", "disabled"]
    vb.customize ["modifyvm", :id, "--drag-and-drop", "disabled"]
    vb.customize ["modifyvm", :id, "--usbehci", "off"]
    vb.customize ["modifyvm", :id, "--usbxhci", "off"]

    # Enable nested VT-x only if your CPU supports it.
    # Needed only if you later install Docker Desktop inside the VM
    # so the launcher's full installer path (git clone + docker-compose) works.
    vb.customize ["modifyvm", :id, "--nested-hw-virt", "on"]
  end

  # -----------------------------------------------------------------
  # Provisioning
  # -----------------------------------------------------------------
  # 1) Create a non-admin user and harden the VM
  config.vm.provision "isolation",
    type: "shell",
    path: "vagrant/provision-isolation.ps1",
    privileged: true

  # 2) Build the .exe and the installer from the pinned commit, then place them
  #    on the test user's desktop. Every pin above is passed in as an
  #    environment variable; the script refuses to run if one is missing.
  config.vm.provision "build",
    type: "shell",
    path: "vagrant/provision-build.ps1",
    privileged: true,
    env: {
      "ODY_BOX_NAME"            => BOX_NAME,
      "ODY_BOX_VERSION"         => BOX_VERSION,
      "ODY_BOX_ARCHITECTURE"    => BOX_ARCHITECTURE,
      "ODY_BOX_SHA256"          => BOX_SHA256,
      "ODY_SOURCE_REPO"         => SOURCE_REPO,
      "ODY_SOURCE_COMMIT"       => SOURCE_COMMIT,
      "ODY_RUSTUP_VERSION"      => RUSTUP_VERSION,
      "ODY_RUSTUP_SHA256"       => RUSTUP_SHA256,
      "ODY_RUST_TOOLCHAIN"      => RUST_TOOLCHAIN,
      "ODY_TAURI_CLI_VERSION"   => TAURI_CLI_VERSION,
      "ODY_CHOCOLATEY_VERSION"  => CHOCOLATEY_VERSION,
      "ODY_CHOCOLATEY_PACKAGES" => CHOCOLATEY_PACKAGES.map { |name, version| "#{name}=#{version}" }.join(","),
      "ODY_BUNDLE_TARGET"       => BUNDLE_TARGET,
    }

  # -----------------------------------------------------------------
  # OPTIONAL: skip the build and import a pre-built .exe from the host
  # -----------------------------------------------------------------
  # Uncomment the block below if you already have odysseus.exe on your
  # host (e.g., in the same folder as this Vagrantfile) and want to
  # copy it into the VM instead of compiling from source.
  #
  # An imported binary carries no build receipt: the VM cannot tell you what
  # commit or toolchain produced it. Use this to test a binary you already
  # trust, never to establish that one is trustworthy.
  #
  # config.vm.provision "upload-exe",
  #   type: "file",
  #   source: "odysseus.exe",
  #   destination: "C:/OdysseusBuild/odysseus.exe"
  #
  # config.vm.provision "stage-desktop",
  #   type: "shell",
  #   inline: <<-SHELL
  #     $user = "OdysseusUser"
  #     $desktop = "C:\Users\$user\Desktop"
  #     New-Item -ItemType Directory -Force -Path $desktop | Out-Null
  #     Copy-Item "C:\OdysseusBuild\odysseus.exe" "$desktop\odysseus.exe" -Force
  #     $Wsh = New-Object -ComObject WScript.Shell
  #     $lnk = $Wsh.CreateShortcut("$desktop\Odysseus.lnk")
  #     $lnk.TargetPath = "C:\OdysseusBuild\odysseus.exe"
  #     $lnk.WorkingDirectory = "C:\OdysseusBuild"
  #     $lnk.IconLocation = "C:\OdysseusBuild\odysseus.exe,0"
  #     $lnk.Save()
  #     # grant read/execute
  #     $acl = Get-Acl "C:\OdysseusBuild"
  #     $rule = New-Object System.Security.AccessControl.FileSystemAccessRule($user, "ReadAndExecute", "ContainerInherit,ObjectInherit", "None", "Allow")
  #     $acl.SetAccessRule($rule)
  #     Set-Acl "C:\OdysseusBuild" $acl
  #   SHELL,
  #   privileged: true
end
