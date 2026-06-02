# Odysseus(中文版)

───────────────────────────────────────────────
 ⊹ ࣪ ˖ ૮( ˶ᵔ ᵕ ᵔ˶ )っ  Odysseus 版本 1.0
───────────────────────────────────────────────

![Odysseus](docs/odysseus.jpg)

一个**自托管的 AI 工作空间**——目标是复刻 ChatGPT / Claude 那样的 UI 体验,只不过更粗糙、更有趣一些。运行在你自己机器上,数据归你所有:**本地优先、隐私优先,没有后门**。

> 这是 [README.md](README.md) 的中文翻译,内容与之保持一致。如发现不一致,请以英文版为准并提 Issue。

## 功能特性

- **Chat(对话)** — 和任何本地模型或 API 聊天;添加新模型非常简单。<br>　<sub>vLLM · llama.cpp · Ollama · OpenRouter · OpenAI</sub>
- **Agent(智能体)** — 把工具交给它,让它自己跑完整个任务。<br>　<sub>基于 [opencode](https://github.com/anomalyco/opencode) · MCP · Web · 文件 · Shell · 技能 · 记忆</sub>
- **Cookbook(模型选型助手)** — 扫描你的硬件,推荐合适的模型,点一下就能下载并部署服务,简单到家。<br>　<sub>基于 [llmfit](https://github.com/AlexsJones/llmfit) · 显存感知 · GGUF / FP8 / AWQ · 适配打分 · vLLM / llama.cpp 部署</sub>
- **Deep Research(深度研究)** — 多步执行,自动收集、阅读、综合资料,生成带可视化的报告。<br>　<sub>改编自 [Tongyi DeepResearch](https://github.com/Alibaba-NLP/DeepResearch)</sub>
- **Compare(模型对比)** — 很有趣的工具,把不同模型放在一起盲测,杜绝偏见!<br>　<sub>多模型 · 盲测 · 综合</sub>
- **Documents(文档编辑)** — **你**写正文,AI 是助手而不是主角。<br>　<sub>多标签页 · Markdown · HTML · CSV · 语法高亮 · AI 编辑 · 建议</sub>
- **Memory / Skills(记忆与技能)** — 持久的记忆和技能,智能体越用越懂你。<br>　<sub>ChromaDB · fastembed(ONNX) · 向量+关键词混合检索 · 导入/导出</sub>
- **Email(邮件)** — IMAP/SMTP 收件箱,自带 AI 分流:紧急提醒、自动标签、自动摘要、自动回复草稿、反垃圾。<br>　<sub>IMAP · SMTP · 多账号路由 · CalDAV 感知</sub>
- **Notes & Tasks(笔记与任务)** — 快速记录带提醒的笔记、待办清单,以及智能体可执行的定时任务。<br>　<sub>笔记提醒 · 清单 · 类 cron 任务 · ntfy / 浏览器 / 邮件 通道</sub>
- **Calendar(日历)** — 本地优先日历,支持 CalDAV 同步到 Radicale / Nextcloud / Apple / Fastmail。<br>　<sub>CalDAV 拉取 · .ics 导入/导出 · 每个日历自定义颜色 · 智能体感知</sub>
- **支持移动端** — 看起来、跑起来在手机上也很顺,不光是桌面。<br>　<sub>响应式 · 可安装(PWA) · 触控手势</sub>
- **Extras(其它)** — 还有不少彩蛋,欢迎你探索!<br>　<sub>图片编辑器 · 主题编辑器 · 文件上传(视觉+PDF) · Web 搜索 · 预设 · 会话 · 2FA</sub>

## 演示

完整的悬停可播放演示在落地页(`docs/index.html`)。

<details>
<summary>截图 / 演示动图</summary>

### Chat & Agents
![Chat & Agents](docs/chat.gif)
### Deep Research
![Deep Research](docs/research.gif)
### Compare
![Compare](docs/compare.gif)
### Documents
![Documents](docs/document.gif)
### Notes & Tasks
![Notes & Tasks](docs/notes.gif)

</details>

## 快速开始

默认配置就能跑:克隆 → 启动 → 在 **Settings(设置)** 里配置模型 / 搜索 / 邮件。**只需要在以下场景改 `.env`**:部署级的覆盖,比如 `APP_BIND`、`APP_PORT`、`AUTH_ENABLED`、`DATABASE_URL`,或者预置一个管理员密码。

首次启动时,Odysseus 会创建一个管理员账号(默认 `admin`,可通过 `ODYSSEUS_ADMIN_USER` 改),并在终端里**打印一个临时密码**。Docker 部署的话,在 `docker compose logs odysseus` 里能看到同一行。用这个密码首次登录后,**在 Settings 里改掉它**。

想贡献?参考 [CONTRIBUTING.md](CONTRIBUTING.md) 了解环境搭建、测试和 PR 规范。

### Docker(推荐)

```bash
git clone https://github.com/pewdiepie-archdaemon/odysseus.git
cd odysseus
cp .env.example .env       # 可选,但建议保留显式默认值
docker compose up -d --build
```

容器健康后打开 `http://localhost:7000`。Docker Compose 默认把 Web UI 绑到 `127.0.0.1`。端口被占用就在 `.env` 里设 `APP_PORT=7001` 再重建容器。只有当你**确实**需要局域网/反代访问时,才设 `APP_BIND=0.0.0.0`。

### Linux / macOS 原生安装

```bash
git clone https://github.com/pewdiepie-archdaemon/odysseus.git
cd odysseus
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python setup.py
python -m uvicorn app:app --host 127.0.0.1 --port 7000
```

依赖:**Python 3.11+**。Cookbook 还需要 `tmux` 来跑后台模型下载和部署。本体应用很轻;真正吃资源的是本地模型部署,具体开销视模型、推理引擎、GPU、显存而定,小机器可以改成连 API 或远程模型服务。仅当**确实**需要局域网/反代访问时才加 `--host 0.0.0.0`。

### Apple Silicon

macOS 上的 Docker 没法用 Metal GPU。要在 M 系列 Mac 上给 Cookbook 启用 GPU 加速,请原生运行:

```bash
git clone https://github.com/pewdiepie-archdaemon/odysseus.git
cd odysseus
./start-macos.sh
```

默认监听 `http://127.0.0.1:7860`(用 7860 是因为 AirPlay 经常占着 7000)。要通过 Tailscale 之类的可信 LAN/VPN 共享给手机,需要绑全部接口:

```bash
ODYSSEUS_HOST=0.0.0.0 ./start-macos.sh
# 然后在手机端打开 http://<tailscale-ip>:7860
```

脚本启动时也会读 `.env`,所以把 `APP_BIND=0.0.0.0`、`APP_PORT` 写在 `.env` 里,以后就不用每次命令行再传。

绑到 loopback 之外前,**保持 `AUTH_ENABLED=true`(默认)**。**不要把这个端口直接暴露到公网**。想打成可双击启动的 macOS App:

```bash
./build-macos-app.sh
```

<details>
<summary>Cookbook、GPU、Ollama 与排错说明</summary>

**Docker 捆绑的服务。** Compose 会起 Odysseus、ChromaDB、SearXNG 和 ntfy。Odysseus 和捆绑服务端口默认都绑到 `127.0.0.1`,所以本机能访问,但不会自动暴露到 LAN/公网——除非你显式开启。

**Docker 里 Cookbook 的存储。** 下载存在 `./data/huggingface`(容器内是 `~/.cache/huggingface`)。Cookbook 安装的 Python CLI 和推理引擎在 `./data/local`(容器内 `~/.local`),这样重建容器不会丢。

**远程服务器。** 在 **Cookbook → Settings → Servers** 里生成 Odysseus 的 SSH 密钥,把公钥加到远程服务器的 `~/.ssh/authorized_keys`。在宿主机上你也可以直接:

```bash
ssh-copy-id -i data/ssh/id_ed25519.pub user@server
```

**Docker GPU overlay。** 纯 CPU 用户可以跳过这节。Cookbook 只能识别 Docker 暴露给容器的 GPU——如果宿主 runtime 或 device passthrough 没配,Cookbook 看到的会是核显、另一张卡或 CPU,而不是你想要的那张。

NVIDIA 用户可以用 `scripts/check-docker-gpu.sh` 诊断 GPU passthrough,可以选装宿主 runtime 或更新 `.env`:

```bash
# 只读诊断(默认,什么都不装,不会动 .env):
scripts/check-docker-gpu.sh

# 只打印系统对应的安装命令(不执行):
scripts/check-docker-gpu.sh --print-install-commands

# 在 Ubuntu/Debian 上安装 NVIDIA Container Toolkit(需要 sudo):
scripts/check-docker-gpu.sh --install-nvidia-toolkit

# 把 COMPOSE_FILE 写入 .env(只在确认 GPU passthrough 可用时):
scripts/check-docker-gpu.sh --enable-nvidia-overlay

# 完整流程:装 toolkit,确认 passthrough 后再开 overlay:
scripts/check-docker-gpu.sh --install-nvidia-toolkit --enable-nvidia-overlay
```

安全提示:
- 应用**不会**自动安装宿主 GPU runtime。
- 应用**不会**自动改 `.env`。
- 只有在显式传入 `--enable-nvidia-overlay`,且 GPU passthrough 确认成功后,`.env` 才会被改。`--yes` 跳过提示,但**不会**绕过 passthrough 检查。
- `--enable-nvidia-overlay` 产生的 `.env.bak.*` 备份,已被 Git 和 Docker 构建上下文忽略。

不通过脚本、手动启用的话,在 `.env` 加一行:

```bash
COMPOSE_FILE=docker-compose.yml:docker/gpu.nvidia.yml
```

**AMD / ROCm。** AMD 设置是只读诊断 + 手动改 `.env`:

```bash
scripts/check-docker-amd-gpu.sh
```

把脚本输出填到 `.env`,`RENDER_GID` 换成宿主里真实的 render 组 GID:

```bash
COMPOSE_FILE=docker-compose.yml:docker/gpu.amd.yml
RENDER_GID=989
```

NVIDIA / AMD 的 GPU 支持,还要看对应 overlay 文件(`docker/gpu.nvidia.yml` 或 `docker/gpu.amd.yml`)里的注释。

启用任一 overlay 后,可以验证一下:

```bash
docker compose exec odysseus nvidia-smi -L   # NVIDIA
docker compose exec odysseus sh -lc 'test -e /dev/kfd && test -d /dev/dri && ls -l /dev/kfd /dev/dri/renderD*'  # AMD
```

> **GPU passthrough ≠ llama.cpp CUDA。** 容器内 `nvidia-smi` 能跑通,只能说明 Docker 拿到了 GPU,**不等于** llama.cpp 也准备好了——它还需要 `cudart` 和 CUDA Toolkit 运行时。如果 Cookbook 日志里出现 `Unable to find cudart library`、`Could NOT find CUDAToolkit`、`CUDA Toolkit not found`,或者张量/层被分到了 CPU,那是 **Cookbook / llama.cpp 编译**的问题,不是 Docker passthrough 的问题。走 **Cookbook → Dependencies** 重装一次 serve engine,就能拿到带 CUDA 的构建。
>
> AMD / ROCm 同理:看到 `/dev/kfd` 和 `/dev/dri` **只**说明设备 passthrough 通了,不代表 ROCm userspace 或 vLLM/llama.cpp 的 ROCm 构建就绪。`rocm-smi` 和 `rocminfo` **不要求**在精简的 Odysseus 镜像里出现。

**Docker 下的 Ollama。** 如果 Ollama 跑在宿主机上,在 Settings 里加这个端点:

```text
http://host.docker.internal:11434/v1
```

Ollama 必须监听 loopback 之外的地址:

```bash
OLLAMA_HOST=0.0.0.0:11434 ollama serve
```

**常用检查命令。**

```bash
docker compose ps
docker compose logs --tail=120 odysseus
docker compose logs odysseus | grep -E 'ChromaDB|MemoryVectorStore|DEGRADED'
```

**macOS 小贴士。** `start-macos.sh` 会装 Homebrew 依赖、建 venv、跑 setup,然后在 `7860` 端口起 uvicorn(避免和 AirPlay 抢 7000)。Metal 用 llama.cpp / Ollama。vLLM / SGLang 只能在 CUDA / ROCm 上跑,macOS 用不了。**只**支持 MLX 的模型 Odysseus 不直接 serve。

</details>

### Windows 原生

**一键启动脚本**(自动建 venv、装依赖、跑 setup、起服务,重复执行也安全):

```powershell
git clone https://github.com/pewdiepie-archdaemon/odysseus.git
cd odysseus
powershell -ExecutionPolicy Bypass -File .\launch-windows.ps1
```

或者手动:

```powershell
git clone https://github.com/pewdiepie-archdaemon/odysseus.git
cd odysseus
py -3.11 -m venv venv
venv\Scripts\Activate.ps1
pip install -r requirements.txt
python setup.py
python -m uvicorn app:app --host 127.0.0.1 --port 7000
```

如果 `python` 指向老的解释器,把 venv 步骤里的 `py -3.11` 换成 `py -3.12` 或其他已装的 3.11+ 版本。

**依赖:** Python 3.11+。核心功能(聊天、智能体、记忆、文档、邮件、日历、深度研究)完全可原生跑。要让 Cookbook 跑后台模型下载、让智能体用 shell 工具,还要装 [Git for Windows](https://git-scm.com/download/win)(提供 `bash.exe`)。本地 GPU 跑 vLLM / SGLang 只能 Linux/WSL2;Windows 本地模型最简单是 [Ollama](https://ollama.com/download),在 Settings 里把它指向 `http://localhost:11434/v1`。

打开 `http://localhost:7000`,用生成的管理员密码登录,然后**所有配置都在 Settings 里完成**。

## 安全提示

Odysseus 是一个自托管工作空间,带很多强力的本地工具:Shell 访问、文件上传、模型下载、Web 研究、邮件/日历集成、API Token 等。请把它当作**管理控制台**来对待。

- 网络可达的部署,**保持 `AUTH_ENABLED=true`**。
- 本地开发以外,**保持 `LOCALHOST_BYPASS=false`**。
- 当 Odysseus 通过可信反代或私有访问网关走 HTTPS 时,**设 `SECURE_COOKIES=true`**。
- **不要**直接把它暴露到公网——除非前面挂了 HTTPS + 可信反代或私有访问层。
- 下列内容**别**进 Git,也不要发到任何公开位置:`.env`、`data/`、`logs/`、数据库、上传文件、生成的媒体、备份、auth/session 文件、API 密钥、模型/Provider Token。默认都被 .gitignore 忽略。
- 首次启动后**复查 `data/auth.json`**:除非你**明确**想开开放注册,否则关掉;只留你自己的账号为 admin;demo / 测试账号不要给 admin。
- **非管理员用户默认拿不到 shell / Python / 文件读写**;只对管理员开放的路由/工具包括:MCP 管理、API Token、Webhook、模型 / Cookbook serve、备份 / Vault、应用设置。其余功能按用户级别权限控制,暴露部署前**逐个审一遍**。
- 凡是曾经在公共聊天、演示、截图、日志里**明文贴过**的 API 密钥 / Token,**立即轮换**。
- 启用 API Token 或 Webhook 时,**每个集成单独建一个 Token**,不用的就删。
- 本地开发**优先绑 `127.0.0.1`**;只有当**确实**需要 LAN / 反代访问时才绑 `0.0.0.0`。
- ChromaDB、SearXNG、ntfy、Ollama、vLLM、llama.cpp、数据库、原始的模型/Provider API,**都保持内网**。只把**鉴权后的** Odysseus Web/API 入口,通过可信反代或私有访问层暴露出去。
- **公开 fork 之前**,跑一次 `git status --short`,确认 `.env`、`data/`、`logs/`、上传、备份、本地数据库里的私有文件**没有**被 stage。

### 私有或反代部署

Odysseus 自身在应用端口上跑的是**纯 HTTP**。Docker Compose 默认把 Odysseus 和捆绑服务都绑到 `127.0.0.1`,所以一个典型的生产/私有部署是这样:

1. 让 Odysseus 留在 localhost,例如 `127.0.0.1:7000`。
2. HTTPS 在一个**可信反代或私有访问网关**上终结。
3. **鉴权后的** Odysseus Web/API 入口放在这一层后面。
4. 原始的服务端口、模型端口**只在内网可达**。

Cloudflare Access、Tailscale、Caddy、nginx、Traefik 都可以套这个模式,Odysseus **不强制依赖**任何一种。如果你的访问层和 Odysseus 同一台机器,反代指向 `http://127.0.0.1:7000`,并保持 `AUTH_ENABLED=true`、`LOCALHOST_BYPASS=false`、`SECURE_COOKIES=true`。

文档/Compose 默认用到的、应该保持内网可达的端口:

| 端口 | 服务 |
|---|---|
| `7000` | Odysseus 原始应用端口 |
| `8080` | SearXNG |
| `8091` | ntfy |
| `8100` | 手动 / Compose 访问 ChromaDB 用的宿主端口 |
| `11434` | Ollama |
| `8000-8020` | 常见的本地模型/Provider API 端口 |

## 贡献

欢迎帮忙。最适合新手的切入点:全新安装测试、Provider 配置类 Bug、移动端 / 编辑器打磨、文档、小而专的重构。当前"需要帮助"的清单见 [ROADMAP.md](ROADMAP.md)。

## 配置

绝大多数配置在应用内 `/setup` 或 **Settings** 完成。`.env` 用来放部署级默认值、首次启动前就要存在的密钥。常用项:

| 变量 | 默认值 | 说明 |
|---|---|---|
| `LLM_HOST` | `localhost` | 你的 LLM 服务(例如 `llm-host.local:8000`) |
| `LLM_HOSTS` | -- | 模型发现用的逗号分隔列表 |
| `OPENAI_API_KEY` | -- | 可选的 OpenAI Key。建议在应用内添加 Provider,除非你想预置。 |
| `SEARXNG_INSTANCE` | `http://localhost:8080` | SearXNG 地址。Docker 会覆盖为 `http://searxng:8080`。 |
| `SEARXNG_SECRET` | 首次 Docker 启动时生成 | 可选的 SearXNG cookie / CSRF 密钥。除非需要钉死,否则留空。 |
| `APP_BIND` | `127.0.0.1` | Docker Compose 把 Web UI 绑到的宿主地址。**只在确实需要 LAN / 反代访问时才设 `0.0.0.0`**。 |
| `APP_PORT` | `7000` | Docker Compose 把 Web UI 暴露的宿主端口。 |
| `AUTH_ENABLED` | `true` | 是否启用登录 |
| `LOCALHOST_BYPASS` | `false` | 仅用于开发的 loopback 鉴权绕过。共享 / 网络部署**保持 false**。 |
| `SECURE_COOKIES` | `false` | 当 Odysseus 通过可信反代走 HTTPS 时,设为 `true`。 |
| `DATABASE_URL` | `sqlite:///./data/app.db` | 数据库连接串 |
| `CHROMADB_HOST` | `localhost` | ChromaDB 主机(用于向量记忆)。Docker 会覆盖为 `chromadb`。 |
| `CHROMADB_PORT` | `8100` | 手动宿主运行时 ChromaDB 端口。Docker 会覆盖为 `8000`。 |
| `EMBEDDING_URL` | -- | OpenAI 兼容的 Embedding 端点 |

### 内置 MCP 服务(可选)

Odysseus 在启动时**自动注册**几个内置 MCP 服务。基于 `npx` 的(目前是浏览器服务 `@playwright/mcp`)只有在 npm 包**已经**在本地 npx 缓存里时才会启动。如果包没缓存,会启动时**跳过**并在日志里告诉你怎么装——所以**全新安装不会被几分钟的 npm 下载卡住**,也不会因为 Playwright 系统依赖缺失而卡死。

要启用浏览器 MCP(页面导航、截图、视觉),先跑一次:

```bash
npx -y @playwright/mcp@latest --version
```

这会装 `@playwright/mcp` + Playwright(共约 300MB)。重启 Odysseus,这个服务就会在启动时注册。

## 架构

```
app.py                   # FastAPI 入口
core/      auth, database, middleware, constants
src/       llm_core, agent_loop, agent_tools, chat_processor, search/
routes/    chat, session, document, memory, model … 端点
services/  docs, memory, search, hwfit(Cookbook)…
static/    index.html + app.js + style.css + js/(模块化前端)
docs/      落地页(index.html) + 演示动图
```

## 数据

所有用户数据都在 `data/`(已被 gitignore):`app.db`(会话、消息、文档)、`memory.json`、`presets.json`、`uploads/`、`personal_docs/`、`chroma/`、`settings.json`。

## Star History

<a href="https://www.star-history.com/?repos=pewdiepie-archdaemon%2Fodysseus&type=date&legend=top-left">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/chart?repos=pewdiepie-archdaemon/odysseus&type=date&theme=dark&legend=top-left" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/chart?repos=pewdiepie-archdaemon/odysseus&type=date&legend=top-left" />
   <img alt="Star History Chart" src="https://api.star-history.com/chart?repos=pewdiepie-archdaemon/odysseus&type=date&legend=top-left" />
 </picture>
</a>

## 许可证

MIT——见 [LICENSE](LICENSE) 和 [ACKNOWLEDGMENTS.md](ACKNOWLEDGMENTS.md)。

```
                                  |
                                 |||
                                |||||
                  |    |    |   |||||||
                 )_)  )_)  )_)   ~|~
                )___))___))___)\  |
               )____)____)_____)\\|
             _____|____|____|_____\\\__
             \                       /
       ~^~^~~^~^~~^~^~~^~^~~^~^~~^~^~~^~^~~^~^~
               ~^~  全体上船!  ~^~
       ~^~^~~^~^~~^~^~~^~^~~^~^~~^~^~~^~^~~^~^~
```
