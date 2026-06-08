# Python package overrides for Odysseus.
#
# Some packages need their test suites disabled on Darwin because they fail
# there (notably under nixos-unstable, which consumers pull in via
# inputs.nixpkgs.follows). This overlay is applied to the Python package set
# used by the odysseus package.
final: prev: {
  niquests = prev.niquests.overridePythonAttrs (old: {
    doCheck = !final.stdenv.isDarwin;
  });

  # caldav's test suite fails on Darwin. Skip its checks there; they pass
  # on Linux / the pinned nixpkgs.
  caldav = prev.caldav.overridePythonAttrs (old: {
    doCheck = !final.stdenv.isDarwin;
    doInstallCheck = !final.stdenv.isDarwin;
  });
}
