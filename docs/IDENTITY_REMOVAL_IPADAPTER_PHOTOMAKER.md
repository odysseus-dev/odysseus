## Odstranění identity backendů (IP‑Adapter, PhotoMaker, PuLID)

Titan a Fugassa už **nepoužívají žádné identity backendy**. Portréty i scény jdou
výhradně přes **sd.cpp / sd-server** — jeden checkpoint, konzistentní styl.

### Co bylo odstraněno

- **Titan scheduler (titan-scheduler)**
  - IP‑Adapter routing + worker (`ip_adapter_worker.py`)
  - **PuLID routing + worker** (`pulid_worker.py`, `_pulid_call`)
  - Profily `ip-adapter` a **`pulid`** z `config.yaml`
  - `_identity_backend()` vždy vrací `None`

- **Host launch script**
  - Větve `ip-adapter` a **`pulid`** z host launch scriptu (`diffusion-launch.sh`)

- **Titan (app) / UI / MCP**
  - `ip-adapter-plus` a **`pulid`** jako podporované `ip_method`
  - MCP schema: odstraněny `reference_images`, `ip_method`, `ip_weight` (identity)
  - Image Studio: odstraněn IP‑Adapter mód

- **Fugassa**
  - Scény se generují **bez `reference_images`** — čistý txt2img přes sd.cpp
  - Odstraněno `_scene_reference_images()` z `asset_worker.py`
  - Odstraněny funkce pro identity ref payloady z `scene_character_context.py`
  - Cast zůstává v **textovém promptu** (`scene_characters`, appearance tags)

### Cílová architektura

| Asset | Backend | Identity |
|-------|---------|----------|
| Portrét | sd.cpp (NovaAnimeXL / …) | jen prompt |
| Scéna | sd.cpp (stejný styl) | jen prompt (cast block) |

**Další krok:** ControlNet přes sd.cpp — viz `docs/CONTROLNET.md`.

### ControlNet (implementováno)

- Slot `control: {type, image|path|b64, weight}` v ImageProposal
- Scheduler vkládá `control_image` + `control_strength` přes `<sd_cpp_extra_args>` do promptu (A1111 API)
- `diffusion-launch.sh` načte `--control-net` když existuje `${MODEL_DIR}/controlnet/sdxl_canny.safetensors`
- Fugassa scény: **two-pass** (txt2img → ControlNet canny z pass 1), viz `docs/CONTROLNET.md`


### Jak ověřit, že identity backendy neběží

- `titan-scheduler/config.yaml` — žádné profily `ip-adapter`, `pulid`
- `scheduler.py` — žádné `_ip_adapter_call`, `_pulid_call`, `/v1/pulid/generate`
- `diffusion-launch.sh` — jen `realistic`, `anime`, `pixelart`, `krea`
- Repo search: `ip-adapter`, `pulid`, `photomaker` → bez runtime výskytů

### Deploy / runtime checklist

1. Restart `titan-scheduler` (FastAPI na `:8150`)
2. Restart diffusion služby na `:8110` (sd-server, ne diffusers worker)
3. Ověřit, že `~/.config/diffusion-profile` není nastaven na `pulid` nebo `ip-adapter`
4. Vygenerovat test scénu ve Fugasse — scheduler log nesmí ukazovat VRAM swap na `pulid`
