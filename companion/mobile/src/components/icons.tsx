// Monochrome line icons in the same style the web app uses (24x24, currentColor,
// stroke-width 2, round caps). Inline SVG, no icon font and no emoji -- per the
// project's visual rules.
import type { SVGProps } from 'react';

type IconProps = SVGProps<SVGSVGElement> & { size?: number };

function Svg({ size = 22, children, ...rest }: IconProps & { children: React.ReactNode }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      {...rest}
    >
      {children}
    </svg>
  );
}

export const SessionsIcon = (p: IconProps) => (
  <Svg {...p}>
    <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
  </Svg>
);

export const SettingsIcon = (p: IconProps) => (
  <Svg {...p}>
    <line x1="21" x2="14" y1="6" y2="6" />
    <line x1="10" x2="3" y1="6" y2="6" />
    <line x1="21" x2="12" y1="12" y2="12" />
    <line x1="8" x2="3" y1="12" y2="12" />
    <line x1="21" x2="16" y1="18" y2="18" />
    <line x1="12" x2="3" y1="18" y2="18" />
    <line x1="14" x2="14" y1="4" y2="8" />
    <line x1="8" x2="8" y1="10" y2="14" />
    <line x1="16" x2="16" y1="16" y2="20" />
  </Svg>
);

export const RefreshIcon = (p: IconProps) => (
  <Svg {...p}>
    <path d="M21 12a9 9 0 1 1-9-9c2.5 0 4.85.99 6.6 2.6L21 8" />
    <path d="M21 3v5h-5" />
  </Svg>
);

export const ChevronLeftIcon = (p: IconProps) => (
  <Svg {...p}>
    <path d="m15 18-6-6 6-6" />
  </Svg>
);

export const ChevronRightIcon = (p: IconProps) => (
  <Svg {...p}>
    <path d="m9 18 6-6-6-6" />
  </Svg>
);

// The Odysseus mark: filled sail + wave, reused from the web app's favicon.
export const BrandMark = ({ size = 28 }: { size?: number }) => (
  <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden="true">
    <path d="M16 4L16 22L6 22Z" fill="var(--red)" />
    <path d="M16 8L16 22L24 22Z" fill="var(--red)" opacity="0.6" />
    <path
      d="M4 24Q10 20 16 24Q22 28 28 24"
      stroke="var(--red)"
      strokeWidth="2.5"
      fill="none"
      strokeLinecap="round"
    />
  </svg>
);
