import { describe, expect, it, vi } from 'vitest';

import type { ApiClient } from '../src/api';
import { PresetSettingsView } from '../src/preset_settings_view';
import { createState } from '../src/state';
import type { MediaPreset } from '../src/types';

function preset(overrides: Partial<MediaPreset> = {}): MediaPreset {
  return {
    id: 'p1',
    name: 'Illustrious booru',
    kind: 'image',
    enabled: true,
    priority: 50,
    routing_card: 'Use for illustrated pictures.',
    operations: ['generate'],
    domains: [],
    content_tags: [],
    features: [],
    definition: {
      base_model_resource_id: 'm1',
      prompt_dialect: {
        style: 'booru',
        prefix: 'score_9',
        suffix: '',
        negative_prompt: 'worst quality',
        supports_negative: true,
        trigger_placement: 'prefix',
        target_length: 0,
      },
      sampler: { steps: 28, cfg_scale: 7 },
      dimensions: ['1024x1024', '832x1216'],
    },
    estimated_vram_mb: 6000,
    notes: '',
    revision: 1,
    created_at: 1,
    updated_at: 1,
    ...overrides,
  };
}

function view(items: MediaPreset[], client: Partial<ApiClient> = {}) {
  const appState = createState();
  const api = {
    mediaPresets: vi.fn().mockResolvedValue({ items }),
    updateMediaPreset: vi.fn().mockResolvedValue(items[0]),
    deleteMediaPreset: vi.fn().mockResolvedValue(undefined),
    ...client,
  } as unknown as ApiClient;
  return { appState, api, instance: new PresetSettingsView(appState, api, () => undefined) };
}

describe('preset settings', () => {
  it('presents the values that decide how a picture comes out as named fields', async () => {
    const { instance } = view([preset()]);
    await instance.refresh();
    const text = instance.node().map((node) => node.textContent).join(' ');

    expect(text).toContain('Prompt style');
    expect(text).toContain('CFG');
    expect(text).toContain('Dimensions');
    expect(text).toContain('When should this be used?');
    // The raw shape stays reachable but is no longer the only way in.
    expect(text).toContain('Raw definition');
  });

  it('says plainly when a model takes no negative prompt', async () => {
    const item = preset();
    item.definition.prompt_dialect.supports_negative = false;
    const { instance } = view([item]);
    await instance.refresh();
    const text = instance.node().map((node) => node.textContent).join(' ');

    expect(text).toContain('safety negative cannot be carried');
    expect(text).not.toContain('Negative prompt\n');
  });

  it('warns when a preset has nothing describing when to use it', async () => {
    const { instance } = view([preset({ routing_card: '' })]);
    await instance.refresh();
    expect(instance.node().map((node) => node.textContent).join(' ')).toContain('No routing card yet');
  });

  it('only offers to save once something changed', async () => {
    const { instance } = view([preset()]);
    await instance.refresh();

    const editor = instance.node()[1]!;
    const save = editor.querySelector('[data-testid="preset-save-p1"]') as HTMLButtonElement;
    expect(save.disabled).toBe(true);

    const name = editor.querySelectorAll('input')[0] as HTMLInputElement;
    name.value = 'Renamed';
    name.dispatchEvent(new Event('input', { bubbles: true }));

    const after = instance.node()[1]!.querySelector('[data-testid="preset-save-p1"]') as HTMLButtonElement;
    expect(after.disabled).toBe(false);
  });

  it('tells the operator when there are no presets and where they come from', async () => {
    const { instance } = view([]);
    await instance.refresh();
    expect(instance.node()[0]!.textContent).toContain('Enable an image model, or install a starter preset');
  });
});
