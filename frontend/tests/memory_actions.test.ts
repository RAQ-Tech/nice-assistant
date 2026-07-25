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
    access_state: 'grants',
    memory_type: 'durable',
    validity_status: 'current',
    valid_until: null,
    stateful_status: null,
    last_confirmed_at: 1,
    origin: {
      source_kind: 'manual',
      source_chat_id: null,
      source_persona_id: 'persona-1',
      source_workspace_id: null,
      source_message_id: null,
      source_turn_id: null,
      evidence: {},
      provenance_status: 'resolved',
      revision_of_memory_id: null,
      created_at: 1,
    },
    grants: [{
      id: `grant-${id}`,
      grant_type: 'persona',
      target_id: 'persona-1',
      grant_source: 'owner',
      granted_by_human_id: 'human-1',
      granted_at: 1,
    }],
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

  it('renders quarantined legacy memories as read-only while keeping history and delete available', () => {
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
    appState.memorySections.pending = true;
    const legacy = memory('legacy', 'pending');
    legacy.access_state = 'legacy_quarantined';
    legacy.memory_type = 'legacy_unknown';
    legacy.validity_status = 'legacy_unknown';
    legacy.last_confirmed_at = null;
    legacy.origin = {
      ...legacy.origin,
      source_kind: 'legacy',
      provenance_status: 'legacy_unresolved',
    };
    legacy.grants = [];
    appState.memories = [legacy];
    const view = new SettingsView(
      vi.fn(),
      vi.fn(),
      { prompt: vi.fn(), confirm: vi.fn(), info: vi.fn() } as unknown as Dialogs,
      appState,
      {} as ApiClient,
    );

    const node = view.node();
    const row = node.querySelector('[data-testid="memory-legacy"]') as HTMLElement;
    const actions = [...row.querySelectorAll('button')].map((button) => button.textContent);

    expect((row.querySelector('textarea') as HTMLTextAreaElement).disabled).toBe(true);
    expect(row.textContent).toContain('migrated memory · read-only');
    expect(actions).not.toContain('Approve');
    expect(actions).not.toContain('Reject');
    expect(actions).not.toContain('Forget');
    expect(actions).not.toContain('Save edit');
    expect(actions).not.toContain('Undo');
    expect(actions).toContain('History');
    expect(actions).toContain('Delete');
    expect(node.textContent).toContain('approved, active, current, and authorized');
  });
});
