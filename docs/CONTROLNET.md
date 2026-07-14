## ControlNet (sd.cpp)

ControlNet řídí **kompozici scény** (hrany, hloubka, póza) — ne identitu postav.
Vše běží přes stejný sd-server checkpoint jako portréty.

### Kontrakt

```json
{
  "control": {
    "type": "canny",
    "path": "/abs/or/relative.png",
    "weight": 0.35,
    "preprocess": true
  }
}
```

Alternativy k `path`: `b64`, `image` (data URL), `gallery_id` (Titan gallery).

Typy: `canny` (default), `depth`, `pose`, `openpose`, `raw` (mapa už hotová).

Canny preprocess: `cv2.Canny(low, high)` — default **100 / 200** (`TITAN_CANNY_LOW`, `TITAN_CANNY_HIGH`).

### Jak to teče

1. **ImageProposal** → `control` slot
2. **image_kernel** / **asset_gen** → resolve na b64 (+ canny preprocess v scheduleru)
3. **titan-scheduler** → vloží do promptu:
   `<sd_cpp_extra_args>{"control_image":"…","control_strength":0.35}</sd_cpp_extra_args>`
4. **sd-server** (A1111 `/sdapi/v1/txt2img` nebo `/img2img`) → sd.cpp aplikuje ControlNet

### Model (host)

ControlNet se načítá **při startu** sd-serveru:

```bash
mkdir -p ~/titan/data/sd-models/controlnet
cd ~/titan/scripts
python convert_sdxl_controlnet_for_sdcpp.py \
  ~/titan/data/sd-models/controlnet/sdxl_canny.diffusers.safetensors \
  ~/titan/data/sd-models/controlnet/sdxl_canny.safetensors
```

Restart diffusion služby. V logu launch scriptu uvidíš `controlnet: … (CPU offload)`.

### Fugassa scény (default two-pass)

Každá **scéna** = dvě generace v jednom jobu:

| Pass | Co | Prompt | Parametry |
|------|-----|--------|-----------|
| **1** | txt2img | kompozice — postavy, akce, interiér | z `chat_defaults` profilu (`quality: high`) |
| **2** | img2img + CN canny | **refinement** — materiály, světlo, styl (bez shot tagů) | nižší CFG/steps + CN weight |

Pass 2 bere init z pass 1 (`FUGASSA_SCENE_PASS2_STRENGTH`, default **0.35**) a Canny mapu z pass 1.

```bash
# CN síla pass 2 (default 0.35 — refinement, ne nová kompozice)
export FUGASSA_SCENE_CONTROLNET_WEIGHT=0.35

# img2img denoise pass 2 (default 0.35; 0 = jen CN bez img2img)
export FUGASSA_SCENE_PASS2_STRENGTH=0.35

# volitelně přepsat generační parametry pass 2 (jinak profil − offset)
export FUGASSA_SCENE_PASS2_STEPS=22
export FUGASSA_SCENE_PASS2_CFG=5.5

# vypnout two-pass (debug — jen pass 1)
export FUGASSA_SCENE_SINGLE_PASS=1
```

Chat / Image Studio CN: `TITAN_CONTROLNET_WEIGHT` (default 0.55).

Portréty: pořád **1× generace** bez ControlNetu.

### Ověření

```bash
cd titan-scheduler && python test_payload_helpers.py
cd titan && pytest tests/test_control_net.py titan/fugassa/tests/test_asset_gen_two_pass.py -q
```
