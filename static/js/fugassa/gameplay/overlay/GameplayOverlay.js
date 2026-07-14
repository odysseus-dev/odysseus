import { mountCharacterScreen } from '../screens/CharacterScreen.js';
import { mountEstatesScreen } from '../screens/EstatesScreen.js';
import { mountLevelUpScreen } from '../screens/LevelUpScreen.js';
import { mountCraftingScreen } from '../screens/CraftingScreen.js';
import { mountInventoryScreen } from '../screens/InventoryScreen.js';
import { mountLocationScreen } from '../screens/LocationScreen.js';
import { mountMapScreen } from '../screens/MapScreen.js';
import { mountPauseScreen } from '../screens/PauseScreen.js';
import { mountSummaryScreen } from '../screens/SummaryScreen.js';

export function createGameplayOverlay(host, ctx) {
  let current = null;

  const close = () => {
    host.innerHTML = '';
    host.hidden = true;
    host.classList.remove('is-open');
    current = null;
  };

  const open = (screenId, options = {}) => {
    host.hidden = false;
    host.classList.add('is-open');
    host.innerHTML = '';
    const panel = document.createElement('div');
    panel.className = 'fugassa-overlay-panel';
    host.appendChild(panel);
    current = screenId;

    const base = {
      ...ctx,
      state: ctx.getState?.() || null,
      onClose: close,
      onOpenInventory: () => open('inventory'),
      onOpenEstates: () => open('estates'),
      ...options,
    };

    switch (screenId) {
      case 'inventory':
        mountInventoryScreen(panel, {
          ...base,
          onOpenCharacter: (memberIndex) => open('character', { memberIndex }),
        });
        break;
      case 'crafting':
        mountCraftingScreen(panel, base);
        break;
      case 'character':
        mountCharacterScreen(panel, {
          ...base,
          onOpenLevelUp: () => open('level-up'),
          initialMemberIndex: options.memberIndex ?? 0,
        });
        break;
      case 'estates':
        mountEstatesScreen(panel, {
          ...base,
          onStateChange: (newState) => ctx.onStateChange?.(newState),
        });
        break;
      case 'level-up':
        mountLevelUpScreen(panel, {
          ...base,
          onApplied: (newState) => {
            ctx.onStateChange?.(newState);
          },
        });
        break;
      case 'map':
        mountMapScreen(panel, base);
        break;
      case 'location':
        mountLocationScreen(panel, base);
        break;
      case 'pause':
        mountPauseScreen(panel, base);
        break;
      case 'summary':
        mountSummaryScreen(panel, base);
        break;
      default:
        close();
    }
  };

  return { open, close, isOpen: () => Boolean(current) };
}
