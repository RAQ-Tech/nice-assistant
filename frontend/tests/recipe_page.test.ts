import { describe, expect, it, vi } from 'vitest';

import type { ApiClient } from '../src/api';
import { RecipePageView } from '../src/recipe_page_view';
import type { SettingsDialogs } from '../src/settings_contracts';
import { createState } from '../src/state';
import type { AppState, MediaPreset } from '../src/types';

/**
 * A recipe is a page of its own now, in the model page's shape: the name as
 * the headline, the note that says when to use it, the numbers, and the
 * wording folded. Help waits on hover; the one line said out loud is the one
 * that matters - that nothing says when to use it.
 */

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

async function opened(items: MediaPreset[], client: Partial<ApiClient> = {}) {
  const appState = createState();
  appState.mediaCatalog = {
    settings: { vram_budget_mb: 0, max_loras: 2 },
    resources: [{ id: 'm1', resource_type: 'model', kind: 'image', name: 'Illustrious', external_id: 'illustrious.safetensors', enabled: true }],
    vocabulary: { operations: [], domains: [], content_tags: [], features: [] },
  } as unknown as AppState['mediaCatalog'];
  const api = {
    mediaPresets: vi.fn().mockResolvedValue({ items }),
    updateMediaPreset: vi.fn().mockImplementation(async (_id: string, values: Record<string, unknown>) => ({ ...items[0], ...values })),
    deleteMediaPreset: vi.fn().mockResolvedValue(undefined),
    ...client,
  } as unknown as ApiClient;
  const dialogs = { confirm: vi.fn().mockResolvedValue(true), choice: vi.fn().mockResolvedValue(1) } as unknown as SettingsDialogs;
  const navigate = vi.fn();
  const view = new RecipePageView(appState, api, () => undefined, dialogs, navigate);
  await view.refresh();
  view.open(items[0]?.id ?? '');
  return { appState, api, view, navigate, dialogs };
}

function fieldNamed(node: HTMLElement, label: string): HTMLInputElement {
  return [...node.querySelectorAll('input')].find((input) => input.previousElementSibling?.textContent === label) as HTMLInputElement;
}

describe('the recipe page', () => {
  it('presents the values that decide how a picture comes out as named fields, with help on hover', async () => {
    const { view } = await opened([preset()]);
    const node = view.node();
    const text = node.textContent ?? '';

    expect(text).toContain('When should this be used?');
    expect(text).toContain('CFG');
    expect(text).toContain('Sizes');
    expect(text).toContain('Prompt style');
    expect(text).toContain('Raw definition');
    expect(node.querySelectorAll('.info-tip-trigger')).toHaveLength(0);
    expect(node.querySelectorAll('.page-hint')).toHaveLength(0);
    expect((node.querySelector('[data-testid="recipe-page-name"]') as HTMLInputElement).value).toBe('Illustrious booru');
    expect((node.querySelector('[data-testid="recipe-page-more"]') as HTMLDetailsElement).open).toBe(false);
    expect(fieldNamed(node, 'Steps').value).toBe('28');
    expect((node.querySelector('[data-testid="recipe-page-model"]') as HTMLSelectElement).selectedOptions[0]?.textContent).toBe('Illustrious');
  });

  it('says once, out loud, when nothing says when to use it', async () => {
    const { view } = await opened([preset({ routing_card: '' })]);
    const node = view.node();

    expect(node.querySelectorAll('.page-hint')).toHaveLength(1);
    expect(node.querySelector('[data-testid="recipe-page-hint"]')?.textContent).toContain('tags and priority');
  });

  it('says plainly when a model takes no negative prompt', async () => {
    const item = preset();
    item.definition.prompt_dialect.supports_negative = false;
    const { view } = await opened([item]);
    const text = view.node().textContent ?? '';

    expect(text).toContain('Accepts a negative prompt');
    expect(text).not.toContain('Negative prompt');
  });

  it('only offers to save once something changed, then saves the named fields into the definition', async () => {
    const { view, api } = await opened([preset()]);
    let node = view.node();
    expect((node.querySelector('[data-testid="recipe-page-save"]') as HTMLButtonElement).disabled).toBe(true);

    const card = node.querySelector('[data-testid="recipe-page-card"]') as HTMLTextAreaElement;
    card.value = 'Use for painterly portraits.';
    card.dispatchEvent(new Event('input'));
    node = view.node();
    const save = node.querySelector('[data-testid="recipe-page-save"]') as HTMLButtonElement;
    expect(save.disabled).toBe(false);
    save.click();

    await vi.waitFor(() => expect(api.updateMediaPreset).toHaveBeenCalledWith('p1', expect.objectContaining({
      routing_card: 'Use for painterly portraits.',
      definition: expect.objectContaining({
        base_model_resource_id: 'm1',
        sampler: expect.objectContaining({ steps: 28, cfg_scale: 7 }),
        dimensions: ['1024x1024', '832x1216'],
      }),
    })));
    expect((view.node().querySelector('[data-testid="recipe-page-save"]') as HTMLButtonElement).disabled).toBe(true);
  });

  it('follows a hand-edited raw definition once it parses', async () => {
    const { view } = await opened([preset()]);
    let node = view.node();
    const raw = node.querySelector('[data-testid="recipe-page-raw"]') as HTMLTextAreaElement;
    raw.value = JSON.stringify({ ...preset().definition, sampler: { steps: 40, cfg_scale: 5 } });
    raw.dispatchEvent(new Event('input'));

    node = view.node();
    expect(fieldNamed(node, 'Steps').value).toBe('40');
    expect(fieldNamed(node, 'CFG').value).toBe('5');
  });

  it('deletes after a confirmation and returns to the list', async () => {
    const { view, api, navigate, dialogs } = await opened([preset()]);
    (view.node().querySelector('[data-testid="recipe-page-delete"]') as HTMLButtonElement).click();

    await vi.waitFor(() => expect(api.deleteMediaPreset).toHaveBeenCalledWith('p1'));
    expect(dialogs.confirm).toHaveBeenCalled();
    expect(navigate).toHaveBeenCalledWith(null);
  });

  it('walks to the next recipe by its address, and says so when the address points at nothing', async () => {
    const { view, navigate } = await opened([preset(), preset({ id: 'p2', name: 'Realistic' })]);
    const node = view.node();
    (node.querySelector('[data-testid="recipe-page-next"]') as HTMLButtonElement).click();
    await vi.waitFor(() => expect(navigate).toHaveBeenCalledWith('p2'));

    view.open('gone');
    expect(view.node().textContent).toContain('no longer in the catalog');
  });
});
