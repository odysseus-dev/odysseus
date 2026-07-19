# Getting Local Voice (STT/TTS) Working in Odysseus on Python 3.14

*A debugging story, for anyone else who tries this and hits the same walls.*

## The goal

Odysseus is a self-hosted personal AI assistant — Docker + FastAPI, running local Ollama models (`qwen3:8b`, Gemma). The one thing missing was voice: talk to it with a microphone, have it talk back, all **fully local** — no cloud STT/TTS APIs, no browser Web Speech API fallback.

The plan looked simple on paper:

- **STT** — [faster-whisper](https://github.com/SYSTRAN/faster-whisper) (Whisper on CTranslate2), model `base`, CPU.
- **TTS** — [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M), voice `af_heart`, CPU.

It was not simple in practice. Here's every wall we hit, in the order we hit them, and how each one actually got fixed.

## Wall 1: Kokoro silently refuses to run without a GPU

The existing `_KokoroPipeline` class had this near the top of its init:

```python
if not torch.cuda.is_available():
    logger.warning("CUDA not available for Kokoro TTS")
    return
```

No CUDA, no Kokoro — full stop, no CPU fallback. On a Ryzen CPU-only box, this means TTS reports "not available" forever, regardless of whether the packages are installed correctly.

Kokoro is an 82M-parameter model. It runs on CPU just fine. The fix mirrored a pattern the STT service already used for `faster-whisper`: try CUDA, fall back to CPU silently.

```python
use_cuda = torch.cuda.is_available()
self.device = torch.device("cuda:0") if use_cuda else torch.device("cpu")
```

Same treatment for the actual synthesis call — only wrap it in `torch.cuda.device(...)` when a CUDA device is actually in play.

## Wall 2: the spaCy/blis/thinc trap

Kokoro doesn't do text-to-phoneme conversion itself — it hands that to a separate library, [misaki](https://github.com/hexgrad/misaki). For English, misaki's G2P **hard-imports spaCy** to POS-tag the input (needed to disambiguate words like "read" — present vs. past tense — by grammatical context). That's not a Kokoro quirk; it's baked into misaki's own source.

spaCy pulls in `thinc`, which pulls in `blis` — both are C-extension packages. On a very new Python (3.14), prebuilt wheels for these don't reliably exist yet, so `pip install spacy` as part of a *combined* dependency resolve (alongside kokoro, torch, etc.) made pip fall back to compiling `blis` from source — which failed on Cython errors.

The fix, found by testing in a disposable container rather than guessing:

1. Install `spacy` **standalone, first** — on its own, pip resolves it to prebuilt `cp314` wheels cleanly, no compilation.
2. Install `kokoro`, `soundfile`, and `misaki[en]` with `--no-deps` — this sidesteps pip's resolver deciding to rebuild `blis`/`thinc` from source when it re-evaluates the whole dependency graph together.
3. Manually install misaki's *actual* runtime deps that `--no-deps` skipped (`scipy`, `huggingface-hub`, `loguru`, `transformers`, `addict`, `regex`, `espeakng-loader`, `num2words`, `phonemizer-fork`).
4. Deliberately **skip** `spacy-curated-transformers` (Kokoro's alternate, heavier G2P path for the `en_core_web_trf` model — not needed for the plain `en_core_web_sm` model). This one turned out to matter a lot: spaCy eagerly imports *any* installed plugin's entry points at startup, so merely having that package installed — even completely unused — broke loading the plain model.

This is the kind of thing you only find by actually running the install in a throwaway container and reading the traceback, not by reasoning about it in the abstract. We built the whole install order iteratively that way: install, fail, read the exact missing/broken piece, fix just that, retry.

## Wall 3: it kept "un-installing" itself

Every prior attempt at this had gone through `docker exec pip install ...` into an already-running container. That writes into the container's writable layer — which is **discarded** on the next `docker compose down`, rebuild, or `--force-recreate`. So it looked "installed," worked for a session, then silently vanished on the next restart. This is why STT/TTS had "worked before" multiple times and then mysteriously stopped.

The fix: bake the entire install sequence above into the **Dockerfile** itself, behind an opt-in build arg (`INSTALL_VOICE=true`), and set that arg's default in `docker-compose.yml` — not just pass it manually on the command line once. A plain `docker compose build` now always includes it, permanently.

## Wall 4: a partial branch sync

While chasing an unrelated `ModuleNotFoundError`, we found the live app folder had `core/session_manager.py` importing from `src/attachment_refs.py` — a file that didn't exist. It turned out this file *did* exist, correctly, in this project's `dev` branch (the actual primary branch — confirmed via `origin/HEAD -> origin/dev`), but the live deployment was a **partial** mix of files from different points in history. Fixing just that one file surfaced the next missing piece (a function in `src/upload_handler.py` from the same commit), which is exactly the whack-a-mole pattern that had caused real damage in earlier troubleshooting sessions.

Rather than keep patching file-by-file, we did a full, clean sync of the entire app-code tree from `origin/dev` (via a disposable `git worktree`, so the working copy's own uncommitted state was never touched), preserving only the runtime directories (`data/`, `logs/`). That fixed the *whole* class of "file X expects a sibling change in file Y that isn't there" bugs at once, instead of one at a time.

## Wall 5: a completely different torch, silently shadowing the real one

With STT and TTS packages correctly baked in, imports still failed — but with a **new** error: `libcublasLt.so not found`, a CUDA-loading crash, on a CPU-only setup that shouldn't be touching CUDA libs at all.

The cause: a separate Odysseus feature ("Cookbook," for locally serving models like vLLM) persists its installed packages in `data/local`, bind-mounted to `/app/.local` specifically so a container rebuild doesn't wipe out a real, deliberate ~3GB vLLM install. Python's user-site-packages mechanism searches `/app/.local` **before** the normal system site-packages — so the first `import torch` anywhere in the main app process was silently resolving to Cookbook's GPU-only torch build instead of the CPU one just installed for voice.

Cookbook's engines run as **separate subprocesses**, not in-process — confirmed by checking that every Cookbook-launched engine goes through `subprocess.Popen`/`asyncio.create_subprocess_exec`. That meant the fix could be scoped tightly: temporarily strip the `.local` path from `sys.path`, in-memory, just for the moment `torch`/`faster-whisper`/`kokoro` get imported in the main process. Since this only touches an in-memory list (not an environment variable), it has zero effect on Cookbook's subprocess-launched engines, which build their own `sys.path` fresh in a new interpreter.

## Wall 6: it worked — and then read the model's thinking process out loud

qwen3 (like other reasoning models) emits a `<think>...</think>` reasoning block before its actual answer. The visible chat UI already had fairly sophisticated logic to detect and hide this — including the *untagged* variant some model variants use (a raw `"Thinking: ..."` prefix instead of literal tags). The TTS module, however, had its own much simpler regex that only handled the literal-tag case, so untagged reasoning leaked straight into speech.

Fix: point the TTS text-extraction function at the *same* thinking-detection function the visible UI already trusts, instead of a second, weaker implementation that could disagree with it.

## Wall 7: an emoji took down synthesis entirely

Once real replies started reaching Kokoro, TTS crashed with:

```
TypeError: unsupported operand type(s) for +: 'NoneType' and 'str'
```

Binary-searching a handful of test strings isolated it to a single emoji character. misaki's phonemizer has no pronunciation entry for emoji, and doesn't fail gracefully when it hits one — some internal token ends up with `None` phonemes instead of an empty string. Since emoji aren't pronounceable anyway, the fix is simple: strip them before the text ever reaches Kokoro, scoped to the local pipeline only (API-based TTS providers handle this fine on their own).

## The payoff: a real hands-free conversation

With the backend solid, the last piece was UX. By default, using the mic meant: click to record, click again to stop, click send — every single turn. The final feature layered three things on top of the existing STT/TTS pipeline, active only in "TTS Mode":

- **Auto-stop on silence** — a `Web Audio API` `AnalyserNode` watches the mic stream's volume in parallel with the recorder; ~3 seconds of continuous quiet auto-stops the recording.
- **Auto-send** — once a transcript lands, the send button gets a synthesized click (reusing its existing logic, not a parallel submit path).
- **Auto-relisten** — once the reply finishes speaking *in full* (not interrupted), the mic automatically starts listening again for the next turn. Manually clicking mic/send while it's recording exits the loop.

The trickiest part of that last one was ordering: the UI's "recording" state has to be cleared *before* the synthetic send-button click fires, otherwise the click handler sees stale "still recording" state and swallows its own click as a stop-request instead of a send.

## Takeaways, if you're doing this yourself

- **Back up before you touch anything.** We `docker commit`-tagged the working image and `robocopy`'d the full data directory before the first change — turned every subsequent step into a "worst case, we lose time, not data" situation.
- **Test installs in a disposable container**, not the real one. `docker run --rm --entrypoint bash <image> -c "..."` costs nothing and tells you the truth immediately, instead of guessing from package metadata (which, in our case, was misleading about wheel availability).
- **A pip warning about a version conflict isn't automatically fatal.** `kokoro` complained about wanting a newer `misaki` and an older `numpy` than what got installed — both warnings, and both turned out to be harmless in practice, confirmed by actually running a synthesis call rather than trusting the warning text.
- **Bake fixes into the image, not into a running container.** Anything installed via `docker exec` disappears on the next rebuild. If it needs to survive, it belongs in the Dockerfile.
- **When something *should* work but doesn't, check for a second copy shadowing the first** — in our case, Python's user-site-packages silently out-prioritizing the "real" install.

Total root causes, once you count them: seven, stacked on top of each other, each hiding the next one until the previous was fixed. None of them were visible from the error message alone — every one needed either reading the actual library source, or reproducing it directly in a throwaway container.
