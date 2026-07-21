# Atlas directory size report

Generated: 2026-07-21 14:08 -06:00
Phase: post-clutter organization complete

## Total

| Metric | Value |
|--------|-------|
| Total bytes | 419245 |
| Total KB | 409.4 |
| Total MB | 0.4 |

## By subfolder

| `.tmp/` | 119740 bytes (116.9 KB) |
| `args/` | 457 bytes (0.4 KB) |
| `context/` | 283839 bytes (277.2 KB) |
| `goals/` | 8032 bytes (7.8 KB) |
| `hardprompts/` | 1353 bytes (1.3 KB) |
| `memory/` | 510 bytes (0.5 KB) |
| `tools/` | 3201 bytes (3.1 KB) |

## Notes

- Loose root clutter relocated into `context/` and `.tmp/`.
- Runtime folders unchanged; manifests index in-place dirs.
- Expected band ~0.5–1.0 MB: within range for loose-file-only organize (419245 bytes ≈ 0.4 MB).