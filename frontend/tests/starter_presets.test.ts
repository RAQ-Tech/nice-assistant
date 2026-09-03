import { describe, expect, it, vi } from 'vitest';

import type { ApiClient } from '../src/api';
import { StarterPresetsView } from '../src/starter_presets_view';
import { createState } from '../src/state';
import type { StarterPreset } from '../src/types';

function starter(overrides: Partial<StarterPreset> = {}): StarterPreset {
  return {
    name: 'Flux (starter)',
    routing_card: 'Use when the request needs strong prompt adherence.',
    notes: 'Not tested on this deployment.',
    installable: true,
    already_present: false,
    missing_assets: [],
    ...overrides,
  };
}

function client(overrides: Partial<ApiClient> = {}): ApiClient {
  return {
    starterPresets: vi.fn().mockResolvedValue({ version: 1, presets: [starter()] }),
    installStarterPresets: vi.fn().mockResolvedValue({
      installed: [{ name: 'Flux (starter)', id: 'p1' }],
      skipped: [],
    }),
    ...overrides,
  } as unknown as ApiClient;
}

describe('starter presets', () => {
  it('says these are a starting point rather than a measurement', () => {
    const view = new StarterPresetsView(createState(), client(), () => undefined, async () => undefined);
    const hover = view.node().querySelector('.settings-subheading')?.getAttribute('title') ?? '';
    expect(hover).toContain('a starting point, not a measurement');
    expect(hover).toContain('tested on this deployment');
  });

  it('names the model file a starter needs instead of offering to install it', async () => {
    const api = client({
      starterPresets: vi.fn().mockResolvedValue({
        version: 1,
        presets: [starter({ installable: false, missing_assets: ['sd_xl_base_1.0.safetensors'] })],
      }),
    } as Partial<ApiClient>);
    const view = new StarterPresetsView(createState(), api, () => undefined, async () => undefined);

    (view.node().querySelector('[data-testid="starter-presets-check"]') as HTMLButtonElement).click();
    await vi.waitFor(() => expect(view.node().textContent).toContain('sd_xl_base_1.0.safetensors'));

    // Nothing installable, so no install action is offered at all.
    expect(view.node().querySelector('[data-testid="starter-presets-install"]')).toBeNull();
  });

  it('reports what was installed and what was skipped', async () => {
    const api = client({
      installStarterPresets: vi.fn().mockResolvedValue({
        installed: [{ name: 'Flux (starter)', id: 'p1' }],
        skipped: [{ name: 'SDXL portrait (starter)', reason: 'not installed: sd_xl_base_1.0.safetensors' }],
      }),
    } as Partial<ApiClient>);
    const view = new StarterPresetsView(createState(), api, () => undefined, async () => undefined);

    (view.node().querySelector('[data-testid="starter-presets-check"]') as HTMLButtonElement).click();
    await vi.waitFor(() => expect(view.node().querySelector('[data-testid="starter-presets-install"]')).toBeTruthy());
    (view.node().querySelector('[data-testid="starter-presets-install"]') as HTMLButtonElement).click();

    await vi.waitFor(() => {
      const text = view.node().textContent ?? '';
      expect(text).toContain('Installed Flux (starter)');
      expect(text).toContain('SDXL portrait (starter)');
    });
  });

  it('shows an existing preset as kept rather than replaceable', async () => {
    const api = client({
      starterPresets: vi.fn().mockResolvedValue({
        version: 1,
        presets: [starter({ installable: false, already_present: true })],
      }),
    } as Partial<ApiClient>);
    const view = new StarterPresetsView(createState(), api, () => undefined, async () => undefined);

    (view.node().querySelector('[data-testid="starter-presets-check"]') as HTMLButtonElement).click();
    await vi.waitFor(() => expect(view.node().textContent).toContain('will not be overwritten'));
  });
});
