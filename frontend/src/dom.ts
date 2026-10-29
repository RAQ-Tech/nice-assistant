export type Child = Node | string | number | null | undefined | false;

export interface ElementAttributes {
  class?: string;
  textContent?: string;
  html?: string;
  ariaLabel?: string;
  ariaHidden?: boolean;
  [key: string]: unknown;
}

export function el<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  attributes: ElementAttributes = {},
  children: Child | Child[] = [],
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attributes)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === 'class') node.className = String(value);
    else if (key === 'html') node.innerHTML = String(value);
    else if (key === 'ariaLabel') node.setAttribute('aria-label', String(value));
    else if (key === 'ariaHidden') node.setAttribute('aria-hidden', String(value));
    else if (key.startsWith('on') && typeof value === 'function') {
      node.addEventListener(key.slice(2), value as EventListener);
    } else if (key === 'style' && typeof value === 'object') {
      Object.assign(node.style, value);
    } else if (key in node) {
      Reflect.set(node, key, value);
    } else {
      node.setAttribute(key, String(value));
    }
  }
  const values = Array.isArray(children) ? children : [children];
  for (const child of values) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

export function escapeHtml(value: string): string {
  return value.replace(/[&<>"']/g, (character) => {
    const replacements: Record<string, string> = {
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      '"': '&quot;',
      "'": '&#39;',
    };
    return replacements[character] ?? character;
  });
}

interface FocusSnapshot {
  /** What the control is: its tag and the name a person would know it by. */
  key: string;
  /** Which of the controls with that key. */
  index: number;
  /** Where it was among every focusable control, for when its name changed. */
  position: number;
  start: number | null;
  end: number | null;
}

/**
 * Where a scrolling region was, so a re-render can put it back.
 *
 * Sits beside focus capture because it is the same problem: rendering replaces
 * the tree, and anything the browser was holding on the reader's behalf goes
 * with it.
 */
export function captureScroll(selector: string): number | null {
  const pane = document.querySelector<HTMLElement>(selector);
  return pane ? pane.scrollTop : null;
}

export function restoreScroll(selector: string, top: number | null): void {
  // Zero is where a fresh pane already is, so restoring it is a no-op worth
  // skipping rather than a position worth forcing.
  if (top === null || top <= 0) return;
  const pane = document.querySelector<HTMLElement>(selector);
  if (pane) pane.scrollTop = top;
}

/**
 * Every scrolled region on the page, so a re-render can put them all back.
 *
 * The messages pane got this treatment first, and every other screen turned
 * out to need it too: settings, the homepage, the chat drawer, and the
 * overlays each scroll their own pane, and rebuilding the tree yanked each of
 * them to the top the moment any control was touched. One sweep over the
 * document catches whatever scrolls today or grows a scrollbar later, where a
 * list of selectors would quietly go stale.
 *
 * A pane's shape - tag, id, class, position among look-alikes - says which
 * kind of pane it is. Its opening text says which content it is showing. Both
 * have to match before a position is restored: a settings detail pane showing
 * Memory is not the same place as one showing General, and restoring one onto
 * the other would drop the reader mid-way down a page they have not read.
 * Navigation therefore still starts at the top, while the panes that carried
 * over - the section list, the chat drawer - keep their place.
 */
export interface ScrollSnapshot {
  shape: string;
  key: string;
  index: number;
  top: number;
  left: number;
}

function paneShape(node: Element): string {
  return `${node.tagName}|${node.id}|${node.getAttribute('class') ?? ''}`;
}

function paneIdentity(node: Element): string {
  // The first heading names what a pane is showing; a pane without one is
  // fingerprinted by whatever it opens with instead.
  const anchor = node.querySelector('h1, h2, h3, h4') ?? node.firstElementChild;
  return `${paneShape(node)}|${(anchor?.textContent ?? '').slice(0, 64)}`;
}

/**
 * Counting per full identity, gated by shape: elements of other shapes can
 * never produce a matching identity, so skipping them cannot change any
 * per-identity index, and the sweep stays one cheap string check per element.
 */
export function captureScrollPositions(root: ParentNode): ScrollSnapshot[] {
  const scrolled = new Set<HTMLElement>();
  const shapes = new Set<string>();
  for (const node of root.querySelectorAll<HTMLElement>('*')) {
    if (node.scrollTop <= 0 && node.scrollLeft <= 0) continue;
    scrolled.add(node);
    shapes.add(paneShape(node));
  }
  if (!scrolled.size) return [];
  const seen = new Map<string, number>();
  const snapshots: ScrollSnapshot[] = [];
  for (const node of root.querySelectorAll<HTMLElement>('*')) {
    const shape = paneShape(node);
    if (!shapes.has(shape)) continue;
    const key = paneIdentity(node);
    const index = seen.get(key) ?? 0;
    seen.set(key, index + 1);
    if (scrolled.has(node)) snapshots.push({ shape, key, index, top: node.scrollTop, left: node.scrollLeft });
  }
  return snapshots;
}

