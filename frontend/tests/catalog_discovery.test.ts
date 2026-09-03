import { describe, expect, it, vi } from 'vitest';

import type { ApiClient } from '../src/api';
import { CatalogModelsView } from '../src/catalog_models_view';
import { WorkflowImportView } from '../src/workflow_import_view';
import { normalizeSettings } from '../src/settings';
import { createState } from '../src/state';
import type { MediaCatalogResource } from '../src/types';

function model(overrides: Partial<MediaCatalogResource> = {}): MediaCatalogResource {
  return {
    id: 'model-1',
    resource_type: 'model',
    kind: 'image',
    name: 'DreamShaper XL',
    provider_key: 'local-image',
    backend: 'comfyui',
    external_id: 'dreamshaper.safetensors',
    enabled: true,
    priority: 50,
    operations: ['generate'],
    domains: [],
    content_tags: ['general'],
    features: ['text_to_image'],
    estimated_vram_mb: 7168,
    estimated_load_seconds: 0,
    default_settings: {},
    notes: '',
    compatible_model_ids: [],
    revision: 1,
    created_at: 1,
    updated_at: 1,
    ...overrides,
  };
}

describe('finding models on ComfyUI', () => {
  function built(client: Partial<ApiClient>) {
    const appState = createState();
    const refresh = vi.fn(async () => undefined);
    const view = new CatalogModelsView(appState, client as ApiClient, vi.fn(), refresh);
    return { appState, view, refresh };
  }

  it('warns that one model means one look', () => {
    const { view } = built({});

    const node = view.node([model()]);

    // The warning waits on hover now; the catalog page says it out loud once.
    expect(node.querySelector('.settings-subheading')?.getAttribute('title')).toContain('One model means every picture shares its look');
    expect(node.querySelector('.info-tip-trigger')).toBeNull();
  });

  it('lists what ComfyUI has, minus what the catalog already knows', async () => {
    const { view } = built({
      comfyuiCheckpoints: vi.fn().mockResolvedValue({
        ok: true,
        message: '3 checkpoint file(s) reported by ComfyUI.',
        checkpoints: [
          { name: 'dreamshaper.safetensors', cataloged: true },
          { name: 'juggernaut.safetensors', cataloged: false },
          { name: 'anything-v5.safetensors', cataloged: false },
        ],
      }),
    });
    const first = view.node([model()]);
    (first.querySelector('[data-testid="catalog-discover-models"]') as HTMLButtonElement).click();
    await vi.waitFor(() => {
      expect(view.node([model()]).querySelector('[data-testid="catalog-discovery-list"]')).toBeTruthy();
    });

    const node = view.node([model()]);
    // Already-cataloged names are not offered again: ticking them could only
    // produce a duplicate or a refusal.
    expect(node.textContent).toContain('juggernaut.safetensors');
    expect(node.querySelector('[data-testid="catalog-discovery-dreamshaper.safetensors"]')).toBeNull();
  });

  it('adds exactly what was ticked', async () => {
    const addModelsFromCheckpoints = vi.fn().mockResolvedValue({ added: ['juggernaut.safetensors'], skipped: [] });
    const listing = {
      ok: true,
      message: '',
      checkpoints: [
        { name: 'juggernaut.safetensors', cataloged: false },
        { name: 'anything-v5.safetensors', cataloged: false },
      ],
    };
    const { view, refresh } = built({
      comfyuiCheckpoints: vi.fn().mockResolvedValue(listing),
      addModelsFromCheckpoints,
    });
    (view.node([]).querySelector('[data-testid="catalog-discover-models"]') as HTMLButtonElement).click();
    await vi.waitFor(() => {
      expect(view.node([]).querySelector('[data-testid="catalog-discovery-list"]')).toBeTruthy();
    });
    (view.node([]).querySelector('[data-testid="catalog-discovery-juggernaut.safetensors"]') as HTMLInputElement)
      .dispatchEvent(new Event('change'));
    const checkbox = view.node([]).querySelector(
      '[data-testid="catalog-discovery-juggernaut.safetensors"]',
    ) as HTMLInputElement;
    checkbox.checked = true;
    checkbox.dispatchEvent(new Event('change'));
    (view.node([]).querySelector('[data-testid="catalog-add-selected"]') as HTMLButtonElement).click();

    await vi.waitFor(() => expect(addModelsFromCheckpoints).toHaveBeenCalledWith(['juggernaut.safetensors']));
    // The catalog refetches so the new models and their recipes appear.
    await vi.waitFor(() => expect(refresh).toHaveBeenCalled());
  });

  it('says plainly when ComfyUI is down instead of erroring', async () => {
    const { view } = built({
      comfyuiCheckpoints: vi.fn().mockResolvedValue({
        ok: false,
        message: 'ComfyUI is not reachable. Start it, then try again.',
        checkpoints: [],
      }),
    });
    (view.node([]).querySelector('[data-testid="catalog-discover-models"]') as HTMLButtonElement).click();

    await vi.waitFor(() => {
      expect(view.node([]).textContent).toContain('ComfyUI is not reachable');
    });
  });
});

