import type { ApiClient } from './api';
import { el, errorMessage, formatBytes, formatDate } from './dom';
import type { SettingsDialogs } from './settings_contracts';
import { actionRow, choiceField, numberField, pageHint, switchField } from './settings_page';
import { advancedSettings, operatorEditor, settingsCard, titleCase } from './settings_ui';
import type { AppState, ResourceEndpointStatus } from './types';

/**
 * GPU coordination and Data: the two administrator pages.
 *
 * Each is one sparse page. Coordination is a mode, a save, and the endpoints
 * it may act on, each endpoint a row that opens; Data is the actions and the
 * archives they produce. What each page used to explain in a banner and four
 * readiness tiles now waits on hover, except the one warning that changes
 * another service's state, which stays in the flow.
 */
export class OperationsSettingsView {
  private readonly openEndpoints = new Set<string>();

  constructor(
    private readonly renderApp: () => void,
    private readonly appState: AppState,
    private readonly client: ApiClient,
    private readonly dialogs: SettingsDialogs,
  ) {}

  gpuNodes(): HTMLElement[] {
    if (!this.appState.session?.is_admin) {
      return [el('div', { class: 'settings-empty-state', textContent: 'Sign in as the administrator to change GPU coordination.' })];
    }
    const coordination = this.appState.resourceCoordination;
    if (!coordination) {
      return [
        el('div', {
          class: 'settings-empty-state',
          textContent: this.appState.resourceCoordinationBusy
            ? 'Asking the providers what they have…'
            : 'GPU coordination is unavailable. Retry, then check provider addresses and server logs.',
        }),
        actionRow([
          el('button', { class: 'pill-btn', textContent: 'Retry', disabled: this.appState.resourceCoordinationBusy, onclick: () => void this.refreshCoordination() }),
        ]),
      ];
    }
    const busy = this.appState.resourceCoordinationBusy;
    return [
      settingsCard([
        choiceField('Mode', coordination.settings.mode, ['disabled', 'observe', 'managed'], (value) => {
          coordination.settings.mode = value as typeof coordination.settings.mode;
          this.renderApp();
        }, {
          testId: 'resource-coordination-mode',
          display: modeLabel,
          hover: 'Off leaves every provider alone. Observe waits for measured room before starting media work. Managed may also unload models, but only on endpoints authorized below.',
        }),
        coordination.settings.mode === 'managed'
          ? el('div', {
              class: 'settings-warning',
              textContent: 'Managed mode can unload models on the endpoints authorized below. Authorize only services nothing else uses.',
            })
          : null,
        actionRow([
          el('button', {
            class: 'send-btn',
            textContent: busy ? 'Saving…' : 'Save coordination',
            disabled: busy,
            'data-testid': 'resource-coordination-save',
            onclick: () => void this.saveCoordination(),
          }),
          el('button', {
            class: 'pill-btn',
            textContent: busy ? 'Checking…' : 'Refresh telemetry',
            disabled: busy,
            onclick: () => void this.checkCoordination(),
          }),
        ]),
      ]),
      advancedSettings('More options', 'How much room to keep free, and how long to wait for it.', [
        el('div', { class: 'settings-grid' }, [
          numberField('Reserved VRAM (MB)', String(coordination.settings.reserve_vram_mb), (value) => {
            coordination.settings.reserve_vram_mb = boundedInteger(value, 0, 131072, 1024);
          }, { hover: 'Kept unused after admitting a job’s estimated demand.' }),
          numberField('Longest wait (seconds)', String(coordination.settings.max_wait_seconds), (value) => {
            coordination.settings.max_wait_seconds = boundedInteger(value, 1, 3600, 300);
          }, { hover: 'A job that has waited this long for room fails safely instead of hanging.' }),
          numberField('Check every (seconds)', String(coordination.settings.poll_interval_seconds), (value) => {
            coordination.settings.poll_interval_seconds = boundedNumber(value, 0.25, 60, 2);
          }, { hover: 'How often capacity is re-read while a job waits.' }),
        ]),
      ], { testId: 'gpu-advanced-settings' }),
      pageHint('Unknown capacity is never presented as free VRAM.'),
      ...coordination.endpoints.map((endpoint) => this.endpointCard(endpoint)),
      this.coordinationEvents(),
    ];
  }

