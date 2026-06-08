# Odysseus
───────────────────────────────────────────────
 ⊹ ࣪ ˖ ૮( ˶ᵔ ᵕ ᵔ˶ )っ  Odysseus vers. 1.0
───────────────────────────────────────────────

![Odysseus](docs/odysseus.jpg)

Um workspace de IA self-hosted — o equivalente caseiro da experiência de interface que você tem no ChatGPT e no Claude. Mas com mais charme e diversão. Rodando no seu próprio hardware, com seus próprios dados — local-first, privacidade em primeiro lugar, e sem trojan.

## Funcionalidades
  - **Chat** -- converse com qualquer modelo local ou via API; adicionar novos é super simples.<br>　<sub>vLLM · llama.cpp · Ollama · OpenRouter · OpenAI</sub>
  - **Agente** -- dê ferramentas a ele e deixe-o executar a tarefa inteira sozinho.<br>　<sub>baseado em [opencode](https://github.com/anomalyco/opencode) · MCP · web · arquivos · shell · skills · memória</sub>
  - **Cookbook** -- Escaneia seu hardware, recomenda modelos, clique para baixar e servir... fácil!<br>　<sub>baseado em [llmfit](https://github.com/AlexsJones/llmfit) · VRAM-aware · GGUF / FP8 / AWQ · pontuação de compatibilidade · vLLM / llama.cpp serving</sub>
  - **Pesquisa Profunda** -- execuções em múltiplas etapas que coletam, leem e sintetizam fontes em um relatório visual organizado.<br>　<sub>adaptado de [Tongyi DeepResearch](https://github.com/Alibaba-NLP/DeepResearch)</sub>
  - **Comparar** -- uma ferramenta divertida para comparar modelos lado a lado. Teste completamente às cegas, sem viés!<br>　<sub>multi-modelo · teste cego · síntese</sub>
  - **Documentos** -- VOCÊ escreve o texto, a IA está lá para auxiliar, não o contrário.<br>　<sub>editor multi-abas · markdown · HTML · CSV · realce de sintaxe · edições com IA · sugestões</sub>
  - **Memória / Skills** -- Memória e habilidades persistentes; seu agente evolui com o tempo conforme entende melhor você e suas tarefas!<br>　<sub>ChromaDB · fastembed (ONNX) · busca vetorial + por palavras-chave · import/export</sub>
  - **E-mail** -- Caixa de entrada IMAP/SMTP com triagem por IA integrada: lembretes de urgência, auto-tag, auto-resumo, rascunhos de resposta automática, anti-spam automático.<br>　<sub>IMAP · SMTP · roteamento por conta · compatível com CalDAV</sub>
  - **Notas & Tarefas** -- Notas rápidas com lembretes, lista de afazeres e tarefas agendadas que o agente pode executar.<br>　<sub>avisos de nota · checklist · tarefas estilo cron · canais ntfy / navegador / e-mail</sub>
  - **Calendário** -- Calendário local-first com sincronização CalDAV para Radicale / Nextcloud / Apple / Fastmail.<br>　<sub>pull CalDAV · import/export .ics · cores por calendário · compatível com agente</sub>
  - **Funciona no celular** -- tem ótima aparência e roda bem no seu telefone, não só no desktop.<br>　<sub>responsivo · instalável (PWA) · gestos de toque</sub>
  - **Extras** -- mais para explorar, ficamos felizes se você experimentar!<br>　<sub>editor de imagens · editor de tema · upload de arquivos (visão + PDF) · busca na web · presets · sessões · 2FA</sub>

## Demo
Um tour completo com hover-to-play vive na página inicial (`docs/index.html`).

<details>
<summary>Screenshots / clipes</summary>

### Chat & Agentes
![Chat & Agents](docs/chat.gif)
### Pesquisa Profunda
![Deep Research](docs/research.gif)
### Comparar
![Compare](docs/compare.gif)
### Documentos
![Documents](docs/document.gif)
### Notas & Tarefas
![Notes & Tasks](docs/notes.gif)

</details>

## Início Rápido

Os padrões funcionam imediatamente: clone, rode e configure modelos/busca/e-mail dentro de **Settings**. Edite o `.env` apenas para substituições em nível de implantação como `APP_BIND`, `APP_PORT`, `AUTH_ENABLED`, `DATABASE_URL` ou uma senha de admin pré-configurada.

Na primeira execução, o Odysseus cria uma conta admin (`admin`, a menos que `ODYSSEUS_ADMIN_USER` esteja definido) e exibe uma senha temporária no terminal. Para instalações via Docker, a mesma linha aparece em `docker compose logs odysseus`. Use-a no primeiro login e então troque-a em **Settings**.

Quer contribuir? Veja [CONTRIBUTING.md](CONTRIBUTING.md) para configuração, testes e diretrizes de pull request.

### Docker (recomendado)
```bash
git clone https://github.com/pewdiepie-archdaemon/odysseus.git
cd odysseus
cp .env.example .env       # opcional, mas recomendado para padrões explícitos
docker compose up -d --build
```
Abra `http://localhost:7000` quando os containers estiverem saudáveis. O Docker Compose vincula a interface web a `127.0.0.1` por padrão. Se a porta estiver em uso, defina `APP_PORT=7001` no `.env` e recrie o container. Defina `APP_BIND=0.0.0.0` apenas quando você intencionalmente quiser acesso por LAN/proxy reverso.

### Linux / macOS nativo
```bash
git clone https://github.com/pewdiepie-archdaemon/odysseus.git
cd odysseus
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python setup.py
python -m uvicorn app:app --host 127.0.0.1 --port 7000
```
Requisitos: Python 3.11+. O Cookbook também precisa de `tmux` para downloads e serving de modelos em segundo plano. Use `--host 0.0.0.0` apenas quando você intencionalmente quiser acesso por LAN/proxy reverso.

### Apple Silicon
O Docker no macOS não consegue usar a GPU Metal. Para o Cookbook com aceleração GPU em um Mac com chip M, rode o Odysseus nativamente:

```bash
git clone https://github.com/pewdiepie-archdaemon/odysseus.git
cd odysseus
./start-macos.sh
```

Abre em `http://127.0.0.1:7860`. Para expor ao seu celular via LAN confiável/VPN como o Tailscale, vincule a todas as interfaces:

```bash
ODYSSEUS_HOST=0.0.0.0 ./start-macos.sh
# depois abra http://<tailscale-ip>:7860
```

Mantenha a autenticação ativada ao vincular fora do loopback e não exponha esta porta diretamente à internet pública. Para criar um wrapper de app clicável:

```bash
./build-macos-app.sh
```

<details>
<summary>Notas sobre Cookbook, GPU, Ollama e solução de problemas</summary>

**Serviços integrados do Docker.** O Compose inicia o Odysseus, ChromaDB, SearXNG e ntfy. O Odysseus e as portas dos serviços integrados se vinculam a `127.0.0.1` por padrão, então são acessíveis pelo host mas não expostos à sua LAN/internet pública, a menos que você opte por isso.

**Armazenamento do Cookbook no Docker.** Os downloads ficam em `./data/huggingface` (`~/.cache/huggingface` no container). CLIs Python e engines de serving instalados pelo Cookbook ficam em `./data/local` (`~/.local` no container), então sobrevivem à recriação do container.

**Servidores remotos.** Em **Cookbook -> Settings -> Servers**, gere a chave SSH do Odysseus e adicione a chave pública ao `~/.ssh/authorized_keys` do servidor remoto. Do host você também pode executar:

```bash
ssh-copy-id -i data/ssh/id_ed25519.pub usuario@servidor
```

**Overlay Docker GPU NVIDIA.** Usuários sem GPU podem pular esta seção. `scripts/check-docker-gpu.sh` diagnostica o passthrough de GPU e pode, opcionalmente, instalar o runtime do host ou atualizar o `.env`. O Cookbook só detecta GPUs que o Docker expõe ao container — se o runtime do host não estiver configurado, o Cookbook enxerga a iGPU, outra placa, ou CPU em vez da sua GPU NVIDIA.

```bash
# Diagnóstico somente leitura (padrão — não instala nada, nunca edita o .env):
scripts/check-docker-gpu.sh

# Exibe comandos de instalação específicos do SO sem executá-los:
scripts/check-docker-gpu.sh --print-install-commands

# Instala NVIDIA Container Toolkit no Ubuntu/Debian (requer sudo):
scripts/check-docker-gpu.sh --install-nvidia-toolkit

# Escreve COMPOSE_FILE no .env (somente quando o passthrough de GPU está confirmado):
scripts/check-docker-gpu.sh --enable-nvidia-overlay

# Configuração assistida completa — instala toolkit e ativa overlay se o passthrough funcionar:
scripts/check-docker-gpu.sh --install-nvidia-toolkit --enable-nvidia-overlay
```

Notas de segurança:
- O app nunca instala o runtime de GPU do host automaticamente.
- O app nunca edita o `.env` automaticamente.
- O `.env` só é modificado quando `--enable-nvidia-overlay` é explicitamente passado, e somente após o passthrough de GPU ser confirmado. `--yes` pula prompts mas não bypassa a verificação de passthrough.
- Backups `.env.bak.*` criados por `--enable-nvidia-overlay` são ignorados pelo Git e pelo contexto de build do Docker.

Para ativar manualmente sem o script, adicione isso ao `.env`:

```bash
COMPOSE_FILE=docker-compose.yml:docker/gpu.nvidia.yml
```

**AMD / ROCm.** O passthrough de GPU AMD não é automatizado. Adicione manualmente:

```bash
COMPOSE_FILE=docker-compose.yml:docker/gpu.amd.yml
```

Para suporte a GPU NVIDIA/AMD, leia também os comentários no arquivo de overlay selecionado: `docker/gpu.nvidia.yml` ou `docker/gpu.amd.yml`.

Verifique após ativar qualquer overlay:

```bash
docker compose exec odysseus nvidia-smi -L   # NVIDIA
docker compose exec odysseus rocm-smi        # AMD
```

> **Passthrough de GPU ≠ llama.cpp CUDA.** `nvidia-smi` passando dentro do container confirma o acesso Docker à GPU, mas o llama.cpp também precisa de `cudart` e do CUDA Toolkit em tempo de execução. Se os logs do Cookbook mostrarem `Unable to find cudart library`, `Could NOT find CUDAToolkit`, `CUDA Toolkit not found`, ou tensores/camadas atribuídos à CPU, isso é um problema de build do Cookbook/llama.cpp — não uma falha de passthrough do Docker. Reinstale a engine de serving via **Cookbook → Dependencies** para obter um build com suporte a CUDA.

**Ollama com Docker.** Se o Ollama rodar no host, adicione este endpoint nas Settings:

```text
http://host.docker.internal:11434/v1
```

O Ollama deve ouvir fora da sua própria interface de loopback:

```bash
OLLAMA_HOST=0.0.0.0:11434 ollama serve
```

**Verificações úteis.**

```bash
docker compose ps
docker compose logs --tail=120 odysseus
docker compose logs odysseus | grep -E 'ChromaDB|MemoryVectorStore|DEGRADED'
```

**Detalhes do macOS.** `start-macos.sh` instala dependências do Homebrew, cria o venv, roda o setup e inicia o uvicorn na porta `7860` porque o AirPlay frequentemente ocupa a `7000`. Usa llama.cpp/Ollama para Metal. vLLM/SGLang são exclusivos para CUDA/ROCm e não rodam no macOS. Modelos somente-MLX não são servidos pelo Odysseus.

</details>

### Windows nativo

**Launcher de um comando** (cria o venv, instala dependências, roda o setup, inicia o servidor; seguro para reexecutar):

```powershell
git clone https://github.com/pewdiepie-archdaemon/odysseus.git
cd odysseus
powershell -ExecutionPolicy Bypass -File .\launch-windows.ps1
```

Ou faça manualmente:

```powershell
git clone https://github.com/pewdiepie-archdaemon/odysseus.git
cd odysseus
py -3.11 -m venv venv
venv\Scripts\Activate.ps1
pip install -r requirements.txt
python setup.py
python -m uvicorn app:app --host 127.0.0.1 --port 7000
```

Se `python` aponta para um interpretador mais antigo, use `py -3.12` (ou outra versão 3.11+ instalada) no passo do venv.

**Requisitos:** Python 3.11+. O app principal (chat, agente, memória, documentos, e-mail, calendário, pesquisa profunda) roda totalmente nativo. Para downloads de modelos em segundo plano pelo **Cookbook** e a ferramenta de shell do agente, instale também o [Git for Windows](https://git-scm.com/download/win) (fornece o `bash.exe`). O *serving* local de GPU com vLLM/SGLang precisa de Linux/WSL2; para um modelo local no Windows, [Ollama](https://ollama.com/download) é o caminho mais fácil — aponte o Odysseus para `http://localhost:11434/v1` nas Settings.

Abra `http://localhost:7000`, faça login com a senha de admin gerada e configure todo o resto dentro de **Settings**.

## Notas de Segurança
O Odysseus é um workspace self-hosted com ferramentas locais poderosas: acesso ao shell, upload de arquivos, download de modelos, pesquisa web, integrações de e-mail/calendário e tokens de API. Trate-o como um console de administração.

- Mantenha `AUTH_ENABLED=true` para qualquer implantação acessível pela rede.
- Mantenha `LOCALHOST_BYPASS=false` fora do desenvolvimento local.
- Use `SECURE_COOKIES=true` quando o Odysseus for servido via HTTPS por um proxy reverso confiável ou gateway de acesso privado.
- Não o exponha diretamente à internet pública sem HTTPS e um proxy reverso confiável ou camada de acesso privado.
- Mantenha `.env`, `data/`, `logs/`, bancos de dados, uploads, mídia gerada, backups, arquivos de auth/sessão, chaves de API e tokens de modelo/provedor fora do Git e de compartilhamentos privados. Eles são ignorados por padrão.
- Revise `data/auth.json` após o primeiro boot: desative o cadastro aberto a menos que você intencionalmente o queira, torne apenas sua própria conta admin e mantenha contas de demonstração/teste como não-admin.
- Usuários não-admin não têm shell/Python/leitura-e-escrita de arquivos por padrão, e rotas/ferramentas exclusivas de admin como gerenciamento de MCP, tokens de API, webhooks, serving de modelos/cookbook, backup/vault e configurações do app são restritas a admin. Outras funcionalidades são controladas por privilégios por usuário, então revise os privilégios de cada usuário antes de expor uma implantação.
- Rotacione quaisquer chaves de API ou tokens que foram colados em um chat compartilhado, demo, screenshot ou log.
- Se você habilitar tokens de API ou webhooks, crie tokens separados por integração e delete os não utilizados.
- Prefira vincular execuções de desenvolvimento manual a `127.0.0.1`; vincule a `0.0.0.0` apenas quando você intencionalmente quiser acesso por LAN/proxy reverso.
- Mantenha ChromaDB, SearXNG, ntfy, Ollama, vLLM, llama.cpp, bancos de dados e APIs brutas de modelo/provedor somente internos. Exponha apenas o entrypoint web/API autenticado do Odysseus através do seu proxy confiável ou camada de acesso privado.
- Antes de publicar um fork, execute `git status --short` e confirme que nenhum arquivo privado de `.env`, `data/`, `logs/`, uploads, backups ou bancos de dados locais está staged.

### Implantações privadas ou via proxy
O Odysseus serve HTTP puro na sua porta de app. O Docker Compose vincula o Odysseus e os serviços integrados a `127.0.0.1` por padrão, então uma configuração típica de produção/privada é:

1. Mantenha o Odysseus no localhost, por exemplo `127.0.0.1:7000`.
2. Termine o HTTPS em um proxy reverso confiável ou gateway de acesso privado.
3. Coloque o entrypoint web/API autenticado do Odysseus atrás dessa camada.
4. Mantenha as portas brutas de serviço e modelo somente internas.

Cloudflare Access, Tailscale, Caddy, nginx e Traefik se encaixam nesse padrão; nenhum é exigido pelo Odysseus. Se sua camada de acesso alcança o Odysseus no mesmo host, faça proxy para `http://127.0.0.1:7000` e mantenha `AUTH_ENABLED=true`, `LOCALHOST_BYPASS=false` e `SECURE_COOKIES=true`.

Portas somente internas da configuração padrão de docs/compose:

| Porta | Serviço |
|---|---|
| `7000` | Porta bruta do app Odysseus |
| `8080` | SearXNG |
| `8091` | ntfy |
| `8100` | Porta do host ChromaDB para acesso manual/compose |
| `11434` | Ollama |
| `8000-8020` | APIs comuns de modelo/provedor local |

## Contribuindo
Ajuda é bem-vinda. Os melhores pontos de entrada são testes de instalação limpa, bugs de configuração de provedores, polimento mobile/editor, documentação e refatorações pequenas e focadas. Veja [ROADMAP.md](ROADMAP.md) para a lista atual de tarefas abertas.

## Configuração
A maior parte da configuração é feita dentro do app com `/setup` ou **Settings**. Use o `.env` para padrões em nível de implantação e segredos que você quer que estejam presentes antes do primeiro boot. Configurações principais:

| Variável | Padrão | Descrição |
|---|---|---|
| `LLM_HOST` | `localhost` | Seu servidor LLM (ex.: `llm-host.local:8000`) |
| `LLM_HOSTS` | -- | Lista separada por vírgulas para descoberta de modelos |
| `OPENAI_API_KEY` | -- | Chave OpenAI opcional. Prefira adicionar provedores no app a menos que esteja pré-configurando. |
| `SEARXNG_INSTANCE` | `http://localhost:8080` | URL do SearXNG. O Docker substitui para `http://searxng:8080`. |
| `SEARXNG_SECRET` | gerado no primeiro boot do Docker | Segredo opcional de cookie/CSRF do SearXNG. Deixe em branco a menos que precise fixá-lo. |
| `APP_BIND` | `127.0.0.1` | Endereço de bind do host Docker Compose para a interface web. Use `0.0.0.0` somente para acesso intencional por LAN/proxy reverso. |
| `APP_PORT` | `7000` | Porta do host Docker Compose para a interface web. |
| `AUTH_ENABLED` | `true` | Habilitar/desabilitar login |
| `LOCALHOST_BYPASS` | `false` | Bypass de auth somente para desenvolvimento para requisições de loopback. Mantenha false para implantações compartilhadas/em rede. |
| `SECURE_COOKIES` | `false` | Defina como true ao servir o Odysseus via HTTPS em um proxy confiável ou gateway de acesso privado. |
| `DATABASE_URL` | `sqlite:///./data/app.db` | String de conexão do banco de dados |
| `CHROMADB_HOST` | `localhost` | Host do ChromaDB para memória vetorial. O Docker substitui para `chromadb`. |
| `CHROMADB_PORT` | `8100` | Porta do ChromaDB para execuções manuais no host. O Docker substitui para `8000`. |
| `EMBEDDING_URL` | -- | Endpoint de embeddings compatível com OpenAI |

### Servidores MCP integrados (configuração opcional)

O Odysseus auto-registra alguns servidores MCP integrados na inicialização. Os baseados em npx (atualmente o servidor de navegador, `@playwright/mcp`) só iniciam quando o pacote npm já está no cache local do npx. Se um pacote não estiver em cache, aquele servidor é ignorado com uma mensagem de log explicando o que fazer, então uma instalação limpa não fica bloqueada num download npm de vários minutos nem trava se as dependências do sistema do Playwright estiverem faltando.

Para habilitar o MCP de navegador (navegação de páginas, screenshots, visão), execute uma vez:

```bash
npx -y @playwright/mcp@latest --version
```

Isso instala `@playwright/mcp` mais o Playwright (~300MB no total). Reinicie o Odysseus e o servidor se registrará na inicialização.

## Arquitetura
```
app.py                   # ponto de entrada FastAPI
core/      auth, database, middleware, constants
src/       llm_core, agent_loop, agent_tools, chat_processor, search/
routes/    chat, session, document, memory, model … endpoints
services/  docs, memory, search, hwfit (Cookbook) …
static/    index.html + app.js + style.css + js/ (front-end modular)
docs/      página inicial (index.html) + clipes de preview
```

## Dados
Todos os dados do usuário ficam em `data/` (ignorado pelo git): `app.db` (sessões, mensagens, documentos), `memory.json`, `presets.json`, `uploads/`, `personal_docs/`, `chroma/`, `settings.json`.

## Histórico de Stars

<a href="https://www.star-history.com/?repos=pewdiepie-archdaemon%2Fodysseus&type=date&legend=top-left">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/chart?repos=pewdiepie-archdaemon/odysseus&type=date&theme=dark&legend=top-left" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/chart?repos=pewdiepie-archdaemon/odysseus&type=date&legend=top-left" />
   <img alt="Star History Chart" src="https://api.star-history.com/chart?repos=pewdiepie-archdaemon/odysseus&type=date&legend=top-left" />
 </picture>
</a>

## Licença
MIT -- veja [LICENSE](LICENSE) e [ACKNOWLEDGMENTS.md](ACKNOWLEDGMENTS.md).

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
               ~^~  todos a bordo!  ~^~
       ~^~^~~^~^~~^~^~~^~^~~^~^~~^~^~~^~^~~^~^~
```
