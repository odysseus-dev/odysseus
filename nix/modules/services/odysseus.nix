# NixOS and nix-darwin service modules for Odysseus.
#
# `src` is the repo root, passed by the flake so package builds resolve it
# from this nested module file. Exposes { nixosModule, darwinModule }.
{ src }:
{
  nixosModule = import ./nixos.nix { inherit src; };
  darwinModule = import ./darwin.nix { inherit src; };
}
