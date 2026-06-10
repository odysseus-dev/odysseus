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
  #   nix/overlay.nix                     — Python package overrides
  #   nix/modules/services/odysseus.nix   — the NixOS & nix-darwin service modules
  #                                         (Chroma, SearXNG, firewall, app)
  #   nix/modules/checks/integration.nix  — the integration tests
  outputs =
    {
      self,
      nixpkgs,
      flake-utils,
      nix-darwin,
    }:
    let
      inherit (import ./nix/lib.nix) mkContainer mkRuntimeLibs;
      odysseusModules = import ./nix/modules/services/odysseus.nix { src = ./.; };
    in
    flake-utils.lib.eachDefaultSystem (
      system:
      let
        pkgs = import nixpkgs { inherit system; };
        # Default app package (no extra packages); uses callPackage.
        odysseus = pkgs.callPackage ./nix/odysseus.nix {
          src = ./.;
          extraPythonPackages = _ps: [ ];
        };
      in
      {
        # The dev shell is defined in nix/shell.nix so it
        # also works via a bare `nix-shell nix/shell.nix`. Pass the flake's
        # pinned pkgs so `nix develop` uses the locked nixpkgs.
        devShells.default = import ./nix/shell.nix { inherit pkgs; };

        packages = {
          default = odysseus;
          container = mkContainer pkgs odysseus;
        };
      }
    )
    // {
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
