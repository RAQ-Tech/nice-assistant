import { afterEach, describe, expect, it, vi } from 'vitest';

import { applyRememberedTheme, applyTheme, themeLabel } from '../src/theme';

type Listener = () => void;

function deviceIs(light: boolean): { flip: (next: boolean) => void } {
  let matches = light;
  const listeners: Listener[] = [];
  vi.stubGlobal('matchMedia', (query: string) => ({
    get matches() {
      return query.includes('light') ? matches : !matches;
    },
    addEventListener: (_type: string, listener: Listener) => listeners.push(listener),
  }));
  return {
    flip: (next: boolean) => {
      matches = next;
      listeners.forEach((listener) => listener());
    },
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

describe('theme', () => {
  it('matches the device, and follows it when it changes', () => {
    const device = deviceIs(true);
    applyTheme('system');
    expect(document.documentElement.dataset.theme).toBe('light');
    device.flip(false);
    expect(document.documentElement.dataset.theme).toBe('dark');
  });

  it('a chosen theme stays put whatever the device does', () => {
    const device = deviceIs(true);
    applyTheme('dark');
    device.flip(true);
    expect(document.documentElement.dataset.theme).toBe('dark');
  });

  it('remembers the last choice for the next first paint', () => {
    deviceIs(false);
    applyTheme('light');
    document.documentElement.dataset.theme = '';
    applyRememberedTheme();
    expect(document.documentElement.dataset.theme).toBe('light');
  });

  it('treats anything unknown as the device setting', () => {
    deviceIs(true);
    applyTheme('sepia');
    expect(document.documentElement.dataset.theme).toBe('light');
    expect(themeLabel('system')).toBe('Match my device');
  });
});
