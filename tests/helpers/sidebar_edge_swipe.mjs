import fs from 'node:fs';
import vm from 'node:vm';

// Execute the production gesture wiring with document/input ports. Actual
// touch dispatch and DOM selection are checked in the running application.
const input = JSON.parse(process.argv[2]);
const source = fs.readFileSync('static/js/sidebar-layout.js', 'utf8');
const start = source.indexOf('function _initChatSwipeToOpenSidebar()');
const end = source.indexOf('\nif (document.readyState', start);
if (start < 0 || end < start) throw new Error('gesture initializer not found');
const listeners = {}, opened = [];
let prevented = 0, selected = !!input.selected;
const sidebar = { classList: { contains: cls => cls === 'hidden' && !input.sidebar_open } };
const chat = { classList: { contains: cls => cls === 'compare-active' && input.compare } };
const target = { closest: selector => selector === '#chat-container' ? !input.outside : !!input.excluded };
const context = vm.createContext({
  window: {
    innerWidth: input.width || 390,
    _chipDragging: !!input.chip_dragging,
    getSelection: () => ({ isCollapsed: !selected }),
    _odyOpenSidebar: side => opened.push(side),
  },
  document: {
    body: { classList: { contains: cls => input.body_view === cls } },
    getElementById: id => id === 'sidebar' ? sidebar : id === 'chat-container' ? chat : null,
    querySelectorAll: () => input.modal_open ? [{ classList: { contains: () => false } }] : [],
    addEventListener: (name, fn) => { listeners[name] = fn; },
  },
  getComputedStyle: () => ({ display: 'block' }),
});
vm.runInContext(source.slice(start, end) + '\n_initChatSwipeToOpenSidebar();', context);
const event = (x, y, count = 1) => ({
  touches: Array.from({ length: count }, () => ({ clientX: x, clientY: y })),
  target, cancelable: true, preventDefault() { prevented++; },
});
const x = input.x ?? 10, y = 200;
listeners.touchstart(event(x, y, input.touch_count || 1));
if (input.select_during_drag) selected = true;
listeners.touchmove(event(x + (input.dx ?? 70), y + (input.dy || 0)));
listeners[input.cancel ? 'touchcancel' : 'touchend'](event(x, y, 0));
// A canceled/ended gesture cannot open later without another touchstart.
listeners.touchmove(event(x + 100, y));
console.log(JSON.stringify({ opened, prevented }));
