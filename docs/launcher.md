# Odysseus launcher

`odysseus.sh` (macOS / Linux) and `odysseus.ps1` (Windows) are the
single entry points for every native install path. They replace the
older `start-macos.sh`, `launch-windows.ps1`, `install-service.sh`, and
`update_windows.bat` scripts, which still exist as thin deprecation
shims for one release and will be removed in a follow-up cleanup PR.

## Flag surface

The same flags work on both `odysseus.sh` and `odysseus.ps1`:

| Flag | What it does |
|---|---|
| `--launch=native` *(default)* | Run the app on this machine via venv + uvicorn. Right choice on macOS — keeps GPU/Metal access. |
| `--launch=docker` | `docker compose up`. Auto-detects the right GPU overlay (NVIDIA → `docker-compose.gpu-nvidia.yml`, AMD/ROCm on Linux → `docker-compose.gpu-amd.yml`, else CPU). |
| `--launch=docker-nvidia` | Force the NVIDIA overlay. |
| `--launch=docker-amd` | Force the AMD/ROCm overlay (Linux only — ROCm runtime needs `/dev/kfd`). |
| `--update` | `git pull` + reinstall Python deps (only if `requirements.txt` changed) + rebuild Docker images (when `--launch=docker*`). Safe to re-run. |
| `--add-to-path` | Symlink `odysseus` into `~/.local/bin` and ensure that dir is on `PATH` in `~/.zshrc` / `~/.bashrc`, so `odysseus` works from anywhere. |
| `--remove-from-path` | Reverse `--add-to-path`. |
| `--install-service` | Install the platform auto-start agent (launchd on macOS, systemd on Linux). |
| `--uninstall-service` | Remove the auto-start agent (leaves `data/` and `venv/` alone). |
| `--port=N` | Override the port. Default `7000`; macOS defaults to `7860` because AirPlay Receiver holds `7000`. |
| `--host=H` | Override the bind address. Default `127.0.0.1`. Use `0.0.0.0` for LAN/Tailscale. |
| `--no-open` | Don't open the browser when the server is ready. |
| `-h` / `--help` | Show the in-script help. |

## Common workflows

```sh
# First-time install + launch on macOS
./odysseus.sh --launch=native

# Daily use
odysseus                        # from anywhere, after --add-to-path
odysseus --port=7900            # different port
odysseus --host=0.0.0.0         # expose on LAN/Tailscale

# Pull the latest code, reinstall deps if needed
odysseus --update

# Run in Docker (CPU, with auto-GPU detection)
odysseus --launch=docker

# Auto-start at login (background)
odysseus --install-service
odysseus --uninstall-service
```

## macOS-specific notes

- **First launch on a new machine** — `odysseus.sh --launch=native` does the same thing as the old `start-macos.sh`: creates a venv with Homebrew's Python 3.11, installs deps with the requirements-hash cache so warm launches skip pip, brings up SearXNG via Docker if Docker is available, and opens the UI at `http://127.0.0.1:7860`.
- **Port 7000 → 7860** — the default flips on macOS to dodge AirPlay Receiver. Use `--port=7000` if you've actually freed it.
- **GPU** — Metal on Apple Silicon is available on the native path only. Docker on macOS runs in a Linux VM with no GPU access; `--launch=docker` prints a one-line warning and falls back to CPU.
- **Auto-start at login** — `--install-service` writes `~/Library/LaunchAgents/com.odysseus.ui.plist` and `launchctl load`s it. The agent runs `odysseus.sh --launch=native` on user login, keeps the server alive across crashes, and survives reboots. Use `odysseus --uninstall-service` to remove it.
- **The .app** — `odysseus.sh` doesn't yet build the clickable .app; that's still `./build-macos-app.sh` for now. Phase 3 of the macOS plan adds `odysseus.sh --package-mac` for that.

## Windows-specific notes

- PowerShell 5.1 (built into Windows 10+) or PowerShell 7+ both work.
- The native path requires Git for Windows for the Cookbook / agent shell
  tool. The core app (chat, agent, memory, documents, email, calendar,
  deep research) works without it.
- `--launch=docker-nvidia` is supported. `--launch=docker-amd` is not
  (ROCm on Windows isn't a thing); use `--launch=docker-nvidia` or
  `--launch=native` instead.

## Old scripts (deprecated, will be removed)

| Old | New |
|---|---|
| `./start-macos.sh` | `./odysseus.sh --launch=native` |
| `powershell -File .\launch-windows.ps1` | `powershell -File .\odysseus.ps1 --launch=native` |
| `./install-service.sh` | `./odysseus.sh --install-service` |
| `.\update_windows.bat` | `powershell -File .\odysseus.ps1 --update --launch=docker` |

Each old script still works for one release: it prints a deprecation
notice and forwards to the new launcher.
