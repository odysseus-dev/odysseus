# Extract the app version from src/constants.py at Nix evaluation time.
#
# The single source of truth for the version is APP_VERSION in
# src/constants.py (the value the app serves at /version). This derivation
# parses it so the Nix package version can't drift from the app's reported
# version.
#
# Usage: pkgs.callPackage ./version.nix { src = ../.; }
{ src }:
let
  constantsContent = builtins.readFile (src + "/src/constants.py");
  versionLine = builtins.head (
    builtins.filter
      (line: builtins.match ''APP_VERSION[[:space:]]*=.*'' line != null)
      (builtins.filter (line: line != "") (builtins.filter (l: builtins.typeOf l == "string") (builtins.split "\n" constantsContent)))
  );
  version = builtins.head (
    builtins.match ''APP_VERSION[[:space:]]*=[[:space:]]*"([^"]+)".*'' versionLine
  );
in
version
