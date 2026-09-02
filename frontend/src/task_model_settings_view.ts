import type { ApiClient } from './api';
import { el, errorMessage, formatDate } from './dom';
import { providerLabel } from './everyday_settings_view';
import {
  actionRow,
  choiceField,
  numberField,
  pageHead,
  pageHint,
  pageNav,
  switchField,
  thingList,
} from './settings_page';
import { advancedSettings, settingsCard, titleCase } from './settings_ui';
import type { AppState, TaskModelProfile, TaskModelRole } from './types';

/**
 * Background models: the roles, and one page per role.
 *
 * A role is a job the platform does around a conversation - naming it,
 * summarising it, proposing memories, planning a picture - with a model of its
 * own so the persona's model is never borrowed for it. The list is the jobs;
 * a job's page is its model, its fallback, and what happens when it fails.
 */

const ROLE_HELP: Record<TaskModelRole, string> = {
  title_generation: 'Names a chat after its first turns.',
  conversation_summary: 'Compresses older history when the context budget requires it.',
  memory_extraction: 'Proposes memories for review. Nothing is remembered until you approve it.',
  capability_planning: 'Decides what kind of thing a request is asking for. Which model, workflow or face to use is decided later, by the media coordinator.',
};

export class TaskModelSettingsView {
  private readonly dirtyRoles = new Set<TaskModelRole>();
  private readonly versions = new Map<TaskModelRole, number>();
  private moreOpen = false;

  constructor(
    private readonly renderApp: () => void,
    private readonly appState: AppState,
    private readonly client: ApiClient,
    private readonly navigate: (role: string | null) => void,
  ) {}

  nodes(item: string | null): HTMLElement[] {
    const profile = item ? this.appState.taskModels.find((entry) => entry.role === item) : undefined;
    if (item && !profile) {
      return [
        pageNav({ back: 'All background models', onBack: () => this.navigate(null), testId: 'task-model-page' }),
        el('p', { class: 'meta', textContent: 'That role was not returned by the server.' }),
      ];
    }
    return profile ? this.page(profile) : this.list();
  }

  private list(): HTMLElement[] {
    const profiles = this.appState.taskModels;
    return [
      thingList(profiles.map((profile) => ({
        id: profile.role,
        label: profile.title,
        note: profile.enabled ? (profile.model || 'automatic') : 'off',
        testId: `task-model-${profile.role}`,
        onOpen: () => this.navigate(profile.role),
      })), 'No background roles were returned. Reload, then check the server logs if they stay missing.', 'task-model-list'),
      pageHint('Work done around a conversation - never by the persona’s own model, and never overlapping chat on the GPU.'),
      this.runAudit(),
    ];
  }

  private page(profile: TaskModelProfile): HTMLElement[] {
    const profiles = this.appState.taskModels;
    const index = profiles.indexOf(profile);
    const previous = profiles[index - 1];
    const next = profiles[index + 1];
    const readiness = this.appState.taskModelChecks[profile.role];
    const busy = Boolean(this.appState.taskModelBusy[profile.role]);
    const modelOptions = ['', ...this.appState.models];
    const displayModel = (value: string) => value || 'Automatic — the first installed model';
    const fallbackPolicies = profile.role === 'title_generation' ? ['deterministic', 'skip', 'fail'] : ['skip', 'fail'];
    return [
      pageNav({
        back: 'All background models',
        onBack: () => this.navigate(null),
        arrows: {
          previous: previous ? () => this.navigate(previous.role) : null,
          next: next ? () => this.navigate(next.role) : null,
        },
        busy,
        testId: 'task-model-page',
      }),
      settingsCard([
        pageHead({ name: profile.title, line: ROLE_HELP[profile.role], testId: 'task-model-page' }),
        switchField('On', profile.enabled, (value) => this.change(profile.role, 'enabled', value), {
          hover: 'Off, the role follows its failure behavior instead of running a model.',
          testId: `task-model-enabled-${profile.role}`,
        }),
        choiceField('Model', profile.model ?? '', modelOptions, (value) => this.change(profile.role, 'model', value || null), {
          display: displayModel,
          hover: `${providerLabel(profile.provider)}. An explicit choice is more predictable than Automatic.`,
          testId: `task-model-model-${profile.role}`,
        }),
        choiceField('Fallback model', profile.fallback_model ?? '', modelOptions, (value) => {
          this.change(profile.role, 'fallback_model', value || null, false);
          this.change(profile.role, 'fallback_provider', value ? profile.provider : null);
        }, { display: (value) => value || 'None', hover: 'Tried only after the first model fails, before the failure behavior applies.' }),
        advancedSettings('More options', 'Budgets, and what happens when it fails.', [
          el('div', { class: 'settings-grid' }, [
            numberField('Input limit (tokens)', String(profile.max_input_tokens), (value) => this.changeNumber(profile.role, 'max_input_tokens', value),
              { hover: 'The most this role is ever sent.' }),
            numberField('Output limit (tokens)', String(profile.max_output_tokens), (value) => this.changeNumber(profile.role, 'max_output_tokens', value),
              { hover: 'The most this role may answer with.' }),
            numberField('Timeout (seconds)', String(profile.timeout_seconds), (value) => this.changeNumber(profile.role, 'timeout_seconds', value),
              { hover: 'Stops waiting after this long.' }),
            numberField('Temperature', String(profile.temperature), (value) => this.changeNumber(profile.role, 'temperature', value),
              { hover: 'Low is right for platform work that should come out the same way twice.', step: '0.1' }),
          ]),
          choiceField('When it fails', profile.fallback_policy, fallbackPolicies, (value) => {
            this.change(profile.role, 'fallback_policy', value as TaskModelProfile['fallback_policy']);
          }, { display: policyLabel }),
        ], { testId: `task-model-advanced-${profile.role}`, open: this.moreOpen, onToggle: (open) => { this.moreOpen = open; } }),
        actionRow([
          el('button', {
            class: 'send-btn',
            textContent: busy ? 'Saving…' : 'Save role',
            disabled: busy,
            'data-testid': `task-model-save-${profile.role}`,
            onclick: () => void this.save(profile.role),
          }),
          el('button', {
            class: 'pill-btn',
            textContent: busy ? 'Checking…' : 'Check readiness',
            title: 'Confirms the adapter is installed, its credential is configured, and the model exists. Sends no request, so it never proves the provider answers.',
            disabled: busy,
            onclick: () => void this.check(profile.role),
          }),
          readiness
            ? el('span', {
                class: `provider-status ${readiness.ready ? 'ok' : 'fail'}`,
                textContent: `${titleCase(readiness.status)}: ${readiness.message}`,
              })
            : null,
        ]),
      ], 'task-model-page', 'task-model-page'),
    ];
  }

