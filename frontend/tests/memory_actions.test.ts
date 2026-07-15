import { describe, expect, it, vi } from 'vitest';

import type { ApiClient } from '../src/api';
import { normalizeSettings } from '../src/settings';
import { SettingsView, type Dialogs } from '../src/settings_view';
import { createState } from '../src/state';
import type { Memory } from '../src/types';

function memory(id: string, status: Memory['status']): Memory {
  return {
    id,
    scope: 'global',
    scope_id: null,
    content: `Memory ${id}`,
    status,
    confidence: null,
    source_type: 'manual',
    source_message_id: null,
    source_turn_id: null,
    extractor_provider: null,
    extractor_model: null,
    extractor_version: null,
    supersedes_id: null,
    created_at: 1,
    updated_at: 1,
    reviewed_at: 1,
    forgotten_at: status === 'forgotten' ? 1 : null,
    can_undo: status === 'forgotten',
  };
}

describe('memory actions', () => {
  it('selects all memories and permanently deletes them only after confirmation', async () => {
    const appState = createState();
    appState.settings = normalizeSettings({
      global_default_model: null,
      default_memory_mode: 'saved',
      stt_provider: 'disabled',
      tts_provider: 'disabled',
      tts_format: 'wav',
      openai_api_key: null,
      onboarding_done: true,
      preferences: {},
    });
    appState.settingsSection = 'Memory';
    appState.memories = [memory('one', 'active'), memory('two', 'forgotten')];
    const client = {
      bulkMemoryAction: vi.fn().mockResolvedValue({ action: 'delete', requested_count: 2, affected_count: 2, ids: ['one', 'two'] }),
      memories: vi.fn().mockResolvedValue({ items: [] }),
    } as unknown as ApiClient;
    const dialogs = {
      prompt: vi.fn(),
      confirm: vi.fn().mockResolvedValue(true),
      info: vi.fn(),
    } as unknown as Dialogs;
    const view = new SettingsView(vi.fn(), vi.fn(), dialogs, appState, client);

    const first = view.node();
    [...first.querySelectorAll('button')].find((button) => button.textContent === 'Select all')!.click();
    const selected = view.node();
    const remove = selected.querySelector('[data-testid="memory-bulk-delete"]') as HTMLButtonElement;
    expect(remove.textContent).toContain('(2)');
    remove.click();

    await vi.waitFor(() => expect(client.bulkMemoryAction).toHaveBeenCalledWith('delete', ['one', 'two']));
    expect(dialogs.confirm).toHaveBeenCalledWith(
      'Permanently delete memories',
      expect.stringContaining('cannot be undone'),
      'Delete permanently',
    );
    expect(appState.memories).toEqual([]);
  });
});
