# === deploy-branding-to-app.ps1 ===
# Kopiert die Branding-Änderungen in das deployed /app/ Verzeichnis
# Das deployed /app/ ist KEIN Git-Repo – es wird manuell aktualisiert
# Basis-Repo: /app/data/odysseus-vita-machina/

$ErrorActionPreference = "Stop"
$deployedApp = "/app"
$repoRoot = "/app/data/odysseus-vita-machina"

Write-Host "=== Branding ins deployed /app/ einspielen ===" -ForegroundColor Cyan

# ─────────────────────────────────────────────────────
# 1. settings.py – Branding-Defaults (bereits vorhanden?)
# ─────────────────────────────────────────────────────
$settingsFile = "$deployedApp/src/settings.py"
if (Test-Path $settingsFile) {
    $content = Get-Content $settingsFile -Raw
    if ($content -match '"app_name"') {
        Write-Host "✓ settings.py hat bereits Branding-Defaults" -ForegroundColor Green
    } else {
        Write-Host "➕ Füge Branding-Defaults in settings.py hinzu..." -ForegroundColor Yellow
        $content = $content -replace '("app_port":\s*\d+)', "`$1,`n    `"app_name`": `"Odysseus`",`n    `"app_logo`": `"`",`n    `"app_icon`": `"`""
        Set-Content $settingsFile $content
    }
}

# ─────────────────────────────────────────────────────
# 2. prefs_routes.py – GET /branding-Endpoint
# ─────────────────────────────────────────────────────
$prefsFile = "$deployedApp/routes/prefs_routes.py"
if (Test-Path $prefsFile) {
    $content = Get-Content $prefsFile -Raw
    if ($content -match 'def get_branding') {
        Write-Host "✓ /branding-Endpoint existiert bereits" -ForegroundColor Green
    } else {
        Write-Host "➕ Füge /branding-Endpoint hinzu..." -ForegroundColor Yellow
        $brandingEndpoint = @"

@router.get("/branding")
async def get_branding(request: Request):
    \"\"\"Return merged branding settings: user prefs + server defaults.\"\"\"
    try:
        from src.settings import get_setting
        user = get_current_user(request)
        prefs = _load_for_user(user)
        return {
            "app_name": prefs.get("app_name") or get_setting("app_name", "Odysseus"),
            "app_logo": prefs.get("app_logo") or get_setting("app_logo", ""),
            "app_icon": prefs.get("app_icon") or get_setting("app_icon", ""),
        }
    except Exception as e:
        return {
            "app_name": "Odysseus",
            "app_logo": "",
            "app_icon": "",
        }

"@
        $content = $content.TrimEnd() + "`n`n" + $brandingEndpoint
        Set-Content $prefsFile $content
    }
}

