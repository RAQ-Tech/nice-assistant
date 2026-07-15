import { describe, expect, it, vi } from 'vitest';

import type { ApiClient } from '../src/api';
import { normalizeSettings } from '../src/settings';
import { SettingsView, type Dialogs } from '../src/settings_view';
import { createState } from '../src/state';
import type { MediaCatalog, MediaCatalogResource, MediaPlan } from '../src/types';

function resource(): MediaCatalogResource {
  return {
    id: 'model-1',
    resource_type: 'model',
    kind: 'image',
    name: 'Fantasy model',
    provider_key: 'local-image',
    backend: 'automatic1111',
    external_id: 'fantasy.safetensors',
    enabled: true,
    priority: 50,
    operations: ['generate'],
    domains: ['fantasy'],
    content_tags: ['general'],
    features: ['text_to_image'],
    estimated_vram_mb: 6500,
    estimated_load_seconds: 3,
    default_settings: { steps: 24 },
    notes: '',
    compatible_model_ids: [],
    revision: 1,
    created_at: 1,
    updated_at: 1,
  };
}

function catalog(): MediaCatalog {
  return {
    settings: { vram_budget_mb: 10240, max_loras: 4 },
    resources: [resource()],
    vocabulary: {
      operations: ['generate'],
      domains: ['fantasy'],
      content_tags: ['general'],
      features: ['text_to_image'],
    },
  };
}

const dialogs = {
  prompt: vi.fn(),
  confirm: vi.fn(),
  info: vi.fn(),
} as unknown as Dialogs;

