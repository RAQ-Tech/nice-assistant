import { describe, expect, it, vi } from 'vitest';

import type { ApiClient } from '../src/api';
import { normalizeSettings } from '../src/settings';
import { SettingsView, type Dialogs } from '../src/settings_view';
import { createState } from '../src/state';
import type { SettingsSection } from '../src/settings';

/**
 * The rule the redone pages live by: a page says at most one thing out loud,
 * and never through an information icon. Everything else waits on hover.
 */

const dialogs = { prompt: vi.fn(), confirm: vi.fn(), info: vi.fn(), choice: vi.fn() } as unknown as Dialogs;

function fullState() {
  const appState = createState();
  appState.session = { user_id: 'owner', expires_at: 1, ttl_seconds: 1800, is_admin: true };
  appState.settings = normalizeSettings({
    global_default_model: 'demo',
    default_memory_mode: 'saved',
    stt_provider: 'local',
    tts_provider: 'local',
    tts_format: 'wav',
    openai_api_key: null,
    onboarding_done: true,
    preferences: {},
  });
  appState.models = ['demo', 'other'];
  appState.workspaces = [{ id: 'home', name: 'Home', created_at: 1 }, { id: 'work', name: 'Work', created_at: 2 }];
  appState.personas = [{
    id: 'nova', workspace_id: 'home', workspace_ids: ['home'], name: 'Nova', avatar_url: null, allow_image_sends: true,
    system_prompt: '', personality_details: '', traits: {}, default_model: null, voice_preferences: {}, created_at: 1,
  }];
  appState.taskModels = [{
    role: 'title_generation', title: 'Chat titles', description: '', enabled: true, provider: 'ollama', model: null,
    fallback_provider: null, fallback_model: null, max_input_tokens: 512, max_output_tokens: 64, timeout_seconds: 30,
    temperature: 0.1, fallback_policy: 'deterministic', updated_at: 1,
  }];
  appState.memories = [];
  appState.backupItems = [{ name: 'snapshot.zip', size: 123, created_at: 1, include_media: false }];
  appState.resourceCoordination = {
    settings: { mode: 'observe', reserve_vram_mb: 1024, max_wait_seconds: 300, poll_interval_seconds: 2 },
    endpoints: [{
      provider: 'comfyui', endpoint_label: 'box:8188', fingerprint: 'f',
      authorization: { exclusive_control: false, allow_release: false, authorized_at: null },
      capabilities: { reports_capacity: true, reports_queue: true, supports_release: true, supports_precise_cancel: false },
      snapshot: { status: 'known', source: '/system_stats', observed_at: 1, total_vram_mb: 12288, free_vram_mb: 6144, queue_depth: 0, active_jobs: 0, loaded_models: [], message: '' },
    }],
  };
  return appState;
}

const REDONE: readonly { section: SettingsSection; item?: string }[] = [
  { section: 'General' },
  { section: 'Models' },
  { section: 'Models', item: 'demo' },
  { section: 'Memory' },
  { section: 'TTS' },
  { section: 'STT' },
  { section: 'Image Generation' },
  { section: 'Video Generation' },
  { section: 'Personas' },
  { section: 'Personas', item: 'nova' },
  { section: 'Workspaces' },
  { section: 'User' },
  { section: 'Task Models' },
  { section: 'Task Models', item: 'title_generation' },
  { section: 'GPU Coordination' },
  { section: 'Data' },
];

describe('the redone settings pages', () => {
  it.each(REDONE)('$section $item says at most one thing out loud, and never through an icon', ({ section, item }) => {
    const appState = fullState();
    appState.settingsSection = section;
    appState.settingsItem = item ?? null;
    const node = new SettingsView(vi.fn(), vi.fn(), dialogs, appState, {} as ApiClient).node();

    expect(node.querySelectorAll('.info-tip-trigger')).toHaveLength(0);
    expect(node.querySelectorAll('.settings-intro')).toHaveLength(0);
    expect(node.querySelectorAll('.page-hint').length).toBeLessThanOrEqual(1);
  });

  it('opens a thing by its address and offers the way back', () => {
    const appState = fullState();
    appState.settingsSection = 'Personas';
    appState.settingsItem = 'nova';
    const view = new SettingsView(vi.fn(), vi.fn(), dialogs, appState, {} as ApiClient);
    const page = view.node();

    expect(page.querySelector('[data-testid="persona-page"]')).not.toBeNull();
    // Two workspaces, so the persona can say where it is available.
    expect(page.textContent).toContain('Available in');
    (page.querySelector('[data-testid="persona-page-back"]') as HTMLButtonElement).click();
    expect(appState.settingsItem).toBeNull();
    expect(view.node().querySelector('[data-testid="persona-list"]')).not.toBeNull();
  });

  it('keeps the arrows honest at either end of a list', () => {
    const appState = fullState();
    appState.settingsSection = 'Models';
    appState.settingsItem = 'demo';
    const view = new SettingsView(vi.fn(), vi.fn(), dialogs, appState, {} as ApiClient);
    const first = view.node();
    expect((first.querySelector('[data-testid="model-settings-page-previous"]') as HTMLButtonElement).disabled).toBe(true);
    (first.querySelector('[data-testid="model-settings-page-next"]') as HTMLButtonElement).click();
    expect(appState.settingsItem).toBe('other');
    const last = view.node();
    expect((last.querySelector('[data-testid="model-settings-page-next"]') as HTMLButtonElement).disabled).toBe(true);
  });

  it('says plainly when the address points at nothing', () => {
    const appState = fullState();
    appState.settingsSection = 'Personas';
    appState.settingsItem = 'gone';
    const node = new SettingsView(vi.fn(), vi.fn(), dialogs, appState, {} as ApiClient).node();
    expect(node.textContent).toContain('no longer here');
  });
});
