import { describe, expect, it, vi } from 'vitest';

import type { ApiClient } from '../src/api';
import { PictureLibraryView } from '../src/picture_library_view';
import { createState } from '../src/state';
import type { LibraryEntry, VisualIdentityProfile } from '../src/types';

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
    mediaPresets: vi.fn().mockResolvedValue({
      items: [
        { id: 'preset-a', name: 'Everyday portrait' },
        { id: 'preset-b', name: 'Hand detail' },
      ],
    }),
    deleteLibraryEntry: vi.fn().mockResolvedValue(undefined),
    updateVisualIdentity: vi.fn().mockResolvedValue({}),
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

  it('says routing decides on its own until a recipe is preferred', async () => {
    const { instance } = view([entry()]);
    await instance.refresh('p1');
    expect(instance.node('p1').textContent).toContain('routing decides on its own');
  });

  it('records a preferred recipe and can reorder it', async () => {
    const { instance, client } = view([entry()]);
    await instance.refresh('p1');
    const profile = { id: 'v1', preferred_preset_ids: [] } as unknown as VisualIdentityProfile;

    const add = instance
      .node('p1', profile)
      .querySelector('[data-testid="preference-add"]') as HTMLSelectElement;
    add.value = 'preset-b';
    add.dispatchEvent(new Event('change', { bubbles: true }));
    await vi.waitFor(() =>
      expect(client.updateVisualIdentity).toHaveBeenCalledWith(
        'p1',
        expect.objectContaining({ preferred_preset_ids: ['preset-b'] }),
      ),
    );

    const ordered = { id: 'v1', preferred_preset_ids: ['preset-a', 'preset-b'] } as unknown as VisualIdentityProfile;
    const node = instance.node('p1', ordered);
    const moveUp = [...node.querySelectorAll('button')].find((button) => button.textContent === 'Move up');
    (moveUp as HTMLButtonElement).click();
    await vi.waitFor(() =>
      expect(client.updateVisualIdentity).toHaveBeenCalledWith(
        'p1',
        expect.objectContaining({ preferred_preset_ids: ['preset-b', 'preset-a'] }),
      ),
    );
  });

  it('removes a preference without touching the preset itself', async () => {
    const { instance, client } = view([entry()]);
    await instance.refresh('p1');
    const profile = { id: 'v1', preferred_preset_ids: ['preset-a'] } as unknown as VisualIdentityProfile;
    const node = instance.node('p1', profile);
    (node.querySelector('[data-testid="preference-remove-preset-a"]') as HTMLButtonElement).click();
    await vi.waitFor(() =>
      expect(client.updateVisualIdentity).toHaveBeenCalledWith(
        'p1',
        expect.objectContaining({ preferred_preset_ids: [] }),
      ),
    );
  });
});