  dataNodes(): HTMLElement[] {
    if (!this.appState.session?.is_admin) {
      return [el('div', { class: 'settings-empty-state', textContent: 'Sign in as the administrator to manage backups and diagnostic logs.' })];
    }
    const running = this.appState.backupActionRunning;
    return [
      settingsCard([
        el('div', { class: 'operator-action-grid' }, [
          el('button', {
            class: 'send-btn',
            textContent: running ? 'Creating…' : 'Create database backup',
            title: 'Written to the archive directory. Copy it somewhere independent.',
            disabled: running,
            onclick: () => void this.createBackup(false),
          }),
          el('button', {
            class: 'pill-btn',
            textContent: running ? 'Creating…' : 'Create backup with media',
            title: 'The database plus the protected media files. Larger.',
            disabled: running,
            onclick: () => void this.createBackup(true),
          }),
          el('button', {
            class: 'pill-btn',
            textContent: 'Download diagnostic log',
            title: 'Secrets are left out; timings and safe error context are not.',
            onclick: () => window.open(this.client.diagnosticLogUrl(), '_blank', 'noopener'),
          }),
          el('button', {
            class: 'pill-btn',
            textContent: this.appState.backupsLoading ? 'Refreshing…' : 'Refresh',
            disabled: this.appState.backupsLoading,
            onclick: () => void this.refreshBackups(),
          }),
        ]),
      ]),
      pageHint('Verify an archive before relying on it. Deleting one cannot be undone.'),
      ...(this.appState.backupItems.length
        ? this.appState.backupItems.map((item) => operatorEditor(
            item.name,
            `${formatBytes(item.size)} · ${formatDate(item.created_at)}`,
            item.include_media ? 'Database + media' : 'Database',
            [
              el('div', { class: 'operator-actions' }, [
                el('button', { class: 'pill-btn', textContent: 'Download', onclick: () => window.open(this.client.backupDownloadUrl(item.name), '_blank', 'noopener') }),
                el('button', {
                  class: 'pill-btn',
                  textContent: running ? 'Verifying…' : 'Verify restore',
                  title: 'A temporary restore that checks integrity and migration compatibility. The live database is untouched.',
                  disabled: running,
                  onclick: () => void this.verifyBackup(item.name),
                }),
                el('button', { class: 'pill-btn danger', textContent: 'Delete archive', onclick: () => void this.deleteBackup(item.name) }),
              ]),
            ],
            { testId: `backup-${item.name}`, className: 'backup-editor', statusClass: 'idle' },
          ))
        : [el('div', { class: 'settings-empty-state', textContent: 'No backups yet. Make and verify one before any risky change.' })]),
    ];
  }

