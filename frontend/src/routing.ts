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

  settings(section = 'General', replace = false): void {
    this.go(`#/settings/${encodeURIComponent(section)}`, replace);
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
  const [kind, encoded] = value.split('/', 2);
  if (kind === 'chats' && encoded) return { kind: 'chat', chatId: decodeURIComponent(encoded) };
  if (kind === 'settings') return { kind: 'settings', section: decodeURIComponent(encoded || 'General') };
  return { kind: 'home' };
}
