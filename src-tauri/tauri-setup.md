# Tauri setup guide

This guide covers only the steps required to build and run the Odysseus desktop app via Tauri and Rust.

## 1) Install the required tools

You need:

- Git
- Rust and Cargo
- Node.js LTS and npm
- Python 3.11+ (the app backend still runs through the Python service on localhost:7000)
- A C++ build toolchain for native dependencies

### Windows

Install:

- Rust from rustup
- Node.js LTS
- Python 3.11+
- Visual Studio Build Tools 2022 with C++ desktop development

Verify:

```powershell
rustc --version
cargo --version
node --version
npm --version
python --version
```

### macOS

Install:

- Xcode Command Line Tools
- Rust
- Node.js LTS
- Python 3.11+

Verify:

```bash
rustc --version
cargo --version
node --version
npm --version
python3 --version
```

### Linux

Install:

- Rust
- Node.js LTS
- Python 3.11+
- A native build toolchain such as `build-essential` or the distro equivalent

Verify:

```bash
rustc --version
cargo --version
node --version
npm --version
python3 --version
```

---

## 2) Clone the repo

```bash
git clone https://github.com/odysseus-dev/odysseus.git
cd odysseus
```

---

## 3) Install JavaScript dependencies

From the project root:

```bash
npm install
```

---

## 4) Install the Tauri CLI

```bash
cargo install tauri-cli --locked
```

Verify it is available:

```bash
cargo tauri --version
```

---

## 5) Start the Python backend

The Tauri app expects the Odysseus backend to be running on `http://127.0.0.1:7000`.

From the repo root:

```bash
python -m venv venv
```

Activate it:

### Windows PowerShell
```powershell
.\venv\Scripts\Activate.ps1
```

### macOS / Linux
```bash
source venv/bin/activate
```

Then install Python requirements:

```bash
pip install -r requirements.txt
```

Then start the service:

```bash
python setup.py
python -m uvicorn app:app --host 127.0.0.1 --port 7000
```

Keep this terminal running while developing or testing the desktop app.

---

## 6) Run the Tauri desktop app in dev mode

From the repo root:

```bash
cd src-tauri
cargo tauri dev
```

This compiles the Tauri app and launches the desktop window.

If the backend is not running, the app will fall back to the offline page configured in the Tauri setup code.

---

## 7) Build the desktop app

When ready for a production build:

```bash
cd src-tauri
cargo tauri build
```

The compiled app will be produced in the platform-specific output directory for the Tauri build.

---

## 8) Common troubleshooting

### `cargo tauri` is not found

```bash
cargo install tauri-cli --locked
```

### Rust compile errors

Make sure:

- Rust is installed and on PATH
- C++ build tools are installed
- You are running the command from the `src-tauri` directory

### Backend is offline

Check whether the Python service is running:

```bash
curl http://localhost:7000
```

If it fails, restart the backend:

```bash
python -m uvicorn app:app --host 127.0.0.1 --port 7000
```

### Port conflict

The Tauri app is configured to reach the backend on `127.0.0.1:7000`.
If that port is in use, update the backend startup port and the app target accordingly.

---

## Quick summary

```bash
# install toolchain
cargo install tauri-cli --locked
npm install

# start backend
python -m venv venv
source venv/bin/activate   # or .\venv\Scripts\Activate.ps1 on Windows
pip install -r requirements.txt
python -m uvicorn app:app --host 127.0.0.1 --port 7000

# run Tauri
cd src-tauri
cargo tauri dev
```
