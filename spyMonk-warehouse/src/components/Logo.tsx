import type { CSSProperties } from 'react';
import logoMark from '../assets/logo.png';

interface LogoProps {
  /** Rendered height in px (width scales with the mark's 817:367 aspect ratio). */
  size?: number;
  title?: string;
  className?: string;
  style?: CSSProperties;
}

/**
 * spyMonk warehouse mark: the orange data stack, magnifier and crates.
 *
 * A raster on purpose. The earlier vector trace (still in assets/logo.svg)
 * lost each path's translate when it was flattened, so its pieces rendered
 * scattered and mostly outside the viewBox. This PNG is the image it was
 * traced from, on a transparent background.
 */
export default function Logo({ size = 34, title = 'spyMonk warehouse logo', className, style }: LogoProps) {
  return (
    <img
      src={logoMark}
      alt={title}
      height={size}
      width={Math.round((size * 817) / 367)}
      className={className}
      style={{ display: 'block', ...style }}
    />
  );
}