export function restoreScrollPositions(root: ParentNode, snapshots: ScrollSnapshot[]): void {
  if (!snapshots.length) return;
  const shapes = new Set(snapshots.map((snapshot) => snapshot.shape));
  const wanted = new Map<string, ScrollSnapshot>();
  for (const snapshot of snapshots) wanted.set(`${snapshot.key}@${snapshot.index}`, snapshot);
  const seen = new Map<string, number>();
  for (const node of root.querySelectorAll<HTMLElement>('*')) {
    if (!shapes.has(paneShape(node))) continue;
    const key = paneIdentity(node);
    const index = seen.get(key) ?? 0;
    seen.set(key, index + 1);
    const snapshot = wanted.get(`${key}@${index}`);
    if (!snapshot) continue;
    node.scrollTop = snapshot.top;
    node.scrollLeft = snapshot.left;
    wanted.delete(`${key}@${index}`);
    if (!wanted.size) return;
  }
}

const FOCUSABLE = 'input, textarea, select, button, a[href], [tabindex]:not([tabindex="-1"])';

function focusKey(node: Element): string {
  const name = node.getAttribute('data-testid') || node.getAttribute('aria-label') || node.getAttribute('name')
    || node.getAttribute('title') || (node.tagName === 'BUTTON' ? (node.textContent ?? '').trim().slice(0, 40) : '');
  return `${node.tagName}|${name}`;
}

/**
 * Which control had focus, so a re-render can give it back.
 *
 * Rendering replaces every element, and the browser's focus goes with them:
 * a keyboard user who pressed a button was sent back to the top of the page
 * each time. Text fields were the first to be kept, by position; any control
 * is kept now, by what it is called, so a list that changed above it does not
 * move focus onto its neighbour.
 */
export function captureFocus(root: HTMLElement): FocusSnapshot | null {
  const active = document.activeElement;
  if (!(active instanceof HTMLElement) || !root.contains(active) || !active.matches(FOCUSABLE)) return null;
  const key = focusKey(active);
  const all = [...root.querySelectorAll(FOCUSABLE)];
  const field = active instanceof HTMLInputElement || active instanceof HTMLTextAreaElement;
  return {
    key,
    index: all.filter((node) => focusKey(node) === key).indexOf(active),
    position: all.indexOf(active),
    start: field ? active.selectionStart : null,
    end: field ? active.selectionEnd : null,
  };
}

export function restoreFocus(root: HTMLElement, snapshot: FocusSnapshot | null, modalOpen: boolean): void {
  if (!snapshot || modalOpen) return;
  const all = [...root.querySelectorAll<HTMLElement>(FOCUSABLE)];
  const target = all.filter((node) => focusKey(node) === snapshot.key)[snapshot.index]
    ?? (snapshot.key.split('|')[0] === all[snapshot.position]?.tagName ? all[snapshot.position] : undefined);
  if (!target || (target as HTMLButtonElement).disabled) return;
  const field = target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement;
  target.focus({ preventScroll: !field });
  if (field && snapshot.start !== null && snapshot.end !== null) {
    try {
      target.setSelectionRange(snapshot.start, snapshot.end);
    } catch {
      // Not every input type keeps a selection.
    }
  }
}

export function formatDate(timestamp: number | null | undefined): string {
  if (!timestamp) return '';
  return new Date(timestamp * 1000).toLocaleString([], {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

export function formatBytes(bytes = 0): string {
  if (!bytes) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  const index = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)));
  return `${(bytes / 1024 ** index).toFixed(index ? 1 : 0)} ${units[index] ?? 'B'}`;
}

export function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}

export async function copyText(value: string): Promise<void> {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(value);
    return;
  }
  const area = document.createElement('textarea');
  area.value = value;
  area.style.position = 'fixed';
  area.style.opacity = '0';
  document.body.append(area);
  area.select();
  try {
    if (!document.execCommand('copy')) throw new Error('Copy is unavailable.');
  } finally {
    area.remove();
  }
}

export function downloadUrl(url: string, filename: string): void {
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  anchor.rel = 'noopener';
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
}
