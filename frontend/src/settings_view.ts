import { api, type ApiClient, type PersonaInput } from './api';
import { el, errorMessage, formatBytes, formatDate } from './dom';
import { EverydaySettingsView, type EverydaySettingsSection } from './everyday_settings_view';
import { IdentitySettingsView } from './identity_settings_view';
import {
  resetSettingsSection,
  SETTINGS_DEFAULTS,
  SETTINGS_SECTIONS,
  settingsWire,
  type SettingsSection,
} from './settings';
import { inputField, selectField, textareaField, toggleField } from './settings_controls';
import { advancedSettings, infoTip, settingsCard, settingsHeading, settingsIntro } from './settings_ui';
import { state } from './state';
import type {
  AppState,
  MediaCatalogResource,
  MediaPlanRequirements,
  MediaResourceType,
  Memory,
  Persona,
  ProviderCheckResult,
  ResourceEndpointStatus,
  Settings,
  TaskModelProfile,
  TaskModelRole,
} from './types';

const PROVIDERS: readonly [string, string][] = [
  ['ollama', 'Ollama'],
  ['openai', 'OpenAI'],
  ['kokoro', 'Kokoro'],
  ['automatic1111', 'Automatic1111'],
  ['comfyui', 'ComfyUI'],
];

export interface Dialogs {
  prompt(title: string, message: string, initial?: string): Promise<string | null>;
  confirm(title: string, message: string, confirmText?: string): Promise<boolean>;
  info(title: string, message: string): void;
}

export class SettingsView {
  private readonly identityView: IdentitySettingsView;
  private readonly everydayView: EverydaySettingsView;
  private readonly dirtyTaskModelRoles = new Set<TaskModelRole>();
  private readonly taskModelVersions = new Map<TaskModelRole, number>();
  private mediaCatalogSettingsDirty = false;
  private mediaCatalogSettingsVersion = 0;
  private readonly dirtyMediaResourceIds = new Set<string>();
  private readonly mediaResourceVersions = new Map<string, number>();
  private readonly selectedMemoryIds = new Set<string>();
  private memoryActionBusy = false;
  private mediaPreviewRequirements: MediaPlanRequirements = {
    kind: 'image',
    operation: 'generate',
    domains: [],
    content_tags: [],
    required_features: [],
  };

  constructor(
    private readonly renderApp: () => void,
    private readonly close: () => void,
    private readonly dialogs: Dialogs,
    private readonly appState: AppState = state,
    private readonly client: ApiClient = api,
  ) {
    this.identityView = new IdentitySettingsView(renderApp, appState, client, dialogs);
    this.everydayView = new EverydaySettingsView(
      appState,
      (key, value, shouldRender) => this.set(key, value, shouldRender),
      (provider) => this.providerControl(provider),
      () => this.providerPanel(),
    );
  }

  node(): HTMLElement {
    const settings = this.appState.settings;
    if (!settings) return el('div', { class: 'settings-screen', textContent: 'Settings are unavailable.' });
    const section = normalizeSection(this.appState.settingsSection);
    this.appState.settingsSection = section;
    if (section === 'Visual Identity' && !this.appState.identitySettings && !this.appState.identityBusy) {
      void this.identityView.refresh();
    }
    if (
      section === 'GPU Coordination'
      && this.appState.session?.is_admin
      && !this.appState.resourceCoordination
      && !this.appState.resourceCoordinationBusy
    ) {
      void this.refreshResourceCoordination();
    }
    return el('div', { class: 'settings-screen', 'data-testid': 'settings-screen' }, [
      el('div', { class: 'settings-header' }, [
        el('h2', { textContent: 'Settings' }),
        this.appState.settingsSavedAt
          ? el('div', { class: 'success-banner', textContent: 'Settings saved' })
          : null,
        el('div', { class: 'chips' }, [
          el('button', { class: 'icon-btn', textContent: '✕ Close', onclick: this.close }),
          el('button', {
            class: 'send-btn',
            textContent: this.appState.settingsSaving ? 'Saving…' : 'Save all',
            disabled: this.appState.settingsSaving,
            'data-testid': 'settings-save',
            onclick: () => void this.persist(),
          }),
        ]),
      ]),
      this.appState.settingsError
        ? el('div', { class: 'error-banner', textContent: this.appState.settingsError })
        : null,
      el('div', { class: 'settings-layout' }, [
        el(
          'aside',
          { class: 'settings-nav glass' },
          SETTINGS_SECTIONS.map((name) =>
            el('button', {
              class: `settings-nav-item ${name === section ? 'active' : ''}`,
              textContent: name,
              'data-testid': `settings-nav-${slug(name)}`,
              onclick: () => {
                this.appState.settingsSection = name;
                if (name === 'Memory') void this.refreshMemories();
                if (name === 'Task Models') void this.refreshTaskModels();
                if (name === 'Media Catalog') void this.refreshMediaCatalog();
                if (name === 'Visual Identity') void this.identityView.refresh();
                if (name === 'GPU Coordination' && this.appState.session?.is_admin) {
                  void this.refreshResourceCoordination();
                }
                if (name === 'Data' && this.appState.session?.is_admin) void this.refreshBackups();
                this.renderApp();
              },
            }),
          ),
        ),
        el('section', { class: 'settings-detail glass' }, [
          el('div', { class: 'settings-section-head' }, [
            el('h3', { textContent: section }),
            !['Data', 'Task Models', 'Media Catalog', 'Visual Identity', 'GPU Coordination'].includes(section)
              ? el('button', {
                  class: 'pill-btn',
                  textContent: 'Reset to Default',
                  onclick: () => {
                    resetSettingsSection(settings, section);
                    this.renderApp();
                  },
                })
              : null,
          ]),
          ...this.section(section, settings),
        ]),
      ]),
    ]);
  }

  private section(section: SettingsSection, settings: Settings): HTMLElement[] {
    if (['General', 'TTS', 'STT', 'Image Generation', 'Video Generation', 'User'].includes(section)) {
      return this.everydayView.nodes(section as EverydaySettingsSection, settings);
    }
    if (section === 'Memory') return this.memory(settings);
    if (section === 'Personas') return this.personas(settings);
    if (section === 'Workspaces') return this.workspaces(settings);
    if (section === 'Models') return this.models(settings);
    if (section === 'Task Models') return this.taskModels();
    if (section === 'Media Catalog') return this.mediaCatalog();
    if (section === 'Visual Identity') return this.identityView.nodes();
    if (section === 'GPU Coordination') return this.gpuCoordination();
    return this.data();
  }

  private memory(settings: Settings): HTMLElement[] {
    const groups = groupMemories(this.appState.memories);
    const selected = this.appState.memories.filter((memory) => this.selectedMemoryIds.has(memory.id));
    const forgettable = selected.filter((memory) => ['pending', 'active'].includes(memory.status));
    return [
      settingsIntro(
        'Control what carries between conversations',
        'Only approved active memories enter prompts. Review, forget, or permanently delete them here.',
      ),
      settingsCard([
        selectField(
          'Default memory mode',
          settings.default_memory_mode,
          ['saved', 'off'],
          (value) => this.set('default_memory_mode', value === 'off' ? 'off' : 'saved'),
          undefined,
          (value) => value === 'saved' ? 'Use approved memories' : 'Do not use saved memories',
          true,
          'Controls new chats by default. Individual chats can still choose a different memory mode.',
        ),
        el('div', { class: 'settings-concept-strip' }, [
          conceptTip('Pending', 'A proposed memory that does not enter prompts until you approve it.'),
          conceptTip('Forget', 'Stops using the memory while preserving history so the action can be undone.'),
          conceptTip('Delete', 'Permanently removes the memory and its history. This cannot be undone.'),
        ]),
      ]),
      el('div', { class: 'memory-bulk-bar persona-card', 'data-testid': 'memory-bulk-actions' }, [
        el('div', { class: 'settings-heading-with-info' }, [
          el('strong', { textContent: `${selected.length} of ${this.appState.memories.length} selected` }),
          infoTip('Bulk actions apply atomically to the selected memories. Permanent delete cannot be undone.', 'About memory bulk actions'),
        ]),
        el('div', { class: 'chips' }, [
          el('button', {
            class: 'pill-btn',
            textContent: 'Select all',
            disabled: this.memoryActionBusy || this.appState.memories.length === 0,
            onclick: () => this.selectMemories(this.appState.memories, true),
          }),
          el('button', {
            class: 'pill-btn',
            textContent: 'Clear selection',
            disabled: this.memoryActionBusy || selected.length === 0,
            onclick: () => { this.selectedMemoryIds.clear(); this.renderApp(); },
          }),
          el('button', {
            class: 'pill-btn',
            textContent: `Forget eligible (${forgettable.length})`,
            disabled: this.memoryActionBusy || forgettable.length === 0,
            onclick: () => void this.bulkMemoryAction('forget', forgettable.map((memory) => memory.id)),
          }),
          el('button', {
            class: 'pill-btn danger',
            textContent: `Delete permanently (${selected.length})`,
            disabled: this.memoryActionBusy || selected.length === 0,
            'data-testid': 'memory-bulk-delete',
            onclick: () => void this.bulkMemoryAction('delete', selected.map((memory) => memory.id)),
          }),
          el('button', { class: 'pill-btn', textContent: 'Refresh', disabled: this.memoryActionBusy, onclick: () => void this.refreshMemories() }),
        ]),
      ]),
      this.memoryGroup('Pending review', groups.pending, 'pending'),
      this.memoryGroup('Active', groups.active, 'active'),
      this.memoryGroup('History', groups.history, 'history'),
      el('div', { class: 'persona-card' }, [
        settingsHeading('Add a manual memory', 'Manual memories are global, immediately active facts you intentionally want the assistant to remember.', 'strong'),
        el('button', {
          class: 'pill-btn',
          textContent: 'Add global memory',
          'data-testid': 'memory-add',
          onclick: () => void this.addMemory(),
        }),
      ]),
    ];
  }

