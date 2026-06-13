# PWA Packaging & Deployment Guide

This guide describes how to configure, package, and deploy web applications as high-fidelity **Progressive Web Applications (PWAs)**. It explains the request interception architecture, how to generate premium antialiased transparent icons, and how to avoid common installation pitfalls (such as unwanted white borders on mobile devices).

---

## 1. PWA Request & Lifecycle Architecture

A Progressive Web App runs on top of the browser's engine but acts as a standalone application. Below is an architectural overview of how the **PWA Manifest** and **Service Worker** layer sit between the user interface and the backend server.

```
+-----------------------------------------------------------------------+
|                             USER'S DEVICE                             |
|                                                                       |
|   +--------------------+                       +------------------+   |
|   |   Home Screen /    |                       |  PWA App Window  |   |
|   |  System Launcher   |                       |  (Standalone UI) |   |
|   +---------+----------+                       +--------+---------+   |
|             |                                           |             |
|             | 1. Triggers Install                       | 3. Launches |
|             v                                           v             |
|   +---------+-------------------------------------------+---------+   |
|   |                     BROWSER ENGINE / PWA CONTAINER                |   |
|   |                                                                   |   |
|   |   Reads manifest.json:                                            |   |
|   |   - Displays Custom Icon (Transparent, purpose: "any")            |   |
|   |   - Configures display mode ("standalone" - hides browser UI)     |   |
|   |                                                                   |   |
|   |   +-----------------------------------------------------------+   |   |
|   |   |                      SERVICE WORKER                       |   |   |
|   |   |                                                           |   |   |
|   |   |   2. Intercepts fetch requests / Caches app shell         |   |   |
|   |   |   3. Serves offline assets locally                        |   |   |
|   |   +-----------------------------+-----------------------------+   |   |
|   +---------------------------------|---------------------------------+   |
|                                     |                                     |
+-------------------------------------|-------------------------------------+
                                      |
                                      | 4. Proxies APIs & dynamic requests
                                      v
                            +---------+----------+
                            |    BACKEND HOST    |
                            |   (Uvicorn/FastAPI)|
                            +--------------------+
```

---

## 2. Premium Antialiased Transparent Icon Generation

When exporting a shape-based logo (like a sailboat or curved symbol) with a transparent background, standard image drawing can leave jagged, pixelated edges. To achieve a smooth look (like Chrome or MATLAB), we use **4x Supersampling** in Python's Pillow library.

### The Antialiased Drawing Script
This script draws the geometric canvas at four times the target size and downscales it using a high-quality `LANCZOS` resampling filter:

```python
import os
from PIL import Image, ImageDraw

def generate_smooth_icon(size, filename, output_directory):
    S = size
    factor = 4
    super_S = S * factor
    sc = super_S / 32.0  # Assumes a 32x32 grid
    
    # 1. Create a transparent canvas
    img = Image.new("RGBA", (super_S, super_S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # 2. Define Brand Color Palettes
    accent = (224, 108, 117, 255)   # #e06c75 (Brand salmon)
    accent2 = (180, 86, 94, 255)    # Darker shade for secondary elements

    def P(x, y):
        return (x * sc, y * sc)

    # 3. Draw Polygons (Sails)
    d.polygon([P(16, 4), P(16, 22), P(6, 22)], fill=accent)
    d.polygon([P(16, 8), P(16, 22), P(24, 22)], fill=accent2)

    # 4. Draw Wave using Quadratic Bezier Curves
    def quad_bezier(p0, p1, p2, segments=160):
        out = []
        for i in range(segments + 1):
            t = i / segments
            x = (1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * p1[0] + t * t * p2[0]
            y = (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * p1[1] + t * t * p2[1]
            out.append((x, y))
        return out

    wave = quad_bezier(P(4, 24.5), P(10, 20.5), P(16, 24.5)) + quad_bezier(P(16, 24.5), P(22, 28.5), P(28, 24.5))
    d.line(wave, fill=accent, width=int(2.2 * sc), joint="curve")

    # 5. Downscale with LANCZOS for high quality anti-aliasing
    try:
        resample_filter = Image.Resampling.LANCZOS
    except AttributeError:
        try:
            resample_filter = Image.LANCZOS
        except AttributeError:
            resample_filter = Image.ANTIALIAS

    img_resized = img.resize((S, S), resample_filter)
    out_path = os.path.join(output_directory, filename)
    img_resized.save(out_path)
    print(f"Generated antialiased transparent icon: {out_path} ({size}x{size})")

if __name__ == "__main__":
    static_dir = "./static"
    generate_smooth_icon(192, "icon-192.png", static_dir)
    generate_smooth_icon(512, "icon-512.png", static_dir)
```

---

## 3. PWA Manifest Rules (`manifest.json`)

To configure how the app installs, you must supply a web app manifest. 

### Critical Configuration Keys

| Attribute | Value | Description |
| :--- | :--- | :--- |
| `"display"` | `"standalone"` | Hides the browser address bar, back/forward controls, and tabs. |
| `"start_url"` | `"./"` | Keeps the path relative to support subdirectory deployments. |
| `"scope"` | `"./"` | Restricts the service worker and navigation scope to the current directory. |
| `"purpose"` | `"any"` | **Crucial:** Removing `maskable` prevents Chrome/Android from forcing a white circle backing. |

### Manifest Template
```json
{
  "id": "odysseus",
  "name": "Odysseus",
  "short_name": "Odysseus",
  "description": "Self-hosted AI chat with memory, documents, and tools",
  "categories": ["productivity", "utilities"],
  "start_url": "./",
  "scope": "./",
  "display": "standalone",
  "display_override": ["standalone", "minimal-ui"],
  "orientation": "any",
  "background_color": "#282c34",
  "theme_color": "#282c34",
  "icons": [
    { "src": "static/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any" },
    { "src": "static/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any" }
  ]
}
```

---

## 4. Troubleshooting Mobile and Taskbar Layouts

### 1. Removing the White Background Circle on Android/Chrome
By default, mobile platforms try to force icons into a standard shape (circles, rounded rectangles, squircles) using "masking". 
*   **The Problem:** If an icon has `"purpose": "any maskable"` or `"purpose": "maskable"`, Chrome shrinks the transparent PNG and fills the remaining shape with a solid white background color.
*   **The Solution:** Set `"purpose": "any"` only. This indicates to the operating system that the icon already contains its own desired shapes and transparency, allowing it to render natively with a transparent background.

### 2. Relative Subdirectory Support
If the web app is hosted at a sub-path (e.g. `http://example.com/chat/` instead of `http://example.com/`), absolute pathing like `/static/icon-192.png` will fail to resolve. 
*   Always omit the leading slash in the manifest: use `static/icon-192.png`.
*   Ensure service worker caching matches relative scope endpoints by utilizing `self.location.pathname` inside the service worker initialization.
