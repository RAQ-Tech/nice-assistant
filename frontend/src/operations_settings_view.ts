import type { ApiClient } from './api';
import { el, errorMessage, formatBytes, formatDate } from './dom';
import { inputField, selectField, toggleField } from './settings_controls';
import type { SettingsDialogs } from './settings_contracts';
import { advancedSettings, operatorEditor, readinessRow, settingsCard, settingsHeading, settingsIntro, titleCase } from './settings_ui';
import type { AppState, ResourceEndpointStatus } from './types';

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
      return [
        settingsIntro('Coordinate shared GPU work', 'This tab changes installation-wide provider scheduling and requires the administrator account.'),
        el('div', { class: 'settings-empty-state', textContent: 'Sign in with the administrator account to inspect or change GPU coordination.' }),
      ];
    }
    const coordination = this.appState.resourceCoordination;
    if (!coordination) {
      return [
        settingsIntro('Coordinate shared GPU work', 'Nice Assistant can observe measured capacity and release only explicitly authorized exclusive endpoints.'),
        el('div', {
          class: 'settings-empty-state',
          textContent: this.appState.resourceCoordinationBusy
            ? 'Loading provider capacity and authorization…'
            : 'GPU coordination is unavailable. Retry, then check provider addresses and server logs.',
        }),
        el('button', { class: 'pill-btn', textContent: 'Retry GPU status', disabled: this.appState.resourceCoordinationBusy, onclick: () => void this.refreshCoordination() }),
      ];
    }
    const measured = coordination.endpoints.filter((endpoint) => endpoint.snapshot?.status === 'known');
    const releasable = coordination.endpoints.filter((endpoint) => endpoint.capabilities.supports_release);
    const authorized = coordination.endpoints.filter((endpoint) => endpoint.authorization.allow_release);
    return [
      settingsIntro(
        'Coordinate shared GPU work',
        'Observe mode waits for measured capacity. Managed mode may release only endpoints you explicitly attest are exclusive to Nice Assistant.',
      ),
      el('div', { class: 'settings-readiness-list' }, [
        readinessRow('Coordination mode', titleCase(coordination.settings.mode), coordination.settings.mode === 'disabled' ? 'off' : 'ready', 'Disabled preserves provider behavior. Observe waits without unloading. Managed adds verified release controls.'),
        readinessRow('Measured capacity', `${measured.length} of ${coordination.endpoints.length} providers known`, measured.length === coordination.endpoints.length ? 'ready' : 'attention', 'Unknown capacity is never presented as free VRAM.'),
        readinessRow('Release-capable providers', `${releasable.length} reported`, releasable.length ? 'ready' : 'off', 'Provider capability means an adapter exposes a supported release API; it does not grant permission to use it.'),
        readinessRow('Authorized release', `${authorized.length} explicitly allowed`, authorized.length ? 'attention' : 'off', 'Authorization must match the current endpoint fingerprint and is invalidated when the address changes.'),
      ]),
      settingsCard([
        settingsHeading('Coordination policy', 'Choose the least powerful mode that meets the needs of this shared GPU.'),
        selectField('Mode', coordination.settings.mode, ['disabled', 'observe', 'managed'], (value) => {
          coordination.settings.mode = value as typeof coordination.settings.mode;
          this.renderApp();
        }, 'resource-coordination-mode', modeLabel, true, 'Managed mode can invoke release controls only for separately authorized endpoints.'),
        coordination.settings.mode === 'managed'
          ? el('div', {
              class: 'settings-warning',
              textContent: 'Managed mode can unload models or checkpoints on authorized endpoints. Authorize only services used exclusively by Nice Assistant; shared providers must remain unauthorized.',
            })
          : null,
        el('div', { class: 'chips' }, [
          el('button', {
            class: 'send-btn',
            textContent: this.appState.resourceCoordinationBusy ? 'Saving…' : 'Save coordination',
            disabled: this.appState.resourceCoordinationBusy,
            'data-testid': 'resource-coordination-save',
            onclick: () => void this.saveCoordination(),
          }),
          el('button', {
            class: 'pill-btn',
            textContent: this.appState.resourceCoordinationBusy ? 'Checking…' : 'Refresh telemetry',
            disabled: this.appState.resourceCoordinationBusy,
            onclick: () => void this.checkCoordination(),
          }),
        ]),
      ]),
      advancedSettings(
        'Capacity timing and reserve',
        'Expert scheduling bounds used while waiting for enough measured GPU capacity.',
        [
          inputField('Reserved VRAM (MB)', String(coordination.settings.reserve_vram_mb), (value) => {
            coordination.settings.reserve_vram_mb = boundedInteger(value, 0, 131072, 1024);
          }, 'number', false, 'Capacity kept unused after admitting estimated demand.'),
          inputField('Maximum capacity wait (seconds)', String(coordination.settings.max_wait_seconds), (value) => {
            coordination.settings.max_wait_seconds = boundedInteger(value, 1, 3600, 300);
          }, 'number', false, 'Fails an admission attempt safely after this wait rather than hanging indefinitely.'),
          inputField('Telemetry interval (seconds)', String(coordination.settings.poll_interval_seconds), (value) => {
            coordination.settings.poll_interval_seconds = boundedNumber(value, 0.25, 60, 2);
          }, 'number', false, 'How often Nice Assistant refreshes capacity while a job waits.'),
        ],
        { testId: 'gpu-advanced-settings' },
      ),
      settingsCard([
        settingsHeading('Provider endpoints', 'Open an endpoint to review its measured status, adapter capabilities, and release authorization.'),
      ]),
      ...coordination.endpoints.map((endpoint) => this.endpointCard(endpoint)),
      this.coordinationEvents(),
    ];
  }

  dataNodes(): HTMLElement[] {
    if (!this.appState.session?.is_admin) {
      return [
        settingsIntro('Protect and inspect this installation', 'Backups and diagnostic logs contain installation-wide operational data and require the administrator account.'),
        el('div', { class: 'settings-empty-state', textContent: 'Sign in with the administrator account to manage backups and diagnostic logs.' }),
      ];
    }
    return [
      settingsIntro(
        'Protect and inspect this installation',
        'Create recoverable snapshots, verify them before relying on them, and download redacted diagnostic logs when troubleshooting.',
      ),
      el('div', { class: 'settings-readiness-list' }, [
        readinessRow('Available backups', `${this.appState.backupItems.length} archives`, this.appState.backupItems.length ? 'ready' : 'attention', 'A backup is not proven usable until its database integrity and migration compatibility are verified.'),
        readinessRow('Backup verification', 'Available for every listed archive', 'ready', 'Verification runs a temporary restore drill without replacing the live database.'),
        readinessRow('Diagnostic logs', 'Redacted download available', 'ready', 'Logs omit configured secrets but may still contain operational timing and safe error context.'),
        readinessRow('Media coverage', 'Chosen per backup', 'ready', 'Database-only snapshots are smaller. Full backups also include regular protected media files.'),
      ]),
      settingsCard([
        settingsHeading('Create a backup', 'Backups are written to the configured archive directory and should be copied to independent storage.'),
        el('div', { class: 'operator-action-grid' }, [
          el('button', {
            class: 'send-btn',
            textContent: this.appState.backupActionRunning ? 'Creating…' : 'Create database backup',
            disabled: this.appState.backupActionRunning,
            onclick: () => void this.createBackup(false),
          }),
          el('button', {
            class: 'pill-btn',
            textContent: this.appState.backupActionRunning ? 'Creating…' : 'Create backup with media',
            disabled: this.appState.backupActionRunning,
            onclick: () => void this.createBackup(true),
          }),
          el('button', {
            class: 'pill-btn',
            textContent: 'Download diagnostic log',
            onclick: () => window.open(this.client.diagnosticLogUrl(), '_blank', 'noopener'),
          }),
          el('button', {
            class: 'pill-btn',
            textContent: this.appState.backupsLoading ? 'Refreshing…' : 'Refresh backups',
            disabled: this.appState.backupsLoading,
            onclick: () => void this.refreshBackups(),
          }),
        ]),
      ]),
      settingsCard([
        settingsHeading(`Backup archives (${this.appState.backupItems.length})`, 'Verify an archive before treating it as a recovery point. Deleting an archive cannot be undone.'),
      ]),
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
                  textContent: this.appState.backupActionRunning ? 'Verifying…' : 'Verify restore',
                  disabled: this.appState.backupActionRunning,
                  onclick: () => void this.verifyBackup(item.name),
                }),
                el('button', { class: 'pill-btn danger', textContent: 'Delete archive', onclick: () => void this.deleteBackup(item.name) }),
              ]),
            ],
            { testId: `backup-${item.name}`, className: 'backup-editor', statusClass: 'idle' },
          ))
        : [el('div', { class: 'settings-empty-state', textContent: 'No backups are available yet. Create and verify one before making risky deployment or migration changes.' })]),
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

  private endpointCard(endpoint: ResourceEndpointStatus): HTMLElement {
    const snapshot = endpoint.snapshot;
    const capacity = snapshot?.free_vram_mb == null
      ? titleCase(snapshot?.status ?? 'not checked')
      : `${snapshot.free_vram_mb} MB free of ${snapshot.total_vram_mb ?? 'unknown'} MB`;
    const status = snapshot?.status === 'known' ? 'Measured' : titleCase(snapshot?.status ?? 'Not checked');
    return operatorEditor(
      titleCase(endpoint.provider),
      `${endpoint.endpoint_label} · ${capacity}`,
      status,
      [
        el('div', { class: 'settings-readiness-list' }, [
          readinessRow('Capacity reporting', endpoint.capabilities.reports_capacity ? 'Supported' : 'Unavailable', endpoint.capabilities.reports_capacity ? 'ready' : 'off', 'Determines whether Nice Assistant can use measured free VRAM instead of unknown capacity.'),
          readinessRow('Queue reporting', endpoint.capabilities.reports_queue ? 'Supported' : 'Unavailable', endpoint.capabilities.reports_queue ? 'ready' : 'off', 'Queue depth helps explain whether the external provider is already busy.'),
          readinessRow('Release control', endpoint.capabilities.supports_release ? 'Adapter supports release' : 'Not supported', endpoint.capabilities.supports_release ? 'attention' : 'off', 'Support does not authorize release. Exclusive control and allow-release must both be explicitly enabled.'),
          readinessRow('Precise cancellation', endpoint.capabilities.supports_precise_cancel ? 'Supported' : 'Cooperative or unavailable', endpoint.capabilities.supports_precise_cancel ? 'ready' : 'off', 'Providers without precise cancellation may finish work whose late result Nice Assistant discards.'),
        ]),
        snapshot?.message ? el('div', { class: 'meta', textContent: snapshot.message }) : null,
        toggleField('This endpoint is exclusively managed by Nice Assistant', endpoint.authorization.exclusive_control, (checked) => {
          endpoint.authorization.exclusive_control = checked;
          if (!checked) endpoint.authorization.allow_release = false;
          this.renderApp();
        }, 'Enable only if no other application or person uses this exact provider endpoint.'),
        toggleField('Allow verified release controls', endpoint.authorization.allow_release, (checked) => {
          endpoint.authorization.allow_release = checked && endpoint.authorization.exclusive_control;
          this.renderApp();
        }, 'Requires exclusive control. Managed mode verifies provider state after every release attempt and records the result.'),
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
      : [el('div', { class: 'settings-empty-state', textContent: 'No coordination events have been recorded yet.' })];
    return advancedSettings(
      `Recent coordination events (${this.appState.resourceCoordinationEvents.length})`,
      'Content-free operational records of waits, admissions, release attempts, verification, and safe failures.',
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
    disabled: 'Disabled — preserve provider behavior',
    observe: 'Observe — wait for measured capacity',
    managed: 'Managed — allow authorized release controls',
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