  async refreshCoordination(): Promise<void> {
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

  async refreshBackups(): Promise<void> {
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

  /** One provider endpoint: what it reports, and whether this app may act on it. */
  private endpointCard(endpoint: ResourceEndpointStatus): HTMLElement {
    const snapshot = endpoint.snapshot;
    const capacity = snapshot?.free_vram_mb == null
      ? titleCase(snapshot?.status ?? 'not checked')
      : `${snapshot.free_vram_mb} MB free of ${snapshot.total_vram_mb ?? 'unknown'} MB`;
    const status = snapshot?.status === 'known' ? 'Measured' : titleCase(snapshot?.status ?? 'Not checked');
    const abilities = [
      endpoint.capabilities.reports_capacity ? 'reports capacity' : null,
      endpoint.capabilities.reports_queue ? 'reports its queue' : null,
      endpoint.capabilities.supports_release ? 'can release models' : 'cannot release models',
      endpoint.capabilities.supports_precise_cancel ? 'cancels precisely' : 'cancels cooperatively',
    ].filter((item): item is string => item !== null).join(' · ');
    return operatorEditor(
      titleCase(endpoint.provider),
      `${endpoint.endpoint_label} · ${capacity}`,
      status,
      [
        el('p', { class: 'meta', textContent: abilities, title: 'What the adapter can do. Being able to release a model is not permission to.' }),
        snapshot?.message ? el('div', { class: 'meta', textContent: snapshot.message }) : null,
        switchField('Nothing else uses this endpoint', endpoint.authorization.exclusive_control, (checked) => {
          endpoint.authorization.exclusive_control = checked;
          if (!checked) endpoint.authorization.allow_release = false;
          this.renderApp();
        }, { hover: 'Only if no other application or person uses this exact address.' }),
        switchField('Allow releasing its models', endpoint.authorization.allow_release, (checked) => {
          endpoint.authorization.allow_release = checked && endpoint.authorization.exclusive_control;
          this.renderApp();
        }, {
          hover: 'Needs the switch above. Every release is verified afterwards and recorded.',
          disabled: !endpoint.authorization.exclusive_control,
        }),
      ],
      {
        open: this.openEndpoints.has(endpoint.provider),
        onToggle: (open) => open ? this.openEndpoints.add(endpoint.provider) : this.openEndpoints.delete(endpoint.provider),
        testId: `resource-endpoint-${endpoint.provider}`,
        className: 'resource-endpoint-card',
        statusClass: snapshot?.status === 'known' ? 'ok' : snapshot?.status === 'unavailable' ? 'fail' : 'idle',
      },
    );
  }

  private coordinationEvents(): HTMLElement {
    const rows = this.appState.resourceCoordinationEvents.length
      ? this.appState.resourceCoordinationEvents.slice(0, 20).map((event) => el('div', { class: 'task-run-row' }, [
          el('div', {}, [
            el('strong', { textContent: `${titleCase(event.provider)} · ${titleCase(event.action)}` }),
            el('div', { class: 'meta', textContent: `${titleCase(event.outcome)} · ${formatDate(event.created_at)}` }),
          ]),
        ]))
      : [el('div', { class: 'settings-empty-state', textContent: 'No coordination events yet.' })];
    return advancedSettings(
      `Recent events (${this.appState.resourceCoordinationEvents.length})`,
      'Waits, admissions, releases and their verification. Never content.',
      rows,
      { testId: 'gpu-coordination-events' },
    );
  }

  private async saveCoordination(): Promise<void> {
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

  private async checkCoordination(): Promise<void> {
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
    } catch (error) {
      this.appState.settingsError = errorMessage(error, 'Unable to create the backup.');
    } finally {
      this.appState.backupActionRunning = false;
      this.renderApp();
    }
  }

  private async deleteBackup(name: string): Promise<void> {
    if (!(await this.dialogs.confirm('Delete backup', `Permanently delete ${name}? This archive cannot be recovered from Nice Assistant.`, 'Delete archive'))) return;
    try {
      await this.client.deleteBackup(name);
      await this.refreshBackups();
    } catch (error) {
      this.appState.settingsError = errorMessage(error, 'Unable to delete the backup.');
      this.renderApp();
    }
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

function modeLabel(value: string): string {
  const labels: Record<string, string> = {
    disabled: 'Off — leave providers alone',
    observe: 'Observe — wait for measured room',
    managed: 'Managed — may unload models on authorized endpoints',
  };
  return labels[value] ?? titleCase(value);
}

function boundedInteger(value: string, minimum: number, maximum: number, fallback: number): number {
  const parsed = Number.parseInt(value, 10);
  return Number.isFinite(parsed) ? Math.max(minimum, Math.min(maximum, parsed)) : fallback;
}

function boundedNumber(value: string, minimum: number, maximum: number, fallback: number): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? Math.max(minimum, Math.min(maximum, parsed)) : fallback;
}
