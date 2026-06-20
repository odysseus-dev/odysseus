# Python package overrides for Odysseus.
#
# Exposed as a standard nixpkgs overlay so downstream NixOS / nix-darwin modules
# can apply it via `nixpkgs.overlays = [ odysseus.overlays.default ]` and get the
# same Darwin test-skips the flake uses. Uses `pythonPackagesExtensions`, the
# nixpkgs-recommended mechanism for extending the python package set: it composes
# with other overlays and applies across every python3 interpreter, unlike
# `python3.override { packageOverrides }` which is scoped to one instance.
#
# `final` / `prev` are the top-level nixpkgs scopes; the inner `pyFinal` /
# `pyPrev` are the python package scopes.
final: prev: {
  pythonPackagesExtensions = prev.pythonPackagesExtensions ++ [
    (pyFinal: pyPrev: {
      # niquests' test suite fails on Darwin (notably under nixos-unstable,
      # which consumers pull in via inputs.nixpkgs.follows). Skip it there.
      niquests = pyPrev.niquests.overridePythonAttrs (old: {
        doCheck = !final.stdenv.isDarwin;
      });

      # caldav's test suite fails on Darwin. Skip its checks there; they pass
      # on Linux / the pinned nixpkgs.
      caldav = pyPrev.caldav.overridePythonAttrs (old: {
        doCheck = !final.stdenv.isDarwin;
        doInstallCheck = !final.stdenv.isDarwin;
      });
    })
  ];
}
