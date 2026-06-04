{
  description = "Odysseus AI Workspace Environment";

  # Defines the source of our packages. Pinned to the 26.05 branch for stability.
  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixos-26.05";
    flake-utils.url = "github:numtide/flake-utils";
    nix-darwin = {
      url = "github:LnL7/nix-darwin";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  # Nix code is split out of this file (cf. the native-nix effort in issue #605):
  #   nix/odysseus.nix                    — the main odysseus package (callPackage)
  #   nix/lib.nix                         — mkRuntimeLibs / mkContainer helpers
  #   nix/overlay.nix                     — Python package overrides (pythonPackagesExtensions)
  #   nix/modules/services/odysseus.nix   — the NixOS & nix-darwin service modules
  #                                         (Chroma, SearXNG, firewall, app)
  #   nix/modules/checks/integration.nix  — the integration tests
  outputs =
    {
      self,
      nixpkgs,
      flake-utils,
      nix-darwin,
    }@inputs:
    let
      inherit (import ./nix/lib.nix) mkContainer mkRuntimeLibs;
      # Standard nixpkgs overlay (Darwin test-skips via pythonPackagesExtensions).
      # Applied here so the package and dev shell see the same overridden
      # interpreter, and exported as overlays.default for downstream
      # NixOS / nix-darwin modules (`nixpkgs.overlays = [ odysseus.overlays.default ]`).
      pythonOverlay = import ./nix/overlay.nix;
      odysseusModules = import ./nix/modules/services/odysseus.nix { src = ./.; };
    in
    (flake-utils.lib.eachDefaultSystem (
      system:
      let
        pkgs = import nixpkgs {
          inherit system;
          overlays = [ pythonOverlay ];
        };
        # Default app package (no extra packages); uses callPackage.
        odysseus = pkgs.callPackage ./nix/odysseus.nix {
          src = ./.;
          extraPythonPackages = _ps: [ ];
        };
        # Same package with every optional feature from requirements-optional.txt
        # enabled (PyMuPDF, faster-whisper, ddgs, markitdown). Build with
        # `nix build .#odysseus-extras`; for a subset, override `extras`:
        #   odysseus.override { extras = [ "pdf" "stt" ]; }
        odysseus-extras = odysseus.override {
          extras = [
            "pdf"
            "stt"
            "ddg"
            "markitdown"
          ];
        };
        # Helper scripts for the dev shell
        odysseus-setup-wrapper = pkgs.writeShellScriptBin "odysseus-setup-wrapper" (
          builtins.readFile ./nix/scripts/odysseus-setup-wrapper.sh
        );
        odysseus-run-tmux = pkgs.writeShellScriptBin "odysseus-run-tmux" (
          builtins.readFile ./nix/scripts/odysseus-run-tmux.sh
        );
        odysseus-cache-npx = pkgs.writeShellScriptBin "odysseus-cache-npx" (
          builtins.readFile ./nix/scripts/odysseus-cache-npx.sh
        );
      in
      {
        # The dev shell is defined in nix/shell.nix so it
        # also works via a bare `nix-shell nix/shell.nix`. Pass the flake's
        # pinned pkgs so `nix develop` uses the locked nixpkgs.
        devShells.default = import ./nix/shell.nix {
          inherit pkgs;
          extraInputs = [
            odysseus-setup-wrapper
            odysseus-run-tmux
            odysseus-cache-npx
          ];
        };

        packages = {
          default = odysseus;
          odysseus = odysseus;
          odysseus-extras = odysseus-extras;
          container = mkContainer pkgs odysseus;
        };
      }
    ))
    // {
      # System-independent: usable by downstream NixOS / nix-darwin configs.
      overlays.default = pythonOverlay;
      nixosModules.default = odysseusModules.nixosModule;
      darwinModules.default = odysseusModules.darwinModule;
      checks = import ./nix/modules/checks/integration.nix {
        inherit
          self
          nixpkgs
          nix-darwin
          mkRuntimeLibs
          ;
      };
    };
}
