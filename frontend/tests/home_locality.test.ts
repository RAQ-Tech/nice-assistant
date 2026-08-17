import { describe, expect, it, vi } from 'vitest';

import type { ApiClient } from '../src/api';
import { HomeView } from '../src/home_view';
import { createState } from '../src/state';
import type { DataLocality } from '../src/types';

function locality(overrides: Partial<DataLocality> = {}): DataLocality {
  return {
    everything_local: true,
    parts: [
      { label: 'What you type', provider: 'ollama', locality: 'local', detail: 'The reply itself.' },
      { label: 'What you say', provider: 'disabled', locality: 'off', detail: 'Recordings are transcribed.' },
      { label: 'Pictures', provider: 'local-image', locality: 'local', detail: 'Images are generated.' },
    ],
    ...overrides,
  };
}

function setup(reported: DataLocality | null) {
  const appState = createState();
  const client = {
    mediaJournals: vi.fn().mockResolvedValue({ items: [] }),
    libraryEntries: vi.fn().mockResolvedValue({ items: [] }),
    productionReadiness: vi.fn().mockResolvedValue(null),
    sceneBacklog: vi.fn().mockResolvedValue({ items: [] }),
    dataLocality: reported ? vi.fn().mockResolvedValue(reported) : vi.fn().mockRejectedValue(new Error('nope')),
  } as unknown as ApiClient;
  const root = document.createElement('div');
  let view!: HomeView;
  const render = () => root.replaceChildren(view.node());
  view = new HomeView(appState, client, {
    openSettings: vi.fn(),
    openChat: vi.fn(),
    newChat: vi.fn(),
  } as never, render);
  return { view, root, render };
}

describe('Where this goes', () => {
  it('says every part of a conversation stays here when it does', async () => {
    const { view, root, render } = setup(locality());
    await view.refresh();
    render();

    const card = root.querySelector('[data-testid="home-locality"]');
    expect(card?.textContent).toContain('Everything switched on runs on this machine.');
    expect(card?.textContent).toContain('ollama — on this machine');
    // Off is not local and not cloud, and saying either would be a small lie.
    expect(card?.textContent).toContain('Off');
  });

  it('says plainly when something leaves', async () => {
    const { view, root, render } = setup(locality({
      everything_local: false,
      parts: [
        { label: 'What you say', provider: 'openai', locality: 'cloud', detail: 'Recordings are transcribed.' },
      ],
    }));
    await view.refresh();
    render();

    const card = root.querySelector('[data-testid="home-locality"]');
    expect(card?.textContent).toContain('Some of this is sent to a service on the internet.');
    // The words carry it, not the colour, so it reads the same to somebody who
    // cannot see the colour.
    expect(card?.textContent).toContain('openai — leaves this machine');
    expect(root.querySelector('[data-testid="home-locality-cloud"]')).not.toBeNull();
  });

  it('shows the rest of the page when this one thing cannot be loaded', async () => {
    const { view, root, render } = setup(null);
    await view.refresh();
    render();

    // A summary that cannot be fetched must not take the homepage with it.
    expect(root.querySelector('[data-testid="home-locality"]')).toBeNull();
    expect(root.querySelector('[data-testid="home-now"]')).not.toBeNull();
  });
});
