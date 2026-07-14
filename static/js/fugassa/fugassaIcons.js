/** Shared Fugassa mark — keep rail, sidebar, settings, and favicon in sync. */
import { fugassaMarkSvg } from '../titanBrand.js';

/** Sidebar tile (brand fill on neutral tile). */
export const FUGASSA_SIDEBAR_SVG = fugassaMarkSvg({
  size: 14,
  accent: 'var(--brand-color, var(--red))',
  cutout: 'var(--panel)',
}).replace('<svg ', '<svg style="flex-shrink:0;opacity:0.95;" ');

/** Icon rail (currentColor on rail tile). */
export const FUGASSA_RAIL_SVG = fugassaMarkSvg({
  size: 16,
  accent: 'currentColor',
  cutout: 'var(--bg)',
});

/** Settings appearance row (currentColor). */
export const FUGASSA_VIS_SVG = fugassaMarkSvg({
  size: 14,
  accent: 'currentColor',
  cutout: 'var(--bg)',
});

/** Favicon path fragments (32×32 viewBox). */
export function fugassaFaviconShapes(accent) {
  return (
    `<rect x='6' y='5' width='20' height='6' rx='1.2' fill='${accent}'/>` +
    `<rect x='6' y='5' width='6' height='22' rx='1.2' fill='${accent}'/>` +
    `<rect x='6' y='14' width='15' height='5.5' rx='1.2' fill='${accent}'/>` +
    `<circle cx='19' cy='21' r='1.1' fill='var(--bg,#0e0e10)'/>` +
    `<circle cx='22' cy='24' r='0.9' fill='var(--bg,#0e0e10)'/>` +
    `<circle cx='16' cy='24.5' r='0.8' fill='var(--bg,#0e0e10)'/>`
  );
}
