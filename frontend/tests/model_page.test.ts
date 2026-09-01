import { describe, expect, it, vi } from 'vitest';

import type { ApiClient } from '../src/api';
import { CatalogModelsView } from '../src/catalog_models_view';
import { ModelPageView } from '../src/model_page_view';
import { SETTINGS_DEFAULTS } from '../src/settings';
import { createState } from '../src/state';
import type { MediaCatalogResource, MediaPreset, Settings } from '../src/types';

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

function preset(overrides: Partial<MediaPreset> = {}): MediaPreset {
  return {
    id: 'preset-1',
    name: 'DreamShaper XL',
    kind: 'image',
    enabled: true,
    priority: 50,
    routing_card: 'Everyday photoreal pictures.',
    operations: ['generate'],
    domains: [],
    content_tags: ['general'],
    features: [],
    definition: {
      base_model_resource_id: 'model-1',
      prompt_dialect: { style: 'natural_language' },
      sampler: { steps: 30, cfg_scale: 6, sampler_name: 'euler' },
      dimensions: ['1024x1024'],
    },
    estimated_vram_mb: 7168,
    notes: '',
    revision: 1,
    created_at: 1,
    updated_at: 1,
    ...overrides,
  };
}

const LISTING = {
  ok: true,
  message: '',
  checkpoints: [],
  samplers: ['dpmpp_2m', 'euler'],
  schedulers: ['karras', 'normal'],
};

const SUGGESTION = {
  ok: true,
  source: 'file' as const,
  family: 'sdxl',
  family_label: 'SDXL',
  title: null,
  width: 1024,
  height: 1024,
  steps: 30,
  cfg_scale: 6,
  prompt_style: 'natural_language',
  message: 'Typical SDXL settings, read from the file: this is an SDXL model.',
};

function built(overrides: Partial<ApiClient> = {}, dialogChoice = 0) {
  const appState = createState();
  appState.settings = { ...SETTINGS_DEFAULTS } as Settings;
  appState.mediaCatalog = {
    resources: [model(), model({ id: 'model-2', name: 'Juggernaut XL', external_id: 'juggernaut.safetensors' })],
  } as never;
  const client = {
    mediaPresets: vi.fn().mockResolvedValue({ items: [preset()] }),
    comfyuiCheckpoints: vi.fn().mockResolvedValue(LISTING),
    modelPrefill: vi.fn().mockResolvedValue(SUGGESTION),
    updateMediaCatalogResource: vi.fn().mockImplementation(async (resource) => resource),
    updateMediaPreset: vi.fn().mockImplementation(async (_id, values) => ({ ...preset(), ...values })),
    updateSettings: vi.fn().mockImplementation(async (wire) => wire),
    civitaiLookup: vi.fn().mockResolvedValue({
      ok: true,
      message: '1 match(es) on CivitAI.',
      matches: [{
        model_name: 'Juggernaut XL',
        version_name: 'Ragnarok',
        base_model: 'SDXL 1.0',
        file_match: true,
        trigger_words: ['jugg style'],
        url: 'https://civitai.com/models/1',
        steps: 35,
        cfg_scale: 4.5,
        sampler: 'dpmpp_2m',
        scheduler: 'karras',
        width: 832,
        height: 1216,
      }],
    }),
    ...overrides,
  } as unknown as ApiClient;
  const choice = vi.fn().mockResolvedValue(dialogChoice);
  const consent = vi.fn().mockResolvedValue({ ok: true, remember: false });
  const dialogs = { prompt: async () => null, confirm: async () => true, info: () => undefined, choice, consent };
  const view = new ModelPageView(appState, client, vi.fn(), dialogs, async () => undefined);
  return { appState, client, view, choice, consent };
}

async function opened(view: ModelPageView, done = vi.fn()) {
  view.open('model-1');
  await vi.waitFor(() => {
    expect(view.node(done).querySelector('[data-testid="model-page-name"]')).toBeTruthy();
  });
  return view.node(done);
}