# ─────────────────────────────────────────────────────
# 3. index.html – Branding-HTML im Appearance-Panel + Script
# ─────────────────────────────────────────────────────
$htmlFile = "$deployedApp/static/index.html"
if (Test-Path $htmlFile) {
    $content = Get-Content $htmlFile -Raw
    $dirty = $false

    # 3a. Branding-Felder im Appearance-Panel einfügen
    if ($content -notmatch 'brand-app-name') {
        Write-Host "➕ Füge Branding-HTML in Appearance-Panel ein..." -ForegroundColor Yellow
        
        $brandingHtml = @'
          <!-- ── Branding Section ── -->
          <h3>Branding</h3>
          <p style="color:var(--muted);font-size:13px;margin:0 0 12px 0">
            Überschreibe App-Name, Logo und Icon für deine Instanz.
          </p>
          <div class="settings-row">
            <label>App-Name</label>
            <input type="text" id="brand-app-name" class="styled-input" 
                   placeholder="Odysseus" maxlength="64">
          </div>
          <div class="settings-row">
            <label>Logo (URL)</label>
            <input type="text" id="brand-app-logo" class="styled-input" 
                   placeholder="data:image/svg+xml,... oder https://...">
            <div id="brand-logo-preview" class="brand-preview"></div>
          </div>
          <div class="settings-row">
            <label>Icon / Favicon (URL)</label>
            <input type="text" id="brand-app-icon" class="styled-input" 
                   placeholder="data:image/svg+xml,... oder https://...">
            <div id="brand-icon-preview" class="brand-preview" style="width:32px;height:32px"></div>
          </div>
          <button class="confirm-btn confirm-btn-primary" id="brand-save-btn" style="margin-top:8px">
            Branding speichern
          </button>
'@
        # Einfügen vor dem schließenden </div> des Appearance-Panels
        $content = $content -replace '(data-settings-panel="appearance"[^>]*>.*?)(\s*</div>\s*<!-- end panel --?>)', "`$1`n$brandingHtml`n`$2"
        $dirty = $true
    } else {
        Write-Host "✓ Branding-HTML bereits im Appearance-Panel" -ForegroundColor Green
    }

    # 3b. Sidebar-Brand-Elemente dynamisch machen (IDs setzen)
    if ($content -notmatch 'id="sidebar-brand-name"') {
        Write-Host "➕ Mache Sidebar-Elemente dynamisch..." -ForegroundColor Yellow
        $content = $content -replace 'class="sidebar-brand-name">', 'class="sidebar-brand-name" id="sidebar-brand-name">'
        $content = $content -replace '(class="sidebar-logo)', '$1 id="sidebar-logo"'
        $dirty = $true
    }

    # 3c. Title dynamisch machen
    if ($content -notmatch 'id="app-title"') {
        $content = $content -replace '<title>', '<title id="app-title">'
        $dirty = $true
    }

    # 3d. Branding-Script vor </body>
    if ($content -notmatch 'branding') {
        Write-Host "➕ Füge Branding-Fetch-Script ein..." -ForegroundColor Yellow
        $brandingScript = @'
<script nonce="{{CSP_NONCE}}">
(async function() {
  try {
    const res = await fetch('/api/prefs/branding');
    const brand = await res.json();
    if (brand.app_name) {
      document.title = brand.app_name + ' Chat';
      const n = document.getElementById('sidebar-brand-name');
      if (n) n.textContent = brand.app_name;
    }
    if (brand.app_logo) {
      const l = document.getElementById('sidebar-logo');
      if (l) l.style.backgroundImage = 'url(' + brand.app_logo + ')';
    }
    if (brand.app_icon) {
      const link = document.querySelector("link[rel='icon']");
      if (link) link.href = brand.app_icon;
    }
  } catch(e) { /* keep defaults */ }
})();
</script>

'@
        $content = $content -replace '(</body>)', "$brandingScript`$1"
        $dirty = $true
    }

    if ($dirty) {
        Set-Content $htmlFile $content
        Write-Host "✓ index.html aktualisiert" -ForegroundColor Green
    } else {
        Write-Host "✓ index.html bereits vollständig" -ForegroundColor Green
    }
}