describe('bringing your own workflow', () => {
  function built(client: Partial<ApiClient>) {
    const appState = createState();
    const view = new WorkflowImportView(appState, client as ApiClient, vi.fn(), vi.fn(async () => undefined));
    return { appState, view };
  }

  function candidates(prompt: object[], extra: Partial<Record<string, object[]>> = {}) {
    return { prompt, seed: [], width: [], height: [], checkpoint: [], ...extra };
  }

  function typeAndCheck(view: WorkflowImportView, models: MediaCatalogResource[], name: string, json: string) {
    const node = view.node(models);
    const inputs = [...node.querySelectorAll('input, textarea')] as (HTMLInputElement | HTMLTextAreaElement)[];
    inputs[0]!.value = name;
    inputs[0]!.dispatchEvent(new Event('input'));
    inputs[1]!.value = json;
    inputs[1]!.dispatchEvent(new Event('input'));
    (view.node(models).querySelector('[data-testid="workflow-import-submit"]') as HTMLButtonElement).click();
  }

  it('says what it accepts before anything is pasted', () => {
    const { view } = built({});

    expect(view.node([model()]).querySelector('.settings-subheading')?.getAttribute('title')).toContain('Export (API)');
  });

  it('refuses non-JSON with directions instead of an error code', async () => {
    const { view } = built({});

    typeAndCheck(view, [model()], 'My graph', 'not json at all');

    await vi.waitFor(() => {
      expect(view.node([model()]).textContent).toContain('Workflow → Export (API)');
    });
  });

  it('checks with ComfyUI, then adds on confirmation with inspected bindings', async () => {
    const createMediaCatalogResource = vi.fn().mockResolvedValue(model());
    const inspectIdentityWorkflow = vi.fn().mockResolvedValue({
      provider_compatible: true,
      message: '',
      request_input_candidates: candidates(
        [{ node_id: '2', input_name: 'text', label: 'CLIPTextEncode (node 2)', current_value: 'a portrait' }],
        {
          seed: [{ node_id: '5', input_name: 'seed', label: 'KSampler (node 5)', current_value: '7' }],
          checkpoint: [{ node_id: '1', input_name: 'ckpt_name', label: 'CheckpointLoaderSimple (node 1)', current_value: 'baked.safetensors' }],
        },
      ),
    });
    const { view } = built({ inspectIdentityWorkflow, createMediaCatalogResource });
    const models = [model(), model({ id: 'model-2', external_id: 'other.safetensors' })];

    typeAndCheck(view, models, 'My graph', '{"2": {"class_type": "CLIPTextEncode", "inputs": {"text": ""}}}');
    await vi.waitFor(() => {
      expect(view.node(models).querySelector('[data-testid="workflow-import-confirm"]')).toBeTruthy();
    });
    (view.node(models).querySelector('[data-testid="workflow-import-confirm"]') as HTMLButtonElement).click();

    await vi.waitFor(() => expect(createMediaCatalogResource).toHaveBeenCalled());
    // Inspected as a general graph - no identity node required.
    expect(inspectIdentityWorkflow.mock.calls[0]?.[2]).toBe('general');
    const created = createMediaCatalogResource.mock.calls[0]?.[0];
    expect(created.default_settings.prompt_bindings).toEqual([{ node_id: '2', input_name: 'text' }]);
    expect(created.default_settings.seed_bindings).toEqual([{ node_id: '5', input_name: 'seed' }]);
    // The checkpoint is bound, which is what lets a preset run its own model
    // through the graph - and what justifies pairing it with every model.
    expect(created.default_settings.checkpoint_bindings).toEqual([{ node_id: '1', input_name: 'ckpt_name' }]);
    expect(created.compatible_model_ids).toEqual(['model-1', 'model-2']);
  });

  it('lets a person choose the landing spot when the graph has several text inputs', async () => {
    const createMediaCatalogResource = vi.fn().mockResolvedValue(model());
    const { view } = built({
      inspectIdentityWorkflow: vi.fn().mockResolvedValue({
        provider_compatible: true,
        message: '',
        request_input_candidates: candidates([
          { node_id: '2', input_name: 'text', label: 'CLIPTextEncode (node 2)', current_value: 'ugly, blurry' },
          { node_id: '3', input_name: 'text', label: 'CLIPTextEncode (node 3)', current_value: 'a portrait' },
        ]),
      }),
      createMediaCatalogResource,
    });

    typeAndCheck(view, [model()], 'My graph', '{"2": {}}');
    await vi.waitFor(() => {
      expect(view.node([model()]).querySelector('[data-testid="workflow-import-prompt-choice"]')).toBeTruthy();
    });
    // The first candidate holds "ugly, blurry" - the negative prompt. Guessing
    // it would wire every request into the wrong box; choosing is the fix.
    const select = view.node([model()]).querySelector('[data-testid="workflow-import-prompt-choice"]') as HTMLSelectElement;
    select.value = '3:text';
    select.dispatchEvent(new Event('change'));
    (view.node([model()]).querySelector('[data-testid="workflow-import-confirm"]') as HTMLButtonElement).click();

    await vi.waitFor(() => expect(createMediaCatalogResource).toHaveBeenCalled());
    const created = createMediaCatalogResource.mock.calls[0]?.[0];
    expect(created.default_settings.prompt_bindings).toEqual([{ node_id: '3', input_name: 'text' }]);
  });

  it('pairs a graph with no checkpoint input only with its baked-in model', async () => {
    const createMediaCatalogResource = vi.fn().mockResolvedValue(model());
    const { view } = built({
      inspectIdentityWorkflow: vi.fn().mockResolvedValue({
        provider_compatible: true,
        message: '',
        request_input_candidates: candidates([
          { node_id: '2', input_name: 'text', label: 'CLIPTextEncode (node 2)', current_value: '' },
        ]),
      }),
      createMediaCatalogResource,
    });
    const models = [model(), model({ id: 'model-2', external_id: 'baked.safetensors' })];

    typeAndCheck(view, models, 'My graph',
      '{"1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "baked.safetensors"}}}');
    await vi.waitFor(() => {
      expect(view.node(models).querySelector('[data-testid="workflow-import-confirm"]')).toBeTruthy();
    });
    (view.node(models).querySelector('[data-testid="workflow-import-confirm"]') as HTMLButtonElement).click();

    await vi.waitFor(() => expect(createMediaCatalogResource).toHaveBeenCalled());
    // Without a checkpoint binding the graph always loads baked.safetensors,
    // so claiming it pairs with model-1 would be the catalog inferring
    // capability - the thing its own documentation forbids.
    expect(createMediaCatalogResource.mock.calls[0]?.[0].compatible_model_ids).toEqual(['model-2']);
  });

  it('relays the refusal when the graph has nowhere for a prompt', async () => {
    const { view } = built({
      inspectIdentityWorkflow: vi.fn().mockResolvedValue({
        provider_compatible: false,
        message: 'The workflow was not accepted: ComfyUI could not confirm a place for the request prompt to land.',
        request_input_candidates: candidates([]),
      }),
      createMediaCatalogResource: vi.fn(),
    });

    typeAndCheck(view, [model()], 'My graph', '{"1": {"class_type": "SaveImage", "inputs": {}}}');

    await vi.waitFor(() => {
      expect(view.node([model()]).textContent).toContain('could not confirm');
    });
  });
});

