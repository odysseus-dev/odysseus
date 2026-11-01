"""Normalize the small theme palette without discarding explicit choices."""
import re

THEME_PRESETS = ('dark', 'light', 'midnight', 'cyberpunk', 'retrowave', 'forest',
                 'ocean', 'ume', 'terminal', 'organs', 'gpt', 'claude', 'cute',
                 'eclipse', 'porcelain', 'arcade', 'blueprint', 'monolith', 'yoyo')

BACKGROUND_PATTERNS = ('none', 'dots', 'synapse', 'rain', 'constellations',
                       'perlin-flow', 'petals', 'sparkles', 'embers',
                       'starfield-depth', 'ascii-fireflies')


def normalize_theme_background(background, accent):
    if background is None:
        background = {'pattern': 'none'}
    if not isinstance(background, dict):
        raise ValueError('background must be an object with a pattern name.')
    pattern = background.get('pattern', 'none')
    if pattern == 'random':
        import random
        pattern = random.choice(BACKGROUND_PATTERNS[1:])
    if pattern not in BACKGROUND_PATTERNS:
        raise ValueError('Unknown background.pattern. Choose: ' + ', '.join(BACKGROUND_PATTERNS) + ', random.')
    result = {'bgPattern': pattern, 'bgEffectColor': accent}
    for key, low, high in (('intensity', 0, 1), ('size', .2, 3), ('speed', .05, 2.5)):
        value = background.get(key, 1)
        if isinstance(value, bool) or not isinstance(value, (float, int)) or not low <= value <= high:
            raise ValueError(f'background.{key} must be a number between {low} and {high}.')
        result['bgEffect' + key.title()] = value
    return result


def normalize_theme_colors(colors):
    if not isinstance(colors, dict):
        raise ValueError('colors must be an object with bg and accent hex colors.')
    result = {}
    for key, value in colors.items():
        if not isinstance(value, str):
            raise ValueError(f'colors.{key} must be a hex color, for example #d93025.')
        value = value.strip()
        if re.fullmatch(r'#?[0-9a-fA-F]{3}|#?[0-9a-fA-F]{6}', value) is None:
            raise ValueError(f'colors.{key}={value!r} is invalid. Use #RGB or #RRGGBB.')
        value = value.lstrip('#')
        if len(value) == 3:
            value = ''.join(c * 2 for c in value)
        result[key] = '#' + value.lower()
    if 'accent' not in result and 'red' in result:
        result['accent'] = result.pop('red')
    missing = {'bg', 'accent'} - result.keys()
    if missing:
        raise ValueError('Missing colors: ' + ', '.join(sorted(missing)) + '. Other colors are optional.')
    rgb = [int(result['bg'][i:i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= .04045 else ((c + .055) / 1.055) ** 2.4 for c in rgb]
    luminance = sum(c * w for c, w in zip(linear, (.2126, .7152, .0722)))
    light_text = (1.05 / (luminance + .05)) >= ((luminance + .05) / .05)
    result.setdefault('fg', '#ffffff' if light_text else '#000000')
    # Subtle surfaces move toward the contrasting pole, independent of an
    # explicitly chosen foreground that might itself be low contrast.
    target = 255 if light_text else 0
    def surface(amount):
        return '#' + ''.join(f'{round(c * 255 * (1 - amount) + target * amount):02x}' for c in rgb)
    result.setdefault('panel', surface(.06))
    result.setdefault('border', surface(.20))
    return result
