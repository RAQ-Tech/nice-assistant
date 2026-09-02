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
  );
  const root = document.createElement('div');
  return { root, settings, view, change };
}

describe('the image page speaks in shapes, not typed sizes', () => {
  it('offers named shapes and keeps unusual sizes reachable as Custom', () => {
    const { root, settings, view } = setup({ image_size: '1024x1024' });
    root.append(...view.nodes('Image Generation', settings));

    const shape = root.querySelector('[data-testid="image-shape"]') as HTMLSelectElement;
    expect(shape.value).toBe('1024x1024');
    expect([...shape.options].map((option) => option.textContent)).toContain('Portrait — 832×1216');
    // A stored size the picker does not name is shown, not rewritten.
    const custom = setup({ image_size: '1152x896' });
    custom.root.append(...custom.view.nodes('Image Generation', custom.settings));
    expect((custom.root.querySelector('[data-testid="image-shape"]') as HTMLSelectElement).value).toBe('custom');
    expect(custom.root.textContent).toContain('Custom size');
  });

  it('shows prompt enhancement only to the provider that reads it', () => {
    const local = setup({ image_provider: 'local' });
    local.root.append(...local.view.nodes('Image Generation', local.settings));
    expect(local.root.textContent).not.toContain('Prompt enhancement');

    const cloud = setup({ image_provider: 'openai' });
    cloud.root.append(...cloud.view.nodes('Image Generation', cloud.settings));
    expect(cloud.root.textContent).toContain('Prompt enhancement');
  });
});

describe('video is local only', () => {
  it('offers Off and Local, and no cloud option at all', () => {
    const { root, settings, view } = setup();
    root.append(...view.nodes('Video Generation', settings));

    const provider = root.querySelector('[data-testid="video-provider"]') as HTMLSelectElement;
    const options = [...provider.options].map((option) => option.value);
    expect(options).toEqual(['disabled', 'local']);
    // The sora-era controls are gone with the provider they served.
    expect(root.textContent).not.toContain('sora');
    expect(root.textContent).not.toContain('Duration');
  });

  it('renders a stored cloud choice as Off rather than keeping it alive', () => {
    const { root, settings, view } = setup({ video_provider: 'openai' });
    root.append(...view.nodes('Video Generation', settings));

    const provider = root.querySelector('[data-testid="video-provider"]') as HTMLSelectElement;
    expect(provider.value).toBe('disabled');
    expect(root.textContent).toContain('Video generation is off.');
  });

  it('says what local video needs and checks the same ComfyUI pictures use', () => {
    const { root, settings, view } = setup({ video_provider: 'local' });
    root.append(...view.nodes('Video Generation', settings));

    expect(root.textContent).toContain('What local video needs');
    expect(root.textContent).toContain('Media Catalog');
    expect(root.textContent).toContain('check comfyui');
  });
});

describe('the everyday pages are sparse', () => {
  it('shows the few General choices, with help on hover and the rest folded', () => {
    const { root, settings, view } = setup();
    root.append(...view.nodes('General', settings));

    expect(root.querySelectorAll('.info-tip-trigger')).toHaveLength(0);
    expect(root.querySelectorAll('.settings-intro')).toHaveLength(0);
    expect(root.textContent).toContain('Theme');
    expect(root.textContent).toContain('Speak replies aloud');
    const speak = [...root.querySelectorAll('.setting-toggle-row')]
      .find((row) => row.textContent?.includes('Speak replies aloud')) as HTMLElement;
    expect(speak.title).toContain('Plays each finished reply');
    expect((root.querySelector('[data-testid="general-advanced-settings"]') as HTMLDetailsElement).open).toBe(false);
    expect(root.textContent).toContain('Show system and tool messages');
  });

  it('shows the fields for the chosen speech provider and names where it runs', () => {
    const off = setup();
    off.root.append(...off.view.nodes('TTS', off.settings));
    expect(off.root.textContent).not.toContain('Service address');
    expect(off.root.textContent).not.toContain('check kokoro');

    const local = setup({ tts_provider: 'local' });
    local.root.append(...local.view.nodes('TTS', local.settings));
    const provider = local.root.querySelector('[data-testid="tts-provider"]') as HTMLSelectElement;
    expect([...provider.options].map((option) => option.textContent)).toEqual([
      'Off',
      'Local service — on this machine',
      'Openai — leaves this machine',
    ]);
    expect(local.root.textContent).toContain('Service address');
    expect(local.root.textContent).toContain('check kokoro');
    expect(local.root.querySelectorAll('.page-hint').length).toBeLessThanOrEqual(1);
  });

  it('offers hands-free listening only once something can transcribe', () => {
    const off = setup();
    off.root.append(...off.view.nodes('STT', off.settings));
    expect(off.root.querySelector('[data-testid="stt-hands-free"]')).toBeNull();

    const local = setup({ stt_provider: 'local', stt_local_backend: 'wyoming' });
    local.root.append(...local.view.nodes('STT', local.settings));
    expect(local.root.querySelector('[data-testid="stt-hands-free"]')).not.toBeNull();
    expect(local.root.textContent).toContain('Wyoming');
    expect(local.root.textContent).not.toContain('Model');
    expect(local.root.textContent).toContain('check whisper');
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

  it('keeps provider credentials behind the fold on the profile page', () => {
    const { root, settings, view } = setup();
    root.append(...view.nodes('User', settings));

    expect(root.textContent).toContain('About you');
    expect(root.querySelectorAll('.page-hint')).toHaveLength(1);
    const fold = root.querySelector('[data-testid="user-advanced-settings"]') as HTMLDetailsElement;
    expect(fold.open).toBe(false);
    expect(fold.querySelector('input[type="password"]')).not.toBeNull();
  });
});
