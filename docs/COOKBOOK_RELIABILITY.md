# Odysseus Cookbook Reliability Improvements

This document outlines the improvements made to enhance Cookbook reliability, cross-platform compatibility, and user experience.

## Key Improvements

### 1. Error Handling & Reporting

**Enhanced Error Messages:**
- Detailed error messages for failed downloads, installs, and serve jobs
- Clear next steps for common issues
- Copyable logs for debugging

**Error Categories:**
- **Download Errors**: Network issues, missing files, corrupted downloads
- **Installation Errors**: Dependency conflicts, permission issues
- **Serve Errors**: GPU compatibility, model loading failures
- **Runtime Errors**: Out of memory, invalid configurations

### 2. Model Scanning & Ranking

**Improved Ranking Algorithm:**
- Prioritizes newer architectures and better hardware-fit models
- Accounts for:
  - Architecture age and performance
  - Quantization format (AWQ, GPTQ, FP8, etc.)
  - VRAM/RAM requirements
  - Backend support (vLLM, llama.cpp, SGLang)
  - Vision/mmproj requirements
  - Likely serve reliability

**Compatibility Detection:**
- Automatic hardware detection (GPU type, VRAM, RAM)
- Platform-specific recommendations
- Model size and format validation

### 3. Cross-Platform Compatibility

**Tested Platforms:**
| Platform       | Status      | Notes                                  |
|----------------|-------------|----------------------------------------|
| Linux          | ✅ Working  | Tested on Ubuntu 20.04/22.04          |
| macOS          | ✅ Working  | Limited GPU acceleration              |
| Windows        | ⚠️ Partial  | WSL2 recommended for full functionality|
| Docker (NVIDIA)| ✅ Working  | Requires NVIDIA Container Toolkit     |
| Docker (AMD)   | ✅ Working  | Requires ROCm support                 |

**GPU Compatibility:**
- **NVIDIA**: CUDA 11.8+ with appropriate drivers
- **AMD**: ROCm 5.6+ with compatible hardware
- **Intel**: Limited support, testing ongoing
- **Apple Silicon**: Metal support for M1/M2

### 4. Python Environment Compatibility

**Supported Python Versions:** 3.8+

**Dependency Management:**
- Clear dependency requirements in `requirements.txt`
- Optional dependencies in `requirements-optional.txt`
- Virtual environment recommended

## Troubleshooting Guide

### Download Issues

**Symptom: Model download failed**
- **Network Issues**: Verify internet connectivity
- **Repository Access**: Check if model repository is accessible
- **Disk Space**: Ensure sufficient storage available
- **Corrupted Download**: Delete cached files and retry

**Commands:**
```bash
# Clear Hugging Face cache
rm -rf ~/.cache/huggingface

# Retry download with verbose output
python -m cookbook download --model <model_name> --verbose
```

### Installation Issues

**Symptom: Dependency installation failed**
- **Python Version**: Verify Python 3.8+ is installed
- **System Dependencies**: Install required system packages
- **Virtual Environment**: Create and activate a virtual environment
- **Permission Issues**: Use `--user` flag or run as administrator

**Commands:**
```bash
# Create and activate virtual environment
python -m venv venv
source venv/bin/activate  # Linux/macOS
.\venv\Scripts\activate  # Windows

# Install dependencies
pip install -r requirements.txt
pip install -r requirements-optional.txt
```

### Serve Issues

**Symptom: Model serving failed**
- **GPU Compatibility**: Verify GPU drivers and CUDA/ROCm versions
- **VRAM Requirements**: Check if model fits in available VRAM
- **Model Format**: Verify model format is supported by backend
- **Port Conflicts**: Change serving port if default is in use

**Commands:**
```bash
# Check GPU status
nvidia-smi  # NVIDIA
rocm-smi   # AMD

# Serve with specific backend
python -m cookbook serve --model <model_name> --backend vllm
python -m cookbook serve --model <model_name> --backend sglang
```

### Runtime Issues

