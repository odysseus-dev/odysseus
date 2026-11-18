# Music

Local music library playback for Odysseus. The service reads audio files from a
directory you own and controls a local `mpv` process over its IPC socket. There
is no external media server in this path: no second catalog, no second
database, no proxy.

The feature is optional. When `ODYSSEUS_MUSIC_ROOT` is unset, the library is
`<data>/music` (`<data>` is `ODYSSEUS_DATA_DIR`, normally the app's `data/`
directory). Nothing outside that root is indexed or served.

## What is included

- Library browser, search, queue, play/pause/seek/volume, chapters.
- Playlists (`routes/music_routes.py`, `src/music_playlists.py`).
- Play history and related/radio (`src/music_history.py`).
- Lyrics from LRCLIB (`/api/music/lyrics`).
- ReplayGain through mpv (`replaygain=track` in the profile).
- Ten-band equalizer with presets, including the Sennheiser HD 660S2
  (oratory1990/Harman) curve (`/api/music/equalizer`).
- Optional Soulseek search/download through an existing `slskd` instance.
- Optional YouTube search/download through `yt-dlp` (already in
  `requirements.txt`).
- Optional audio features (BPM, key, energy, valence/arousal) used by smart
  playlists and radio.
- A small remote player page (`/static/music-remote.html`) for a phone or a
  second window, authenticated with short-lived tickets.

## Requirements

- Linux with PipeWire (the default `ao=pipewire`); change the profile for PulseAudio
  or ALSA if needed.
- `mpv` in `PATH`.
- `playerctl` (optional, for the media-key/MPRIS controls; the player works
  without it).
- `ffmpeg`/`ffprobe` (chapters, duration fallback, download validation).
- Python dependencies from `requirements.txt` (`mutagen`, `yt-dlp`, etc.).

On Debian/Ubuntu:

```bash
sudo apt install mpv playerctl ffmpeg
python3 -m pip install -r requirements.txt
```

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `ODYSSEUS_MUSIC_ROOT` | `<data>/music` | Library root. Must be a directory. |
| `ODYSSEUS_MPV_SOCKET` | `/run/user/<uid>/odysseus-mpv.sock` | IPC socket used to control mpv. |
| `ODYSSEUS_MPV_CONFIG` | `config/mpv-daemon.conf` | mpv profile included by the service. |
| `ODYSSEUS_MPV_PLAYER` | `odysseus-music` | `playerctl` player name / audio client name. |
| `MUSIC_TICKET_TTL_SEC` | `600` | Lifetime of remote stream/event tickets. |
| `ODYSSEUS_SLSKD_URL` | `http://127.0.0.1:5030` | Optional `slskd` API base URL. |
| `ODYSSEUS_SLSKD_KEY` | empty | Optional `slskd` API key. Empty disables Soulseek. |
| `ODYSSEUS_SLSKD_TIMEOUT` | `10` | `slskd` request timeout, seconds. |
| `LISTENBRAINZ_TOKEN` | empty | Optional ListenBrainz scrobble token. |

`config/mpv-daemon.conf` is a flat profile: no video window, gapless playback,
per-track ReplayGain, and no EQ by default. The service launches mpv with
`--config=no` and includes this file, so the host's `~/.config/mpv/mpv.conf`
does not leak into playback. Uncomment the `script=` line only if `mpv-mpris`
is installed.

## Library layout

Any nested layout works; paths are stored relative to the root:

```text
music/
  Artist/
    Album/
      01 - Track.flac
  Unsorted/
    downloaded-track.opus
```

The service refuses paths that resolve outside `ODYSSEUS_MUSIC_ROOT`. The
`Unsorted/` directory is where YouTube/Soulseek downloads land.

## Optional Soulseek (slskd)

Run `slskd` separately and point Odysseus at its API:

```bash
export ODYSSEUS_SLSKD_URL="http://127.0.0.1:5030"
export ODYSSEUS_SLSKD_KEY="..."   # from slskd's web UI/API key
```

No Soulseek credentials or API keys are stored in this repository. With no
`ODYSSEUS_SLSKD_KEY`, the search endpoint reports the source as unavailable and
`/api/music/download/slskd` returns `503`; the rest of the player keeps working.

## Optional audio features

Features are written to `<data>/music_features.jsonl`. They are not required:
when the file is absent, smart playlists and radio use the tracks that have
metadata and ignore the rest.

The analyzer is `scripts/music_features.py` and uses
[`sonoglyph`](https://pypi.org/project/sonoglyph/), which pulls `librosa`,
`numpy`, `scipy`, and `soundfile`. Keep it out of the app environment if you do
not need features:

```bash
python3 -m venv .venv-music-features
. .venv-music-features/bin/activate
pip install sonoglyph
python scripts/music_features.py --root "$ODYSSEUS_MUSIC_ROOT" --jobs 4
```

The script is incremental (it skips unchanged files by mtime/size), writes
atomically enough for the reader's mtime check, and records per-file errors
instead of aborting the whole run.

## Lyrics and ReplayGain

- Lyrics are fetched on demand from LRCLIB; no API key is needed.
- ReplayGain uses the tags already present in the files (`replaygain=track`).
  Tracks without tags play at their original level. The app does not scan or
  rewrite tags; use `rsgain`, `loudgain`, or foobar2000 to add them.

## Remote player

`/static/music-remote.html` is a standalone page. It asks
`POST /api/music/remote-ticket` for a short-lived ticket and appends it to
`/api/music/stream` and `/api/music/events` (an `<audio>` element and
`EventSource` cannot send an `Authorization` header). Tickets are HMAC-signed,
scoped to `music:stream` / `music:events`, and expire after
`MUSIC_TICKET_TTL_SEC`.

The page is served by the normal `/static` mount, so it is reachable without
extra routing. Guests still need a session with music access; the ticket is not
a replacement for the music-access check.

## Tests

```bash
python -m pytest tests/test_music_ticket.py tests/test_music_routes.py tests/test_music_service.py -q
```

The suite uses a temporary library root; a missing `data/music` directory is not
required.
