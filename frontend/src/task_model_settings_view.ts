import type { ApiClient } from './api';
import { el, errorMessage, formatDate } from './dom';
import { inputField, selectField, toggleField } from './settings_controls';
import { advancedSettings, operatorEditor, readinessRow, settingsCard, settingsHeading, settingsIntro, titleCase } from './settings_ui';
import type { AppState, TaskModelProfile, TaskModelRole } from './types';

const ROLE_HELP: Record<TaskModelRole, string> = {
  title_generation: 'Creates short chat titles after conversation turns.',
  conversation_summary: 'Compresses older conversation history when the context budget requires it.',
  memory_extraction: 'Proposes reviewable memory candidates without automatically approving them.',
  capability_planning: 'Chooses a typed assistant capability. Media models, workflows, LoRAs, and identity controls are selected later by the media coordinator.',
};

export class TaskModelSettingsView {
  private readonly dirtyRoles = new Set<TaskModelRole>();
  private readonly versions = new Map<TaskModelRole, number>();
  private readonly openRoles = new Set<TaskModelRole>();

  constructor(
    private readonly renderApp: () => void,
    private readonly appState: AppState,
    private readonly client: ApiClient,
  ) {}

  nodes(): HTMLElement[] {
    const enabled = this.appState.taskModels.filter((profile) => profile.enabled);
    const ready = enabled.filter((profile) => this.appState.taskModelChecks[profile.role]?.ready);
    const checked = enabled.filter((profile) => this.appState.taskModelChecks[profile.role]);
    const rows: HTMLElement[] = [
      settingsIntro(
        'Configure background intelligence',
        'Task Models handle platform work separate from persona behavior and share the interactive Ollama lane with chat. Capability planning does not choose media workflows, LoRAs, and identity controls; the media coordinator handles those later.',
      ),
      el('div', { class: 'settings-readiness-list' }, [
        readinessRow(
          'Configured roles',
          `${enabled.length} of ${this.appState.taskModels.length} enabled`,
          enabled.length ? 'ready' : 'attention',
          'Disabled roles use their documented fallback behavior and never silently borrow the persona model.',
        ),
        readinessRow(
          'Readiness checks',
          checked.length ? `${ready.length} of ${checked.length} checked roles ready` : 'Not checked in this browser session',
          checked.length && ready.length === checked.length ? 'ready' : (checked.length ? 'attention' : 'off'),
          'A readiness check verifies the configured provider and installed model; it does not judge output quality.',
        ),
        readinessRow(
          'GPU scheduling',
          'Shares the interactive lane with persona chat',
          'ready',
          'The default single interactive worker prevents background inference from overlapping chat on a shared GPU.',
        ),
        readinessRow(
          'Recent audits',
          `${this.appState.taskModelRuns.length} content-free run records loaded`,
          this.appState.taskModelRuns.length ? 'ready' : 'off',
          'Audits retain role, model, timing, token estimates, and safe errors—not prompts or generated task content.',
        ),
      ]),
      settingsCard([
        settingsHeading('Task roles', 'Open a role to choose its model, fallback, limits, and failure behavior.'),
        el('button', {
          class: 'pill-btn',
          textContent: 'Refresh roles and audits',
          onclick: () => void this.refresh(),
        }),
      ]),
    ];
    if (!this.appState.taskModels.length) {
      rows.push(el('div', {
        class: 'settings-empty-state',
        textContent: 'No Task Model profiles were returned. Refresh the page, then check the server logs if the profiles remain unavailable.',
      }));
    } else {
      rows.push(...this.appState.taskModels.map((profile) => this.profileCard(profile)));
    }
    rows.push(this.runAudit());
    return rows;
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
      this.appState.settingsError = errorMessage(error, 'Unable to load Task Models.');
    }
    this.renderApp();
  }

  private profileCard(profile: TaskModelProfile): HTMLElement {
    const readiness = this.appState.taskModelChecks[profile.role];
    const busy = Boolean(this.appState.taskModelBusy[profile.role]);
    const modelOptions = ['', ...this.appState.models];
    const displayModel = (value: string) => value || 'Automatic (first installed model)';
    const fallbackPolicies = profile.role === 'title_generation'
      ? ['deterministic', 'skip', 'fail']
      : ['skip', 'fail'];
    const status = !profile.enabled
      ? 'Disabled'
      : readiness
        ? (readiness.ready ? 'Ready' : titleCase(readiness.status))
        : 'Not checked';
    const statusClass = !profile.enabled ? 'idle' : readiness?.ready ? 'ok' : readiness ? 'fail' : 'idle';
    return operatorEditor(
      profile.title,
      profile.enabled
        ? `${profile.model || 'Automatic model'} · ${profile.fallback_policy} fallback policy`
        : 'This background role is disabled',
      status,
      [
        settingsHeading('Execution path', ROLE_HELP[profile.role]),
        toggleField('Enable this role', profile.enabled, (value) => this.change(profile.role, 'enabled', value, false), 'Disabled roles follow the selected failure behavior instead of running a model.'),
        selectField('Provider', profile.provider, ['ollama'], (value) => this.change(profile.role, 'provider', value, false), undefined, titleCase, false, 'Only providers implementing the structured Task Model contract appear here.'),
        selectField('Primary model', profile.model ?? '', modelOptions, (value) => this.change(profile.role, 'model', value || null, false), undefined, displayModel, false, 'Automatic uses the first installed Ollama model. An explicit choice is more predictable.'),
        selectField('Fallback model', profile.fallback_model ?? '', modelOptions, (value) => {
          this.change(profile.role, 'fallback_model', value || null, false);
          this.change(profile.role, 'fallback_provider', value ? profile.provider : null, false);
        }, undefined, displayModel, false, 'Tried only after the primary execution fails and before the role failure policy is applied.'),
        advancedSettings(
          'Budgets and failure behavior',
          'Bounds keep background work from consuming unbounded context, time, or output.',
          [
            inputField('Maximum input tokens', String(profile.max_input_tokens), (value) => this.changeNumber(profile.role, 'max_input_tokens', value), 'number', false, 'The maximum estimated input size sent to this role.'),
            inputField('Maximum output tokens', String(profile.max_output_tokens), (value) => this.changeNumber(profile.role, 'max_output_tokens', value), 'number', false, 'The maximum structured output size accepted from this role.'),
            inputField('Timeout seconds', String(profile.timeout_seconds), (value) => this.changeNumber(profile.role, 'timeout_seconds', value), 'number', false, 'Stops waiting for this background task after the limit.'),
            inputField('Temperature', String(profile.temperature), (value) => this.changeNumber(profile.role, 'temperature', value), 'number', false, 'Low values are recommended for deterministic platform work.'),
            selectField('Failure behavior', profile.fallback_policy, fallbackPolicies, (value) => this.change(profile.role, 'fallback_policy', value as TaskModelProfile['fallback_policy'], false), undefined, titleCase, false, 'Determines whether the product skips the task, fails it, or uses a narrow deterministic fallback.'),
          ],
          { testId: `task-model-advanced-${profile.role}` },
        ),
        el('div', { class: 'chips' }, [
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
      ],
      {
        open: this.openRoles.has(profile.role),
        onToggle: (open) => open ? this.openRoles.add(profile.role) : this.openRoles.delete(profile.role),
        testId: `task-model-${profile.role}`,
        className: 'task-model-card',
        statusClass,
      },
    );
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
      : [el('div', { class: 'settings-empty-state', textContent: 'No Task Model runs have been recorded yet.' })];
    return advancedSettings(
      `Recent Task Model audits (${this.appState.taskModelRuns.length})`,
      'Prompts and generated task output are not stored. These records contain only role, provider/model, timing, token estimates, status, and safe errors.',
      rows,
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
      this.appState.settingsError = errorMessage(error, 'Unable to check Task Model readiness.');
    } finally {
      this.appState.taskModelBusy[role] = false;
      this.renderApp();
    }
  }
}

function taskRunStatusClass(status: string): string {
  if (status === 'completed') return 'ok';
  if (status === 'running') return 'checking';
  return status === 'fallback' ? 'idle' : 'fail';
}
