import { el } from './dom';
import type { MediaJournal, MediaJournalStage } from './types';

/**
 * The generation log overlay.
 *
 * Reached in one click from a picture in the conversation, deliberately not
 * from settings: the question it answers ("why did this one come out like
 * that?") is asked while looking at the image.
 */

function duration(milliseconds: number | null | undefined): string {
  if (milliseconds === null || milliseconds === undefined) return 'not measured';
  if (milliseconds < 1000) return `${Math.round(milliseconds)} ms`;
  return `${(milliseconds / 1000).toFixed(2)} s`;
}

function stamp(seconds: number | null | undefined): string {
  if (!seconds) return 'unknown';
  return new Date(seconds * 1000).toLocaleString();
}

function stageNode(stage: MediaJournalStage): HTMLElement {
  const detail = stage.detail ?? {};
  const hasDetail = Object.keys(detail).length > 0;
  return el('li', { class: `generation-log-stage stage-${stage.status}` }, [
    el('div', { class: 'generation-log-stage-head' }, [
      el('span', { class: 'generation-log-stage-name', textContent: stage.stage }),
      el('span', { class: 'generation-log-stage-status meta', textContent: stage.status }),
      el('span', { class: 'generation-log-stage-duration meta', textContent: duration(stage.duration_ms) }),
    ]),
    stage.summary ? el('p', { class: 'generation-log-stage-summary', textContent: stage.summary }) : null,
    hasDetail
      ? el('details', { class: 'generation-log-detail' }, [
          el('summary', { textContent: 'Detail' }),
          el('pre', { textContent: JSON.stringify(detail, null, 2) }),
        ])
      : null,
  ]);
}

export function generationLogOverlay(journal: MediaJournal, exportUrl: string, close: () => void): HTMLElement {
  return el(
    'div',
    {
      class: 'modal-backdrop generation-log-backdrop',
      'data-testid': 'generation-log',
      onclick: close,
    },
    [
      el(
        'div',
        {
          class: 'modal-card glass generation-log-card',
          role: 'dialog',
          'aria-modal': 'true',
          'aria-label': 'Generation log',
          onclick: (event: Event) => event.stopPropagation(),
        },
        [
          el('button', {
            class: 'icon-btn generation-log-close',
            textContent: '✕',
            title: 'Close log',
            ariaLabel: 'Close log',
            onclick: close,
          }),
          el('h3', { textContent: 'Generation log' }),
          el('p', { class: 'meta', textContent: `${journal.kind} · ${journal.origin} · ${journal.status}` }),
          el('dl', { class: 'generation-log-summary' }, [
            el('dt', { textContent: 'Started' }),
            el('dd', { textContent: stamp(journal.started_at) }),
            el('dt', { textContent: 'Took' }),
            el('dd', { textContent: duration(journal.duration_ms) }),
            el('dt', { textContent: 'Stages' }),
            el('dd', { textContent: String(journal.stages.length) }),
          ]),
          journal.error
            ? el('p', {
                class: 'generation-log-error',
                textContent: `${journal.error.code}: ${journal.error.message}`,
              })
            : null,
          el('ol', { class: 'generation-log-stages' }, journal.stages.map(stageNode)),
          el('p', { class: 'meta generation-log-privacy' }, [
            'Credentials, provider addresses, and server paths are removed from this log, so it is safe to share with the image.',
          ]),
          el('a', {
            class: 'pill-btn generation-log-download',
            href: exportUrl,
            download: `generation-journal-${journal.id}.md`,
            textContent: 'Download log',
            'data-testid': 'download-generation-log',
          }),
        ],
      ),
    ],
  );
}