describe('the model page', () => {
  it('leads with the nickname and keeps the filename in reach', async () => {
    const { view } = built();

    const node = await opened(view);

    const name = node.querySelector('[data-testid="model-page-name"]') as HTMLInputElement;
    expect(name.value).toBe('DreamShaper XL');
    expect(name.title).toBe('dreamshaper.safetensors');
    expect(node.textContent).toContain('dreamshaper.safetensors');
  });

  it('offers samplers from ComfyUI instead of a typing box', async () => {
    const { view } = built();

    const node = await opened(view);

    const sampler = node.querySelector('[data-testid="model-page-sampler"]') as HTMLSelectElement;
    const options = [...sampler.options].map((option) => option.value);
    expect(options).toContain('dpmpp_2m');
    expect(sampler.value).toBe('euler');
  });

  it('suggests family settings with their provenance and applies them on request', async () => {
    const { view } = built({
      mediaPresets: vi.fn().mockResolvedValue({
        items: [preset({ definition: { base_model_resource_id: 'model-1', sampler: { steps: 20 } } })],
      }),
    } as Partial<ApiClient>);
    const node = await opened(view);
    expect(node.textContent).toContain('read from the file');

    (node.querySelector('[data-testid="model-page-apply-suggestion"]') as HTMLButtonElement).click();

    const after = view.node(vi.fn());
    const save = after.querySelector('[data-testid="model-page-save"]') as HTMLButtonElement;
    expect(save.textContent).toBe('Save');
    expect(save.disabled).toBe(false);
  });

  it('saves the model and its recipe together', async () => {
    const { view, client } = built();
    const node = await opened(view);
    const name = node.querySelector('[data-testid="model-page-name"]') as HTMLInputElement;
    name.value = 'Dreamy';
    name.dispatchEvent(new Event('input'));

    (view.node(vi.fn()).querySelector('[data-testid="model-page-save"]') as HTMLButtonElement).click();
    await vi.waitFor(() => {
      expect(client.updateMediaPreset).toHaveBeenCalled();
    });

    expect(client.updateMediaCatalogResource).toHaveBeenCalledWith(expect.objectContaining({ name: 'Dreamy' }));
    // The recipe follows the nickname, so the two never drift apart silently.
    const values = (client.updateMediaPreset as ReturnType<typeof vi.fn>).mock.calls[0]![1];
    expect(values.name).toBe('Dreamy');
    expect(values.definition.sampler.steps).toBe(30);
  });

  it('guards unsaved changes when leaving, and staying is the safe answer', async () => {
    const { view, choice } = built({}, 0);
    const done = vi.fn();
    const node = await opened(view, done);
    const name = node.querySelector('[data-testid="model-page-name"]') as HTMLInputElement;
    name.value = 'Changed';
    name.dispatchEvent(new Event('input'));

    (view.node(done).querySelector('[data-testid="model-page-back"]') as HTMLButtonElement).click();
    await vi.waitFor(() => {
      expect(choice).toHaveBeenCalled();
    });

    expect(done).not.toHaveBeenCalled();
    expect(view.modelId).toBe('model-1');
  });

  it('can leave without saving when told to', async () => {
    const { view, choice } = built({}, 1);
    const done = vi.fn();
    const node = await opened(view, done);
    const name = node.querySelector('[data-testid="model-page-name"]') as HTMLInputElement;
    name.value = 'Changed';
    name.dispatchEvent(new Event('input'));

    (view.node(done).querySelector('[data-testid="model-page-back"]') as HTMLButtonElement).click();
    await vi.waitFor(() => {
      expect(done).toHaveBeenCalled();
    });

    expect(choice).toHaveBeenCalled();
    expect(view.modelId).toBeNull();
  });

  it('walks to the next model with the arrows', async () => {
    const { view } = built();
    const node = await opened(view);

    (node.querySelector('[data-testid="model-page-next"]') as HTMLButtonElement).click();
    await vi.waitFor(() => {
      expect(view.modelId).toBe('model-2');
    });
  });

  it('explains itself when a model has no recipe yet', async () => {
    const { view } = built({ mediaPresets: vi.fn().mockResolvedValue({ items: [] }) } as Partial<ApiClient>);

    const node = await opened(view);

    expect(node.textContent).toContain('once it has a recipe');
  });
});

describe('the sample picture', () => {
  it('renders one picture with the page values and pins it as the thumbnail', async () => {
    const imageJob = vi.fn().mockResolvedValue({ job_id: 'job-1', capability_request_id: 'cap-1', status: 'queued' });
    const job = vi.fn().mockResolvedValue({ status: 'completed', result: { mediaId: 'media-9' }, error: '' });
    const updateMediaCatalogResource = vi.fn().mockImplementation(async (resource) => resource);
    const { view, appState } = built({ imageJob, job, updateMediaCatalogResource } as Partial<ApiClient>);
    const node = await opened(view);

    (node.querySelector('[data-testid="model-page-make-sample"]') as HTMLButtonElement).click();
    await vi.waitFor(() => {
      expect(updateMediaCatalogResource).toHaveBeenCalled();
    });

    // The sample rendered with the numbers on screen, not global preferences.
    const sent = imageJob.mock.calls[0]![0];
    expect(sent.model).toBe('dreamshaper.safetensors');
    expect(sent.steps).toBe(30);
    expect(sent.sampler_name).toBe('euler');
    // And the picture became the model's face.
    const saved = updateMediaCatalogResource.mock.calls[0]![0];
    expect(saved.default_settings.sample_media_id).toBe('media-9');
    const after = view.node(vi.fn());
    expect((after.querySelector('[data-testid="model-page-thumb"]') as HTMLImageElement).src).toContain('/api/v1/media/media-9');
    expect(appState.settingsError).toBe('');
  });

  it('says plainly when the sample cannot be made', async () => {
    const imageJob = vi.fn().mockResolvedValue({ job_id: 'job-1', capability_request_id: 'cap-1', status: 'queued' });
    const job = vi.fn().mockResolvedValue({ status: 'failed', result: null, error: 'ComfyUI is not reachable.' });
    const { view, appState } = built({ imageJob, job } as Partial<ApiClient>);
    const node = await opened(view);

    (node.querySelector('[data-testid="model-page-make-sample"]') as HTMLButtonElement).click();
    await vi.waitFor(() => {
      expect(appState.settingsError).toContain('ComfyUI is not reachable.');
    });
  });
});

