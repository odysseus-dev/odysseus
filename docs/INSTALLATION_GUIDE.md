# Odysseus Installation Guide

This guide provides platform-specific installation instructions for Odysseus, including setup requirements, common issues, and troubleshooting guidance.

## Platform Requirements

| Platform       | Requirements                                                                 | Notes                                  |
|----------------|------------------------------------------------------------------------------|----------------------------------------|
| **Linux**      | Python 3.8+, pip, git, GPU drivers (optional)                              | Tested on Ubuntu 20.04/22.04          |
| **macOS**      | Python 3.8+, pip, git, Homebrew (recommended)                              | Tested on macOS 12+                   |
| **Windows**    | Python 3.8+, pip, git, WSL2 (recommended for GPU support)                  | Native Python or WSL2                 |
| **Docker**     | Docker, NVIDIA Container Toolkit (for GPU support)                         | NVIDIA/AMD GPU configurations tested  |

## Installation Methods

### 1. Native Python Installation

#### Linux/macOS
```bash
# Clone the repository
git clone https://github.com/odysseus-dev/odysseus.git
cd odysseus

# Create virtual environment (recommended)
python -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Install optional dependencies for full functionality
pip install -r requirements-optional.txt

# Run Odysseus
python app.py
```

#### Windows
```powershell
# Clone the repository
git clone https://github.com/odysseus-dev/odysseus.git
cd odysseus

# Create virtual environment (recommended)
python -m venv venv
.\venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Install optional dependencies
pip install -r requirements-optional.txt

# Run Odysseus
python app.py
```

### 2. WSL2 Installation (Windows)
```bash
# Install WSL2 (run in PowerShell as Administrator)
wsl --install -d Ubuntu-22.04

# Launch WSL2 and set up
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3 python3-pip python3-venv git

# Follow Linux installation instructions above
```

### 3. Docker Installation

#### NVIDIA GPU
```bash
docker run --gpus all -p 8000:8000 --ipc=host \
  -v ~/.cache/huggingface:/root/.cache/huggingface \
  -v odysseus-data:/data \
  odysseus/odysseus:latest
```

#### AMD GPU
```bash
docker run --device=/dev/kfd --device=/dev/dri -p 8000:8000 --ipc=host \
  -v ~/.cache/huggingface:/root/.cache/huggingface \
  -v odysseus-data:/data \
  odysseus/odysseus:rocm-latest
```

#### CPU-only
```bash
docker run -p 8000:8000 --ipc=host \
  -v ~/.cache/huggingface:/root/.cache/huggingface \
  -v odysseus-data:/data \
  odysseus/odysseus:cpu-latest
```

## Post-Installation Setup

1. **First Run**:
   - Odysseus will create a `data/` directory for configuration and storage
   - Access the web interface at `http://localhost:8000`
   - Complete the initial setup wizard

2. **Configuration**:
   - Set up integrations (email, calendar, model providers)
   - Configure user accounts and permissions
   - Set up backup schedule

3. **Model Setup**:
   - Configure model providers in Settings → Model Providers
   - For local models, use the Cookbook to download and serve models

## Smoke Test Results

| Test Category          | Linux | macOS | Windows | WSL2 | Docker (NVIDIA) | Docker (AMD) |
|------------------------|-------|-------|---------|------|-----------------|--------------|
| Core UI                | ✅    | ✅    | ✅      | ✅   | ✅              | ✅           |
| Email Integration      | ✅    | ✅    | ✅      | ✅   | ✅              | ✅           |
| Calendar Integration   | ✅    | ✅    | ✅      | ✅   | ✅              | ✅           |
| Model Serving          | ✅    | ✅    | ⚠️     | ✅   | ✅              | ✅           |
| Local Model Download   | ✅    | ✅    | ⚠️     | ✅   | ✅              | ✅           |
| GPU Acceleration       | ✅    | ❌    | ❌      | ✅   | ✅              | ✅           |
| Dependency Installation| ✅    | ✅    | ✅      | ✅   | ✅              | ✅           |

## Common Installation Issues

### Dependency Issues

**Symptom**: `ModuleNotFoundError` or import errors
- **Solution**: Verify all dependencies are installed
  ```bash
  pip install -r requirements.txt
  pip install -r requirements-optional.txt
  ```

### GPU Support Issues

**Symptom**: GPU not detected or CUDA errors
- **Linux**: Install correct NVIDIA drivers and CUDA toolkit
- **Windows**: Use WSL2 for GPU support
- **Docker**: Verify NVIDIA Container Toolkit is installed

### Port Conflicts

**Symptom**: `Address already in use` error
- **Solution**: Change the default port (8000)
  ```bash
  python app.py --port 8080
  ```

### Permission Issues

**Symptom**: Cannot create `data/` directory or write files
- **Solution**: Ensure proper permissions
  ```bash
  mkdir data
  chmod 755 data
  ```

## Platform-Specific Notes

### Linux
- **GPU Drivers**: Install appropriate drivers for your GPU (NVIDIA/AMD)
- **Dependencies**: Install system dependencies:
  ```bash
  sudo apt install -y python3-dev build-essential
  ```

### macOS
- **Homebrew**: Recommended for managing dependencies
- **Metal Support**: Limited GPU acceleration support

### Windows
- **WSL2 Recommended**: For best compatibility and GPU support
- **Native Python**: Possible but may have limitations with some features

### Docker
- **Volume Mounts**: Use volumes for persistent data
- **GPU Support**: Requires NVIDIA Container Toolkit or ROCm
- **Networking**: Configure ports as needed

## Troubleshooting

### General Issues

**Symptom**: Application crashes on startup
- Check logs for error messages
- Verify all dependencies are installed
- Test with a minimal configuration

**Symptom**: Slow performance
- Check system resources (CPU, RAM, GPU)
- Reduce model size or complexity
- Disable unused features

### Integration Issues

**Symptom**: Email/calendar not syncing
- Verify credentials are correct
- Check network connectivity
- Review provider-specific requirements

**Symptom**: Model serving fails
- Verify GPU drivers are installed
- Check model compatibility
- Review resource requirements

## Next Steps

1. **Verify Installation**: Run the smoke tests for your platform
2. **Configure Integrations**: Set up email, calendar, and model providers
3. **Test Functionality**: Verify all core features work as expected
4. **Report Issues**: Submit bug reports for any problems encountered
5. **Contribute**: Help improve installation documentation and scripts