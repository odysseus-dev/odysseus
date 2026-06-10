# typed: strict
# frozen_string_literal: true

# Homebrew formula for Odysseus — see docs/macos.md for usage.
class Odysseus < Formula
  desc "AI agent runtime with native Apple Silicon / Metal GPU support"
  homepage "https://github.com/pewdiepie-archdaemon/odysseus"
  # Tag a release, then `brew install --build-from-source` will pull
  # a tarball. Until then, `brew install --HEAD` installs from `dev`.
  url "https://github.com/pewdiepie-archdaemon/odysseus/archive/refs/tags/v0.1.0.tar.gz"
  version "0.1.0"
  # sha256 "FILL_AT_RELEASE_TIME"
  license "MIT"
  head "https://github.com/pewdiepie-archdaemon/odysseus.git", branch: "dev"

  # macOS only. The launchd service + Metal GPU path don't apply on
  # Linux; this formula only exists so the user can `brew install`
  # rather than `git clone && ./odysseus.sh`. The repo also has a
  # Linux systemd path that's not part of this formula.
  depends_on xcode: ["15.0", :build]
  depends_on :macos
  depends_on "python@3.11"

  def install
    # Install the whole repo to libexec. The launcher resolves its
    # own install dir from BASH_SOURCE and follows symlinks, so a
    # `bin/odysseus → libexec/odysseus.sh` indirection is transparent.
    libexec.install Dir["*"]

    # Symlink the entry points into bin.
    bin.install_symlink libexec/"odysseus.sh"                   => "odysseus"
    bin.install_symlink libexec/"build-macos-app.sh"            => "odysseus-package"
    bin.install_symlink libexec/"install-macos-service.sh"     => "odysseus-install-service"
    bin.install_symlink libexec/"uninstall-macos-service.sh"   => "odysseus-uninstall-service"

    # The .app worker needs to be findable. build-macos-app.sh
    # also reads Swift source from app/macos/, so that dir is
    # already at libexec/app/macos/.
  end

  # `brew services start odysseus` writes a launchd plist that runs
  # `odysseus --launch=native --no-open` with our libexec as the
  # working dir, keep-alive on crash. The TCC requirement (repo
  # outside ~/Desktop etc.) is satisfied because libexec lives
  # under /opt/homebrew/Cellar.
  service do
    run [opt_bin/"odysseus", "--launch=native", "--no-open"]
    keep_alive true
    working_dir opt_libexec
    log_path var/"log/odysseus.log"
    error_log_path var/"log/odysseus-error.log"
  end

  def caveats
    <<~EOS
      Odysseus has been installed. To get started:

        brew services start odysseus   # auto-start at login, restart on crash
        odysseus --launch=native       # ...or just run it once
        odysseus --add-to-path         # install the `odysseus` symlink to ~/.local/bin

      First launch provisions a Python venv at:
        #{libexec}/venv/

      User data lives at:
        ~/Library/Application Support/Odysseus/

      Logs:
        #{var}/log/odysseus.log
        #{var}/log/odysseus-error.log
        ~/Library/Logs/Odysseus/   (if you also use the .app or --install-service)

      To upgrade:
        brew update && brew upgrade odysseus
        odysseus --update            # pull + reinstall Python deps

      To uninstall:
        brew services stop odysseus
        brew uninstall odysseus
        rm -rf ~/Library/Application Support/Odysseus  # only if you want to wipe data
    EOS
  end

  test do
    # `--help` exits 0 and prints the launcher usage. We don't run
    # the actual install + venv setup in `brew test` because that
    # would touch the user's Homebrew prefix for real.
    assert_match "odysseus — one launcher", shell_output("#{bin}/odysseus --help")
  end
end