  async refresh(): Promise<void> {
    const versionsAtStart = new Map(this.versions);
    try {
      const [profiles, runs] = await Promise.all([
        this.client.taskModels(),
        this.client.taskModelRuns(undefined, 20),
      ]);
      const currentProfiles = new Map(this.appState.taskModels.map((profile) => [profile.role, profile]));
      this.appState.taskModels = profiles.items.map((profile) => {
        const current = currentProfiles.get(profile.role);
        const changedWhileLoading = (this.versions.get(profile.role) ?? 0)
          !== (versionsAtStart.get(profile.role) ?? 0);
        return current && (this.dirtyRoles.has(profile.role) || changedWhileLoading)
          ? current
          : profile;
      });
      this.appState.taskModelRuns = runs.items;
    } catch (error) {
      this.appState.settingsError = errorMessage(error, 'Unable to load background models.');
    }
    this.renderApp();
  }

  private runAudit(): HTMLElement {
    const rows = this.appState.taskModelRuns.length
      ? this.appState.taskModelRuns.map((run) =>
          el('div', { class: 'task-run-row' }, [
            el('div', {}, [
              el('strong', { textContent: titleCase(run.role) }),
              el('div', {
                class: 'meta',
                textContent: `${run.executed_provider ?? run.requested_provider ?? 'No provider'} / ${run.executed_model ?? run.requested_model ?? 'automatic'} · ${formatDate(run.started_at)}`,
              }),
            ]),
            el('div', { class: 'task-run-metrics' }, [
              el('span', { class: `provider-status ${taskRunStatusClass(run.status)}`, textContent: titleCase(run.status) }),
              el('span', { class: 'meta', textContent: `${run.latency_ms ?? 0} ms · ~${run.input_tokens_estimated} in / ~${run.output_tokens_estimated ?? 0} out` }),
              run.error ? el('span', { class: 'provider-check-message', textContent: `${run.error.code}: ${run.error.message}` }) : null,
            ]),
          ]),
        )
      : [el('div', { class: 'settings-empty-state', textContent: 'No runs recorded yet.' })];
    return advancedSettings(
      `Recent runs (${this.appState.taskModelRuns.length})`,
      'Role, model, timing and safe errors only. Prompts and answers are never stored.',
      [
        ...rows,
        actionRow([
          el('button', { class: 'pill-btn', textContent: 'Refresh', onclick: () => void this.refresh() }),
        ]),
      ],
      { testId: 'task-model-run-audits' },
    );
  }

  private change<K extends keyof TaskModelProfile>(
    role: TaskModelRole,
    key: K,
    value: TaskModelProfile[K],
    shouldRender = true,
  ): void {
    const profile = this.appState.taskModels.find((item) => item.role === role);
    if (!profile) return;
    profile[key] = value;
    this.dirtyRoles.add(role);
    this.bumpVersion(role);
    delete this.appState.taskModelChecks[role];
    if (shouldRender) this.renderApp();
  }

  private changeNumber(
    role: TaskModelRole,
    key: 'max_input_tokens' | 'max_output_tokens' | 'timeout_seconds' | 'temperature',
    value: string,
  ): void {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) this.change(role, key, parsed, false);
  }

  private bumpVersion(role: TaskModelRole): void {
    this.versions.set(role, (this.versions.get(role) ?? 0) + 1);
  }

  private async save(role: TaskModelRole): Promise<void> {
    const profile = this.appState.taskModels.find((item) => item.role === role);
    if (!profile) return;
    this.appState.taskModelBusy[role] = true;
    this.appState.settingsError = '';
    this.renderApp();
    try {
      const saved = await this.client.updateTaskModel(profile);
      this.appState.taskModels = this.appState.taskModels.map((item) => item.role === role ? saved : item);
      this.bumpVersion(role);
      this.dirtyRoles.delete(role);
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

  private async check(role: TaskModelRole): Promise<void> {
    this.appState.taskModelBusy[role] = true;
    this.renderApp();
    try {
      this.appState.taskModelChecks[role] = await this.client.checkTaskModel(role);
    } catch (error) {
      this.appState.settingsError = errorMessage(error, 'Unable to check readiness.');
    } finally {
      this.appState.taskModelBusy[role] = false;
      this.renderApp();
    }
  }
}

function policyLabel(value: string): string {
  if (value === 'deterministic') return 'Use a plain fallback without a model';
  if (value === 'skip') return 'Skip the task';
  return 'Fail the task';
}

function taskRunStatusClass(status: string): string {
  if (status === 'completed') return 'ok';
  if (status === 'running') return 'checking';
  return status === 'fallback' ? 'idle' : 'fail';
}
