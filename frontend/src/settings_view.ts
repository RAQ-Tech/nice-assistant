import { api, type ApiClient } from './api';
import { settingsNav } from './settings_nav';
import { el, errorMessage, formatDate } from './dom';
import { EverydaySettingsView, type EverydaySettingsSection } from './everyday_settings_view';
import { IdentitySettingsView } from './identity_settings_view';
import { MediaCatalogSettingsView } from './media_catalog_settings_view';
import { ModelSettingsView } from './model_settings_view';
import { OperationsSettingsView } from './operations_settings_view';
import { PersonaCardView } from './persona_card_view';
import { PersonaFaceView } from './persona_face_view';
import { PersonaLoreView } from './persona_lore_view';
import { PersonaPageView } from './persona_page_view';
import {
  resetSettingsSection,
  SETTINGS_SECTIONS,
  sectionLabel,
  settingsWire,
  type SettingsSection,
} from './settings';
import { textareaField } from './settings_controls';
import type { SettingsDialogs } from './settings_contracts';
import { actionRow, choiceField, pageHint, textField } from './settings_page';
import { advancedSettings, settingsCard, titleCase } from './settings_ui';
import { state } from './state';
import { TaskModelSettingsView } from './task_model_settings_view';
import type {
  AppState,
  IdentitySetupIntent,
  Memory,
  ProviderCheckResult,
  Settings,
} from './types';

const PROVIDERS: readonly [string, string][] = [
  ['ollama', 'Ollama'],
  ['openai', 'OpenAI'],
  ['kokoro', 'Kokoro'],
  ['whisper', 'Whisper'],
  ['automatic1111', 'Automatic1111'],
  ['comfyui', 'ComfyUI'],
];

/** Sections whose pages save themselves, so the header's Save has nothing to write. */
const SELF_SAVING_SECTIONS: readonly SettingsSection[] = ['Data', 'Task Models', 'Media Catalog', 'Persona Pictures', 'GPU Coordination'];

export type Dialogs = SettingsDialogs;

export class SettingsView {
  private searchQuery = '';

  private readonly identityView: IdentitySettingsView;
  private readonly everydayView: EverydaySettingsView;
  private readonly modelView: ModelSettingsView;
  private readonly taskModelView: TaskModelSettingsView;
  private readonly mediaCatalogView: MediaCatalogSettingsView;
  private readonly operationsView: OperationsSettingsView;
  private readonly personaPages: PersonaPageView;
  private readonly selectedMemoryIds = new Set<string>();
  private memoryActionBusy = false;

  constructor(
    private readonly renderApp: () => void,
    private readonly close: () => void,
    private readonly dialogs: Dialogs,
    private readonly appState: AppState = state,
    private readonly client: ApiClient = api,
    private readonly navigateSettings: (section: SettingsSection, item?: string | null) => void = (section, item = null) => {
      appState.settingsSection = section;
      appState.settingsItem = item;
      renderApp();
    },
  ) {
    this.mediaCatalogView = new MediaCatalogSettingsView(
      renderApp,
      appState,
      client,
      dialogs,
      close,
      (item) => this.navigateSettings('Media Catalog', item),
    );
    const setUpIdentityControl = (personaId: string) => this.startIdentitySetup({
      capability_request_id: null,
      chat_id: appState.currentChat?.id ?? null,
      persona_id: personaId,
      prompt: '',
      required_features: ['identity_control'],
      block_code: null,
    });
    this.identityView = new IdentitySettingsView(renderApp, appState, client, dialogs, setUpIdentityControl);
    const change = <K extends keyof Settings>(key: K, value: Settings[K], shouldRender?: boolean) => this.set(key, value, shouldRender);
    this.everydayView = new EverydaySettingsView(appState, change, (provider) => this.providerControl(provider));
    this.modelView = new ModelSettingsView(
      appState,
      change,
      renderApp,
      () => this.providerControl('ollama'),
      (model) => this.navigateSettings('Models', model),
    );
    this.taskModelView = new TaskModelSettingsView(renderApp, appState, client, (role) => this.navigateSettings('Task Models', role));
    this.personaPages = new PersonaPageView(
      appState,
      client,
      renderApp,
      dialogs,
      (personaId) => this.navigateSettings('Personas', personaId),
      new PersonaCardView(renderApp, appState, client),
      new PersonaLoreView(renderApp, appState, client),
      new PersonaFaceView(appState, client, renderApp, dialogs, setUpIdentityControl, (personaId) => {
        appState.identitySelectedPersonaId = personaId;
        this.navigateSettings('Persona Pictures');
        void this.identityView.refresh();
      }),
      change,
    );
    this.operationsView = new OperationsSettingsView(renderApp, appState, client, dialogs);
  }

