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

export function markdown(text = ''): string {
  const blocks: string[] = [];
  let value = text.replace(/```([\s\S]*?)```/g, (_match, code: string) => {
    blocks.push(`<pre><code>${escapeHtml(code.trim())}</code></pre>`);
    return `__CODE_${blocks.length - 1}__`;
  });
  value = escapeHtml(value)
    .replace(/!\[(.*?)\]\(([^\s)]+)\)/g, '<img src="$2" alt="$1" class="msg-inline-image" loading="lazy" />')
    .replace(/\[(.*?)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noreferrer">$1</a>')
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/^\s*[-*]\s+(.+)$/gm, '<li>$1</li>');
  value = value.replace(/(<li>.*<\/li>)/gs, '<ul>$1</ul>');
  value = value
    .split(/\n{2,}/)
    .map((paragraph) => (paragraph.startsWith('<') ? paragraph : `<p>${paragraph.replace(/\n/g, '<br/>')}</p>`))
    .join('');
  blocks.forEach((block, index) => {
    value = value.replace(`__CODE_${index}__`, block);
  });
  return value;
}

interface FocusSnapshot {
  selector: string;
  index: number;
  start: number | null;
  end: number | null;
}

export function captureFocus(root: HTMLElement): FocusSnapshot | null {
  const active = document.activeElement;
  if (!(active instanceof HTMLInputElement || active instanceof HTMLTextAreaElement) || !root.contains(active)) {
    return null;
  }
  const fields = [...root.querySelectorAll<HTMLInputElement | HTMLTextAreaElement>('input, textarea')];
  const index = fields.indexOf(active);
  if (index < 0) return null;
  return {
    selector: 'input, textarea',
    index,
    start: active.selectionStart,
    end: active.selectionEnd,
  };
}

export function restoreFocus(root: HTMLElement, snapshot: FocusSnapshot | null, modalOpen: boolean): void {
  if (!snapshot || modalOpen) return;
  const fields = root.querySelectorAll<HTMLInputElement | HTMLTextAreaElement>(snapshot.selector);
  const target = fields.item(snapshot.index);
  if (!target || target.disabled) return;
  target.focus();
  if (snapshot.start !== null && snapshot.end !== null) target.setSelectionRange(snapshot.start, snapshot.end);
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
