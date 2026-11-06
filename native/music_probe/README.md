# music_probe

One pass over a music library with libavformat: tags, audio properties and the
embedded cover, instead of one `ffprobe` process per file.

Measured on 100 tagged FLACs with a 320×320 cover (local SSD):

| Path | Time |
|---|---|
| C++ prober, one process, 100 covers written | **40 ms** |
| `ffprobe` per file | **102.7 ms/file** (~10.3 s) |

The binary is optional. `src/music_probe.py` falls back to mutagen and returns
the same rows, so a checkout without `libavformat` still works.

```bash
bash native/music_probe/build.sh
./native/music_probe/music_probe --root /music --covers /data/music-covers
```

Environment: `ODYSSEUS_MUSIC_PROBE` overrides the binary path.
