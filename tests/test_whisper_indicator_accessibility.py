import json
import subprocess
from pathlib import Path


def test_whisper_indicator_accessible_name_tracks_state_and_participant():
    module_uri = (Path("static/js/whisperIndicator.js").resolve()).as_uri()
    script = f"""
      import {{ syncWhisperIndicatorAccessibility }} from {json.dumps(module_uri)};

      class Button {{
        constructor() {{ this.attributes = new Map(); }}
        setAttribute(name, value) {{ this.attributes.set(name, String(value)); }}
        getAttribute(name) {{ return this.attributes.get(name) ?? null; }}
      }}

      const button = new Button();
      syncWhisperIndicatorAccessibility(button, false);
      if (button.getAttribute('aria-label') !== 'Whisper mode active') process.exit(1);

      syncWhisperIndicatorAccessibility(button, true, {{ name: 'Athena' }});
      if (button.getAttribute('aria-label') !== 'Whisper to Athena') process.exit(2);

      syncWhisperIndicatorAccessibility(button, true, {{}});
      if (button.getAttribute('aria-label') !== 'Whisper to group participant') process.exit(3);
    """

    subprocess.run(["node", "--input-type=module", "--eval", script], check=True)
