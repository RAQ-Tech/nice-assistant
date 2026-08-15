import { describe, expect, it, vi } from 'vitest';

import type { ApiClient } from '../src/api';
import { RoutingTesterView } from '../src/routing_tester_view';
import { createState } from '../src/state';
import type { RoutingPreview } from '../src/types';

function preview(overrides: Partial<RoutingPreview> = {}): RoutingPreview {
  return {
    message: 'Send me a picture of my nails',
    shortlist: [
      { reference: 'preset_1', title: 'Everyday portrait', routing_card: '' },
      { reference: 'preset_2', title: 'Hand detail', routing_card: 'Use when hands or nails are the point.' },
    ],
    requested: true,
    task_model: { ran: true, error: '', chose: 'preset_2' },
    plan: {
      explanation: {
        summary: 'Selected the Hand detail preset.',
        preset: {
          id: 'p2',
          name: 'Hand detail',
          revision: 1,
          priority: 1,
          routing_card: 'Use when hands or nails are the point.',
          source: 'task_model',
          reason: 'chosen by the task model from the offered shortlist',
          considered: [],
        },
        selected: [],
        warnings: [],
        rejected: [],
      },
      status: 'ready',
    },
    ...overrides,
  } as RoutingPreview;
}

describe('routing tester', () => {
  it('reports the shortlist, the winner, and who chose it', async () => {
    const appState = createState();
    const previewMediaRouting = vi.fn().mockResolvedValue(preview());
    const view = new RoutingTesterView(appState, { previewMediaRouting } as unknown as ApiClient, () => undefined);

    appState.routingPreview = preview();
    const node = view.node();
    const text = node.textContent ?? '';

    expect(text).toContain('Hand detail');
    expect(text).toContain('Use when hands or nails are the point.');
    expect(text).toContain('Chosen: Hand detail');
    expect(text).toContain('Task model chose it');
  });

  it('says plainly when a preset has no routing card to be chosen by', () => {
    const appState = createState();
    appState.routingPreview = preview();
    const view = new RoutingTesterView(appState, {} as ApiClient, () => undefined);

    expect(view.node().textContent).toContain('No routing card');
  });

  it('surfaces a task model failure rather than reading as no image wanted', () => {
    const appState = createState();
    appState.routingPreview = preview({
      requested: false,
      plan: null,
      task_model: { ran: false, error: 'The task model did not answer, so its configured fallback was used.', chose: '' },
    });
    const view = new RoutingTesterView(appState, {} as ApiClient, () => undefined);
    const text = view.node().textContent ?? '';

    expect(text).toContain('configured fallback was used');
    expect(text).not.toContain('No image would be requested');
  });

  it('is labeled as a diagnostic that is expected to be removed', () => {
    const appState = createState();
    const view = new RoutingTesterView(appState, {} as ApiClient, () => undefined);
    expect(view.node().textContent).toContain('expected to be removed');
  });
});
