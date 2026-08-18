import type { ApiClient } from './api';
import { el, errorMessage } from './dom';
import { normalizeSettings, settingsWire } from './settings';
import type { AppState, PregenerationReadiness, SceneBacklogEntry } from './types';

/**
 * The few settings worth seeing without going looking for them.
 *
 * Background picture production is here because it spends real electricity on a
 * schedule while nobody is watching, and a setting nobody sees is a setting
 * nobody revisits. Speech and memory mode are here because the owner named them
 * as the other two that change what happens without announcing it.
 *
 * There is no second copy of any of these values. Every control reads and
 * writes `state.settings`, which is the same object the settings page edits and
 * the same one that goes to `PUT /settings`, so the homepage and the settings
 * page cannot drift apart: there is nothing to drift.
 */

const HOURS = Array.from({ length: 24 }, (_value, hour) => hour);

function hourLabel(hour: number): string {
  return `${String(hour).padStart(2, '0')}:00`;
}

function whenLabel(seconds: number): string {
  // A day, not a timestamp to the second. Nobody reading a dashboard needs to
  // know that the last picture landed at 5:21:40 PM.
  return new Date(seconds * 1000).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

export class HomeControls {
  private saving = false;

  constructor(
    private readonly appState: AppState,
    private readonly client: ApiClient,
    private readonly renderApp: () => void,
  ) {}

  node(readiness: PregenerationReadiness | null, produced: SceneBacklogEntry | null): HTMLElement {
    return el('section', { class: 'home-card', 'data-testid': 'home-controls' }, [
      el('h2', { class: 'home-card-title', textContent: 'Quick settings' }),
      this.pregeneration(readiness, produced),
      this.speech(),
      this.memory(),
    ]);
  }

  private async save(): Promise<void> {
    if (!this.appState.settings || this.saving) return;
    this.saving = true;
    this.renderApp();
    try {
      this.appState.settings = normalizeSettings(await this.client.updateSettings(settingsWire(this.appState.settings)));
    } catch (error) {
      this.appState.uiError = errorMessage(error, 'That setting could not be saved.');
    } finally {
      this.saving = false;
      this.renderApp();
    }
  }

  private pregeneration(readiness: PregenerationReadiness | null, produced: SceneBacklogEntry | null): HTMLElement {
    const settings = this.appState.settings;
    const forbidden = Boolean(readiness?.deployment_forbids);
    const enabled = forbidden ? false : Boolean(settings?.pregeneration_enabled ?? readiness?.enabled ?? false);
    return el('div', { class: 'home-control', 'data-testid': 'home-pregeneration' }, [
      el('label', { class: 'home-control-row' }, [
        el('input', {
          type: 'checkbox',
          checked: enabled,
          disabled: forbidden || this.saving || !settings,
          'data-testid': 'home-pregeneration-toggle',
          onchange: (event: Event) => {
            if (!this.appState.settings) return;
            this.appState.settings.pregeneration_enabled = (event.currentTarget as HTMLInputElement).checked;
            void this.save();
          },
        }),
        el('span', { textContent: 'Make pictures overnight' }),
      ]),
      forbidden
        ? el('p', {
            class: 'meta',
            'data-testid': 'home-pregeneration-forbidden',
            textContent: 'Turned off for this deployment, so it cannot be switched on here.',
          })
        : el('div', { class: 'home-control-row' }, [
            el('span', { class: 'meta', textContent: 'Between' }),
            this.hourSelect('pregeneration_start_hour', readiness?.start_hour ?? 2),
            el('span', { class: 'meta', textContent: 'and' }),
            this.hourSelect('pregeneration_end_hour', readiness?.end_hour ?? 6),
          ]),
      this.status(readiness, produced),
    ]);
  }

  private hourSelect(key: 'pregeneration_start_hour' | 'pregeneration_end_hour', fallback: number): HTMLElement {
    const settings = this.appState.settings;
    const current = Number(settings?.[key] ?? fallback);
    return el(
      'select',
      {
        class: 'chip-select',
        'data-testid': `home-${key.replace(/_/g, '-')}`,
        disabled: this.saving || !settings,
        onchange: (event: Event) => {
          if (!this.appState.settings) return;
          this.appState.settings[key] = Number((event.currentTarget as HTMLSelectElement).value);
          void this.save();
        },
      },
      HOURS.map((hour) => el('option', { value: String(hour), selected: hour === current, textContent: hourLabel(hour) })),
    );
  }

  private status(readiness: PregenerationReadiness | null, produced: SceneBacklogEntry | null): HTMLElement {
    if (!readiness) {
      return el('p', { class: 'meta', textContent: 'Not known — the readiness check did not answer.' });
    }
    // One line. This was four, and two of them said the same thing: that it was
    // outside a window whose hours are in the controls directly above.
    const waiting = readiness.approved_waiting
      ? `${readiness.approved_waiting} ${readiness.approved_waiting === 1 ? 'scene' : 'scenes'} waiting`
      : 'Nothing waiting';
    const made = produced ? `last made ${whenLabel(produced.updated_at)}` : 'none made yet';
    const lines = [`${waiting.charAt(0).toUpperCase()}${waiting.slice(1)} · ${made}.`];
    // Being inside the window and still not running is a fault rather than a
    // schedule, and that is the only time the reason tells anybody anything.
    if (readiness.inside_window && readiness.reason) lines.push(readiness.reason);
    return el('div', { class: 'home-control-status', 'data-testid': 'home-pregeneration-status' },
      lines.map((line) => el('p', { class: 'meta', textContent: line })));
  }

  private speech(): HTMLElement {
    const settings = this.appState.settings;
    return el('label', { class: 'home-control-row', 'data-testid': 'home-speech' }, [
      el('input', {
        type: 'checkbox',
        checked: Boolean(settings?.general_voice_responses),
        disabled: this.saving || !settings,
        'data-testid': 'home-speech-toggle',
        onchange: (event: Event) => {
          if (!this.appState.settings) return;
          this.appState.settings.general_voice_responses = (event.currentTarget as HTMLInputElement).checked;
          void this.save();
        },
      }),
      el('span', { textContent: 'Speak replies aloud' }),
    ]);
  }

  private memory(): HTMLElement {
    const settings = this.appState.settings;
    const mode = settings?.default_memory_mode === 'off' ? 'off' : 'saved';
    return el('div', { class: 'home-control-row', 'data-testid': 'home-memory' }, [
      el('span', { textContent: 'Saved memory' }),
      el(
        'select',
        {
          class: 'chip-select',
          disabled: this.saving || !settings,
          'data-testid': 'home-memory-mode',
          onchange: (event: Event) => {
            if (!this.appState.settings) return;
            const value = (event.currentTarget as HTMLSelectElement).value;
            this.appState.settings.default_memory_mode = value === 'off' ? 'off' : 'saved';
            void this.save();
          },
        },
        [
          el('option', { value: 'saved', selected: mode === 'saved', textContent: 'Use it in new chats' }),
          el('option', { value: 'off', selected: mode === 'off', textContent: 'Do not use it' }),
        ],
      ),
    ]);
  }
}
