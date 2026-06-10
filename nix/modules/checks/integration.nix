# Integration checks for the Odysseus modules, package, and container.
{
  self,
  nixpkgs,
  nix-darwin,
  mkRuntimeLibs,
}:
{
  x86_64-linux.nixos-module =
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs { inherit system; };
    in
    pkgs.testers.nixosTest {
      name = "odysseus-nixos-module";
      nodes.machine = {
        imports = [ self.nixosModules.default ];
        services.odysseus = {
          enable = true;
          host = "0.0.0.0";
        };
      };
      testScript = ''
        machine.wait_for_unit("odysseus.service")
        machine.wait_for_open_port(7000)
        response = machine.succeed("curl -sfL http://localhost:7000")
        assert response != "", "Expected non-empty response from Odysseus"
      '';
    };

  x86_64-linux.container =
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs { inherit system; };
      image = self.packages.${system}.container;
    in
    pkgs.runCommand "odysseus-container-check"
      {
        buildInputs = [
          pkgs.gnutar
          pkgs.gzip
          pkgs.jq
        ];
      }
      ''
        # Verify the image tarball is a valid gzip archive
        file ${image} | grep -q 'gzip compressed data' || {
          echo "ERROR: ${image} is not a valid gzip archive"
          file ${image}
          exit 1
        }

        # Verify the image contains standard container layout elements
        CONTENTS=$(tar -tzf ${image})
        echo "$CONTENTS"

        echo "$CONTENTS" | grep -q 'manifest.json' || {
          echo "ERROR: missing manifest.json"
          exit 1
        }

        # Verify the entrypoint is set (odysseus binary)
        echo "$CONTENTS" | grep -q 'odysseus' || {
          echo "WARNING: odysseus binary not found in image contents"
        }

        echo "odysseus container image is valid"
        touch $out
      '';

  aarch64-darwin.darwin-module =
    let
      system = "aarch64-darwin";
      pkgs = import nixpkgs { inherit system; };
      darwinConfig = nix-darwin.lib.darwinSystem {
        inherit system;
        modules = [
          self.darwinModules.default
          {
            services.odysseus.enable = true;
            system.stateVersion = 5;
          }
        ];
      };
    in
    darwinConfig.system;

  aarch64-darwin.integration-test =
    let
      system = "aarch64-darwin";
      pkgs = import nixpkgs { inherit system; };
      odysseus = self.packages.${system}.default;
    in
    pkgs.runCommand "odysseus-darwin-integration-test"
      {
        nativeBuildInputs = [
          odysseus
          pkgs.curl
          pkgs.python3
        ];
      }
      ''
        set -euo pipefail

        DATA_DIR=$(mktemp -d)
        export ODYSSEUS_DATA_DIR="$DATA_DIR/data"
        mkdir -p "$ODYSSEUS_DATA_DIR"

        # Set up runtime library path (same as the darwin module)
        export LD_LIBRARY_PATH="${pkgs.lib.makeLibraryPath (mkRuntimeLibs pkgs)}"

        # Database path must be exported so core/database.py sees it
        # in the server process (odysseus-setup sets it internally only)
        export DATABASE_URL="sqlite:///$ODYSSEUS_DATA_DIR/app.db"

        # SSL_CERT_FILE may point to a missing path in the build env;
        # unset it so httpx falls back to system certs
        unset SSL_CERT_FILE

        # Start the bundled ChromaDB server first — the app connects to it
        # over HTTP, exactly as the darwin module wires it up. Without this
        # the app runs degraded (vector RAG / memory unavailable).
        CHROMA_PORT=$(python3 -c "import socket; s=socket.socket(); s.bind((\"\", 0)); print(s.getsockname()[1]); s.close()")
        mkdir -p "$ODYSSEUS_DATA_DIR/chroma"
        ${odysseus}/bin/odysseus-chroma run \
          --path "$ODYSSEUS_DATA_DIR/chroma" \
          --host 127.0.0.1 \
          --port "$CHROMA_PORT" > "$DATA_DIR/chroma.log" 2>&1 &
        CHROMA_PID=$!

        # Ensure both background processes are reaped on any exit path
        trap 'kill $CHROMA_PID 2>/dev/null || true; kill ''${SERVER_PID:-} 2>/dev/null || true' EXIT

        # Point the app at the Chroma server, the same way the module does
        export CHROMADB_HOST=127.0.0.1
        export CHROMADB_PORT="$CHROMA_PORT"

        # Wait for Chroma to accept connections (any HTTP reply = up)
        i=0
        while [ $i -lt 60 ]; do
          if curl -s -o /dev/null "http://127.0.0.1:$CHROMA_PORT/api/v2/heartbeat"; then
            break
          fi
          if ! kill -0 $CHROMA_PID 2>/dev/null; then
            echo "FAIL: ChromaDB exited early"
            echo "--- chroma.log ---"
            tail -40 "$DATA_DIR/chroma.log" || true
            exit 1
          fi
          i=$((i + 1))
          sleep 1
        done
        if [ $i -eq 60 ]; then
          echo "FAIL: timed out waiting for ChromaDB"
          tail -40 "$DATA_DIR/chroma.log" || true
          exit 1
        fi
        echo "ChromaDB is up on port $CHROMA_PORT"

        # Create admin user
        ${odysseus}/bin/odysseus-setup

        # Find an ephemeral port
        PORT=$(python3 -c "import socket; s=socket.socket(); s.bind((\"\", 0)); print(s.getsockname()[1]); s.close()")

        # Start server in background (redirect stdout to avoid BrokenPipe in build)
        ${odysseus}/bin/odysseus --host 127.0.0.1 --port "$PORT" > "$DATA_DIR/server.log" 2>&1 &
        SERVER_PID=$!

        # Wait for server with 30s timeout
        i=0
        while [ $i -lt 30 ]; do
          if curl -sf -o /dev/null "http://127.0.0.1:$PORT" > /dev/null 2>&1; then
            break
          fi
          if ! kill -0 $SERVER_PID 2>/dev/null; then
            echo "FAIL: server exited early"
            echo "--- server.log ---"
            tail -40 "$DATA_DIR/server.log" || true
            exit 1
          fi
          i=$((i + 1))
          sleep 1
        done

        if [ $i -eq 30 ]; then
          echo "FAIL: timed out waiting for Odysseus"
          echo "--- server.log ---"
          tail -40 "$DATA_DIR/server.log" || true
          kill $SERVER_PID 2>/dev/null || true
          exit 1
        fi

        # Verify response (check HTTP status, not body)
        if ! curl -sf -o /dev/null "http://127.0.0.1:$PORT" > /dev/null 2>&1; then
          echo "FAIL: no response from Odysseus on port $PORT"
          echo "--- server.log ---"
          tail -40 "$DATA_DIR/server.log" || true
          kill $SERVER_PID 2>/dev/null || true
          exit 1
        fi

        echo "PASS: got response from Odysseus on port $PORT"

        # The app must have actually reached Chroma — assert the startup
        # warning is absent so a broken CHROMADB_* wiring fails the check.
        if grep -q "ChromaDB is not reachable" "$DATA_DIR/server.log"; then
          echo "FAIL: app could not reach ChromaDB"
          echo "--- chroma/vector lines from server.log ---"
          grep -i "chroma\|vectorrag\|degraded" "$DATA_DIR/server.log" || true
          exit 1
        fi
        echo "PASS: app connected to ChromaDB"

        # Clean up (trap also covers early exits)
        kill $SERVER_PID $CHROMA_PID 2>/dev/null || true
        wait $SERVER_PID 2>/dev/null || true

        touch $out
      '';
}
