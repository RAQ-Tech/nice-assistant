import { el, type ElementAttributes } from './dom';

/**
 * The app's icons: line drawings on a 24-unit grid, in the text colour.
 *
 * They replaced text characters - ☰ ⋯ ⚙ ↪ and a few emoji - which drew
 * differently on every phone, ignored the theme, and were read aloud by screen
 * readers as "gear" or "brain". An icon is decoration; the button it sits in
 * carries the name.
 */

const SVG = 'http://www.w3.org/2000/svg';

/** One shape: a path outline, or a solid one where `fill` is set. */
type Shape = { d: string; fill?: boolean };

const dot = (x: number, y: number, r = 1.6): Shape => ({
  d: `M${x - r} ${y}a${r} ${r} 0 1 0 ${2 * r} 0a${r} ${r} 0 1 0 ${-2 * r} 0`,
  fill: true,
});

const ICONS = {
  menu: [{ d: 'M4 7h16M4 12h16M4 17h16' }],
  more: [dot(6, 12), dot(12, 12), dot(18, 12)],
  settings: [{ d: 'M6 4v16M12 4v16M18 4v16' }, dot(6, 9, 2.2), dot(12, 15, 2.2), dot(18, 8, 2.2)],
  signOut: [{ d: 'M10 4H6a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h4M14 8l4 4-4 4M18 12H9' }],
  close: [{ d: 'M6 6l12 12M18 6L6 18' }],
  edit: [{ d: 'M4 20h4L19 9l-4-4L4 16v4zM13.5 6.5l4 4' }],
  play: [{ d: 'M8 5.5v13l10.5-6.5z', fill: true }],
  again: [{ d: 'M19.5 12a7.5 7.5 0 1 1-2.2-5.3M19.5 4.5v4.5H15' }],
  copy: [{ d: 'M9 9h10v11H9zM15 9V5a1 1 0 0 0-1-1H5a1 1 0 0 0-1 1v9a1 1 0 0 0 1 1h4' }],
  download: [{ d: 'M12 4v11M7.5 10.5L12 15l4.5-4.5M5 20h14' }],
  replay: [{ d: 'M4 9.5v5h3.5L12 18V6L7.5 9.5zM15.5 9.5a3.5 3.5 0 0 1 0 5M18 7a7 7 0 0 1 0 10' }],
  stop: [{ d: 'M7 7h10v10H7z', fill: true }],
  image: [{ d: 'M4 5h16a1 1 0 0 1 1 1v12a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1zM21 15.5l-5-5-8 8.5' }, dot(8.5, 9.5, 1.5)],
  show: [{ d: 'M2.5 12s3.5-6.5 9.5-6.5 9.5 6.5 9.5 6.5-3.5 6.5-9.5 6.5S2.5 12 2.5 12z' }, dot(12, 12, 2.6)],
  hide: [{ d: 'M2.5 12s3.5-6.5 9.5-6.5 9.5 6.5 9.5 6.5-3.5 6.5-9.5 6.5S2.5 12 2.5 12zM4 4l16 16' }],
  remember: [{ d: 'M6.5 4h11v16L12 16l-5.5 4z' }],
  down: [{ d: 'M12 5v14M6 13l6 6 6-6' }],
  previous: [{ d: 'M15 5l-7 7 7 7' }],
  next: [{ d: 'M9 5l7 7-7 7' }],
  remove: [{ d: 'M5 7h14M10 7V5h4v2M7 7l1 13h8l1-13M10.5 11v5M13.5 11v5' }],
  camera: [{ d: 'M4 8h3l1.5-2h7L17 8h3a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V9a1 1 0 0 1 1-1zM8.5 13a3.5 3.5 0 1 0 7 0a3.5 3.5 0 1 0-7 0' }],
} satisfies Record<string, Shape[]>;

export type IconName = keyof typeof ICONS;

export function icon(name: IconName): SVGSVGElement {
  const svg = document.createElementNS(SVG, 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('class', 'icon');
  svg.setAttribute('aria-hidden', 'true');
  svg.setAttribute('focusable', 'false');
  for (const shape of ICONS[name] as Shape[]) {
    const path = document.createElementNS(SVG, 'path');
    path.setAttribute('d', shape.d);
    if (shape.fill) path.setAttribute('class', 'icon-fill');
    svg.append(path);
  }
  return svg;
}

/** A button that is only an icon, named for screen readers and on hover. */
export function iconButton(name: IconName, label: string, attributes: ElementAttributes = {}): HTMLButtonElement {
  return el('button', { class: 'icon-btn', type: 'button', title: label, 'aria-label': label, ...attributes }, [icon(name)]);
}
