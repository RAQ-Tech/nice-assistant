import type { RouteState } from './types';

export class Router {
  private listening = false;

  constructor(private readonly onRoute: (route: RouteState) => void) {}

  start(): void {
    if (this.listening) return;
    this.listening = true;
    window.addEventListener('hashchange', this.handleHash);
    this.onRoute(parseRoute(window.location.hash));
  }

  stop(): void {
    if (!this.listening) return;
    this.listening = false;
    window.removeEventListener('hashchange', this.handleHash);
  }

  home(replace = false): void {
    this.go('#/', replace);
  }

  chat(chatId: string, replace = false): void {
    this.go(`#/chats/${encodeURIComponent(chatId)}`, replace);
  }

  /**
   * A settings section, and optionally one thing inside it - a persona, a
   * model, a background role - so a page has an address of its own.
   */
  settings(section = 'General', replace = false, item: string | null = null): void {
    const path = item ? `${encodeURIComponent(section)}/${encodeURIComponent(item)}` : encodeURIComponent(section);
    this.go(`#/settings/${path}`, replace);
  }

  private readonly handleHash = (): void => {
    this.onRoute(parseRoute(window.location.hash));
  };

  private go(hash: string, replace: boolean): void {
    if (window.location.hash === hash) {
      this.onRoute(parseRoute(hash));
      return;
    }
    if (replace) window.history.replaceState(null, '', hash);
    else window.location.hash = hash;
    if (replace) this.onRoute(parseRoute(hash));
  }
}

export function parseRoute(hash: string): RouteState {
  const value = hash.replace(/^#\/?/, '');
  const [kind, encoded, encodedItem] = value.split('/', 3);
  if (kind === 'chats' && encoded) return { kind: 'chat', chatId: decodeURIComponent(encoded) };
  if (kind === 'settings') {
    const route: RouteState = { kind: 'settings', section: decodeURIComponent(encoded || 'General') };
    if (encodedItem) route.item = decodeURIComponent(encodedItem);
    return route;
  }
  return { kind: 'home' };
}
