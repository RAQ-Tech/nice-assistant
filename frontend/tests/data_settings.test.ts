import { describe, expect, it, vi } from 'vitest';

import type { ApiClient } from '../src/api';
import { normalizeSettings } from '../src/settings';
import { SettingsView, type Dialogs } from '../src/settings_view';
import { createState } from '../src/state';

describe('data settings', () => {
  it('runs and reports the server-side backup restore drill', async () => {
    const appState = createState();
    appState.session = { user_id: 'owner', expires_at: 1, ttl_seconds: 1800, is_admin: true };
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
    appState.settingsSection = 'Data';
    appState.backupItems = [{ name: 'snapshot.zip', size: 123, created_at: 1, include_media: false }];
    const client = {
      verifyBackup: vi.fn().mockResolvedValue({
        ok: true,
        name: 'snapshot.zip',
        database_integrity: 'ok',
        migration_revision: '0013',
        entry_count: 2,
        include_media: false,
      }),
      backupDownloadUrl: vi.fn(),
    } as unknown as ApiClient;
    const dialogs = { prompt: vi.fn(), confirm: vi.fn(), info: vi.fn() } as unknown as Dialogs;
    const view = new SettingsView(vi.fn(), vi.fn(), dialogs, appState, client);

    const verify = [...view.node().querySelectorAll('button')].find((button) => button.textContent === 'Verify restore');
    expect(verify).toBeDefined();
    verify!.click();
    await vi.waitFor(() => expect(client.verifyBackup).toHaveBeenCalledWith('snapshot.zip'));
    expect(dialogs.info).toHaveBeenCalledWith('Backup verified', expect.stringContaining('Database integrity: ok'));
  });
});
