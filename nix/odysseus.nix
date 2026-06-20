# The odysseus package.
#
# Bundles the Python env (+ any extras) and wraps the uvicorn / setup / chroma
# entrypoints. Uses stdenv.mkDerivation instead of buildPythonApplication because
# Odysseus is a web application with a custom directory structure, not a standard
# Python package. The setup.py is a first-time setup script, not a setuptools
# build script. We manually create wrappers for the entry points.
{
  lib,
  stdenv,
  makeWrapper,
  python3,
  src,
  extraPythonPackages ? _ps: [ ],
  # Optional feature deps (requirements-optional.txt), selected by name so
  # consumers opt in without hand-listing nixpkgs attrs:
  #   odysseus.override { extras = [ "pdf" "stt" ]; }
  extras ? [ ],
}:
let
  # requirements name -> nixpkgs python3Packages attr, where they differ.
  reqRenames = {
    # The bundled odysseus-chroma needs the full ChromaDB server; the
    # HTTP-only client used by the Docker path isn't packaged in nixpkgs.
    "chromadb-client" = "chromadb";
  };

  # nixpkgs attr names parsed from requirements.txt: drop comments / blanks,
  # strip version specifiers and extras, normalise (lowercase, _/. -> -).
  # Skip packages that don't exist in nixpkgs (test-only deps like httpx2).
  # Also skip fastembed on aarch64-linux where it's marked as badPlatform.
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
      rawNames = lib.filter (n: n != null) (map nameOf lines);
      normalisedNames = map normalise rawNames;
      # Packages to skip (test-only or not in nixpkgs)
      # fastembed is not available on aarch64-linux (badPlatform in nixpkgs)
      skipPackages = [ "httpx2" ] ++ lib.optionals stdenv.isAarch64 [ "fastembed" ];
    in
    lib.unique (lib.filter (n: !builtins.elem n skipPackages) normalisedNames);

  # Deps the native app needs that requirements.txt doesn't declare (the
  # Docker/pip path doesn't need them): libmagic-backed MIME detection, and
  # pillow for the qrcode[pil] extra (the extras spec is stripped above).
  extraDefault = ps: [
    ps.python-magic
    ps.pillow
  ];

  # Optional feature deps, keyed by the `extras` list. nixpkgs' markitdown
  # already bundles mammoth/lxml/python-pptx/pandas/openpyxl/xlrd, so the
  # [docx,pptx,xlsx,xls] extras from requirements-optional.txt come for free.
  optionalExtras = {
    pdf = ps: [ ps.pymupdf ]; # PyMuPDF — PDF form-filling (AGPL-3.0)
    stt = ps: [ ps.faster-whisper ]; # local speech-to-text (CTranslate2 backend)
    ddg = ps: [ ps.ddgs ]; # DuckDuckGo as a search provider option
    markitdown = ps: [ ps.markitdown ]; # Office/EPUB text extraction
  };
  extraFromExtras = ps: lib.concatMap (e: (optionalExtras.${e} or (_: [ ])) ps) extras;

  # The app's Python environment.
  #
  # The default package set is parsed from requirements.txt (the single source
  # of truth) so the Nix env can't drift from the declared deps. Darwin
  # test-skips come from the overlay applied at the pkgs level (nix/overlay.nix,
  # exported as overlays.default from the flake) — not a python3.override here,
  # so the same overrides apply to any interpreter a consumer pulls in.
  #
  # `extraPythonPackages` is a withPackages-style function (ps: [ ... ]) merged
  # on top, so consumers can add deps the Cookbook would otherwise pip-install —
  # which fails on the read-only Nix store.
  pythonEnv = python3.withPackages (
    ps: (map (n: ps.${n}) reqNames) ++ extraDefault ps ++ extraFromExtras ps ++ extraPythonPackages ps
  );

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
    license = lib.licenses.agpl3Only;
  };

  # Exposed so the dev shell (nix/shell.nix) can put the full interpreter
  # (`python`, `uvicorn`, `pip`, `chroma`) on PATH directly, giving an
  # editable dev environment: PYTHONPATH="." in the shell makes the working
  # tree win over this store copy, while the deps here stay importable.
  passthru = {
    inherit pythonEnv extras;
  };
}