describe('the CivitAI lookup', () => {
  it('asks before anything leaves the machine, and cancel sends nothing', async () => {
    const { view, client, consent } = built();
    consent.mockResolvedValue({ ok: false, remember: false });
    const node = await opened(view);

    (node.querySelector('[data-testid="model-lookup-run"]') as HTMLButtonElement).click();
    await vi.waitFor(() => {
      expect(consent).toHaveBeenCalled();
    });

    expect(String((consent as ReturnType<typeof vi.fn>).mock.calls[0]![1])).toContain('civitai.com');
    expect(client.civitaiLookup).not.toHaveBeenCalled();
  });

  it('remembers “don’t show this again” through the ordinary settings save', async () => {
    const { view, client, consent } = built();
    consent.mockResolvedValue({ ok: true, remember: true });
    const node = await opened(view);

    (node.querySelector('[data-testid="model-lookup-run"]') as HTMLButtonElement).click();
    await vi.waitFor(() => {
      expect(client.updateSettings).toHaveBeenCalled();
    });

    const wire = (client.updateSettings as ReturnType<typeof vi.fn>).mock.calls[0]![0];
    expect(wire.preferences.civitai_lookup_skip_confirm).toBe(true);
  });

  it('fills the form from the match a person picked', async () => {
    const { view, appState, consent } = built();
    (appState.settings as Settings).civitai_lookup_skip_confirm = true;
    const node = await opened(view);

    (node.querySelector('[data-testid="model-lookup-run"]') as HTMLButtonElement).click();
    await vi.waitFor(() => {
      expect(view.node(vi.fn()).querySelector('[data-testid="model-lookup-use-0"]')).toBeTruthy();
    });
    (view.node(vi.fn()).querySelector('[data-testid="model-lookup-use-0"]') as HTMLButtonElement).click();

    // The remembered opt-out means no popup, and the pick fills the form for
    // review: name, sampler in ComfyUI vocabulary, trigger words as prefix.
    expect(consent).not.toHaveBeenCalled();
    const after = view.node(vi.fn());
    expect((after.querySelector('[data-testid="model-page-name"]') as HTMLInputElement).value).toBe('Juggernaut XL');
    expect((after.querySelector('[data-testid="model-page-sampler"]') as HTMLSelectElement).value).toBe('dpmpp_2m');
    expect((after.querySelector('[data-testid="model-page-save"]') as HTMLButtonElement).disabled).toBe(false);
    // Pressing Use is acknowledged in words - silence reads as a dead button.
    const note = after.querySelector('[data-testid="model-lookup-message"]');
    expect(note?.textContent).toContain('Filled from Juggernaut XL');
    expect(note?.textContent).toContain('press Save');
  });

  it('says plainly when a match has nothing to fill', async () => {
    const { view, appState } = built({
      civitaiLookup: vi.fn().mockResolvedValue({
        ok: true,
        message: '1 match(es) on CivitAI.',
        matches: [{
          model_name: 'GonzaLomo Chroma',
          version_name: 'v3.0',
          base_model: 'Unmapped Base',
          file_match: true,
          trigger_words: [],
          url: 'https://civitai.com/models/2',
        }],
      }),
    } as Partial<ApiClient>);
    (appState.settings as Settings).civitai_lookup_skip_confirm = true;
    const node = await opened(view);
    (node.querySelector('[data-testid="model-lookup-run"]') as HTMLButtonElement).click();
    await vi.waitFor(() => {
      expect(view.node(vi.fn()).querySelector('[data-testid="model-lookup-use-0"]')).toBeTruthy();
    });

    (view.node(vi.fn()).querySelector('[data-testid="model-lookup-use-0"]') as HTMLButtonElement).click();

    const note = view.node(vi.fn()).querySelector('[data-testid="model-lookup-message"]');
    expect(note?.textContent).toContain('Filled from GonzaLomo Chroma: name');
  });
});

describe('the models list as the front door', () => {
  it('opens a model page from its name, and tucks hidden models away', () => {
    const openModel = vi.fn();
    const view = new CatalogModelsView(createState(), {} as ApiClient, vi.fn(), async () => undefined, openModel);

    const node = view.node([model(), model({ id: 'model-2', name: 'Retired', enabled: false })]);
    (node.querySelector('[data-testid="catalog-model-open-model-1"]') as HTMLButtonElement).click();

    expect(openModel).toHaveBeenCalledWith('model-1');
    expect(node.querySelector('.catalog-hidden-models')?.textContent).toContain('Retired');
    // The headline counts what is shown, not what is hidden.
    expect(node.textContent).toContain('1 enabled');
  });
});
