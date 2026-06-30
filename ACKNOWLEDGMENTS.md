# Благодарности

Odysseus стоит на плечах множества проектов с открытым исходным кодом. Этот файл благодарит проекты, чей код, ресурсы или дизайн включены в этот репозиторий или адаптированы из него, и указывает их лицензии.

Если вы считаете, что здесь есть неточная атрибуция или что-то пропущено, пожалуйста, откройте issue — это будет исправлено оперативно.

---

## Адаптированный / заимствованный код

Части этого проекта были адаптированы из других репозиториев с открытым исходным кодом.
Их оригинальные авторы сохраняют авторское право над адаптированными частями, на условиях лицензий, указанных ниже.

Указанные ниже источники находятся под либеральными лицензиями (MIT / Apache-2.0), которые разрешают такое использование при условии сохранения оригинальных уведомлений об авторском праве и лицензии.
Полные тексты лицензий хранятся в [`licenses/`](licenses/).

- **[opencode](https://github.com/anomalyco/opencode)** — ИИ-агент для кодирования
  с открытым исходным кодом (изначально [opencode-ai/opencode](https://github.com/opencode-ai/opencode),
  архивирован в сентябре 2025; теперь поддерживается в `anomalyco/opencode`). Авторское право © авторы
  opencode. **Лицензия MIT.** Адаптировано для паттернов agent-loop / tool-execution
  и концепций UI.
- **[llmfit](https://github.com/AlexsJones/llmfit)** от **Alex Jones** — движок
  за функцией Cookbook: загрузка / обслуживание / «Что подходит?» для моделей.
  Авторское право © Alex Jones. **Лицензия MIT.** Адаптировано в `services/hwfit/`
  (определение аппаратуры, оценка соответствия с учётом квантизации, каталог моделей),
  `routes/cookbook_*.py`, `routes/hwfit_routes.py`, `static/js/cookbook*.js`
  и `scripts/odysseus-cookbook`.
- **[Tongyi DeepResearch](https://github.com/Alibaba-NLP/DeepResearch)** от
  **Alibaba-NLP / Tongyi Lab** — многошаговый пайплайн глубокого ИИ-исследования.
  Авторское право © Alibaba-NLP / Tongyi Lab. **Apache-2.0.** Адаптировано для функции
  Deep Research Odysseus (`services/research/`, `src/research_handler.py`,
  `routes/research_routes.py`, `services/search/`). Полный текст в
  [`licenses/DeepResearch-Apache-2.0.txt`](licenses/DeepResearch-Apache-2.0.txt).

---

## Связанные через Docker Compose

Эти сервисы подтягиваются как образы проектом `docker-compose.yml`
и работают рядом с Odysseus при `docker compose up`. Они не модифицируются —
только компонуются.

| Сервис | Образ | Назначение | Лицензия |
|---|---|---|---|
| [SearXNG](https://github.com/searxng/searxng) | `searxng/searxng:2026.5.31-7159b8aed` (фиксированный тег; см. compose) | Метапоисковый бэкенд по умолчанию | AGPL-3.0 |
| [ChromaDB](https://github.com/chroma-core/chroma) | `chromadb/chroma:latest` | Векторное хранилище для памяти / RAG | Apache-2.0 |
| [ntfy](https://github.com/binwiederhier/ntfy) | `binwiederhier/ntfy` | Push-уведомления (самостоятельные напоминания) | Apache-2.0 / GPL-2.0 |

## Включённые фронтенд-библиотеки

Включены в `static/lib/` и подаются напрямую:

| Библиотека | Назначение | Лицензия |
|---|---|---|
| [highlight.js](https://github.com/highlightjs/highlight.js) v11.9.0 | Подсветка синтаксиса кода | BSD-3-Clause |
| [SheetJS / xlsx](https://github.com/SheetJS/sheetjs) (`xlsx.full.min.js`) | Чтение/запись электронных таблиц (`.xlsx`) | Apache-2.0 |
| [docx](https://github.com/dolanmiu/docx) (`docx.umd.min.js`) | Генерация документов `.docx` | MIT |
| [mammoth.js](https://github.com/mwilliamson/mammoth.js) | Конвертация `.docx` → HTML | BSD-2-Clause |
| [html2pdf.js](https://github.com/eKoopmans/html2pdf.js) | Экспорт HTML → PDF (включает jsPDF + html2canvas) | MIT |
| [jsPDF](https://github.com/parallax/jsPDF) (включён в html2pdf) | Генерация PDF | MIT |
| [html2canvas](https://github.com/niklasvh/html2canvas) (включён в html2pdf) | Растеризация DOM → canvas | MIT |
| [node-qrcode](https://github.com/soldair/node-qrcode) (`qrcode.min.js`) | Рендеринг QR-кодов (настройка 2FA) | MIT |

## Фронтенд-библиотеки, загружаемые во время выполнения (CDN)

Ссылаются с `cdn.jsdelivr.net` / `cdnjs.cloudflare.com` во время выполнения — не включены:

| Библиотека | Назначение | Лицензия |
|---|---|---|
| [KaTeX](https://github.com/KaTeX/KaTeX) 0.16.22 | Набор математических формул | MIT |
| [Mermaid](https://github.com/mermaid-js/mermaid) 11 | Диаграммы из текста | MIT |
| [Pyodide](https://github.com/pyodide/pyodide) 0.27.5 | Python-рантайм в браузере | MPL-2.0 |
| [PDFObject](https://github.com/pipwerks/PDFObject) 2.1.1 | Встроенная вставка PDF | MIT |

## Шрифты

Включены в `static/fonts/`:

| Шрифт | Лицензия | Автор |
|---|---|---|
| [Fira Code](https://github.com/tonsky/FiraCode) | SIL Open Font License 1.1 | Nikita Prokopov и авторы |
| [Inter](https://github.com/rsms/inter) | SIL Open Font License 1.1 | Rasmus Andersson |
| [GohuFont](https://font.gohu.org/) (`fonts/custom/GohuFont.ttf`) | WTFPL | Hugo Chargois |
| [OpenDyslexic](https://opendyslexic.org/) (`fonts/OpenDyslexic-{Regular,Bold}.woff2`) | SIL Open Font License 1.1 ([`licenses/OpenDyslexic-OFL.txt`](licenses/OpenDyslexic-OFL.txt)) | Abbie Gonzalez |

## Python-зависимости

Основные (`requirements.txt`) и опциональные (`requirements-optional.txt`):

| Пакет | Лицензия |
|---|---|
| FastAPI | MIT |
| Uvicorn | BSD-3-Clause |
| python-multipart | Apache-2.0 |
| python-dotenv | BSD-3-Clause |
| HTTPX | BSD-3-Clause |
| Pydantic / pydantic-settings | MIT |
| SQLAlchemy | MIT |
| pypdf | BSD-3-Clause |
| BeautifulSoup4 | MIT |
| charset-normalizer | MIT |
| NumPy | BSD-3-Clause |
| ChromaDB (chromadb-client) | Apache-2.0 |
| fastembed | Apache-2.0 |
| youtube-transcript-api | MIT |
| markdown | BSD-3-Clause |
| icalendar | BSD-2-Clause |
| caldav | GPL-3.0-or-later OR Apache-2.0 (двойная; используется под Apache-2.0) |
| cryptography | Apache-2.0 / BSD-3-Clause |
| bcrypt | Apache-2.0 |
| MCP (Model Context Protocol SDK) | MIT |
| pyotp | MIT |
| qrcode[pil] | BSD-3-Clause |
| croniter | MIT |
| pytest / pytest-asyncio | MIT / Apache-2.0 |
| duckduckgo-search (опциональный) | MIT |
| markitdown (опциональный — извлечение текста Office/EPUB) | MIT |
| **PyMuPDF** *(опциональный — только для заполнения форм)* | **AGPL-3.0** — см. примечание ниже |

## Сопутствующие сервисы (взаимодействуемые, не включённые)

Odysseus общается с ними через сеть/API. Они **не** распространяются
с этим проектом; их лицензии не связывают эту кодовую базу, но они заслуживают
упоминания:

- [Ollama](https://github.com/ollama/ollama) — локальное обслуживание моделей (MIT)
- [Radicale](https://github.com/Kozea/Radicale) — сервер CardDAV/CalDAV (GPL-3.0)
- [Dovecot](https://www.dovecot.org/) — сервер IMAP
- [isync / mbsync](https://isync.sourceforge.io/) — синхронизация почтовых ящиков IMAP (GPL-2.0)
- [tmux](https://github.com/tmux/tmux) — мультиплексор терминалов; Cookbook вызывает его на Linux/macOS для фоновой загрузки и обслуживания моделей (ISC)
- [OpenSSH](https://www.openssh.com/) (`ssh`, `ssh-keygen`, `ssh-copy-id`) — Cookbook вызывает его для управления удалёнными серверами моделей и provision ключей (либеральная BSD-подобная)
- Провайдеры моделей/API: Anthropic, OpenAI, Google (Gemini), DuckDuckGo

---

### Примечания по совместимости лицензий (для выбора собственной лицензии репозитория)

**Ядро поставляется полностью либеральным** (совместимым с MIT), поэтому две проблемы
copyleft из прошлого разрешены:

- **Извлечение текста PDF** теперь использует **`pypdf`** (BSD-3-Clause), а **определение
  кодировки** использует **`charset-normalizer`** (MIT). chardet (LGPL-2.1) был
  полностью удалён.
- **PyMuPDF (AGPL-3.0)** больше не является основной зависимостью. Он **опциональный** и
  используется *только* функцией заполнения PDF-форм (`src/pdf_forms.py` и эндпоинты форм
  в `routes/document_routes.py`), лениво импортируется и указан в
  `requirements-optional.txt`. Ядро на MIT работает без него. Если вы решите
  установить его, сетевая клауза AGPL применяется к *той функции* для вашего
  развёртывания (Artifex также продаёт коммерческую лицензию PyMuPDF, снимающую это ограничение).
- **`caldav`** (Python-библиотека) имеет **двойную лицензию GPL-3.0-or-later OR Apache-2.0**.
  Odysseus использует его под **Apache-2.0**, что является либеральной и совместимой с MIT.
- **`markitdown`** (Microsoft) имеет **MIT** и используется только как *опциональная* зависимость для извлечения
  текста Office/EPUB (`src/markitdown_runtime.py`), лениво импортируется с грациозным fallback'ом — ядро на MIT работает без
  него. Облачный extras `az-doc-intel` намеренно **не устанавливается**, сохраняя извлечение полностью локальным.

---

## Благодарности

Большая часть кода Odysseus была написана *с помощью* ИИ-моделей, а не только человеком.
Проект не существовал бы без них — честь, кому честь:

- **gpt-oss-120b** — легенда, начавшая этот проект.
- **Qwen3-235B**
- **DeepSeek V3.1 · DeepSeek V4 Pro · DeepSeek V4 Flash**
- **Claude** (Anthropic)
- **Codex** (OpenAI)
- Друзьям, за помощь в отладке.
