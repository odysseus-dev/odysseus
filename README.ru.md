[English](README.md) · **Русский**

# Odysseus

```
───────────────────────────────────────────────
 ⊹ ࣪ ˖ ૮( ˶ᵔ ᵕ ᵔ˶ )っ  Odysseus vers. 1.0
───────────────────────────────────────────────
```

![Odysseus](docs/odysseus.jpg)

Self-hosted AI-рабочее пространство — задумано как локальная версия того интерфейса, что вы привыкли видеть в ChatGPT и Claude. Только с бóльшим количеством костылей и веселья. Работает на вашем железе и с вашими данными — local-first, приватность прежде всего и без троянов.

## Возможности
  - **Chat** — общайтесь с любой локальной моделью или API; добавить их предельно просто.<br>　<sub>vLLM · llama.cpp · Ollama · OpenRouter · OpenAI</sub>
  - **Agent** — дайте ему инструменты и позвольте выполнить всю задачу самостоятельно.<br>　<sub>на базе [opencode](https://github.com/anomalyco/opencode) · MCP · web · files · shell · skills · memory</sub>
  - **Cookbook** — сканирует ваше железо, советует модели, скачивание и запуск в один клик… легко!<br>　<sub>на базе [llmfit](https://github.com/AlexsJones/llmfit) · учёт VRAM · GGUF / FP8 / AWQ · оценка совместимости · сервинг vLLM / llama.cpp</sub>
  - **Deep Research** — многошаговые прогоны, которые собирают, читают и синтезируют источники в наглядный визуальный отчёт.<br>　<sub>на основе [Tongyi DeepResearch](https://github.com/Alibaba-NLP/DeepResearch)</sub>
  - **Compare** — забавный инструмент для сравнения моделей бок о бок. Полностью слепой тест, без предвзятости!<br>　<sub>несколько моделей · слепой тест · синтез</sub>
  - **Documents** — текст пишете ВЫ, а ИИ помогает, а не наоборот.<br>　<sub>редактор с вкладками · markdown · HTML · CSV · подсветка синтаксиса · правки от ИИ · подсказки</sub>
  - **Memory / Skills** — постоянная память и навыки: со временем агент развивается, всё лучше понимая вас и ваши задачи!<br>　<sub>ChromaDB · fastembed (ONNX) · векторный + ключевой поиск · импорт/экспорт</sub>
  - **Email** — почтовый ящик по IMAP/SMTP со встроенной AI-сортировкой: напоминания о срочном, авто-теги, авто-резюме, черновики авто-ответов, авто-спам.<br>　<sub>IMAP · SMTP · маршрутизация по аккаунтам · поддержка CalDAV</sub>
  - **Notes & Tasks** — быстрые заметки с напоминаниями, список дел и задачи по расписанию, которые может выполнять агент.<br>　<sub>пинги по заметкам · чек-лист · задачи в стиле cron · каналы ntfy / браузер / email</sub>
  - **Calendar** — локальный календарь с синхронизацией по CalDAV с Radicale / Nextcloud / Apple / Fastmail.<br>　<sub>загрузка по CalDAV · импорт/экспорт .ics · цвета для каждого календаря · доступен агенту</sub>
  - **Работает на мобильных** — отлично выглядит и работает на телефоне, а не только на десктопе.<br>　<sub>адаптивный · устанавливается (PWA) · сенсорные жесты</sub>
  - **Дополнительно** — есть что ещё поизучать, будем рады, если попробуете!<br>　<sub>редактор изображений · редактор тем · загрузка файлов (vision + PDF) · веб-поиск · пресеты · сессии · 2FA</sub>

## Демо
Полный тур с воспроизведением по наведению живёт на странице-лендинге (`docs/index.html`).

<details>
<summary>Скриншоты / клипы</summary>

### Chat и агенты
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

## Быстрый старт

Значения по умолчанию работают «из коробки»: клонируйте, запустите, затем настройте
модели/поиск/почту в разделе **Settings**. Файл `.env` правьте только ради
переопределений уровня развёртывания — таких как `APP_BIND`, `APP_PORT`,
`AUTH_ENABLED`, `DATABASE_URL` или заранее заданный пароль администратора.

При первой настройке Odysseus создаёт учётную запись администратора (`admin`, если
не задан `ODYSSEUS_ADMIN_USER`) и печатает временный пароль в терминале.
Для установок через Docker эта же строка есть в `docker compose logs odysseus`.
Используйте его для первого входа, а затем смените в **Settings**.

Хотите внести вклад? Смотрите [CONTRIBUTING.ru.md](CONTRIBUTING.ru.md) — настройка,
тестирование и правила оформления pull request'ов.

### Docker (рекомендуется)
```bash
git clone https://github.com/pewdiepie-archdaemon/odysseus.git
cd odysseus
cp .env.example .env       # необязательно, но рекомендуется ради явных значений по умолчанию
docker compose up -d --build
```
Откройте `http://localhost:7000`, когда контейнеры станут «здоровыми». Docker Compose
по умолчанию привязывает веб-интерфейс к `127.0.0.1`. Если порт занят, задайте
`APP_PORT=7001` в `.env` и пересоздайте контейнер. Ставьте `APP_BIND=0.0.0.0`
только если вы осознанно хотите доступ из LAN / через обратный прокси.

### Нативно Linux / macOS
```bash
git clone https://github.com/pewdiepie-archdaemon/odysseus.git
cd odysseus
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python setup.py
python -m uvicorn app:app --host 127.0.0.1 --port 7000
```
Требования: Python 3.11+. Cookbook к тому же требует `tmux` для фоновых загрузок
моделей и их сервинга. Само приложение лёгкое; тяжёлая часть — это локальный сервинг
моделей, который зависит от модели, рантайма, GPU и VRAM, так что небольшие хосты могут
вместо этого подключаться к API или удалённым серверам моделей. Используйте `--host 0.0.0.0`
только если вы осознанно хотите доступ из LAN / через обратный прокси.

### Apple Silicon
Docker на macOS не может использовать GPU через Metal. Чтобы Cookbook работал с
ускорением GPU на Mac с чипом M-серии, запускайте Odysseus нативно:

```bash
git clone https://github.com/pewdiepie-archdaemon/odysseus.git
cd odysseus
./start-macos.sh
```

Он запустится на `http://127.0.0.1:7860`. Чтобы открыть доступ с телефона по доверенной
LAN/VPN (например, через Tailscale), привяжитесь ко всем интерфейсам:

```bash
ODYSSEUS_HOST=0.0.0.0 ./start-macos.sh
# затем откройте http://<tailscale-ip>:7860
```

Скрипт также читает `.env` при старте, поэтому заданные там `APP_BIND=0.0.0.0` и `APP_PORT`
подхватываются автоматически — без переопределения в командной строке при каждом запуске.

Держите `AUTH_ENABLED=true` (значение по умолчанию), прежде чем привязываться вне loopback.
Не выставляйте этот порт напрямую в публичный интернет. Чтобы собрать кликабельную
обёртку-приложение:

```bash
./build-macos-app.sh
```

<details>
<summary>Cookbook, GPU, Ollama и заметки по устранению неполадок</summary>

**Сервисы в комплекте с Docker.** Compose поднимает Odysseus, ChromaDB, SearXNG и
ntfy. Odysseus и порты сопутствующих сервисов по умолчанию привязаны к `127.0.0.1`,
поэтому они доступны с хоста, но не выставлены в вашу LAN / публичный интернет,
пока вы не разрешите это явно.

**Хранилище Cookbook в Docker.** Загрузки живут в `./data/huggingface`
(`~/.cache/huggingface` внутри контейнера). Установленные Cookbook'ом Python-CLI и
движки сервинга живут в `./data/local` (`~/.local` внутри контейнера), поэтому
они переживают пересоздание контейнера.

**Удалённые серверы.** В **Cookbook -> Settings -> Servers** сгенерируйте SSH-ключ
Odysseus и добавьте публичный ключ в `~/.ssh/authorized_keys` на удалённом сервере.
С хоста также можно выполнить:

```bash
ssh-copy-id -i data/ssh/id_ed25519.pub user@server
```

**GPU-оверлеи для Docker.** Пользователи без GPU могут пропустить этот раздел.
Cookbook может обнаружить только те GPU, которые Docker пробрасывает в контейнер — если
рантайм хоста или проброс устройства не настроены, Cookbook вместо нужного GPU увидит
встроенную графику, другую карту или CPU.

Для NVIDIA скрипт `scripts/check-docker-gpu.sh` диагностирует проброс GPU и при
желании может установить рантайм на хост или обновить `.env`.

```bash
# Диагностика только для чтения (по умолчанию — ничего не ставит, не правит .env):
scripts/check-docker-gpu.sh

# Вывести команды установки под вашу ОС, не запуская их:
scripts/check-docker-gpu.sh --print-install-commands

# Установить NVIDIA Container Toolkit на Ubuntu/Debian (нужен sudo):
scripts/check-docker-gpu.sh --install-nvidia-toolkit

# Записать COMPOSE_FILE в .env (только когда проброс GPU подтверждённо работает):
scripts/check-docker-gpu.sh --enable-nvidia-overlay

# Полная ассистированная настройка — установить toolkit, затем включить оверлей, если проброс работает:
scripts/check-docker-gpu.sh --install-nvidia-toolkit --enable-nvidia-overlay
```

Заметки по безопасности:
- Приложение никогда не устанавливает GPU-рантайм хоста автоматически.
- Приложение никогда не правит `.env` автоматически.
- `.env` изменяется только когда явно передан `--enable-nvidia-overlay`,
  и только после успешного проброса GPU. `--yes` пропускает подтверждения, но не
  обходит проверку проброса.
- Резервные копии `.env.bak.*`, создаваемые `--enable-nvidia-overlay`, игнорируются
  Git'ом и контекстом сборки Docker.

Чтобы включить вручную, без скрипта, добавьте это в `.env`:

```bash
COMPOSE_FILE=docker-compose.yml:docker/gpu.nvidia.yml
```

**AMD / ROCm.** Настройка AMD — это диагностика только для чтения плюс ручная правка
`.env`. Выполните:

```bash
scripts/check-docker-amd-gpu.sh
```

Затем добавьте сообщённые значения в `.env`, заменив `RENDER_GID` числовым id
render-группы вашего хоста:

```bash
COMPOSE_FILE=docker-compose.yml:docker/gpu.amd.yml
RENDER_GID=989
```

Для поддержки GPU NVIDIA/AMD также прочитайте комментарии в выбранном файле оверлея: docker/gpu.nvidia.yml или docker/gpu.amd.yml.

Проверьте после включения любого из оверлеев:

```bash
docker compose exec odysseus nvidia-smi -L   # NVIDIA
docker compose exec odysseus sh -lc 'test -e /dev/kfd && test -d /dev/dri && ls -l /dev/kfd /dev/dri/renderD*'  # AMD
```

> **Проброс GPU ≠ CUDA в llama.cpp.** Прохождение `nvidia-smi` внутри контейнера
> подтверждает доступ Docker к GPU, но llama.cpp дополнительно нужны `cudart` и
> CUDA Toolkit во время выполнения. Если в логах Cookbook видно `Unable to find cudart
> library`, `Could NOT find CUDAToolkit`, `CUDA Toolkit not found` или
> тензоры/слои назначаются на CPU — это проблема сборки Cookbook/llama.cpp, а
> не сбой проброса Docker. Переустановите движок сервинга через
> **Cookbook → Dependencies**, чтобы получить сборку с поддержкой CUDA.
>
> То же разделение применимо к AMD/ROCm: наличие `/dev/kfd` и `/dev/dri` внутри
> контейнера подтверждает проброс устройства, а не userspace ROCm или сборку
> vLLM/llama.cpp с поддержкой ROCm. `rocm-smi` и `rocminfo` не предполагаются
> внутри облегчённого образа Odysseus.

**Ollama с Docker.** Если Ollama работает на хосте, добавьте в Settings такой
endpoint:

```text
http://host.docker.internal:11434/v1
```

Ollama должна слушать вне собственного loopback-интерфейса:

```bash
OLLAMA_HOST=0.0.0.0:11434 ollama serve
```

**Полезные проверки.**

```bash
docker compose ps
docker compose logs --tail=120 odysseus
docker compose logs odysseus | grep -E 'ChromaDB|MemoryVectorStore|DEGRADED'
```

**Детали для macOS.** `start-macos.sh` ставит зависимости через Homebrew, создаёт venv,
запускает setup и стартует uvicorn на порту `7860`, потому что AirPlay часто занимает
`7000`. Для Metal используется llama.cpp/Ollama. vLLM/SGLang работают только на CUDA/ROCm
и не запускаются на macOS. Модели только для MLX Odysseus не обслуживает.

</details>

### Нативно Windows

**Запуск одной командой** (создаёт venv, ставит зависимости, запускает setup, стартует
сервер; повторный запуск безопасен):

```powershell
git clone https://github.com/pewdiepie-archdaemon/odysseus.git
cd odysseus
powershell -ExecutionPolicy Bypass -File .\launch-windows.ps1
```

Или вручную:

```powershell
git clone https://github.com/pewdiepie-archdaemon/odysseus.git
cd odysseus
py -3.11 -m venv venv
venv\Scripts\Activate.ps1
pip install -r requirements.txt
python setup.py
python -m uvicorn app:app --host 127.0.0.1 --port 7000
```

Если `python` указывает на более старый интерпретатор, используйте `py -3.12` (или
другую установленную версию 3.11+) для шага с venv.

**Требования:** Python 3.11+. Ядро приложения (chat, agent, память, документы,
почта, календарь, deep research) работает полностью нативно. Для полноценных фоновых
загрузок моделей в **Cookbook** и инструмента shell у агента также установите
[Git for Windows](https://git-scm.com/download/win) (он даёт `bash.exe`).
Локальный *сервинг* vLLM/SGLang на GPU требует Linux/WSL2; для локальной модели на Windows
проще всего [Ollama](https://ollama.com/download) — укажите Odysseus адрес
`http://localhost:11434/v1` в Settings.

Откройте `http://localhost:7000`, войдите со сгенерированным паролем администратора
и настройте всё остальное в **Settings**.

## Заметки по безопасности
Odysseus — это self-hosted рабочее пространство с мощными локальными инструментами: доступ к shell, загрузка файлов, скачивание моделей, веб-исследования, интеграции с почтой/календарём и API-токены. Относитесь к нему как к консоли администратора.

- Держите `AUTH_ENABLED=true` для любого развёртывания, доступного по сети.
- Держите `LOCALHOST_BYPASS=false` вне локальной разработки.
- Используйте `SECURE_COOKIES=true`, когда Odysseus отдаётся по HTTPS через доверенный обратный прокси или приватный шлюз доступа.
- Не выставляйте его напрямую в публичный интернет без HTTPS и доверенного обратного прокси либо приватного слоя доступа.
- Держите `.env`, `data/`, `logs/`, базы данных, загрузки, сгенерированные медиа, резервные копии, файлы авторизации/сессий, API-ключи и токены моделей/провайдеров вне Git и приватных шар. По умолчанию они игнорируются.
- Проверьте `data/auth.json` после первого запуска: отключите открытую регистрацию, если только она вам осознанно не нужна, сделайте администратором лишь свою учётную запись и держите демо-/тестовые аккаунты без прав администратора.
- Не-администраторы по умолчанию не получают shell/Python/чтение-запись файлов, а маршруты и инструменты только для администраторов — такие как управление MCP, API-токены, вебхуки, сервинг моделей/cookbook, бэкап/vault и настройки приложения — закрыты правами администратора. Прочие возможности управляются привилегиями каждого пользователя, поэтому проверяйте привилегии каждого пользователя, прежде чем открывать развёртывание.
- Ротируйте любые API-ключи или токены, которые когда-либо попадали в общий чат, демо, скриншот или лог.
- Если вы включаете API-токены или вебхуки, создавайте отдельные токены под каждую интеграцию и удаляйте неиспользуемые.
- Предпочитайте привязывать ручные запуски для разработки к `127.0.0.1`; привязывайтесь к `0.0.0.0` только если осознанно хотите доступ из LAN / через обратный прокси.
- Держите ChromaDB, SearXNG, ntfy, Ollama, vLLM, llama.cpp, базы данных и «сырые» API моделей/провайдеров только во внутренней сети. Выставляйте наружу только аутентифицированную точку входа веб/API Odysseus через ваш доверенный прокси или приватный слой доступа.
- Перед публикацией форка выполните `git status --short` и убедитесь, что ни один приватный файл из `.env`, `data/`, `logs/`, загрузок, резервных копий или локальных баз данных не попал в индекс.

### Приватные или проксируемые развёртывания
Odysseus отдаёт обычный HTTP на своём порту приложения. Docker Compose по умолчанию привязывает Odysseus и сопутствующие сервисы к `127.0.0.1`, поэтому типичная продакшен-/приватная установка выглядит так:

1. Держите Odysseus на localhost, например `127.0.0.1:7000`.
2. Терминируйте HTTPS на доверенном обратном прокси или приватном шлюзе доступа.
3. Поставьте аутентифицированную точку входа веб/API Odysseus за этот слой.
4. Держите «сырые» порты сервисов и моделей только во внутренней сети.

Cloudflare Access, Tailscale, Caddy, nginx и Traefik — все вписываются в этот паттерн; ни один из них Odysseus не требует. Если ваш слой доступа достаёт до Odysseus на том же хосте, проксируйте на `http://127.0.0.1:7000` и держите `AUTH_ENABLED=true`, `LOCALHOST_BYPASS=false` и `SECURE_COOKIES=true`.

Типичные внутренние порты из стандартной настройки docs/compose:

| Порт | Сервис |
|---|---|
| `7000` | Сырой порт приложения Odysseus |
| `8080` | SearXNG |
| `8091` | ntfy |
| `8100` | Хост-порт ChromaDB для ручного/compose-доступа |
| `11434` | Ollama |
| `8000-8020` | Распространённые локальные API моделей/провайдеров |

## Вклад в проект
Помощь приветствуется. Лучшие точки входа — тестирование свежей установки, баги настройки
провайдеров, доводка мобильной версии/редактора, документация и небольшие точечные рефакторинги.
Смотрите [ROADMAP.ru.md](ROADMAP.ru.md) — там актуальный список «нужна помощь».

## Конфигурация
Большая часть настройки делается в самом приложении через `/setup` или **Settings**. Файл `.env`
используйте для значений по умолчанию уровня развёртывания и секретов, которые должны
присутствовать до первого запуска. Ключевые настройки:

| Переменная | По умолчанию | Описание |
|---|---|---|
| `LLM_HOST` | `localhost` | Ваш LLM-сервер (например, `llm-host.local:8000`) |
| `LLM_HOSTS` | — | Список через запятую для обнаружения моделей |
| `OPENAI_API_KEY` | — | Необязательный ключ OpenAI. Предпочтительнее добавлять провайдеров в приложении, кроме случаев предварительного засева. |
| `SEARXNG_INSTANCE` | `http://localhost:8080` | URL SearXNG. Docker переопределяет это на `http://searxng:8080`. |
| `SEARXNG_SECRET` | генерируется при первом запуске Docker | Необязательный секрет cookie/CSRF для SearXNG. Оставьте пустым, если не нужно его зафиксировать. |
| `APP_BIND` | `127.0.0.1` | Адрес привязки хоста для веб-интерфейса в Docker Compose. Используйте `0.0.0.0` только для осознанного доступа из LAN / через обратный прокси. |
| `APP_PORT` | `7000` | Порт хоста для веб-интерфейса в Docker Compose. |
| `AUTH_ENABLED` | `true` | Включить/выключить вход |
| `LOCALHOST_BYPASS` | `false` | Обход авторизации только для разработки, для loopback-запросов. Держите false для общих/сетевых развёртываний. |
| `SECURE_COOKIES` | `false` | Выставьте true, когда отдаёте Odysseus по HTTPS через доверенный прокси или приватный шлюз доступа. |
| `DATABASE_URL` | `sqlite:///./data/app.db` | Строка подключения к базе данных |
| `CHROMADB_HOST` | `localhost` | Хост ChromaDB для векторной памяти. Docker переопределяет это на `chromadb`. |
| `CHROMADB_PORT` | `8100` | Порт ChromaDB для ручных запусков на хосте. Docker переопределяет это на `8000`. |
| `EMBEDDING_URL` | — | OpenAI-совместимый endpoint эмбеддингов |

### Встроенные MCP-серверы (необязательная настройка)

Odysseus автоматически регистрирует несколько встроенных MCP-серверов при старте. Те из них, что основаны на npx (сейчас это browser-сервер, `@playwright/mcp`), запускаются только если их npm-пакет уже есть в локальном кэше npx. Если пакета в кэше нет, такой сервер пропускается с сообщением в логе старта, поясняющим, что делать, — поэтому свежая установка не зависает на многоминутной загрузке npm и не виснет, если системные зависимости Playwright отсутствуют.

Чтобы включить browser MCP (навигация по страницам, скриншоты, vision), выполните один раз:

```bash
npx -y @playwright/mcp@latest --version
```

Это установит `@playwright/mcp` плюс Playwright (~300 МБ суммарно). Перезапустите Odysseus, и сервер зарегистрируется при старте.

## Архитектура
```
app.py                   # точка входа FastAPI
core/      авторизация, база данных, middleware, константы
src/       llm_core, agent_loop, agent_tools, chat_processor, search/
routes/    chat, session, document, memory, model … эндпоинты
services/  docs, memory, search, hwfit (Cookbook) …
static/    index.html + app.js + style.css + js/ (модульный фронтенд)
docs/      страница-лендинг (index.html) + превью-клипы
```

## Данные
Все пользовательские данные живут в `data/` (в .gitignore): `app.db` (сессии, сообщения, документы),
`memory.json`, `presets.json`, `uploads/`, `personal_docs/`, `chroma/`, `settings.json`.

## История звёзд

<a href="https://www.star-history.com/?repos=pewdiepie-archdaemon%2Fodysseus&type=date&legend=top-left">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/chart?repos=pewdiepie-archdaemon/odysseus&type=date&theme=dark&legend=top-left" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/chart?repos=pewdiepie-archdaemon/odysseus&type=date&legend=top-left" />
   <img alt="Star History Chart" src="https://api.star-history.com/chart?repos=pewdiepie-archdaemon/odysseus&type=date&legend=top-left" />
 </picture>
</a>

## Лицензия
MIT — смотрите [LICENSE](LICENSE) и [ACKNOWLEDGMENTS.md](ACKNOWLEDGMENTS.md).

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
               ~^~  all aboard!  ~^~
       ~^~^~~^~^~~^~^~~^~^~~^~^~~^~^~~^~^~~^~^~
```