# ─────────────────────────────────────────────────────
# 4. style.css – Branding-Stile
# ─────────────────────────────────────────────────────
$cssFile = "$deployedApp/static/style.css"
if (Test-Path $cssFile) {
    $content = Get-Content $cssFile -Raw
    if ($content -match 'sidebar-brand') {
        Write-Host "✓ CSS-Stile bereits vorhanden" -ForegroundColor Green
    } else {
        Write-Host "➕ Füge Branding-CSS hinzu..." -ForegroundColor Yellow
        $brandingCss = @'

/* ── Sidebar Branding (dynamisch) ── */
.sidebar-brand {
  padding: 12px 16px;
  display: flex;
  align-items: center;
  gap: 10px;
  border-bottom: 1px solid var(--border);
}
.sidebar-brand-link {
  display: flex;
  align-items: center;
  gap: 10px;
  text-decoration: none;
  color: var(--fg);
}
.sidebar-logo {
  width: 28px;
  height: 28px;
  background-size: contain;
  background-repeat: no-repeat;
  background-position: center;
  flex-shrink: 0;
}
.sidebar-brand-name {
  font-size: 16px;
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* ── Branding Preview in Settings ── */
.brand-preview {
  width: 64px;
  height: 64px;
  margin-top: 8px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background-size: contain;
  background-repeat: no-repeat;
  background-position: center;
  background-color: var(--panel);
}

/* ── Icon Gallery ── */
.icon-gallery {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 8px;
}
.icon-gallery-item {
  width: 40px;
  height: 40px;
  border-radius: 8px;
  border: 2px solid var(--border);
  background: var(--panel);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 20px;
  transition: transform 0.15s, border-color 0.15s, box-shadow 0.15s;
}
.icon-gallery-item:hover {
  transform: scale(1.12);
  border-color: var(--accent);
}
.icon-gallery-item.selected {
  border-color: var(--primary);
  box-shadow: 0 0 0 2px color-mix(in srgb, var(--primary) 40%, transparent);
  transform: scale(1.1);
}
'@
        Add-Content $cssFile $brandingCss
        Write-Host "✓ CSS hinzugefügt" -ForegroundColor Green
    }
}

# ─────────────────────────────────────────────────────
# 5. settings.js – Branding-JS-Logik
# ─────────────────────────────────────────────────────
$settingsJs = "$deployedApp/static/js/settings.js"
if (Test-Path $settingsJs) {
    $content = Get-Content $settingsJs -Raw
    $dirty = $false

    if ($content -match 'renderBrandingSection') {
        Write-Host "✓ Branding-JS-Funktionen bereits vorhanden" -ForegroundColor Green
    } else {
        Write-Host "➕ Füge Branding-JS-Funktionen hinzu..." -ForegroundColor Yellow
        
        $brandingJs = @'

/* ── BRANDING SECTION ── */
function renderBrandingSection(container) {
  const section = document.createElement('div');
  section.className = 'settings-section';
  section.innerHTML = `
    <h3>Branding</h3>
    <p class="muted">Überschreibe App-Name, Logo und Icon für deine Instanz.</p>
    
    <div class="settings-row">
      <label>App-Name</label>
      <input type="text" id="brand-app-name" class="styled-input" 
             placeholder="Odysseus" maxlength="64">
    </div>
    
    <div class="settings-row">
      <label>Logo (URL)</label>
      <input type="text" id="brand-app-logo" class="styled-input" 
             placeholder="data:image/svg+xml,... oder https://...">
      <div id="brand-logo-preview" class="brand-preview"></div>
    </div>
    
    <div class="settings-row">
      <label>Icon / Favicon (URL)</label>
      <input type="text" id="brand-app-icon" class="styled-input" 
             placeholder="data:image/svg+xml,... oder https://...">
      <div id="brand-icon-preview" class="brand-preview" style="width:32px;height:32px"></div>
    </div>
    
    <button class="confirm-btn confirm-btn-primary" id="brand-save-btn">
      Branding speichern
    </button>
  `;
  container.appendChild(section);

  // Laden
  fetch('/api/prefs/branding').then(r => r.json()).then(b => {
    document.getElementById('brand-app-name').value = b.app_name || '';
    document.getElementById('brand-app-logo').value = b.app_logo || '';
    document.getElementById('brand-app-icon').value = b.app_icon || '';
    updateBrandPreviews(b);
  }).catch(() => {});

  // Speichern
  document.getElementById('brand-save-btn').addEventListener('click', async () => {
    const data = {
      app_name: document.getElementById('brand-app-name').value.trim(),
      app_logo: document.getElementById('brand-app-logo').value.trim(),
      app_icon: document.getElementById('brand-app-icon').value.trim(),
    };
    try {
      await fetch('/api/prefs/app_name', { method: 'PUT', headers: {'Content-Type':'application/json'}, body: JSON.stringify({value: data.app_name}) });
      await fetch('/api/prefs/app_logo', { method: 'PUT', headers: {'Content-Type':'application/json'}, body: JSON.stringify({value: data.app_logo}) });
      await fetch('/api/prefs/app_icon', { method: 'PUT', headers: {'Content-Type':'application/json'}, body: JSON.stringify({value: data.app_icon}) });
      updateBrandPreviews(data);
      applyBranding(data);
      if (typeof uiModule !== 'undefined' && uiModule.showToast) {
        uiModule.showToast('Branding gespeichert!');
      }
    } catch(e) {
      console.error('Branding save failed', e);
    }
  });
}

function updateBrandPreviews(b) {
  const lp = document.getElementById('brand-logo-preview');
  if (lp) lp.style.backgroundImage = b.app_logo ? 'url(' + b.app_logo + ')' : 'none';
  const ip = document.getElementById('brand-icon-preview');
  if (ip) ip.style.backgroundImage = b.app_icon ? 'url(' + b.app_icon + ')' : 'none';
}

function applyBranding(b) {
  if (b.app_name) {
    document.title = b.app_name + ' Chat';
    const n = document.getElementById('sidebar-brand-name');
    if (n) n.textContent = b.app_name;
  }
  const l = document.getElementById('sidebar-logo');
  if (l) l.style.backgroundImage = b.app_logo ? 'url(' + b.app_logo + ')' : '';
  const fav = document.querySelector("link[rel='icon']");
  if (fav && b.app_icon) fav.href = b.app_icon;
}

/* ── ICON GALLERY ── */
const BRAND_ICONS = [
  { name: 'Stern', char: '⭐', url: '/static/icons/star.png' },
  { name: 'Blitz', char: '⚡', url: '/static/icons/bolt.png' },
  { name: 'Herz', char: '❤️', url: '/static/icons/heart.png' },
  { name: 'Zahnrad', char: '⚙️', url: '/static/icons/gear.png' },
  { name: 'Mond', char: '🌙', url: '/static/icons/moon.png' },
  { name: 'Feder', char: '✒️', url: '/static/icons/feather.png' },
  { name: 'Krone', char: '👑', url: '/static/icons/crown.png' },
  { name: 'Roboter', char: '🤖', url: '/static/icons/robot.png' },
  { name: 'Kompass', char: '🧭', url: '/static/icons/compass.png' },
  { name: 'Auge', char: '👁️', url: '/static/icons/eye.png' },
];

function renderIconGallery(containerId, inputId) {
  const container = document.getElementById(containerId);
  if (!container) return;
  container.innerHTML = '';
  container.className = 'icon-gallery';
  
  BRAND_ICONS.forEach(icon => {
    const item = document.createElement('div');
    item.className = 'icon-gallery-item';
    item.title = icon.name;
    item.textContent = icon.char;
    item.dataset.url = icon.url;
    item.addEventListener('click', () => {
      // Alle Auswahl entfernen
      container.querySelectorAll('.icon-gallery-item').forEach(el => el.classList.remove('selected'));
      item.classList.add('selected');
      // URL ins Textfeld setzen
      const input = document.getElementById(inputId);
      if (input) {
        input.value = icon.url;
        input.dispatchEvent(new Event('input'));
      }
      // Preview aktualisieren
      const previewId = inputId.replace('brand-', 'brand-').replace('-icon', '-icon-preview').replace('-logo', '-logo-preview');
      // Fallback: Suche Preview per ID
      const previewMap = { 'brand-app-icon': 'brand-icon-preview', 'brand-app-logo': 'brand-logo-preview' };
      const pid = previewMap[inputId];
      if (pid) {
        const p = document.getElementById(pid);
        if (p) p.style.backgroundImage = 'url(' + icon.url + ')';
      }
    });
    container.appendChild(item);
  });
}

function upgradeBrandingToVisualIcons() {
  // Warte auf DOM
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', _doUpgrade);
  } else {
    _doUpgrade();
  }
  function _doUpgrade() {
    // Icon-Gallery unter Icon-Input
    let iconGallery = document.getElementById('brand-icon-gallery');
    if (!iconGallery) {
      const iconRow = document.getElementById('brand-app-icon')?.closest('.settings-row');
      if (iconRow) {
        iconGallery = document.createElement('div');
        iconGallery.id = 'brand-icon-gallery';
        iconRow.appendChild(iconGallery);
      }
    }
    if (iconGallery) renderIconGallery('brand-icon-gallery', 'brand-app-icon');
    
    // Logo-Gallery unter Logo-Input (nur 5 Icons, da Logos größer sind)
    let logoGallery = document.getElementById('brand-logo-gallery');
    if (!logoGallery) {
      const logoRow = document.getElementById('brand-app-logo')?.closest('.settings-row');
      if (logoRow) {
        logoGallery = document.createElement('div');
        logoGallery.id = 'brand-logo-gallery';
        logoRow.appendChild(logoGallery);
      }
    }
    if (logoGallery) renderIconGallery('brand-logo-gallery', 'brand-app-logo');
  }
}

// Automatisch aktivieren
upgradeBrandingToVisualIcons();

'@
        
        # Anhängen ans Ende der Datei
        $content = $content.TrimEnd() + "`n`n" + $brandingJs
        $dirty = $true
    }

    # Aufruf in renderAppearanceTab einfügen
    if ($content -match 'function renderAppearanceTab' -and $content -notmatch 'renderBrandingSection\(container\)') {
        Write-Host "➕ Füge renderBrandingSection-Aufruf in renderAppearanceTab ein..." -ForegroundColor Yellow
        $content = $content -replace '(renderAppearanceTab\([^)]*\)[\s\S]*?)(\n\s*\})', '${1}' + "`n  renderBrandingSection(container);`$2"
        $dirty = $true
    }

    if ($dirty) {
        Set-Content $settingsJs $content
        Write-Host "✓ settings.js aktualisiert" -ForegroundColor Green
    } else {
        Write-Host "✓ settings.js bereits vollständig" -ForegroundColor Green
    }
}