  startIdentitySetup(intent: IdentitySetupIntent): void {
    if (isVisualIdentityBlock(intent.block_code)) {
      this.appState.mediaCatalogIdentitySetupIntent = null;
      // The face lives on the persona's own page now; that is where a missing
      // or changed reference gets fixed.
      if (intent.persona_id && this.appState.personas.some((item) => item.id === intent.persona_id)) {
        this.appState.identitySelectedPersonaId = intent.persona_id;
        this.navigateSettings('Personas', intent.persona_id);
        return;
      }
      this.navigateSettings('Persona Pictures');
      void this.identityView.refresh();
      return;
    }
    this.appState.mediaCatalogIdentitySetupIntent = intent;
    this.mediaCatalogView.openIdentitySetup();
    void this.mediaCatalogView.refresh();
  }

  node(): HTMLElement {
    const settings = this.appState.settings;
    if (!settings) return el('div', { class: 'settings-screen', textContent: 'Settings are unavailable.' });
    const section = normalizeSection(this.appState.settingsSection);
    const item = this.appState.settingsItem;
    // A persona's page saves itself; the list around it still holds one
    // ordinary setting, so the header's Save belongs to the list only.
    const selfSaving = SELF_SAVING_SECTIONS.includes(section) || (section === 'Personas' && Boolean(item));
    this.appState.settingsSection = section;
    if (section === 'Persona Pictures' && !this.appState.identitySettings && !this.appState.identityBusy) {
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
          !selfSaving
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
        settingsNav({
          section,
          query: this.searchQuery,
          onQuery: (value) => {
            this.searchQuery = value;
            this.renderApp();
          },
          onOpen: (name) => this.openSection(name),
        }),
        el('section', { class: 'settings-detail glass' }, [
          el('div', { class: 'settings-section-head' }, [
            el('h3', { textContent: sectionLabel(section) }),
            !selfSaving && !item
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
          ...this.section(section, item, settings),
        ]),
      ]),
    ]);
  }

  /** Switch sections, and load whatever that section reads on arrival. */
  private openSection(name: SettingsSection): void {
    this.personaPages.beforeLeave(() => this.mediaCatalogView.beforeLeave(() => {
      this.navigateSettings(name, null);
      if (name === 'Memory') void this.refreshMemories();
      if (name === 'Task Models') void this.taskModelView.refresh();
      if (name === 'Media Catalog') void this.mediaCatalogView.refresh();
      if (name === 'Persona Pictures') void this.identityView.refresh();
      if (name === 'GPU Coordination' && this.appState.session?.is_admin) {
        void this.operationsView.refreshCoordination();
      }
      if (name === 'Data' && this.appState.session?.is_admin) void this.operationsView.refreshBackups();
      this.renderApp();
    }));
  }

  private section(section: SettingsSection, item: string | null, settings: Settings): HTMLElement[] {
    if (['General', 'TTS', 'STT', 'Image Generation', 'Video Generation', 'User'].includes(section)) {
      return this.everydayView.nodes(section as EverydaySettingsSection, settings);
    }
    if (section === 'Memory') return this.memory(settings);
    if (section === 'Personas') return item ? this.personaPages.page(item) : this.personaPages.list(settings);
    if (section === 'Workspaces') return this.workspaces(settings);
    if (section === 'Models') return this.modelView.nodes(settings, item);
    if (section === 'Task Models') return this.taskModelView.nodes(item);
    if (section === 'Media Catalog') return this.mediaCatalogView.nodes(item);
    if (section === 'Persona Pictures') return this.identityView.nodes();
    if (section === 'GPU Coordination') return this.operationsView.gpuNodes();
    return this.operationsView.dataNodes();
  }

  private memory(settings: Settings): HTMLElement[] {
    const groups = groupMemories(this.appState.memories);
    const selected = this.appState.memories.filter((memory) => this.selectedMemoryIds.has(memory.id));
    const forgettable = selected.filter((memory) => ['pending', 'active'].includes(memory.status));
    return [
      settingsCard([
        choiceField('New chats', settings.default_memory_mode, ['saved', 'off'], (value) => {
          this.set('default_memory_mode', value === 'off' ? 'off' : 'saved');
        }, {
          display: (value) => value === 'saved' ? 'Use approved memories' : 'Do not use saved memories',
          hover: 'Each chat can still choose differently.',
        }),
      ]),
      pageHint('Only approved memories reach a conversation. Forget keeps the history so it can be undone; delete is for good.'),
      el('div', { class: 'memory-bulk-bar persona-card', 'data-testid': 'memory-bulk-actions' }, [
        el('strong', { textContent: `${selected.length} of ${this.appState.memories.length} selected` }),
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
      actionRow([
        el('button', {
          class: 'pill-btn',
          textContent: '+ Add a memory',
          title: 'A fact you want remembered everywhere, active at once.',
          'data-testid': 'memory-add',
          onclick: () => void this.addMemory(),
        }),
      ]),
    ];
  }

  private workspaces(settings: Settings): HTMLElement[] {
    const workspaces = this.appState.workspaces;
    return [
      settingsCard([
        choiceField('Default workspace', settings.workspaces_default_workspace_id, ['', ...workspaces.map((item) => item.id)], (value) => {
          this.set('workspaces_default_workspace_id', value);
        }, {
          display: (value) => workspaces.find((item) => item.id === value)?.name ?? 'None',
          hover: 'Where a new persona goes when nothing more specific has been chosen.',
        }),
      ]),
      pageHint('A workspace is a private grouping of personas and conversations, not a separate account.'),
      ...workspaces.map((workspace) =>
        el('div', { class: 'persona-card workspace-card', 'data-testid': `workspace-${workspace.id}` }, [
          textField('Name', workspace.name, (value) => { workspace.name = value; }, {
            commit: () => void this.renameWorkspace(workspace.id, workspace.name),
          }),
          actionRow([
            el('button', { class: 'icon-btn danger', textContent: 'Delete', title: 'Only an empty workspace can be deleted.', onclick: () => void this.deleteWorkspace(workspace.id, workspace.name) }),
          ]),
        ]),
      ),
      actionRow([
        el('button', { class: 'send-btn', textContent: '+ New workspace', onclick: () => void this.addWorkspace() }),
      ]),
    ];
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

  private providerControl(provider: string): HTMLElement {
    const label = PROVIDERS.find(([key]) => key === provider)?.[1] ?? provider;
    const running = Boolean(this.appState.providerChecksRunning[provider]);
    const result = this.appState.providerChecks[provider];
    return el('div', { class: 'provider-check-row', title: 'Tries the service with the values on this page, saved or not. Changes nothing.' }, [
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

  private async addWorkspace(): Promise<void> {
    const name = await this.dialogs.prompt('New workspace', 'Choose a workspace name.');
    if (!name?.trim()) return;
    this.appState.workspaces.push(await this.client.createWorkspace(name.trim()));
    this.renderApp();
  }

  private async renameWorkspace(id: string, name: string): Promise<void> {
    if (!name.trim()) return;
    try {
      const updated = await this.client.updateWorkspace(id, name.trim());
      this.appState.workspaces = this.appState.workspaces.map((item) => (item.id === id ? updated : item));
    } catch (error) {
      this.appState.settingsError = errorMessage(error, 'Unable to rename the workspace.');
    }
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

function normalizeSection(value: string): SettingsSection {
  return SETTINGS_SECTIONS.includes(value as SettingsSection) ? (value as SettingsSection) : 'General';
}

function isVisualIdentityBlock(code: string | null | undefined): boolean {
  return [
    'identity_persona_required',
    'identity_profile_unavailable',
    'identity_reference_unavailable',
    'identity_reference_changed',
  ].includes(code ?? '');
}