  private personas(settings: Settings): HTMLElement[] {
    return [
      settingsIntro(
        'Manage the people you talk with',
        'Create personas and open one only when you want to change its model, appearance source, or behavior.',
      ),
      el('div', { class: 'settings-primary-actions' }, [
        el('button', { class: 'send-btn', textContent: '+ New persona', onclick: () => void this.addPersona() }),
        el('span', { class: 'meta', textContent: `${this.appState.personas.length} ${this.appState.personas.length === 1 ? 'persona' : 'personas'}` }),
      ]),
      advancedSettings(
        'New-persona default instructions',
        'Applied only when a new persona is created; existing personas keep their own instructions.',
        [textareaField(
          'Default system prompt',
          settings.personas_default_system_prompt,
          (value) => this.set('personas_default_system_prompt', value),
          true,
          'Starting system instructions for newly created personas.',
        )],
        { testId: 'personas-advanced-settings' },
      ),
      ...this.appState.personas.map((persona) => this.personaCard(persona)),
    ];
  }

  private workspaces(settings: Settings): HTMLElement[] {
    return [
      settingsIntro(
        'Organize personas and conversations',
        'Workspaces are private organizational groups. They do not create separate user accounts or provider environments.',
      ),
      settingsCard([
        selectField(
          'Default workspace',
          settings.workspaces_default_workspace_id,
          ['', ...this.appState.workspaces.map((item) => item.id)],
          (value) => this.set('workspaces_default_workspace_id', value),
          undefined,
          (value) => this.appState.workspaces.find((item) => item.id === value)?.name ?? 'None',
          true,
          'Used as the initial workspace when a feature needs one and no more specific choice exists.',
        ),
        el('button', { class: 'send-btn', textContent: '+ New workspace', onclick: () => void this.addWorkspace() }),
      ]),
      ...this.appState.workspaces.map((workspace) =>
        el('div', { class: 'persona-card workspace-card' }, [
          el('strong', { textContent: workspace.name }),
          el('div', { class: 'chips' }, [
            el('button', { class: 'pill-btn', textContent: 'Rename', onclick: () => void this.renameWorkspace(workspace.id, workspace.name) }),
            el('button', { class: 'icon-btn danger', textContent: 'Delete', onclick: () => void this.deleteWorkspace(workspace.id, workspace.name) }),
          ]),
        ]),
      ),
    ];
  }

  private models(settings: Settings): HTMLElement[] {
    return [
      selectField('Global model', settings.global_default_model, ['', ...this.appState.models], (value) => this.set('global_default_model', value)),
      inputField('Temperature', settings.models_temperature, (value) => this.set('models_temperature', value), 'number'),
      inputField('Top P', settings.models_top_p, (value) => this.set('models_top_p', value), 'number'),
      inputField('Maximum output tokens', settings.models_num_predict, (value) => this.set('models_num_predict', value), 'number'),
      inputField('Context window tokens', settings.models_context_window_tokens, (value) => this.set('models_context_window_tokens', value), 'number'),
      inputField('Presence penalty', settings.models_presence_penalty, (value) => this.set('models_presence_penalty', value), 'number'),
      inputField('Frequency penalty', settings.models_frequency_penalty, (value) => this.set('models_frequency_penalty', value), 'number'),
      el('div', { class: 'meta', textContent: 'Per-model overrides are retained in typed settings and applied by the chat controller. The selected context window is sent to Ollama.' }),
      this.providerControl('ollama'),
    ];
  }

  private mediaCatalog(): HTMLElement[] {
    const catalog = this.appState.mediaCatalog;
    if (!catalog) {
      return [
        el('div', { class: 'meta', textContent: 'The media catalog is unavailable.' }),
        el('button', { class: 'pill-btn', textContent: 'Retry', onclick: () => void this.refreshMediaCatalog() }),
      ];
    }
    const rows: HTMLElement[] = [
      el('div', {
        class: 'meta',
        textContent:
          'This operator catalog is the source of truth for media selection. Names and filenames never imply fitness: only enabled metadata, explicit compatibility, hard requirements, priority, and the VRAM budget affect a plan.',
      }),
      el('div', {
        class: 'meta',
        textContent:
          'ComfyUI workflows can execute image-to-image, inpaint, and outpaint only when their inline workflow declares exact source and mask bindings. Automatic1111 remains generation-only, and the task model cannot invent protected source-image IDs.',
      }),
      el('div', { class: 'settings-grid' }, [
        inputField('Shared VRAM budget (MB; 0 disables limit)', String(catalog.settings.vram_budget_mb), (value) => {
          catalog.settings.vram_budget_mb = boundedInteger(value, 0, 131072, catalog.settings.vram_budget_mb);
          this.markMediaCatalogSettingsDirty();
        }, 'number', false),
        inputField('Maximum selected LoRAs', String(catalog.settings.max_loras), (value) => {
          catalog.settings.max_loras = boundedInteger(value, 0, 8, catalog.settings.max_loras);
          this.markMediaCatalogSettingsDirty();
        }, 'number', false),
      ]),
      el('div', { class: 'chips' }, [
        el('button', {
          class: 'send-btn',
          textContent: this.appState.mediaCatalogBusy ? 'Saving…' : 'Save catalog policy',
          disabled: this.appState.mediaCatalogBusy,
          'data-testid': 'media-catalog-save-policy',
          onclick: () => void this.saveMediaCatalogSettings(),
        }),
        el('button', { class: 'pill-btn', textContent: 'Add model', onclick: () => void this.addMediaResource('model') }),
        el('button', { class: 'pill-btn', textContent: 'Add LoRA', onclick: () => void this.addMediaResource('lora') }),
        el('button', { class: 'pill-btn', textContent: 'Add workflow', onclick: () => void this.addMediaResource('workflow') }),
        el('button', { class: 'pill-btn', textContent: 'Refresh', onclick: () => void this.refreshMediaCatalog() }),
      ]),
      el('h4', { textContent: 'Catalog resources' }),
    ];
    if (!catalog.resources.length) {
      rows.push(el('div', { class: 'meta', textContent: 'No resources are cataloged. Add a base model first.' }));
    } else {
      rows.push(...catalog.resources.map((resource) => this.mediaResourceCard(resource)));
    }
    rows.push(el('h4', { textContent: 'Plan preview' }), ...this.mediaPlanPreview());
    return rows;
  }