# ─────────────────────────────────────────────────────
# 6. PRÜFUNG: PUT-Endpunkte in prefs_routes.py
# ─────────────────────────────────────────────────────
if (Test-Path $prefsFile) {
    $content = Get-Content $prefsFile -Raw
    if ($content -match '"app_name"') {
        Write-Host "✓ PUT-Endpunkte für app_name/app_logo/app_icon existieren" -ForegroundColor Green
    } else {
        Write-Host "⚠️  ACHTUNG: PUT-Endpunkte für Branding-Felder fehlen!" -ForegroundColor Yellow
        Write-Host "   Die UI kann speichern, aber die Werte werden nicht persistiert."
        Write-Host "   Prüfe ob die generischen PUT /{key}-Endpoints bereits existieren."
    }
}

# ─────────────────────────────────────────────────────
# 7. APP NEUSTARTEN
# ─────────────────────────────────────────────────────
Write-Host "`n=== Deployment abgeschlossen! ===" -ForegroundColor Cyan
Write-Host ""
Write-Host "Starte die App neu (z.B. Docker-Container restart oder dev-server neustarten):" -ForegroundColor Yellow
Write-Host "  Strg+C (dev-server beenden) und dann neu starten"
Write-Host "  ODER: sudo systemctl restart odysseus (falls als Service installiert)"
Write-Host "  ODER: docker-compose restart (falls Docker)"
Write-Host ""
Write-Host "Nach Neustart:" -ForegroundColor Green
Write-Host "  1. Browser: Strg+Shift+R (Hard-Refresh)"
Write-Host "  2. Settings öffnen > Appearance-Tab"
Write-Host "  3. Branding-Section sollte erscheinen"
Write-Host "  4. App-Name ändern, speichern, Sidebar checken"
Write-Host "  5. Icon-Gallery: Klick auf ein Icon setzt URL + Preview"
Write-Host ""
Write-Host "=== Fertig! ===" -ForegroundColor Cyan
