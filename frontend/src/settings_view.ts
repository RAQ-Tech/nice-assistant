import { api, type ApiClient, type PersonaInput } from './api';
import { el, errorMessage, formatDate } from './dom';
import { EverydaySettingsView, type EverydaySettingsSection } from './everyday_settings_view';
import { IdentitySettingsView } from './identity_settings_view';
import { MediaCatalogSettingsView } from './media_catalog_settings_view';
import { ModelSettingsView } from './model_settings_view';
import { OperationsSettingsView } from './operations_settings_view';
import {
  resetSettingsSection,
  SETTINGS_DEFAULTS,
  SETTINGS_SECTIONS,
  settingsWire,
  type SettingsSection,
} from './settings';
import { inputField, selectField, textareaField, toggleField } from './settings_controls';
import type { SettingsDialogs } from './settings_contracts';
import { advancedSettings, infoTip, settingsCard, settingsHeading, settingsIntro, titleCase } from './settings_ui';
import { state } from './state';
import { TaskModelSettingsView } from './task_model_settings_view';
import type {
  AppState,
  IdentitySetupIntent,
  Memory,
  Persona,
  ProviderCheckResult,
  Settings,
} from './types';

const PROVIDERS: readonly [string, string][] = [
  ['ollama', 'Ollama'],
  ['openai', 'OpenAI'],
  ['kokoro', 'Kokoro'],
  ['automatic1111', 'Automatic1111'],
  ['comfyui', 'ComfyUI'],
];

export type Dialogs = SettingsDialogs;

export class SettingsView {
  private readonly identityView: IdentitySettingsView;
  private readonly everydayView: EverydaySettingsView;
  private readonly modelView: ModelSettingsView;
  private readonly taskModelView: TaskModelSettingsView;
  private readonly mediaCatalogView: MediaCatalogSettingsView;
  private readonly operationsView: OperationsSettingsView;
  private readonly selectedMemoryIds = new Set<string>();
  private memoryActionBusy = false;

  constructor(
    private readonly renderApp: () => void,
    private readonly close: () => void,
    private readonly dialogs: Dialogs,
    private readonly appState: AppState = state,
    private readonly client: ApiClient = api,
    private readonly navigateSettings: (section: SettingsSection) => void = (section) => {
      appState.settingsSection = section;
      renderApp();
    },
  ) {
    this.mediaCatalogView = new MediaCatalogSettingsView(renderApp, appState, client, dialogs, close);
    this.identityView = new IdentitySettingsView(
      renderApp,
      appState,
      client,
      dialogs,
      (personaId) => this.startIdentitySetup({
        capability_request_id: null,
        chat_id: appState.currentChat?.id ?? null,
        persona_id: personaId,
        prompt: '',
        required_features: ['identity_control'],
        block_code: null,
      }),
    );
    this.everydayView = new EverydaySettingsView(
      appState,
      (key, value, shouldRender) => this.set(key, value, shouldRender),
      (provider) => this.providerControl(provider),
      () => this.providerPanel(),
    );
    this.modelView = new ModelSettingsView(
      appState,
      (key, value, shouldRender) => this.set(key, value, shouldRender),
      renderApp,
      () => this.providerControl('ollama'),
    );
    this.taskModelView = new TaskModelSettingsView(renderApp, appState, client);
    this.operationsView = new OperationsSettingsView(renderApp, appState, client, dialogs);
  }

  startIdentitySetup(intent: IdentitySetupIntent): void {
    if (isVisualIdentityBlock(intent.block_code)) {
      this.appState.mediaCatalogIdentitySetupIntent = null;
      if (intent.persona_id && this.appState.personas.some((item) => item.id === intent.persona_id)) {
        this.appState.identitySelectedPersonaId = intent.persona_id;
      }
      this.navigateSettings('Visual Identity');
      void this.identityView.refresh();
      return;
    }
    this.appState.mediaCatalogIdentitySetupIntent = intent;
    this.mediaCatalogView.openIdentitySetup();
    this.navigateSettings('Media Catalog');
    void this.mediaCatalogView.refresh();
  }

  node(): HTMLElement {
    const settings = this.appState.settings;
    if (!settings) return el('div', { class: 'settings-screen', textContent: 'Settings are unavailable.' });
    const section = normalizeSection(this.appState.settingsSection);
    const usesDedicatedActions = ['Data', 'Task Models', 'Media Catalog', 'Visual Identity', 'GPU Coordination'].includes(section);
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
      void this.operationsView.refreshCoordination();
    }
    return el('div', { class: 'settings-screen', 'data-testid': 'settings-screen' }, [
      el('div', { class: 'settings-header' }, [
        el('h2', { textContent: 'Settings' }),
        this.appState.settingsSavedAt
          ? el('div', { class: 'success-banner', textContent: 'Settings saved' })
          : null,
        el('div', { class: 'chips' }, [
          el('button', { class: 'icon-btn', textContent: '✕ Close', onclick: this.close }),
          !usesDedicatedActions
            ? el('button', {
                class: 'send-btn',
                textContent: this.appState.settingsSaving ? 'Saving…' : 'Save settings',
                disabled: this.appState.settingsSaving,
                'data-testid': 'settings-save',
                onclick: () => void this.persist(),
              })
            : null,
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
                if (name === 'Task Models') void this.taskModelView.refresh();
                if (name === 'Media Catalog') void this.mediaCatalogView.refresh();
                if (name === 'Visual Identity') void this.identityView.refresh();
                if (name === 'GPU Coordination' && this.appState.session?.is_admin) {
                  void this.operationsView.refreshCoordination();
                }
                if (name === 'Data' && this.appState.session?.is_admin) void this.operationsView.refreshBackups();
                this.renderApp();
              },
            }),
          ),
        ),
        el('section', { class: 'settings-detail glass' }, [
          el('div', { class: 'settings-section-head' }, [
            el('h3', { textContent: section }),
            !usesDedicatedActions
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
    if (section === 'Models') return this.modelView.nodes(settings);
    if (section === 'Task Models') return this.taskModelView.nodes();
    if (section === 'Media Catalog') return this.mediaCatalogView.nodes();
    if (section === 'Visual Identity') return this.identityView.nodes();
    if (section === 'GPU Coordination') return this.operationsView.gpuNodes();
    return this.operationsView.dataNodes();
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

function slug(value: string): string {
  return value.toLowerCase().replace(/\s+/g, '-');
}

function isVisualIdentityBlock(code: string | null | undefined): boolean {
  return [
    'identity_persona_required',
    'identity_profile_unavailable',
    'identity_reference_unavailable',
    'identity_reference_changed',
  ].includes(code ?? '');
}

function conceptTip(label: string, help: string): HTMLElement {
  return el('span', { class: 'settings-concept' }, [
    el('span', { textContent: label }),
    infoTip(help, `About ${label}`),
  ]);
}
