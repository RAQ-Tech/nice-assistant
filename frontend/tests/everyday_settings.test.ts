import { describe, expect, it, vi } from 'vitest';

import { EverydaySettingsView } from '../src/everyday_settings_view';
import { SETTINGS_DEFAULTS } from '../src/settings';
import { createState } from '../src/state';
import type { Settings } from '../src/types';

function setup(overrides: Partial<Settings> = {}) {
  const appState = createState();
  appState.models = ['local-model'];
  const settings = { ...SETTINGS_DEFAULTS, ...overrides } as Settings;
  const change = vi.fn();
  const view = new EverydaySettingsView(
    appState,
    change,
    (provider) => document.createTextNode(`check ${provider}`) as unknown as HTMLElement,
    () => document.createElement('div'),
  );
  const root = document.createElement('div');
  return { root, settings, view, change };
}

describe('everyday settings presentation', () => {
  it('keeps common General choices visible and optional controls closed', () => {
    const { root, settings, view } = setup();
    root.append(...view.nodes('General', settings));

    expect(root.textContent).toContain('Choose the everyday experience');
    expect(root.querySelectorAll('.info-tip-trigger').length).toBeGreaterThan(3);
    expect((root.querySelector('[data-testid="general-advanced-settings"]') as HTMLDetailsElement).open).toBe(false);
    expect(root.textContent).toContain('Show system and tool messages');
  });

  it('shows local image connection choices while collapsing tuning details', () => {
    const { root, settings, view } = setup({ image_provider: 'local', image_local_backend: 'comfyui' });
    root.append(...view.nodes('Image Generation', settings));

    expect(root.textContent).not.toContain('When you explicitly ask for a picture');
    expect(root.textContent).toContain('Local image service');
    expect(root.textContent).toContain('Service address');
    expect(root.textContent).toContain('Additional JSON parameters');
    expect((root.querySelector('[data-testid="image-advanced-settings"]') as HTMLDetailsElement).open).toBe(false);
  });

  it('keeps provider credentials behind optional disclosure', () => {
    const { root, settings, view } = setup();
    root.append(...view.nodes('User', settings));

    expect(root.textContent).toContain('Your account defaults');
    expect((root.querySelector('[data-testid="user-advanced-settings"]') as HTMLDetailsElement).open).toBe(false);
  });
});
