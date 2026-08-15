import { describe, expect, it, vi } from 'vitest';

import type { ApiClient } from '../src/api';
import { PictureLibraryView } from '../src/picture_library_view';
import { createState } from '../src/state';
import type { LibraryEntry } from '../src/types';

function entry(overrides: Partial<LibraryEntry> = {}): LibraryEntry {
  return {
    id: 'l1',
    persona_id: 'p1',
    media_id: 'm1',
    content_url: '/api/v1/media/m1',
    scene: { subject: 'avery with dark hair', action: 'walking a small dog', setting: 'a park' },
    state: 'ready',
    served_count: 0,
    created_at: 1,
    last_served_at: null,
    ...overrides,
  };
}

function view(items: LibraryEntry[], overrides: Partial<ApiClient> = {}) {
  const appState = createState();
  const client = {
    libraryEntries: vi.fn().mockResolvedValue({ items }),
    deleteLibraryEntry: vi.fn().mockResolvedValue(undefined),
    ...overrides,
  } as unknown as ApiClient;
  return { appState, client, instance: new PictureLibraryView(appState, client, () => undefined) };
}

describe('kept pictures', () => {
  it('shows the description a picture will be matched against', async () => {
    const { instance } = view([entry()]);
    await instance.refresh('p1');
    const text = instance.node('p1').textContent ?? '';

    expect(text).toContain('avery with dark hair, walking a small dog, a park');
    expect(text).toContain('Ready to send');
  });

  it('explains a retired picture without implying it was deleted', async () => {
    const { instance } = view([entry({ state: 'retired' })]);
    await instance.refresh('p1');
    const text = instance.node('p1').textContent ?? '';

    expect(text).toContain('past the keep limit');
    expect(text).toContain('picture itself is untouched');
  });

  it('forgets an entry without claiming to remove the picture', async () => {
    const { instance, client } = view([entry()]);
    await instance.refresh('p1');
    const forget = instance.node('p1').querySelector('[data-testid="library-forget-l1"]') as HTMLButtonElement;

    expect(forget.title).toContain('picture itself stays');
    forget.click();
    await vi.waitFor(() => expect(client.deleteLibraryEntry).toHaveBeenCalledWith('l1'));
  });

  it('says nothing is kept rather than showing an empty list', async () => {
    const { instance } = view([]);
    await instance.refresh('p1');
    expect(instance.node('p1').textContent).toContain('Nothing kept yet');
  });
});