**Symptom: Out of memory errors**
- **Reduce Model Size**: Use smaller models or quantization
- **Limit Concurrency**: Reduce number of concurrent requests
- **Increase Resources**: Add more RAM/VRAM or use a more powerful machine

**Symptom: Slow performance**
- **Enable GPU Acceleration**: Verify GPU is being used
- **Optimize Model**: Use quantized models (AWQ, GPTQ, FP8)
- **Adjust Batch Size**: Find optimal batch size for your hardware

## Model Recommendations

### Hardware-Specific Presets

| Hardware Profile       | Recommended Models                          | Notes                                  |
|------------------------|---------------------------------------------|----------------------------------------|
| **Low-End (4-8GB VRAM)** | TinyLlama, Phi-2, Qwen1.5-0.5B, Gemma-2B  | Use 4-bit quantization                |
| **Mid-Range (8-16GB VRAM)** | Mistral-7B, Llama2-13B, Qwen1.5-7B      | Use 8-bit or AWQ quantization         |
| **High-End (16-24GB VRAM)** | Llama2-70B, Mixtral-8x7B, Qwen1.5-14B   | Use FP16 or minimal quantization      |
| **Very High-End (24GB+ VRAM)** | Llama3-70B, custom fine-tunes          | Full precision if needed              |

### Quantization Recommendations

| Quantization Format | Pros                          | Cons                          | Best For                |
|----------------------|-------------------------------|-------------------------------|------------------------|
| **FP16**            | Best quality, full precision  | High VRAM usage               | High-end GPUs          |
| **AWQ**             | Good quality, efficient       | Requires specific hardware    | Modern GPUs            |
| **GPTQ**            | Good quality, widely supported| Slower than AWQ               | Older GPUs             |
| **FP8**             | Good balance, efficient       | Limited hardware support      | Latest GPUs            |
| **4-bit**           | Low VRAM usage                | Reduced quality               | Low-end GPUs           |

## Cookbook Commands

### Basic Commands

```bash
# List available models
python -m cookbook list

# Download a model
python -m cookbook download --model <model_name>

# Serve a model
python -m cookbook serve --model <model_name>

# Scan for compatible models
python -m cookbook scan
```

### Advanced Commands

```bash
# Serve with specific backend
python -m cookbook serve --model <model_name> --backend vllm
python -m cookbook serve --model <model_name> --backend sglang

# Use quantization
python -m cookbook serve --model <model_name> --quantization awq

# Specify port
python -m cookbook serve --model <model_name> --port 8001

# Enable speculative decoding
python -m cookbook serve --model <model_name> --speculative
```

## Best Practices

1. **Start Small**: Test with smaller models before moving to larger ones
2. **Use Quantization**: Reduce VRAM usage with appropriate quantization
3. **Monitor Resources**: Watch GPU/CPU usage during serving
4. **Update Regularly**: Keep Cookbook and dependencies up to date
5. **Backup Models**: Save downloaded models for offline use
6. **Test Compatibility**: Verify model compatibility before production use
7. **Use Virtual Environments**: Isolate dependencies for different projects

## Known Issues & Limitations

| Issue                          | Workaround                                  | Status          |
|--------------------------------|---------------------------------------------|-----------------|
| Windows native GPU support     | Use WSL2 for full GPU acceleration         | Ongoing         |
| Apple Silicon Metal support    | Limited to smaller models                   | Improving       |
| Intel GPU support              | Use CPU-only mode                           | Experimental    |
| Large model (>50GB) downloads  | Download in parts or use torrent           | Ongoing         |
| Multi-GPU serving              | Use specific backends (vLLM, SGLang)        | Supported       |

## Next Steps

1. **Implement Model Caching**: Reduce download times for frequently used models
2. **Add Benchmarking**: Include performance benchmarks for models
3. **Improve GPU Detection**: Better automatic hardware configuration
4. **Enhance Documentation**: Add more detailed setup guides
5. **Expand Testing**: Increase coverage for edge cases and rare hardware
6. **Add Model Conversion**: Support for converting between model formats