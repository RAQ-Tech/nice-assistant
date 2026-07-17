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

  it('uses generic setup copy when no blocked request is attached', async () => {
    const appState = createState();
    appState.settings = normalizeSettings({
      global_default_model: null, default_memory_mode: 'saved', stt_provider: 'disabled',
      tts_provider: 'disabled', tts_format: 'wav', openai_api_key: null, onboarding_done: true, preferences: {},
    });
    const comfyModel = { ...resource(), backend: 'comfyui' as const };
    appState.mediaCatalog = { ...catalog(), resources: [comfyModel] };
    appState.personas = [{
      id: 'nova', workspace_id: 'home', workspace_ids: ['home'], name: 'Nova', avatar_url: null,
      system_prompt: '', personality_details: '', traits: {}, default_model: null, preferred_voice: null,
      preferred_tts_model: null, preferred_tts_speed: null, preferred_voice_openai: null,
      preferred_tts_model_openai: null, preferred_tts_speed_openai: null, preferred_voice_local: null,
      preferred_tts_model_local: null, preferred_tts_speed_local: null, created_at: 1,
    }];
    const client = { mediaCatalog: vi.fn().mockResolvedValue(appState.mediaCatalog) } as unknown as ApiClient;
    const root = document.createElement('div');
    let view!: SettingsView;
    const render = () => root.replaceChildren(view.node());
    view = new SettingsView(render, vi.fn(), dialogs, appState, client);

    view.startIdentitySetup({
      capability_request_id: null, chat_id: 'chat-1', persona_id: 'nova', prompt: '',
      required_features: ['identity_control'], block_code: null,
    });
    await vi.waitFor(() => expect(client.mediaCatalog).toHaveBeenCalled());

    expect(root.textContent).toContain('Set up a reference-aware ComfyUI workflow for Nova');
    expect(root.textContent).toContain('No blocked image request is attached');
    expect(root.textContent).not.toContain('The blocked image request for Nova');
  });

  it('routes persona profile and reference remediation to Visual Identity', async () => {
    const appState = createState();
    appState.settings = normalizeSettings({
      global_default_model: null, default_memory_mode: 'saved', stt_provider: 'disabled',
      tts_provider: 'disabled', tts_format: 'wav', openai_api_key: null, onboarding_done: true, preferences: {},
    });
    appState.personas = [{
      id: 'nova', workspace_id: 'home', workspace_ids: ['home'], name: 'Nova', avatar_url: null,
      system_prompt: '', personality_details: '', traits: {}, default_model: null, preferred_voice: null,
      preferred_tts_model: null, preferred_tts_speed: null, preferred_voice_openai: null,
      preferred_tts_model_openai: null, preferred_tts_speed_openai: null, preferred_voice_local: null,
      preferred_tts_model_local: null, preferred_tts_speed_local: null, created_at: 1,
    }];
    appState.mediaCatalogIdentitySetupIntent = {
      capability_request_id: 'old-request', chat_id: 'old-chat', persona_id: 'nova', prompt: '',
      required_features: ['identity_control'], block_code: 'identity_workflow_unavailable',
    };
    const client = {
      identitySettings: vi.fn().mockResolvedValue({ provider: 'disabled', base_url: '', api_key: '', timeout_seconds: 15 }),
      visualIdentity: vi.fn().mockResolvedValue({ persona_id: 'nova', references: [] }),
      identityValidations: vi.fn().mockResolvedValue({ items: [] }),
      identityHistory: vi.fn().mockResolvedValue({ items: [] }),
    } as unknown as ApiClient;
    const navigate = vi.fn();
    const view = new SettingsView(vi.fn(), vi.fn(), dialogs, appState, client, navigate);

    view.startIdentitySetup({
      capability_request_id: 'request-1', chat_id: 'chat-1', persona_id: 'nova', prompt: 'portrait',
      required_features: ['identity_control'], block_code: 'identity_reference_unavailable',
    });

    expect(navigate).toHaveBeenCalledWith('Visual Identity');
    expect(appState.identitySelectedPersonaId).toBe('nova');
    expect(appState.mediaCatalogIdentitySetupIntent).toBeNull();
    await vi.waitFor(() => expect(client.visualIdentity).toHaveBeenCalledWith('nova'));
  });

  it('guides identity workflow import, provider inspection, exact binding, and automatic image retry', async () => {
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
    appState.phase = 'idle';
    appState.settingsSection = 'Media Catalog';
    const comfyModel = { ...resource(), name: 'Comfy portrait model', backend: 'comfyui' as const };
    const initialCatalog = { ...catalog(), resources: [comfyModel] };
    appState.mediaCatalog = initialCatalog;
    appState.personas = [{
      id: 'nova', workspace_id: 'home', workspace_ids: ['home'], name: 'Nova', avatar_url: null,
      system_prompt: '', personality_details: '', traits: {}, default_model: null, preferred_voice: null,
      preferred_tts_model: null, preferred_tts_speed: null, preferred_voice_openai: null,
      preferred_tts_model_openai: null, preferred_tts_speed_openai: null, preferred_voice_local: null,
      preferred_tts_model_local: null, preferred_tts_speed_local: null, created_at: 1,
    }];
    const workflowPatch = {
      '100': { class_type: 'LoadImage', inputs: { image: 'placeholder.png' } },
      '101': { class_type: 'IPAdapterAdvanced', inputs: { image: ['100', 0] } },
    };
    const savedWorkflow: MediaCatalogResource = {
      ...resource(),
      id: 'workflow-1',
      resource_type: 'workflow',
      name: 'Nova identity workflow',
      backend: 'comfyui',
      external_id: 'identity-control-test',
      features: ['identity_control'],
      default_settings: {
        workflow_patch: workflowPatch,
        identity_image_bindings: [{ node_id: '100', input_name: 'image' }],
      },
      compatible_model_ids: ['model-1'],
    };
    const replacement = {
      id: 'capability-2', capability_key: 'media.generate_image', status: 'queued' as const,
      permission_mode: 'auto' as const, arguments: { prompt: 'a portrait' }, result: null, error: null,
      chat_id: 'chat-1', turn_id: null, assistant_message_id: 'message-1', job_id: 'job-2',
      requested_at: 2, decided_at: null, started_at: null, completed_at: null, expires_at: null,
      media_plan: {
        id: 'plan-2', source: 'coordinator' as const, status: 'ready' as const, kind: 'image' as const,
        operation: 'generate',
        requirements: { kind: 'image' as const, operation: 'generate' as const, domains: [], content_tags: [], required_features: ['identity_control'] },
        selected_resources: [], explanation: { summary: 'Ready.', selected: [], warnings: [], rejected: [] },
        estimated_vram_mb: 7000, identity_conditioning: null, block: null, created_at: 2,
      },
    };
    const client = {
      mediaCatalog: vi.fn()
        .mockResolvedValueOnce(initialCatalog)
        .mockResolvedValue({ ...initialCatalog, resources: [comfyModel, savedWorkflow] }),
      inspectIdentityWorkflow: vi.fn().mockResolvedValue({
        provider: 'comfyui', status: 'provider_compatible', provider_compatible: true, live_tested: false,
        message: 'The deployed ComfyUI accepts the workflow contract.',
        identity_input_candidates: [{ node_id: '100', input_name: 'image', label: 'LoadImage 100 · image' }],
        detected_node_types: ['IPAdapterAdvanced'], missing_node_types: [], asset_checks: [], warnings: [],
      }),
      createMediaCatalogResource: vi.fn().mockResolvedValue(savedWorkflow),
      retryCapability: vi.fn().mockResolvedValue(replacement),
    } as unknown as ApiClient;
    const localDialogs = { prompt: vi.fn(), confirm: vi.fn(), info: vi.fn() } as unknown as Dialogs;
    const close = vi.fn();
    const root = document.createElement('div');
    let view!: SettingsView;
    const render = () => root.replaceChildren(view.node());
    view = new SettingsView(render, close, localDialogs, appState, client);
    view.startIdentitySetup({
      capability_request_id: 'capability-1', chat_id: 'chat-1', persona_id: 'nova',
      prompt: 'a portrait', required_features: ['identity_control'], block_code: 'no_compatible_media_plan',
    });
    await vi.waitFor(() => expect(client.mediaCatalog).toHaveBeenCalled());
    render();

    expect(root.textContent).toContain('Identity control');
    expect(root.textContent).toContain('failed image request for Nova requires identity_control');
    (root.querySelector('[data-testid="identity-workflow-setup-toggle"]') as HTMLButtonElement).click();
    expect(root.querySelector('[data-testid="identity-workflow-inspect"]')).toBeNull();
    (root.querySelector('[data-testid="identity-workflow-setup-toggle"]') as HTMLButtonElement).click();
    expect(root.querySelector('[data-testid="identity-workflow-inspect"]')).not.toBeNull();
    const oversizedFile = new File(['x'.repeat(200_001)], 'workflow.json', { type: 'application/json' });
    const fileInput = root.querySelector('[data-testid="identity-workflow-file"]') as HTMLInputElement;
    Object.defineProperty(fileInput, 'files', { configurable: true, value: [oversizedFile] });
    fileInput.dispatchEvent(new Event('change', { bubbles: true }));
    await vi.waitFor(() => expect(root.textContent).toContain('no larger than 200 KB'));

    let workflowJson = root.querySelector('[data-testid="identity-workflow-json"]') as HTMLTextAreaElement;
    workflowJson.value = JSON.stringify(workflowPatch);
    workflowJson.dispatchEvent(new Event('input', { bubbles: true }));
    (root.querySelector('[data-testid="identity-workflow-inspect"]') as HTMLButtonElement).click();
    await vi.waitFor(() => expect(client.inspectIdentityWorkflow).toHaveBeenCalledWith(
      workflowPatch,
    ));
    await vi.waitFor(() => expect(root.textContent).toContain('Provider-compatible'));
    expect(root.textContent).toContain('not generation-tested');

    workflowJson = root.querySelector('[data-testid="identity-workflow-json"]') as HTMLTextAreaElement;
    workflowJson.value = JSON.stringify({ ...workflowPatch, '102': { class_type: 'PreviewImage', inputs: {} } });
    workflowJson.dispatchEvent(new Event('input', { bubbles: true }));
    expect(root.textContent).not.toContain('Provider-compatible');
    expect(root.textContent).toContain('Workflow changed. Check it against ComfyUI again');
    expect(root.querySelector('[data-testid="identity-workflow-binding"]')).toBeNull();
    expect((root.querySelector('[data-testid="identity-workflow-save"]') as HTMLButtonElement).disabled).toBe(true);

    workflowJson.value = JSON.stringify({ oversized: 'x'.repeat(200_100) });
    workflowJson.dispatchEvent(new Event('input', { bubbles: true }));
    expect(root.textContent).toContain('larger than the 200 KB catalog limit');
    (root.querySelector('[data-testid="identity-workflow-inspect"]') as HTMLButtonElement).click();
    await vi.waitFor(() => expect(root.textContent).toContain('must be no larger than 200 KB'));
    expect(client.inspectIdentityWorkflow).toHaveBeenCalledTimes(1);

    workflowJson = root.querySelector('[data-testid="identity-workflow-json"]') as HTMLTextAreaElement;
    workflowJson.value = JSON.stringify(workflowPatch);
    workflowJson.dispatchEvent(new Event('input', { bubbles: true }));
    (root.querySelector('[data-testid="identity-workflow-inspect"]') as HTMLButtonElement).click();
    await vi.waitFor(() => expect(client.inspectIdentityWorkflow).toHaveBeenCalledTimes(2));
    await vi.waitFor(() => expect(root.textContent).toContain('Provider-compatible'));

    (root.querySelector('[data-testid="identity-workflow-save"]') as HTMLButtonElement).click();
    await vi.waitFor(() => expect(client.createMediaCatalogResource).toHaveBeenCalled());
    expect(client.createMediaCatalogResource).toHaveBeenCalledWith(expect.objectContaining({
      resource_type: 'workflow',
      enabled: true,
      features: ['identity_control'],
      compatible_model_ids: ['model-1'],
      default_settings: {
        workflow_patch: workflowPatch,
        identity_image_bindings: [{ node_id: '100', input_name: 'image' }],
      },
    }));
    await vi.waitFor(() => expect(localDialogs.info).toHaveBeenCalledWith(
      'Identity control added',
      expect.stringContaining('recorded your selected catalog model as an explicit pairing'),
    ));
    expect(localDialogs.info).not.toHaveBeenCalledWith(
      'Identity control added',
      expect.stringContaining('base-model compatibility passed inspection'),
    );

    await vi.waitFor(() => expect(
      (root.querySelector('[data-testid="identity-workflow-retry-plan"]') as HTMLButtonElement).disabled,
    ).toBe(false));
    (root.querySelector('[data-testid="identity-workflow-retry-plan"]') as HTMLButtonElement).click();
    await vi.waitFor(() => expect(client.retryCapability).toHaveBeenCalledWith('capability-1'));
    expect(appState.mediaCatalogIdentitySetupIntent).toBeNull();
    expect(appState.capabilityRequests[0]?.id).toBe('capability-2');
    expect(close).toHaveBeenCalled();
  });
});