describe('setting up every model at once', () => {
  function report(remaining: number, name: string) {
    return {
      processed: [{
        model_id: name.toLowerCase(), file: `${name}.safetensors`, name,
        filled: ['family: SDXL (read from the file)', 'steps and CFG (SDXL family defaults)', 'name (CivitAI)', 'trigger words (CivitAI)'],
        notes: [], lookup: 'exact', routing_card: false,
      }],
      remaining,
      total: 2,
      without_routing_card: ['Juggernaut XL', 'RealVis'],
    };
  }

  function built(setupModels: ReturnType<typeof vi.fn>, consentOk = true) {
    const appState = createState();
    appState.settings = normalizeSettings({
      global_default_model: null, default_memory_mode: 'saved', stt_provider: 'disabled', tts_provider: 'disabled',
      tts_format: 'wav', openai_api_key: null, onboarding_done: true, preferences: {},
    });
    const client = { setupModels, updateSettings: vi.fn().mockResolvedValue({}) } as unknown as ApiClient;
    const consent = vi.fn().mockResolvedValue({ ok: consentOk, remember: false });
    const root = document.createElement('div');
    let view!: CatalogModelsView;
    const render = () => root.replaceChildren(view.node([model(), model({ id: 'model-2', name: 'RealVis', external_id: 'realvis.safetensors' })]));
    view = new CatalogModelsView(appState, client, render, vi.fn(async () => undefined), () => undefined, { consent });
    render();
    return { root, consent, setupModels };
  }

  it('asks once, takes the pass a page at a time, and names who still has no routing card', async () => {
    const setupModels = vi.fn()
      .mockResolvedValueOnce(report(1, 'Juggernaut XL'))
      .mockResolvedValueOnce(report(0, 'RealVis'));
    const { root, consent } = built(setupModels);

    (root.querySelector('[data-testid="catalog-setup-models"]') as HTMLButtonElement).click();
    await vi.waitFor(() => expect(root.querySelector('[data-testid="catalog-setup-report"]')).not.toBeNull());

    expect(consent).toHaveBeenCalledTimes(1);
    expect(setupModels).toHaveBeenCalledTimes(2);
    expect(setupModels).toHaveBeenCalledWith({ limit: 5, lookup: true });
    const summary = root.querySelector('[data-testid="catalog-setup-report"]')?.textContent ?? '';
    expect(summary).toContain('2 of 2 set up');
    expect(summary).toContain('Family for 2');
    expect(root.querySelector('[data-testid="catalog-setup-without-card"]')?.textContent).toBe('Juggernaut XL, RealVis');
  });

  it('sets up from the files alone when the person declines the lookup', async () => {
    const setupModels = vi.fn().mockResolvedValue(report(0, 'Juggernaut XL'));
    const { root } = built(setupModels, false);

    (root.querySelector('[data-testid="catalog-setup-models"]') as HTMLButtonElement).click();
    await vi.waitFor(() => expect(root.querySelector('[data-testid="catalog-setup-report"]')).not.toBeNull());

    expect(setupModels).toHaveBeenCalledWith({ limit: 5, lookup: false });
    expect(root.querySelector('[data-testid="catalog-setup-report"]')?.textContent).toContain('CivitAI was skipped');
  });
});
