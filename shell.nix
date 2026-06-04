# Compatibility shim so non-flake users can `nix-shell` into the same dev
# environment as `nix develop`. Reuses the flake's devShell via flake-compat,
# pinned through flake.lock (no separate nixpkgs pin to drift).
(
  import (
    let
      lock = builtins.fromJSON (builtins.readFile ./flake.lock);
      compat = lock.nodes.flake-compat.locked;
    in
    fetchTarball {
      url = "https://github.com/edolstra/flake-compat/archive/${compat.rev}.tar.gz";
      sha256 = compat.narHash;
    }
  ) { src = ./.; }
).shellNix
