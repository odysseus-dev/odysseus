# Provider Setup Guide

This document provides setup instructions for integrating various model providers and serving backends with Odysseus.

---

## SGLang

[SGLang](https://github.com/sgl-project/sglang) is a serving backend for large language models (LLMs) that provides optimized inference and speculative decoding. It supports NVIDIA and AMD GPUs across Linux, Windows/WSL, macOS, and Docker.

### **Prerequisites**
- **Linux/macOS**: Python 3.8+ and pip.
- **Windows**: WSL2 with Ubuntu 20.04+.
- **Docker**: NVIDIA Container Toolkit (for NVIDIA GPUs) or ROCm (for AMD GPUs).
- **Hardware**: NVIDIA GPU (CUDA 11.8+) or AMD GPU (ROCm 5.6+).

---

### **Installation**

#### **Linux/macOS**
```bash
pip install "sglang[all]"
```

#### **Windows/WSL**
1. Install WSL2 and Ubuntu 20.04+ from the Microsoft Store.
2. Open WSL and run:
   ```bash
   pip install "sglang[all]"
   ```

#### **Docker (NVIDIA)**
```bash
docker run --gpus all -p 8000:8000 --ipc=host \
  -v ~/.cache/huggingface:/root/.cache/huggingface \
  sglang/sglang:latest \
  python3 -m sglang.launch_server --model-path lmsys/vicuna-7b-v1.3 --host 0.0.0.0 --port 8000
```

#### **Docker (AMD)**
```bash
docker run --device=/dev/kfd --device=/dev/dri -p 8000:8000 --ipc=host \
  -v ~/.cache/huggingface:/root/.cache/huggingface \
  rocm/sglang:latest \
  python3 -m sglang.launch_server --model-path lmsys/vicuna-7b-v1.3 --host 0.0.0.0 --port 8000
```

---

### **Serving a Model**
To serve a model with SGLang, use the following command:
```bash
python3 -m sglang.launch_server --model-path <model-repo-id> --host 0.0.0.0 --port 8000
```

Replace `<model-repo-id>` with the Hugging Face model repository ID (e.g., `lmsys/vicuna-7b-v1.3`).

---

### **Troubleshooting**

#### **SGLang Native Kernel Errors**
If you encounter errors related to the SGLang native kernel, reinstall the kernel:
```bash
pip install --upgrade sglang-kernel
```

#### **CUDA/ROCm Compatibility**
- Ensure your GPU drivers are up-to-date.
- For NVIDIA GPUs, install CUDA 11.8+.
- For AMD GPUs, install ROCm 5.6+.

#### **Port Conflicts**
If port `8000` is already in use, specify a different port:
```bash
python3 -m sglang.launch_server --model-path <model-repo-id> --host 0.0.0.0 --port 8001
```

---

### **Supported Models**
SGLang supports most Hugging Face models, including:
- `lmsys/vicuna-7b-v1.3`
- `mistralai/Mistral-7B-v0.1`
- `meta-llama/Llama-2-7b-chat-hf`

For best performance, use models with:
- **Quantization**: AWQ, GPTQ, or FP8.
- **Architecture**: Llama, Mistral, or Qwen.

---

## Speculative Decoding

Speculative decoding is a technique to accelerate model inference by predicting multiple tokens in parallel. It is supported for **vLLM**, **llama.cpp**, and **SGLang** in Odysseus.

### **Supported Backends**
| Backend      | Speculative Decoding Support | Notes |
|--------------|-------------------------------|-------|
| **vLLM**     | ✅ Yes                        | Use `--speculative-config` flag. |
| **llama.cpp**| ✅ Yes                        | Use `--spec-type draft-mtp` flag. |
| **SGLang**   | ✅ Yes (Built-in)             | No additional flags required. |

### **Enabling Speculative Decoding**

#### **vLLM**
Add the following flag to your serve command:
```bash
--speculative-config '{"method":"mtp","num_speculative_tokens":3}'
```

#### **llama.cpp**
Add the following flags to your serve command:
```bash
--spec-type draft-mtp --spec-draft-n-max 3
```

#### **SGLang**
Speculative decoding is built into SGLang and does not require additional flags.

### **Model Compatibility**
Speculative decoding works best with models that:
- Are **tuned for speculative decoding** (e.g., models with MTP heads).
- Use **quantization** (AWQ, GPTQ, or FP8).
- Belong to the **Llama, Mistral, or Qwen** families.

---

## Other Providers
- [Anthropic](./PROVIDER_SETUP.md#anthropic)
- [Gemini](./PROVIDER_SETUP.md#gemini)
- [Groq](./PROVIDER_SETUP.md#groq)
- [xAI](./PROVIDER_SETUP.md#xai)
- [OpenRouter](./PROVIDER_SETUP.md#openrouter)
- [DeepSeek](./PROVIDER_SETUP.md#deepseek)

---

## Email Performance Optimizations

Odysseus includes SMTP and IMAP connection pooling to improve email performance. This reduces the overhead of establishing new connections for repeated email operations.

### **Connection Pooling**

- **SMTP Pooling**: Reuses SMTP connections for sending multiple emails, reducing connection setup time.
- **IMAP Pooling**: Reuses IMAP connections for fetching, searching, and managing emails, reducing latency for repeated operations.

### **Performance Monitoring**

The `/email/performance-report` endpoint exposes performance metrics for email operations:

```bash
curl http://localhost:8000/email/performance-report
```

**Response Structure:**
```json
{
  "smtp_pool_size": 2,
  "imap_pool_size": 1,
  "connection_metrics": {
    "smtp": {
      "avg_connection_time_ms": 245,
      "total_connections": 10,
      "reused_connections": 8
    },
    "imap": {
      "avg_connection_time_ms": 320,
      "total_connections": 5,
      "reused_connections": 4
    }
  },
  "recent_errors": []
}
```

### **Best Practices**
- **Connection Limits**: The pool size is dynamically managed, but you can adjust it based on your email provider's rate limits.
- **Error Handling**: Failed connections are automatically removed from the pool and replaced with new ones.
- **Monitoring**: Use the `/email/performance-report` endpoint to track connection reuse and identify potential bottlenecks.