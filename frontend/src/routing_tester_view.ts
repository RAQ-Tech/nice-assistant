import type { ApiClient } from './api';
import { el, errorMessage } from './dom';
import { textareaField } from './settings_controls';
import { advancedSettings, settingsCard, settingsHeading } from './settings_ui';
import type { AppState } from './types';

/**
 * The routing tester.
 *
 * Deliberately temporary tooling, and deliberately its own module so removing
 * it later is deleting a file rather than untangling one. It exists because
 * authoring a routing card is otherwise guesswork: nothing else shows whether
 * the sentence you wrote makes the preset you meant win.
 */
export class RoutingTesterView {
  private text = '';

  constructor(
    private readonly appState: AppState,
    private readonly client: ApiClient,
    private readonly renderApp: () => void,
  ) {}

  node(): HTMLElement {
    const preview = this.appState.routingPreview;
    return settingsCard([
      settingsHeading(
        'Routing tester',
        'Paste a message and see which presets were offered, which one routing chose, and why. This is a diagnostic and is expected to be removed once routing is stable.',
      ),
      advancedSettings(
        'Test routing',
        'Runs the real shortlist, task model, and planner for one message. Nothing is generated.',
        [
          textareaField(
            'Message to test',
            this.text,
            (value) => { this.text = value; },
            false,
            'The same shortlist, task model, and planner a real turn would use.',
          ),
          el('div', { class: 'chips' }, [
            el('button', {
              class: 'pill-btn',
              textContent: this.appState.mediaCatalogBusy ? 'Testing…' : 'Test routing',
              disabled: this.appState.mediaCatalogBusy || !this.text.trim(),
              'data-testid': 'routing-tester-run',
              onclick: () => void this.run(),
            }),
          ]),
          preview ? this.result(preview) : null,
        ],
        { testId: 'routing-tester' },
      ),
    ]);
  }

  private result(preview: NonNullable<AppState['routingPreview']>): HTMLElement {
    const winner = preview.plan?.explanation?.preset;
    return el('div', { class: 'routing-preview', 'data-testid': 'routing-tester-result' }, [
      el('p', { class: 'meta', textContent: `Presets offered: ${preview.shortlist.length}` }),
      el('ul', { class: 'routing-shortlist' }, preview.shortlist.map((item) =>
        el('li', {}, [
          el('span', { class: 'routing-shortlist-title', textContent: item.title }),
          el('span', {
            class: 'meta',
            textContent: item.routing_card || 'No routing card, so this can only be chosen by tags and priority.',
          }),
        ]),
      )),
      preview.task_model.error
        ? el('p', { class: 'settings-warning', textContent: preview.task_model.error })
        : null,
      !preview.requested && !preview.task_model.error
        ? el('p', { textContent: 'No image would be requested for this message.' })
        : null,
      winner
        ? el('div', { class: 'routing-outcome' }, [
            el('p', { textContent: `Chosen: ${winner.name}` }),
            el('p', {
              class: 'meta',
              textContent: `${winner.source === 'task_model' ? 'Task model chose it' : 'Deterministic score chose it'} — ${winner.reason}`,
            }),
          ])
        : null,
      preview.plan && preview.plan.status !== 'ready'
        ? el('p', {
            class: 'settings-warning',
            textContent: preview.plan.block?.message || 'No preset could serve this request.',
          })
        : null,
      ...(preview.plan?.explanation?.warnings ?? []).map((warning) =>
        el('p', { class: 'settings-warning', textContent: warning }),
      ),
    ]);
  }

  private async run(): Promise<void> {
    this.appState.mediaCatalogBusy = true;
    this.appState.settingsError = '';
    this.renderApp();
    try {
      this.appState.routingPreview = await this.client.previewMediaRouting(this.text);
    } catch (error) {
      this.appState.settingsError = errorMessage(error, 'Unable to test routing.');
    } finally {
      this.appState.mediaCatalogBusy = false;
      this.renderApp();
    }
  }
}