  private mediaResourceCard(resource: MediaCatalogResource): HTMLElement {
    const models = (this.appState.mediaCatalog?.resources ?? []).filter((item) =>
      item.resource_type === 'model'
      && item.kind === resource.kind
      && item.provider_key === resource.provider_key
      && item.backend === resource.backend
      && item.id !== resource.id
    );
    const providerOptions = resource.kind === 'video'
      ? ['openai-video']
      : (resource.resource_type === 'model' ? ['local-image', 'openai-image'] : ['local-image']);
    const backendOptions = resource.provider_key === 'local-image'
      ? (resource.resource_type === 'workflow' ? ['comfyui'] : ['automatic1111', 'comfyui'])
      : ['openai'];
    const markDirty = () => this.markMediaResourceDirty(resource.id);
    return el('div', {
      class: 'media-resource-card',
      'data-testid': `media-resource-${resource.id}`,
      oninput: markDirty,
      onchange: markDirty,
    }, [
      el('div', { class: 'task-model-head' }, [
        el('div', {}, [
          el('strong', { textContent: resource.name || 'Unnamed resource' }),
          el('div', { class: 'meta', textContent: `${titleCase(resource.resource_type)} · revision ${resource.revision}` }),
        ]),
        toggleField('Enabled', resource.enabled, (value) => { resource.enabled = value; }),
      ]),
      el('div', { class: 'settings-grid' }, [
        inputField('Name', resource.name, (value) => { resource.name = value; }, 'text', false),
        selectField('Resource type', resource.resource_type, ['model', 'lora', 'workflow'], (value) => {
          resource.resource_type = value as MediaResourceType;
        }, undefined, titleCase, false),
        selectField('Media kind', resource.kind, ['image', 'video'], (value) => {
          resource.kind = value as 'image' | 'video';
        }, undefined, titleCase, false),
        selectField('Provider adapter', resource.provider_key, providerOptions, (value) => {
          resource.provider_key = value as MediaCatalogResource['provider_key'];
        }, undefined, titleCase, false),
        selectField('Backend', resource.backend, backendOptions, (value) => {
          resource.backend = value as MediaCatalogResource['backend'];
        }, undefined, titleCase, false),
        inputField(
          resource.resource_type === 'workflow' ? 'Catalog workflow ID (patch is inline)' : 'Provider resource ID / filename',
          resource.external_id,
          (value) => { resource.external_id = value; },
          'text',
          false,
        ),
        inputField('Priority (0–100)', String(resource.priority), (value) => {
          resource.priority = boundedInteger(value, 0, 100, resource.priority);
        }, 'number', false),
        inputField('Estimated VRAM (MB; 0 unknown)', String(resource.estimated_vram_mb), (value) => {
          resource.estimated_vram_mb = boundedInteger(value, 0, 131072, resource.estimated_vram_mb);
        }, 'number', false),
        inputField('Estimated load seconds', String(resource.estimated_load_seconds), (value) => {
          resource.estimated_load_seconds = boundedNumber(value, 0, 3600, resource.estimated_load_seconds);
        }, 'number', false),
        inputField('Operations', resource.operations.join(', '), (value) => {
          resource.operations = tagList(value) as MediaCatalogResource['operations'];
        }, 'text', false),
        inputField('Domain strengths', resource.domains.join(', '), (value) => { resource.domains = tagList(value); }, 'text', false),
        inputField('Content strengths', resource.content_tags.join(', '), (value) => { resource.content_tags = tagList(value); }, 'text', false),
        inputField('Features', resource.features.join(', '), (value) => { resource.features = tagList(value); }, 'text', false),
      ]),
      resource.resource_type !== 'model'
        ? el('div', { class: 'compatibility-list' }, [
            el('strong', { textContent: 'Compatible base models' }),
            models.length
              ? el('div', { class: 'chips' }, models.map((model) =>
                  el('label', { class: 'checkbox-row' }, [
                    el('input', {
                      type: 'checkbox',
                      checked: resource.compatible_model_ids.includes(model.id),
                      onchange: (event: Event) => {
                        const checked = (event.currentTarget as HTMLInputElement).checked;
                        resource.compatible_model_ids = checked
                          ? [...new Set([...resource.compatible_model_ids, model.id])]
                          : resource.compatible_model_ids.filter((id) => id !== model.id);
                      },
                    }),
                    model.name,
                  ]),
                ))
              : el('div', { class: 'meta', textContent: 'No same-provider, same-backend model is available.' }),
          ])
        : null,
      textareaField('Default settings JSON', JSON.stringify(resource.default_settings, null, 2), (value) => {
        try {
          const parsed = JSON.parse(value || '{}');
          if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) resource.default_settings = parsed;
          this.appState.settingsError = '';
        } catch {
          this.appState.settingsError = `Default settings for ${resource.name || 'resource'} are not valid JSON.`;
        }
      }, false),
      resource.resource_type === 'workflow'
        ? el('div', {
            class: 'meta',
            textContent:
              'Disabled workflows may be saved as drafts. Identity workflows require the identity_control feature plus identity_image_bindings that target exact inputs in the workflow_patch.',
          })
        : null,
      textareaField('Operator notes', resource.notes, (value) => { resource.notes = value; }, false),
      el('div', { class: 'chips' }, [
        el('button', {
          class: 'send-btn',
          textContent: this.appState.mediaCatalogBusy ? 'Saving…' : 'Save resource',
          disabled: this.appState.mediaCatalogBusy,
          'data-testid': `media-resource-save-${resource.id}`,
          onclick: () => void this.saveMediaResource(resource),
        }),
        el('button', {
          class: 'pill-btn danger',
          textContent: 'Delete',
          disabled: this.appState.mediaCatalogBusy,
          onclick: () => void this.deleteMediaResource(resource),
        }),
      ]),
    ]);
  }

  private mediaPlanPreview(): HTMLElement[] {
    const requirements = this.mediaPreviewRequirements;
    const plan = this.appState.mediaPlanPreview;
    const rows: HTMLElement[] = [
      el('div', {
        class: 'meta',
        textContent: 'Preview deterministic selection using semantic requirements only. Prompt text is unnecessary and is not stored.',
      }),
      el('div', { class: 'settings-grid' }, [
        selectField('Media kind', requirements.kind, ['image', 'video'], (value) => {
          requirements.kind = value as 'image' | 'video';
        }, undefined, titleCase, false),
        selectField('Operation', requirements.operation, ['generate', 'inpaint', 'outpaint', 'image_to_image'], (value) => {
          requirements.operation = value as MediaPlanRequirements['operation'];
        }, undefined, titleCase, false),
        inputField('Preferred domains', requirements.domains.join(', '), (value) => { requirements.domains = tagList(value); }, 'text', false),
        inputField('Required content tags', requirements.content_tags.join(', '), (value) => { requirements.content_tags = tagList(value); }, 'text', false),
        inputField('Required features', requirements.required_features.join(', '), (value) => { requirements.required_features = tagList(value); }, 'text', false),
      ]),
      el('button', {
        class: 'pill-btn',
        textContent: this.appState.mediaCatalogBusy ? 'Planning…' : 'Preview selection',
        disabled: this.appState.mediaCatalogBusy,
        'data-testid': 'media-plan-preview',
        onclick: () => void this.previewMediaPlan(),
      }),
    ];
    if (plan) {
      rows.push(el('div', { class: `media-plan-preview plan-${plan.status}` }, [
        el('strong', { textContent: `${titleCase(plan.status)} · ${plan.estimated_vram_mb || 'unknown'} MB VRAM` }),
        el('p', { textContent: plan.block?.message || plan.explanation.summary }),
        plan.selected_resources.length
          ? el('ul', {}, plan.selected_resources.map((item) => el('li', { textContent: `${titleCase(item.resource_type)}: ${item.name}` })))
          : null,
        plan.identity_conditioning
          ? el('div', {
              class: 'meta',
              textContent: plan.identity_conditioning.status === 'ready'
                ? 'Persona reference conditioning is ready; configured validation runs after generation and may trigger bounded correction attempts.'
                : 'A real persona chat and active reviewed reference are required before this identity workflow can run.',
            })
          : null,
        ...plan.explanation.warnings.map((warning) => el('div', { class: 'meta', textContent: warning })),
      ]));
    }
    return rows;
  }

  private taskModels(): HTMLElement[] {
    const rows: HTMLElement[] = [
      el('div', {
        class: 'meta',
        textContent:
          'Platform tasks are separate from persona behavior. They share the interactive model-work lane with chat; the default single interactive worker avoids overlapping chat and background inference on a shared GPU.',
      }),
      el('div', {
        class: 'meta',
        textContent:
          'Capability planning chooses only a typed ability. Media checkpoints, workflows, LoRAs, and identity controls belong to the media coordinator and are not selected here.',
      }),
      el('button', {
        class: 'pill-btn',
        textContent: 'Refresh task models',
        onclick: () => void this.refreshTaskModels(),
      }),
    ];
    if (!this.appState.taskModels.length) {
      rows.push(el('div', { class: 'meta', textContent: 'No task-model profiles are available.' }));
      return rows;
    }
    rows.push(...this.appState.taskModels.map((profile) => this.taskModelCard(profile)));
    rows.push(
      el('h4', { textContent: 'Recent task runs' }),
      el('div', {
        class: 'meta',
        textContent:
          'Run records retain role, model, timing, token estimates, and safe errors only. Prompts and generated task output are not stored in this audit.',
      }),
    );
    if (!this.appState.taskModelRuns.length) {
      rows.push(el('div', { class: 'meta', textContent: 'No task runs have been recorded yet.' }));
    } else {
      rows.push(...this.appState.taskModelRuns.map((run) =>
        el('div', { class: 'task-run-row' }, [
          el('div', {}, [
            el('strong', { textContent: titleCase(run.role) }),
            el('div', {
              class: 'meta',
              textContent: `${run.executed_provider ?? run.requested_provider ?? 'No provider'} / ${run.executed_model ?? run.requested_model ?? 'auto'} · ${formatDate(run.started_at)}`,
            }),
          ]),
          el('div', { class: 'task-run-metrics' }, [
            el('span', { class: `provider-status ${taskRunStatusClass(run.status)}`, textContent: titleCase(run.status) }),
            el('span', { class: 'meta', textContent: `${run.latency_ms ?? 0} ms · ~${run.input_tokens_estimated} in / ~${run.output_tokens_estimated ?? 0} out` }),
            run.error ? el('span', { class: 'provider-check-message', textContent: `${run.error.code}: ${run.error.message}` }) : null,
          ]),
        ]),
      ));
    }
    return rows;
  }

  private taskModelCard(profile: TaskModelProfile): HTMLElement {
    const readiness = this.appState.taskModelChecks[profile.role];
    const busy = Boolean(this.appState.taskModelBusy[profile.role]);
    const modelOptions = ['', ...this.appState.models];
    const displayModel = (value: string) => value || 'Auto (first installed model)';
    const fallbackPolicies = profile.role === 'title_generation'
      ? ['deterministic', 'skip', 'fail']
      : ['skip', 'fail'];
    return el('div', { class: 'task-model-card', 'data-testid': `task-model-${profile.role}` }, [
      el('div', { class: 'task-model-head' }, [
        el('div', {}, [
          el('strong', { textContent: profile.title }),
          el('div', { class: 'meta', textContent: profile.description }),
        ]),
        toggleField('Enabled', profile.enabled, (value) => this.changeTaskModel(profile.role, 'enabled', value)),
      ]),
      selectField('Provider', profile.provider, ['ollama'], (value) => this.changeTaskModel(profile.role, 'provider', value), undefined, titleCase, false),
      selectField('Primary model', profile.model ?? '', modelOptions, (value) => this.changeTaskModel(profile.role, 'model', value || null), undefined, displayModel, false),
      selectField('Fallback model', profile.fallback_model ?? '', modelOptions, (value) => {
        this.changeTaskModel(profile.role, 'fallback_model', value || null, false);
        this.changeTaskModel(profile.role, 'fallback_provider', value ? profile.provider : null);
      }, undefined, displayModel, false),
      inputField('Maximum input tokens', String(profile.max_input_tokens), (value) => this.changeTaskNumber(profile.role, 'max_input_tokens', value), 'number', false),
      inputField('Maximum output tokens', String(profile.max_output_tokens), (value) => this.changeTaskNumber(profile.role, 'max_output_tokens', value), 'number', false),
      inputField('Timeout seconds', String(profile.timeout_seconds), (value) => this.changeTaskNumber(profile.role, 'timeout_seconds', value), 'number', false),
      inputField('Temperature', String(profile.temperature), (value) => this.changeTaskNumber(profile.role, 'temperature', value), 'number', false),
      selectField('Failure behavior', profile.fallback_policy, fallbackPolicies, (value) => this.changeTaskModel(profile.role, 'fallback_policy', value as TaskModelProfile['fallback_policy']), undefined, titleCase, false),
      el('div', { class: 'chips' }, [
        el('button', {
          class: 'send-btn',
          textContent: busy ? 'Saving…' : 'Save role',
          disabled: busy,
          'data-testid': `task-model-save-${profile.role}`,
          onclick: () => void this.saveTaskModel(profile.role),
        }),
        el('button', {
          class: 'pill-btn',
          textContent: busy ? 'Checking…' : 'Check readiness',
          disabled: busy,
          onclick: () => void this.checkTaskModel(profile.role),
        }),
        readiness
          ? el('span', {
              class: `provider-status ${readiness.ready ? 'ok' : 'fail'}`,
              textContent: `${titleCase(readiness.status)}: ${readiness.message}`,
            })
          : null,
      ]),
    ]);
  }

  private data(): HTMLElement[] {
    if (!this.appState.session?.is_admin) {
      return [el('div', { class: 'meta', textContent: 'Backup and diagnostic operations require the administrator account.' })];
    }
    return [
      el('div', { class: 'chips' }, [
        el('button', { class: 'pill-btn', textContent: 'Create backup', disabled: this.appState.backupActionRunning, onclick: () => void this.createBackup(false) }),
        el('button', { class: 'pill-btn', textContent: 'Create backup + media', disabled: this.appState.backupActionRunning, onclick: () => void this.createBackup(true) }),
        el('button', { class: 'pill-btn', textContent: 'Download diagnostic log', onclick: () => window.open(this.client.diagnosticLogUrl(), '_blank', 'noopener') }),
      ]),
      el('div', { class: 'meta', textContent: this.appState.backupsLoading ? 'Loading backups…' : `${this.appState.backupItems.length} backups` }),
      ...this.appState.backupItems.map((item) =>
        el('div', { class: 'manager-row' }, [
          el('div', {}, [
            el('strong', { textContent: item.name }),
            el('div', { class: 'meta', textContent: `${formatBytes(item.size)} · ${formatDate(item.created_at)}` }),
          ]),
          el('div', { class: 'chips' }, [
            el('button', { class: 'pill-btn', textContent: 'Download', onclick: () => window.open(this.client.backupDownloadUrl(item.name), '_blank', 'noopener') }),
            el('button', {
              class: 'pill-btn',
              textContent: 'Verify',
              disabled: this.appState.backupActionRunning,
              onclick: () => void this.verifyBackup(item.name),
            }),
            el('button', { class: 'icon-btn danger', textContent: 'Delete', onclick: () => void this.deleteBackup(item.name) }),
          ]),
        ]),
      ),
    ];
  }

  private gpuCoordination(): HTMLElement[] {
    if (!this.appState.session?.is_admin) {
      return [el('div', { class: 'meta', textContent: 'GPU coordination requires the administrator account.' })];
    }
    const coordination = this.appState.resourceCoordination;
    if (!coordination) {
      return [el('div', { class: 'meta', textContent: this.appState.resourceCoordinationBusy ? 'Checking provider capacity…' : 'GPU coordination is unavailable.' })];
    }
    return [
      el('div', {
        class: 'meta',
        textContent: 'Disabled preserves existing behavior. Observe waits for measured capacity without unloading services. Managed mode may release only endpoints explicitly attested as exclusively controlled by Nice Assistant, including reclaiming the media provider after a local image job.',
      }),
      selectField('Coordination mode', coordination.settings.mode, ['disabled', 'observe', 'managed'], (value) => {
        coordination.settings.mode = value as typeof coordination.settings.mode;
      }, 'resource-coordination-mode', titleCase, false),
      inputField('Reserved VRAM (MB)', String(coordination.settings.reserve_vram_mb), (value) => {
        coordination.settings.reserve_vram_mb = boundedInteger(value, 0, 131072, 1024);
      }, 'number', false),
      inputField('Maximum capacity wait (seconds)', String(coordination.settings.max_wait_seconds), (value) => {
        coordination.settings.max_wait_seconds = boundedInteger(value, 1, 3600, 300);
      }, 'number', false),
      inputField('Telemetry interval (seconds)', String(coordination.settings.poll_interval_seconds), (value) => {
        coordination.settings.poll_interval_seconds = boundedNumber(value, 0.25, 60, 2);
      }, 'number', false),
      ...coordination.endpoints.map((endpoint) => this.resourceEndpointCard(endpoint)),
      el('div', { class: 'chips' }, [
        el('button', {
          class: 'send-btn',
          textContent: this.appState.resourceCoordinationBusy ? 'Saving…' : 'Save coordination',
          disabled: this.appState.resourceCoordinationBusy,
          'data-testid': 'resource-coordination-save',
          onclick: () => void this.saveResourceCoordination(),
        }),
        el('button', {
          class: 'pill-btn',
          textContent: this.appState.resourceCoordinationBusy ? 'Checking…' : 'Refresh telemetry',
          disabled: this.appState.resourceCoordinationBusy,
          onclick: () => void this.checkResourceCoordination(),
        }),
      ]),
      el('div', { class: 'meta', textContent: `${this.appState.resourceCoordinationEvents.length} recent coordination events` }),
      ...this.appState.resourceCoordinationEvents.slice(0, 20).map((event) => el('div', { class: 'manager-row' }, [
        el('div', {}, [
          el('strong', { textContent: `${titleCase(event.provider)} · ${titleCase(event.action)}` }),
          el('div', { class: 'meta', textContent: `${titleCase(event.outcome)} · ${formatDate(event.created_at)}` }),
        ]),
      ])),
    ];
  }

  private resourceEndpointCard(endpoint: ResourceEndpointStatus): HTMLElement {
    const snapshot = endpoint.snapshot;
    const capacity = snapshot?.free_vram_mb == null
      ? titleCase(snapshot?.status ?? 'not checked')
      : `${snapshot.free_vram_mb} MB free of ${snapshot.total_vram_mb ?? 'unknown'} MB`;
    return el('div', { class: 'task-model-card', 'data-testid': `resource-endpoint-${endpoint.provider}` }, [
      el('div', {}, [
        el('strong', { textContent: titleCase(endpoint.provider) }),
        el('div', { class: 'meta', textContent: `${endpoint.endpoint_label} · ${capacity}` }),
        el('div', { class: 'meta', textContent: snapshot?.message || `Source: ${snapshot?.source ?? 'not checked'}` }),
      ]),
      toggleField('This endpoint is exclusively managed by Nice Assistant', endpoint.authorization.exclusive_control, (checked) => {
        endpoint.authorization.exclusive_control = checked;
        if (!checked) endpoint.authorization.allow_release = false;
        this.renderApp();
      }),
      toggleField('Allow verified release controls', endpoint.authorization.allow_release, (checked) => {
        endpoint.authorization.allow_release = checked && endpoint.authorization.exclusive_control;
        this.renderApp();
      }),
      el('div', {
        class: 'meta',
        textContent: endpoint.capabilities.supports_release
          ? 'The adapter supports release on compatible provider versions; Nice Assistant records failures and verifies effects with fresh telemetry.'
          : 'Provider does not expose a supported release control.',
      }),
    ]);
  }

  private personaCard(persona: Persona): HTMLElement {
    const workspaceIds = new Set(persona.workspace_ids.length ? persona.workspace_ids : [persona.workspace_id]);
    return el('details', { class: 'persona-card persona-editor', 'data-testid': `persona-${persona.id}` }, [
      el('summary', {}, [
        el('div', {}, [
          el('strong', { textContent: persona.name }),
          el('div', { class: 'meta', textContent: persona.default_model ? `Model: ${persona.default_model}` : 'Automatic model' }),
        ]),
        el('span', { class: 'meta', textContent: 'Edit' }),
      ]),
      inputField('Name', persona.name, (value) => { persona.name = value; }, 'text', false, 'The name shown in chat and persona selectors.'),
      inputField('Avatar image URL', persona.avatar_url ?? '', (value) => { persona.avatar_url = value; }, 'url', false, 'A reachable image URL used as this persona’s avatar.'),
      selectField(
        'Default model',
        persona.default_model ?? '',
        ['', ...this.appState.models],
        (value) => { persona.default_model = value; },
        undefined,
        (value) => value || 'Automatic',
        false,
        'Overrides the account default model for this persona.',
      ),
      el('div', { class: 'setting-row' }, [
        el('div', { class: 'setting-label-line' }, [
          el('label', { textContent: 'Workspaces' }),
          infoTip('Choose every workspace where this persona should be available.', 'About persona workspaces'),
        ]),
        ...this.appState.workspaces.map((workspace) =>
          el('label', { class: 'checkbox-row' }, [
            el('input', {
              type: 'checkbox',
              checked: workspaceIds.has(workspace.id),
              onchange: (event: Event) => {
                const checked = (event.currentTarget as HTMLInputElement).checked;
                if (checked) workspaceIds.add(workspace.id);
                else workspaceIds.delete(workspace.id);
                persona.workspace_ids = [...workspaceIds];
                persona.workspace_id = persona.workspace_ids[0] ?? persona.workspace_id;
              },
            }),
            workspace.name,
          ]),
        ),
      ]),
      advancedSettings(
        'Personality instructions',
        'These instructions strongly influence persona behavior. Change them deliberately.',
        [
          textareaField(
            'Personality details',
            persona.personality_details ?? '',
            (value) => { persona.personality_details = value; },
            false,
            'Descriptive traits and background used to support this persona’s behavior.',
          ),
          textareaField(
            'System prompt',
            persona.system_prompt ?? '',
            (value) => { persona.system_prompt = value; },
            false,
            'Highest-priority persona instructions sent with each conversation turn.',
          ),
        ],
        { testId: `persona-advanced-${persona.id}` },
      ),
      el('div', { class: 'chips' }, [
        el('button', { class: 'send-btn', textContent: 'Save persona', onclick: () => void this.savePersona(persona) }),
        el('button', { class: 'icon-btn danger', textContent: 'Delete', onclick: () => void this.deletePersona(persona) }),
      ]),
    ]);
  }

  private memoryGroup(title: string, items: Memory[], key: string): HTMLElement {
    const expanded = Boolean(this.appState.memorySections[key]);
    const selectedCount = items.filter((memory) => this.selectedMemoryIds.has(memory.id)).length;
    return el('div', { class: 'memory-section' }, [
      el('div', { class: 'memory-section-head' }, [
        el('button', {
          class: 'memory-section-toggle',
          textContent: `${expanded ? '▾' : '▸'} ${title} (${items.length})`,
          onclick: () => {
            this.appState.memorySections[key] = !expanded;
            this.renderApp();
          },
        }),
        el('button', {
          class: 'pill-btn',
          textContent: selectedCount === items.length && items.length ? 'Clear group' : `Select group (${items.length})`,
          disabled: this.memoryActionBusy || items.length === 0,
          onclick: () => this.selectMemories(items, !(selectedCount === items.length && items.length > 0)),
        }),
      ]),
      ...(expanded ? items.map((memory) => this.memoryRow(memory)) : []),
    ]);
  }

  private memoryRow(memory: Memory): HTMLElement {
    return el('div', { class: 'memory-row persona-card', 'data-testid': `memory-${memory.id}` }, [
      el('label', { class: 'checkbox-row memory-select' }, [
        el('input', {
          type: 'checkbox',
          checked: this.selectedMemoryIds.has(memory.id),
          disabled: this.memoryActionBusy,
          onchange: (event: Event) => {
            if ((event.currentTarget as HTMLInputElement).checked) this.selectedMemoryIds.add(memory.id);
            else this.selectedMemoryIds.delete(memory.id);
            this.renderApp();
          },
        }),
        'Select',
      ]),
      textareaField('Memory', memory.content, (value) => { memory.content = value; }, false),
      el('div', { class: 'meta', textContent: `${memory.status} · ${memory.scope}${memory.confidence === null ? '' : ` · ${Math.round(memory.confidence * 100)}% confidence`} · ${memory.source_type}` }),
      el('div', { class: 'chips' }, [
        memory.status === 'pending' ? el('button', { class: 'pill-btn', textContent: 'Approve', onclick: () => void this.memoryAction(memory, 'approve') }) : null,
        memory.status === 'pending' ? el('button', { class: 'pill-btn', textContent: 'Reject', onclick: () => void this.memoryAction(memory, 'reject') }) : null,
        ['pending', 'active'].includes(memory.status) ? el('button', { class: 'pill-btn', textContent: 'Forget', onclick: () => void this.memoryAction(memory, 'forget') }) : null,
        !['superseded'].includes(memory.status) ? el('button', { class: 'pill-btn', textContent: 'Save edit', onclick: () => void this.saveMemory(memory) }) : null,
        memory.can_undo ? el('button', { class: 'pill-btn', textContent: 'Undo', onclick: () => void this.memoryAction(memory, 'undo') }) : null,
        el('button', { class: 'icon-btn', textContent: 'History', onclick: () => void this.memoryHistory(memory) }),
        el('button', { class: 'icon-btn danger', textContent: 'Delete', onclick: () => void this.deleteMemory(memory) }),
      ]),
    ]);
  }

  private providerPanel(): HTMLElement {
    return el('div', { class: 'provider-readiness-panel persona-card' }, [
      el('strong', { textContent: 'Provider readiness' }),
      ...PROVIDERS.map(([provider]) => this.providerControl(provider)),
    ]);
  }

  private providerControl(provider: string): HTMLElement {
    const label = PROVIDERS.find(([key]) => key === provider)?.[1] ?? provider;
    const running = Boolean(this.appState.providerChecksRunning[provider]);
    const result = this.appState.providerChecks[provider];
    return el('div', { class: 'provider-check-row' }, [
      el('button', { class: 'pill-btn', textContent: running ? `Testing ${label}…` : `Test ${label}`, disabled: running, onclick: () => void this.testProvider(provider) }),
      el('span', { class: `provider-status ${providerStatusClass(result, running)}`, textContent: running ? 'Testing…' : providerStatusText(result) }),
      result?.message ? el('span', { class: 'provider-check-message', textContent: String(result.message) }) : null,
    ]);
  }

  private set<K extends keyof Settings>(key: K, value: Settings[K], shouldRender = true): void {
    const settings = this.appState.settings;
    if (!settings) return;
    settings[key] = value;
    this.appState.settingsSavedAt = 0;
    if (key === 'general_theme') document.documentElement.dataset.theme = String(value);
    if (key === 'general_show_system_messages') this.appState.showSystemMessages = Boolean(value);
    if (key === 'general_show_thinking') this.appState.showThinkingByDefault = Boolean(value);
    if (key === 'general_voice_responses') this.appState.voiceResponsesEnabled = Boolean(value);
    if (key === 'general_show_viz') this.appState.showViz = Boolean(value);
    if (shouldRender) this.renderApp();
  }

  private async persist(): Promise<void> {
    const settings = this.appState.settings;
    if (!settings) return;
    this.appState.settingsSaving = true;
    this.appState.settingsError = '';
    this.renderApp();
    try {
      const saved = await this.client.updateSettings(settingsWire(settings));
      Object.assign(settings, saved.preferences, {
        global_default_model: saved.global_default_model ?? '',
        default_memory_mode: saved.default_memory_mode,
        stt_provider: saved.stt_provider,
        tts_provider: saved.tts_provider,
        tts_format: saved.tts_format,
        openai_api_key: saved.openai_api_key ?? settings.openai_api_key,
        onboarding_done: saved.onboarding_done,
      });
      this.appState.settingsSavedAt = Date.now();
    } catch (error) {
      this.appState.settingsError = errorMessage(error, 'Unable to save settings.');
    } finally {
      this.appState.settingsSaving = false;
      this.renderApp();
    }
  }

  private async testProvider(provider: string): Promise<void> {
    const settings = this.appState.settings;
    if (!settings) return;
    this.appState.providerChecksRunning[provider] = true;
    this.renderApp();
    try {
      this.appState.providerChecks[provider] = await this.client.providerCheck(provider, settingsWire(settings));
    } catch (error) {
      this.appState.providerChecks[provider] = { provider, status: 'error', message: errorMessage(error, 'Provider check failed.'), ready: false };
    } finally {
      this.appState.providerChecksRunning[provider] = false;
      this.renderApp();
    }
  }

  private async refreshMemories(): Promise<void> {
    try {
      this.appState.memories = (await this.client.memories()).items;
      const currentIds = new Set(this.appState.memories.map((memory) => memory.id));
      for (const id of this.selectedMemoryIds) if (!currentIds.has(id)) this.selectedMemoryIds.delete(id);
    } catch (error) {
      this.appState.settingsError = errorMessage(error, 'Unable to refresh memory.');
    }
    this.renderApp();
  }

  private async refreshMediaCatalog(): Promise<void> {
    const settingsVersionAtStart = this.mediaCatalogSettingsVersion;
    const resourceVersionsAtStart = new Map(this.mediaResourceVersions);
    this.appState.mediaCatalogBusy = true;
    try {
      const catalog = await this.client.mediaCatalog();
      this.appState.mediaCatalog = this.mergeMediaCatalogSnapshot(
        catalog,
        settingsVersionAtStart,
        resourceVersionsAtStart,
      );
    } catch (error) {
      this.appState.settingsError = errorMessage(error, 'Unable to load the media catalog.');
    } finally {
      this.appState.mediaCatalogBusy = false;
      this.renderApp();
    }
  }

  private markMediaCatalogSettingsDirty(): void {
    this.mediaCatalogSettingsDirty = true;
    this.mediaCatalogSettingsVersion += 1;
  }

  private markMediaResourceDirty(resourceId: string): void {
    this.dirtyMediaResourceIds.add(resourceId);
    this.mediaResourceVersions.set(resourceId, (this.mediaResourceVersions.get(resourceId) ?? 0) + 1);
  }

  private mergeMediaCatalogSnapshot(
    incoming: NonNullable<AppState['mediaCatalog']>,
    settingsVersionAtStart = this.mediaCatalogSettingsVersion,
    resourceVersionsAtStart = new Map(this.mediaResourceVersions),
  ): NonNullable<AppState['mediaCatalog']> {
    const current = this.appState.mediaCatalog;
    if (!current) return incoming;
    const preserveSettings = this.mediaCatalogSettingsDirty
      || this.mediaCatalogSettingsVersion !== settingsVersionAtStart;
    const currentResources = new Map(current.resources.map((resource) => [resource.id, resource]));
    const incomingIds = new Set(incoming.resources.map((resource) => resource.id));
    const resources = incoming.resources.map((resource) => {
      const currentResource = currentResources.get(resource.id);
      const changedWhileLoading = (this.mediaResourceVersions.get(resource.id) ?? 0)
        !== (resourceVersionsAtStart.get(resource.id) ?? 0);
      return currentResource && (this.dirtyMediaResourceIds.has(resource.id) || changedWhileLoading)
        ? currentResource
        : resource;
    });
    resources.push(...current.resources.filter((resource) =>
      this.dirtyMediaResourceIds.has(resource.id) && !incomingIds.has(resource.id)
    ));
    return {
      ...incoming,
      settings: preserveSettings ? current.settings : incoming.settings,
      resources,
    };
  }

  private async saveMediaCatalogSettings(): Promise<void> {
    const catalog = this.appState.mediaCatalog;
    if (!catalog) return;
    this.appState.mediaCatalogBusy = true;
    this.appState.settingsError = '';
    this.renderApp();
    try {
      catalog.settings = await this.client.updateMediaCatalogSettings(catalog.settings);
      this.mediaCatalogSettingsVersion += 1;
      this.mediaCatalogSettingsDirty = false;
    } catch (error) {
      this.appState.settingsError = errorMessage(error, 'Unable to save the media catalog policy.');
    } finally {
      this.appState.mediaCatalogBusy = false;
      this.renderApp();
    }
  }

  private async addMediaResource(resourceType: MediaResourceType): Promise<void> {
    const catalog = this.appState.mediaCatalog;
    if (!catalog) return;
    const name = await this.dialogs.prompt(`Add ${resourceType}`, 'Operator-facing resource name.');
    if (!name?.trim()) return;
    const externalId = await this.dialogs.prompt(
      `Add ${resourceType}`,
      resourceType === 'model' ? 'Exact provider model/checkpoint ID or filename.' : 'Exact provider resource ID or filename.',
      resourceType === 'model' ? 'provider-default' : '',
    );
    if (!externalId?.trim()) return;
    let backend: MediaCatalogResource['backend'] = 'automatic1111';
    let compatibleModelIds: string[] = [];
    if (resourceType !== 'model') {
      const candidates = catalog.resources.filter((item) =>
        item.resource_type === 'model'
        && item.kind === 'image'
        && item.provider_key === 'local-image'
        && (resourceType !== 'workflow' || item.backend === 'comfyui')
      );
      if (!candidates.length) {
        this.dialogs.info(
          'Base model required',
          resourceType === 'workflow'
            ? 'Add a ComfyUI base model before adding a workflow.'
            : 'Add a local image base model before adding a LoRA.',
        );
        return;
      }
      const firstCandidate = candidates[0];
      if (!firstCandidate) return;
      backend = resourceType === 'workflow' ? 'comfyui' : firstCandidate.backend;
      compatibleModelIds = [candidates.find((item) => item.backend === backend)?.id ?? firstCandidate.id];
    }
    this.appState.mediaCatalogBusy = true;
    this.renderApp();
    try {
      await this.client.createMediaCatalogResource({
        resource_type: resourceType,
        kind: 'image',
        name: name.trim(),
        provider_key: 'local-image',
        backend,
        external_id: externalId.trim(),
        enabled: resourceType !== 'workflow',
        priority: 50,
        operations: ['generate'],
        domains: [],
        content_tags: resourceType === 'model' ? ['general'] : [],
        features: resourceType === 'model' ? ['text_to_image'] : [],
        estimated_vram_mb: 0,
        estimated_load_seconds: 0,
        default_settings: resourceType === 'lora'
          ? { weight: 1, trigger_words: [] }
          : (resourceType === 'workflow' ? { workflow_patch: {} } : {}),
        notes: '',
        compatible_model_ids: compatibleModelIds,
      });
      this.appState.mediaCatalog = this.mergeMediaCatalogSnapshot(await this.client.mediaCatalog());
    } catch (error) {
      this.appState.settingsError = errorMessage(error, `Unable to add ${resourceType}.`);
    } finally {
      this.appState.mediaCatalogBusy = false;
      this.renderApp();
    }
  }

  private async saveMediaResource(resource: MediaCatalogResource): Promise<void> {
    this.appState.mediaCatalogBusy = true;
    this.appState.settingsError = '';
    this.renderApp();
    try {
      const saved = await this.client.updateMediaCatalogResource(resource);
      if (this.appState.mediaCatalog) {
        this.appState.mediaCatalog.resources = this.appState.mediaCatalog.resources.map((item) =>
          item.id === saved.id ? saved : item
        );
        this.mediaResourceVersions.set(saved.id, (this.mediaResourceVersions.get(saved.id) ?? 0) + 1);
        this.dirtyMediaResourceIds.delete(saved.id);
        this.appState.mediaCatalog.vocabulary = (await this.client.mediaCatalog()).vocabulary;
      }
    } catch (error) {
      this.appState.settingsError = errorMessage(error, `Unable to save ${resource.name}.`);
    } finally {
      this.appState.mediaCatalogBusy = false;
      this.renderApp();
    }
  }

  private async deleteMediaResource(resource: MediaCatalogResource): Promise<void> {
    if (!(await this.dialogs.confirm('Delete media resource', `Delete ${resource.name}? Existing plans remain auditable but cannot be approved.`, 'Delete'))) return;
    this.appState.mediaCatalogBusy = true;
    this.renderApp();
    try {
      await this.client.deleteMediaCatalogResource(resource.id);
      this.dirtyMediaResourceIds.delete(resource.id);
      this.mediaResourceVersions.set(resource.id, (this.mediaResourceVersions.get(resource.id) ?? 0) + 1);
      this.appState.mediaCatalog = this.mergeMediaCatalogSnapshot(await this.client.mediaCatalog());
    } catch (error) {
      this.appState.settingsError = errorMessage(error, `Unable to delete ${resource.name}.`);
    } finally {
      this.appState.mediaCatalogBusy = false;
      this.renderApp();
    }
  }

  private async previewMediaPlan(): Promise<void> {
    this.appState.mediaCatalogBusy = true;
    this.appState.settingsError = '';
    this.renderApp();
    try {
      this.appState.mediaPlanPreview = await this.client.previewMediaPlan(this.mediaPreviewRequirements);
    } catch (error) {
      this.appState.settingsError = errorMessage(error, 'Unable to preview media selection.');
    } finally {
      this.appState.mediaCatalogBusy = false;
      this.renderApp();
    }
  }

  private changeTaskModel<K extends keyof TaskModelProfile>(
    role: TaskModelRole,
    key: K,
    value: TaskModelProfile[K],
    shouldRender = true,
  ): void {
    const profile = this.appState.taskModels.find((item) => item.role === role);
    if (!profile) return;
    profile[key] = value;
    this.dirtyTaskModelRoles.add(role);
    this.bumpTaskModelVersion(role);
    delete this.appState.taskModelChecks[role];
    if (shouldRender) this.renderApp();
  }

  private bumpTaskModelVersion(role: TaskModelRole): void {
    this.taskModelVersions.set(role, (this.taskModelVersions.get(role) ?? 0) + 1);
  }

  private changeTaskNumber(
    role: TaskModelRole,
    key: 'max_input_tokens' | 'max_output_tokens' | 'timeout_seconds' | 'temperature',
    value: string,
  ): void {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) this.changeTaskModel(role, key, parsed, false);
  }

  private async refreshTaskModels(): Promise<void> {
    const versionsAtStart = new Map(this.taskModelVersions);
    try {
      const [profiles, runs] = await Promise.all([
        this.client.taskModels(),
        this.client.taskModelRuns(undefined, 20),
      ]);
      const currentProfiles = new Map(this.appState.taskModels.map((profile) => [profile.role, profile]));
      this.appState.taskModels = profiles.items.map((profile) => {
        const current = currentProfiles.get(profile.role);
        const changedWhileLoading = (this.taskModelVersions.get(profile.role) ?? 0)
          !== (versionsAtStart.get(profile.role) ?? 0);
        return current && (this.dirtyTaskModelRoles.has(profile.role) || changedWhileLoading)
          ? current
          : profile;
      });
      this.appState.taskModelRuns = runs.items;
    } catch (error) {
      this.appState.settingsError = errorMessage(error, 'Unable to load task models.');
    }
    this.renderApp();
  }

  private async saveTaskModel(role: TaskModelRole): Promise<void> {
    const profile = this.appState.taskModels.find((item) => item.role === role);
    if (!profile) return;
    this.appState.taskModelBusy[role] = true;
    this.appState.settingsError = '';
    this.renderApp();
    try {
      const saved = await this.client.updateTaskModel(profile);
      this.appState.taskModels = this.appState.taskModels.map((item) => item.role === role ? saved : item);
      this.bumpTaskModelVersion(role);
      this.dirtyTaskModelRoles.delete(role);
      try {
        this.appState.taskModelChecks[role] = await this.client.checkTaskModel(role);
      } catch (error) {
        this.appState.settingsError = errorMessage(error, `${saved.title} was saved, but readiness could not be checked.`);
      }
    } catch (error) {
      this.appState.settingsError = errorMessage(error, `Unable to save ${profile.title}.`);
    } finally {
      this.appState.taskModelBusy[role] = false;
      this.renderApp();
    }
  }

  private async checkTaskModel(role: TaskModelRole): Promise<void> {
    this.appState.taskModelBusy[role] = true;
    this.renderApp();
    try {
      this.appState.taskModelChecks[role] = await this.client.checkTaskModel(role);
    } catch (error) {
      this.appState.settingsError = errorMessage(error, 'Unable to check task-model readiness.');
    } finally {
      this.appState.taskModelBusy[role] = false;
      this.renderApp();
    }
  }

  private async addMemory(): Promise<void> {
    const content = await this.dialogs.prompt('Add memory', 'Save an explicit global memory.');
    if (!content?.trim()) return;
    await this.client.createMemory('global', null, content.trim());
    await this.refreshMemories();
  }

  private async saveMemory(memory: Memory): Promise<void> {
    await this.client.updateMemory(memory.id, memory.scope, memory.scope_id, memory.content);
    await this.refreshMemories();
  }

  private async memoryAction(memory: Memory, action: 'approve' | 'reject' | 'forget' | 'undo'): Promise<void> {
    if (action === 'forget' && !(await this.dialogs.confirm('Forget memory', 'Remove this memory from future context while retaining its history?', 'Forget'))) return;
    await this.client.memoryAction(memory.id, action);
    await this.refreshMemories();
  }

  private selectMemories(memories: Memory[], selected: boolean): void {
    for (const memory of memories) {
      if (selected) this.selectedMemoryIds.add(memory.id);
      else this.selectedMemoryIds.delete(memory.id);
    }
    this.renderApp();
  }

  private async bulkMemoryAction(action: 'forget' | 'delete', ids: string[]): Promise<void> {
    if (!ids.length) return;
    const confirmed = action === 'delete'
      ? await this.dialogs.confirm(
          'Permanently delete memories',
          `Permanently delete ${ids.length} selected ${ids.length === 1 ? 'memory' : 'memories'} and all associated history? This cannot be undone.`,
          'Delete permanently',
        )
      : await this.dialogs.confirm(
          'Forget memories',
          `Forget ${ids.length} selected ${ids.length === 1 ? 'memory' : 'memories'}? They will stop entering prompts, but their history can still be restored.`,
          'Forget',
        );
    if (!confirmed) return;
    this.memoryActionBusy = true;
    this.appState.settingsError = '';
    this.renderApp();
    try {
      await this.client.bulkMemoryAction(action, ids);
      ids.forEach((id) => this.selectedMemoryIds.delete(id));
      await this.refreshMemories();
    } catch (error) {
      this.appState.settingsError = errorMessage(error, `Unable to ${action} the selected memories.`);
    } finally {
      this.memoryActionBusy = false;
      this.renderApp();
    }
  }

  private async deleteMemory(memory: Memory): Promise<void> {
    if (!(await this.dialogs.confirm(
      'Permanently delete memory',
      'Permanently delete this memory and all associated history? This cannot be undone.',
      'Delete permanently',
    ))) return;
    this.memoryActionBusy = true;
    this.renderApp();
    try {
      await this.client.deleteMemory(memory.id);
      this.selectedMemoryIds.delete(memory.id);
      await this.refreshMemories();
    } catch (error) {
      this.appState.settingsError = errorMessage(error, 'Unable to permanently delete the memory.');
    } finally {
      this.memoryActionBusy = false;
      this.renderApp();
    }
  }

  private async memoryHistory(memory: Memory): Promise<void> {
    const history = await this.client.memoryHistory(memory.id);
    this.dialogs.info('Memory history', history.events.map((event) => `${formatDate(event.created_at)} — ${event.action}${event.undone_at ? ' (undone)' : ''}`).join('\n') || 'No events.');
  }

  private async addPersona(): Promise<void> {
    const workspace = this.appState.workspaces[0];
    if (!workspace) {
      this.appState.settingsError = 'Create a workspace before adding a persona.';
      this.renderApp();
      return;
    }
    const name = await this.dialogs.prompt('New persona', 'Choose a persona name.');
    if (!name?.trim()) return;
    const persona = await this.client.createPersona({
      workspace_id: workspace.id,
      workspace_ids: [workspace.id],
      name: name.trim(),
      system_prompt: this.appState.settings?.personas_default_system_prompt ?? SETTINGS_DEFAULTS.personas_default_system_prompt,
      default_model: this.appState.models[0] ?? null,
    });
    this.appState.personas.push(persona);
    this.renderApp();
  }

  private async savePersona(persona: Persona): Promise<void> {
    const input = personaInput(persona);
    const updated = await this.client.updatePersona(persona.id, input);
    this.appState.personas = this.appState.personas.map((item) => (item.id === updated.id ? updated : item));
    this.renderApp();
  }

  private async deletePersona(persona: Persona): Promise<void> {
    if (!(await this.dialogs.confirm('Delete persona', `Delete ${persona.name}?`, 'Delete'))) return;
    await this.client.deletePersona(persona.id);
    this.appState.personas = this.appState.personas.filter((item) => item.id !== persona.id);
    this.renderApp();
  }

  private async addWorkspace(): Promise<void> {
    const name = await this.dialogs.prompt('New workspace', 'Choose a workspace name.');
    if (!name?.trim()) return;
    this.appState.workspaces.push(await this.client.createWorkspace(name.trim()));
    this.renderApp();
  }

  private async renameWorkspace(id: string, current: string): Promise<void> {
    const name = await this.dialogs.prompt('Rename workspace', 'Choose a new name.', current);
    if (!name?.trim()) return;
    const updated = await this.client.updateWorkspace(id, name.trim());
    this.appState.workspaces = this.appState.workspaces.map((item) => (item.id === id ? updated : item));
    this.renderApp();
  }

  private async deleteWorkspace(id: string, name: string): Promise<void> {
    if (!(await this.dialogs.confirm('Delete workspace', `Delete ${name}? It must not contain personas or chats.`, 'Delete'))) return;
    try {
      await this.client.deleteWorkspace(id);
      this.appState.workspaces = this.appState.workspaces.filter((item) => item.id !== id);
    } catch (error) {
      this.appState.settingsError = errorMessage(error, 'Unable to delete workspace.');
    }
    this.renderApp();
  }

  private async refreshBackups(): Promise<void> {
    if (!this.appState.session?.is_admin) return;
    this.appState.backupsLoading = true;
    this.renderApp();
    try {
      this.appState.backupItems = (await this.client.backups()).items;
    } catch (error) {
      this.appState.settingsError = errorMessage(error, 'Unable to load backups.');
    } finally {
      this.appState.backupsLoading = false;
      this.renderApp();
    }
  }

  private async refreshResourceCoordination(): Promise<void> {
    if (!this.appState.session?.is_admin || this.appState.resourceCoordinationBusy) return;
    this.appState.resourceCoordinationBusy = true;
    this.renderApp();
    try {
      const [coordination, events] = await Promise.all([
        this.client.resourceCoordination(),
        this.client.resourceCoordinationEvents(),
      ]);
      this.appState.resourceCoordination = coordination;
      this.appState.resourceCoordinationEvents = events.items;
    } catch (error) {
      this.appState.settingsError = errorMessage(error, 'Unable to load GPU coordination.');
    } finally {
      this.appState.resourceCoordinationBusy = false;
      this.renderApp();
    }
  }

  private async saveResourceCoordination(): Promise<void> {
    const coordination = this.appState.resourceCoordination;
    if (!this.appState.session?.is_admin || !coordination) return;
    this.appState.resourceCoordinationBusy = true;
    this.renderApp();
    try {
      this.appState.resourceCoordination = await this.client.saveResourceCoordination(coordination);
      this.appState.resourceCoordinationEvents = (await this.client.resourceCoordinationEvents()).items;
    } catch (error) {
      this.appState.settingsError = errorMessage(error, 'Unable to save GPU coordination.');
    } finally {
      this.appState.resourceCoordinationBusy = false;
      this.renderApp();
    }
  }

  private async checkResourceCoordination(): Promise<void> {
    if (!this.appState.session?.is_admin) return;
    this.appState.resourceCoordinationBusy = true;
    this.renderApp();
    try {
      this.appState.resourceCoordination = await this.client.checkResourceCoordination();
    } catch (error) {
      this.appState.settingsError = errorMessage(error, 'Unable to refresh GPU telemetry.');
    } finally {
      this.appState.resourceCoordinationBusy = false;
      this.renderApp();
    }
  }

  private async createBackup(includeMedia: boolean): Promise<void> {
    this.appState.backupActionRunning = true;
    this.renderApp();
    try {
      await this.client.createBackup(includeMedia);
      await this.refreshBackups();
    } finally {
      this.appState.backupActionRunning = false;
      this.renderApp();
    }
  }

  private async deleteBackup(name: string): Promise<void> {
    if (!(await this.dialogs.confirm('Delete backup', `Delete ${name}?`, 'Delete'))) return;
    await this.client.deleteBackup(name);
    await this.refreshBackups();
  }

  private async verifyBackup(name: string): Promise<void> {
    this.appState.backupActionRunning = true;
    this.renderApp();
    try {
      const result = await this.client.verifyBackup(name);
      this.dialogs.info(
        'Backup verified',
        `${result.name}\nDatabase integrity: ${result.database_integrity}\nMigration: ${result.migration_revision}\nArchive entries: ${result.entry_count}\nIncludes media: ${result.include_media ? 'yes' : 'no'}`,
      );
    } catch (error) {
      this.appState.settingsError = errorMessage(error, 'Backup verification failed.');
    } finally {
      this.appState.backupActionRunning = false;
      this.renderApp();
    }
  }
}

