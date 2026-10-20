import { useId, type CSSProperties, type ReactNode } from 'react';

type WindowTone = 'coral' | 'orange' | 'lavender' | 'tan';

interface RetroWindowProps {
  title: ReactNode;
  tone?: WindowTone;
  /** Controls at the right end of the title bar, e.g. a close button. */
  actions?: ReactNode;
  className?: string;
  bodyClassName?: string;
  children: ReactNode;
}

const cx = (...names: Array<string | false | undefined>) => names.filter(Boolean).join(' ');

/** The three circles at the left of every title bar. Decoration only. */
export function WindowDots() {
  return (
    <span className="win-dots" aria-hidden="true">
      <i />
      <i />
      <i />
    </span>
  );
}

/**
 * A window from the retro desktop: ink outline, coloured title bar, hard
 * offset shadow. The title is an <h2> and labels the window, so each one is
 * a named region a screen reader can jump between.
 */
export default function RetroWindow({ title, tone = 'coral', actions, className, bodyClassName, children }: RetroWindowProps) {
  const titleId = useId();

  return (
    <section className={cx('win', `win--${tone}`, className)} aria-labelledby={titleId}>
      <div className="win-bar">
        <WindowDots />
        <h2 id={titleId} className="win-title">{title}</h2>
        {actions && <div className="win-actions">{actions}</div>}
      </div>
      <div className={cx('win-body', bodyClassName)}>{children}</div>
    </section>
  );
}

/** The four-point star scattered around the windows on the mood boards. */
export function Sparkle({ size = 18, style }: { size?: number; style?: CSSProperties }) {
  return (
    <svg className="sparkle" width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden="true" style={style}>
      <path
        d="M12 2c.6 4.6 2.4 6.9 7.5 8-5.1 1.1-6.9 3.4-7.5 8-.6-4.6-2.4-6.9-7.5-8C9.6 8.9 11.4 6.6 12 2Z"
        fill="var(--paper-1)"
        stroke="var(--ink-0)"
        strokeWidth="1.8"
        strokeLinejoin="round"
      />
    </svg>
  );
}
