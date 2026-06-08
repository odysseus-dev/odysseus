# The odysseus package.
#
# Bundles the Python env (+ any extras) and wraps the uvicorn / setup / chroma
# entrypoints. Uses stdenv.mkDerivation instead of buildPythonApplication because
# Odysseus is a web application with a custom directory structure, not a standard
# Python package. The setup.py is a first-time setup script, not a setuptools
# build script. We manually create wrappers for the entry points.
{ lib
, stdenv
, makeWrapper
, python3
, src
, extraPythonPackages ? _ps: [ ]
}:
let
  # Python package overrides (test skips for Darwin, etc.)
  pythonOverlay = import ./overlay.nix;

  # requirements name -> nixpkgs python3Packages attr, where they differ.
  reqRenames = {
    # The bundled odysseus-chroma needs the full ChromaDB server; the
    # HTTP-only client used by the Docker path isn't packaged in nixpkgs.
    "chromadb-client" = "chromadb";
  };

  # nixpkgs attr names parsed from requirements.txt: drop comments / blanks,
  # strip version specifiers and extras, normalise (lowercase, _/. -> -).
  reqNames =
    let
      nameOf =
        line:
        let
          noComment = builtins.head (lib.splitString "#" line);
          m = builtins.match "[[:space:]]*([A-Za-z0-9][A-Za-z0-9._-]*).*" noComment;
        in
        if m == null then null else builtins.head m;
      normalise = n: reqRenames.${n} or (lib.replaceStrings [ "_" "." ] [ "-" "-" ] (lib.toLower n));
      lines = lib.splitString "\n" (builtins.readFile ../requirements.txt);
    in
    lib.unique (map normalise (lib.filter (n: n != null) (map nameOf lines)));

  # Deps the native app needs that requirements.txt doesn't declare (the
  # Docker/pip path doesn't need them): libmagic-backed MIME detection, and
  # pillow for the qrcode[pil] extra (the extras spec is stripped above).
  extraDefault = ps: [
    ps.python-magic
    ps.pillow
  ];

  # The app's Python environment.
  #
  # The default package set is parsed from requirements.txt (the single source
  # of truth) so the Nix env can't drift from the declared deps.
  #
  # `extraPythonPackages` is a withPackages-style function (ps: [ ... ]) merged
  # on top, so consumers can add deps the Cookbook would otherwise pip-install —
  # which fails on the read-only Nix store.
  pythonEnv = (python3.override {
    packageOverrides = pythonOverlay;
  }).withPackages
    (ps: (map (n: ps.${n}) reqNames) ++ extraDefault ps ++ extraPythonPackages ps);

  # Single source of truth for the version: src/constants.py's APP_VERSION
  # (the value the app serves at /version), parsed at eval time.
  version = import ./version.nix { inherit src; };
in
stdenv.mkDerivation {
  pname = "odysseus";
  inherit version src;

  nativeBuildInputs = [ makeWrapper ];

  dontBuild = true;
  dontConfigure = true;

  installPhase = ''
    mkdir -p $out/share/odysseus
    cp -r . $out/share/odysseus/

    mkdir -p $out/bin
    makeWrapper ${pythonEnv}/bin/uvicorn $out/bin/odysseus \
      --set PYTHONUNBUFFERED "1" \
      --set PYTHONPATH "$out/share/odysseus" \
      --set-default ODYSSEUS_DATA_DIR "$out/share/odysseus/data" \
      --add-flags "app:app"

    makeWrapper ${pythonEnv}/bin/python $out/bin/odysseus-setup \
      --set PYTHONPATH "$out/share/odysseus" \
      --set-default ODYSSEUS_DATA_DIR "$out/share/odysseus/data" \
      --add-flags "$out/share/odysseus/setup.py"

    # ChromaDB server CLI (from chromadb in pythonEnv) so the service
    # modules can run the vector DB the app connects to over HTTP.
    makeWrapper ${pythonEnv}/bin/chroma $out/bin/odysseus-chroma
  '';

  meta = {
    mainProgram = "odysseus";
    description = "Odysseus AI assistant";
  };
}