function providerStatusClass(result: ProviderCheckResult | undefined, running: boolean): string {
  if (running) return 'checking';
  if (!result) return 'idle';
  return result.ready || result.status === 'ready' ? 'ok' : 'fail';
}

function providerStatusText(result: ProviderCheckResult | undefined): string {
  if (!result) return 'Not checked';
  return titleCase(result.status || (result.ready ? 'ready' : 'error'));
}

function taskRunStatusClass(status: string): string {
  if (status === 'completed') return 'ok';
  if (status === 'running') return 'checking';
  return status === 'fallback' ? 'idle' : 'fail';
}

function groupMemories(items: Memory[]): { pending: Memory[]; active: Memory[]; history: Memory[] } {
  return {
    pending: items.filter((item) => item.status === 'pending'),
    active: items.filter((item) => item.status === 'active'),
    history: items.filter((item) => ['rejected', 'forgotten', 'superseded'].includes(item.status)),
  };
}

function personaInput(persona: Persona): PersonaInput {
  const workspaceIds = persona.workspace_ids.length ? persona.workspace_ids : [persona.workspace_id];
  return {
    workspace_id: workspaceIds[0] ?? persona.workspace_id,
    workspace_ids: workspaceIds,
    name: persona.name,
    avatar_url: persona.avatar_url,
    system_prompt: persona.system_prompt,
    personality_details: persona.personality_details,
    traits: persona.traits,
    default_model: persona.default_model,
    preferred_voice: persona.preferred_voice,
    preferred_tts_model: persona.preferred_tts_model,
    preferred_tts_speed: persona.preferred_tts_speed,
    preferred_voice_openai: persona.preferred_voice_openai,
    preferred_tts_model_openai: persona.preferred_tts_model_openai,
    preferred_tts_speed_openai: persona.preferred_tts_speed_openai,
    preferred_voice_local: persona.preferred_voice_local,
    preferred_tts_model_local: persona.preferred_tts_model_local,
    preferred_tts_speed_local: persona.preferred_tts_speed_local,
  };
}

function normalizeSection(value: string): SettingsSection {
  return SETTINGS_SECTIONS.includes(value as SettingsSection) ? (value as SettingsSection) : 'General';
}

function titleCase(value: string): string {
  if (!value) return 'None';
  return value.replace(/[-_]/g, ' ').replace(/\b\w/g, (character) => character.toUpperCase());
}

function slug(value: string): string {
  return value.toLowerCase().replace(/\s+/g, '-');
}

function conceptTip(label: string, help: string): HTMLElement {
  return el('span', { class: 'settings-concept' }, [
    el('span', { textContent: label }),
    infoTip(help, `About ${label}`),
  ]);
}

function tagList(value: string): string[] {
  return [...new Set(value.split(',').map((item) => item.trim().toLowerCase()).filter(Boolean))];
}

function boundedInteger(value: string, minimum: number, maximum: number, fallback: number): number {
  const parsed = Number.parseInt(value, 10);
  return Number.isFinite(parsed) ? Math.max(minimum, Math.min(maximum, parsed)) : fallback;
}

function boundedNumber(value: string, minimum: number, maximum: number, fallback: number): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? Math.max(minimum, Math.min(maximum, parsed)) : fallback;
}