describe('Media catalog settings', () => {
  it('creates workflows as disabled drafts so executable JSON can be reviewed before enabling', async () => {
    const appState = createState();
    appState.settings = normalizeSettings({
      global_default_model: null,
      default_memory_mode: 'saved',
      stt_provider: 'disabled',
      tts_provider: 'disabled',
      tts_format: 'wav',
      openai_api_key: null,
      onboarding_done: true,
      preferences: {},
    });
    appState.settingsSection = 'Media Catalog';
    const comfyModel = { ...resource(), backend: 'comfyui' as const };
    appState.mediaCatalog = { ...catalog(), resources: [comfyModel] };
    const localDialogs = {
      prompt: vi.fn().mockResolvedValueOnce('Identity workflow').mockResolvedValueOnce('identity-v1'),
      confirm: vi.fn(),
      info: vi.fn(),
    } as unknown as Dialogs;
    const client = {
      createMediaCatalogResource: vi.fn().mockResolvedValue({}),
      mediaCatalog: vi.fn().mockResolvedValue({ ...catalog(), resources: [comfyModel] }),
    } as unknown as ApiClient;
    const node = new SettingsView(vi.fn(), vi.fn(), localDialogs, appState, client).node();
    const addWorkflow = [...node.querySelectorAll('button')].find((button) => button.textContent === 'Add workflow');
    (addWorkflow as HTMLButtonElement).click();
    await vi.waitFor(() => expect(client.createMediaCatalogResource).toHaveBeenCalled());

    expect(client.createMediaCatalogResource).toHaveBeenCalledWith(expect.objectContaining({
      resource_type: 'workflow',
      backend: 'comfyui',
      enabled: false,
      default_settings: { workflow_patch: {} },
    }));
  });

  it('edits explicit catalog metadata and saves through the typed API', async () => {
    const appState = createState();
    appState.settings = normalizeSettings({
      global_default_model: null,
      default_memory_mode: 'saved',
      stt_provider: 'disabled',
      tts_provider: 'disabled',
      tts_format: 'wav',
      openai_api_key: null,
      onboarding_done: true,
      preferences: {},
    });
    appState.settingsSection = 'Media Catalog';
    appState.mediaCatalog = catalog();
    const saved = { ...resource(), name: 'Fantasy portrait model', revision: 2 };
    const client = {
      updateMediaCatalogResource: vi.fn().mockResolvedValue(saved),
      mediaCatalog: vi.fn().mockResolvedValue({ ...catalog(), resources: [saved] }),
    } as unknown as ApiClient;
    const node = new SettingsView(vi.fn(), vi.fn(), dialogs, appState, client).node();

    expect(node.textContent).toContain('filenames never imply fitness');
    const name = [...node.querySelectorAll('input')].find((input) =>
      input.parentElement?.textContent?.includes('Name'),
    ) as HTMLInputElement;
    name.value = 'Fantasy portrait model';
    name.dispatchEvent(new Event('input'));
    (node.querySelector('[data-testid="media-resource-save-model-1"]') as HTMLButtonElement).click();
    await new Promise((resolve) => window.setTimeout(resolve, 0));

    expect(client.updateMediaCatalogResource).toHaveBeenCalledWith(expect.objectContaining({
      id: 'model-1',
      name: 'Fantasy portrait model',
      domains: ['fantasy'],
    }));
    expect(appState.mediaCatalog?.resources[0]?.revision).toBe(2);
  });

  it('preserves unsaved resource metadata when a late catalog refresh completes', async () => {
    const appState = createState();
    appState.settings = normalizeSettings({
      global_default_model: null,
      default_memory_mode: 'saved',
      stt_provider: 'disabled',
      tts_provider: 'disabled',
      tts_format: 'wav',
      openai_api_key: null,
      onboarding_done: true,
      preferences: {},
    });
    appState.settingsSection = 'General';
    appState.mediaCatalog = catalog();
    let finishRefresh!: (value: MediaCatalog) => void;
    const pendingRefresh = new Promise<MediaCatalog>((resolve) => {
      finishRefresh = resolve;
    });
    const saved = { ...resource(), name: 'Fantasy portrait model', revision: 2 };
    const client = {
      mediaCatalog: vi.fn()
        .mockReturnValueOnce(pendingRefresh)
        .mockResolvedValue({ ...catalog(), resources: [saved] }),
      updateMediaCatalogResource: vi.fn().mockResolvedValue(saved),
    } as unknown as ApiClient;
    const view = new SettingsView(vi.fn(), vi.fn(), dialogs, appState, client);

    (view.node().querySelector('[data-testid="settings-nav-media-catalog"]') as HTMLButtonElement).click();
    const catalogNode = view.node();
    const name = [...catalogNode.querySelectorAll('input')].find((input) =>
      input.parentElement?.textContent?.includes('Name'),
    ) as HTMLInputElement;
    name.value = 'Fantasy portrait model';
    name.dispatchEvent(new Event('input', { bubbles: true }));
    finishRefresh(catalog());
    await new Promise((resolve) => window.setTimeout(resolve, 0));

    expect(appState.mediaCatalog?.resources[0]?.name).toBe('Fantasy portrait model');
    (view.node().querySelector('[data-testid="media-resource-save-model-1"]') as HTMLButtonElement).click();
    await vi.waitFor(() => {
      expect(client.updateMediaCatalogResource).toHaveBeenCalledWith(expect.objectContaining({
        id: 'model-1',
        name: 'Fantasy portrait model',
      }));
    });
  });

  it('previews an explainable deterministic selection without prompt content', async () => {
    const appState = createState();
    appState.settings = normalizeSettings({
      global_default_model: null,
      default_memory_mode: 'saved',
      stt_provider: 'disabled',
      tts_provider: 'disabled',
      tts_format: 'wav',
      openai_api_key: null,
      onboarding_done: true,
      preferences: {},
    });
    appState.settingsSection = 'Media Catalog';
    appState.mediaCatalog = catalog();
    const plan: MediaPlan = {
      id: null,
      source: 'coordinator',
      status: 'ready',
      kind: 'image',
      operation: 'generate',
      requirements: { kind: 'image', operation: 'generate', domains: [], content_tags: [], required_features: [] },
      selected_resources: [{ ...resource(), resource_type: 'model' }],
      explanation: {
        summary: 'Selected deterministically.',
        selected: [{ resource_id: 'model-1', role: 'model', name: 'Fantasy model', reason: 'priority' }],
        warnings: [],
        rejected: [],
      },
      estimated_vram_mb: 6500,
      identity_conditioning: null,
      block: null,
      created_at: null,
    };
    const client = { previewMediaPlan: vi.fn().mockResolvedValue(plan) } as unknown as ApiClient;
    const node = new SettingsView(vi.fn(), vi.fn(), dialogs, appState, client).node();
    (node.querySelector('[data-testid="media-plan-preview"]') as HTMLButtonElement).click();
    await new Promise((resolve) => window.setTimeout(resolve, 0));

    expect(client.previewMediaPlan).toHaveBeenCalledWith(expect.not.objectContaining({ prompt: expect.anything() }));
    expect(appState.mediaPlanPreview?.selected_resources[0]?.name).toBe('Fantasy model');
  });
});
