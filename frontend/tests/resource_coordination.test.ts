import { describe, expect, it, vi } from 'vitest';

import type { ApiClient } from '../src/api';
import { normalizeSettings } from '../src/settings';
import { SettingsView, type Dialogs } from '../src/settings_view';
import { createState } from '../src/state';
import type { ResourceCoordinationStatus } from '../src/types';

const dialogs = {
  prompt: vi.fn(),
  confirm: vi.fn(),
  info: vi.fn(),
} as unknown as Dialogs;

function coordination(): ResourceCoordinationStatus {
  return {
    settings: {
      mode: 'observe',
      reserve_vram_mb: 1024,
      max_wait_seconds: 300,
      poll_interval_seconds: 2,
    },
    endpoints: [{
      provider: 'comfyui',
      endpoint_label: 'unraid:8188',
      fingerprint: 'endpoint-fingerprint',
      authorization: { exclusive_control: false, allow_release: false, authorized_at: null },
      capabilities: {
        reports_capacity: true,
        reports_queue: true,
        supports_release: true,
        supports_precise_cancel: false,
      },
      snapshot: {
        status: 'known',
        source: '/system_stats',
        observed_at: 1,
        total_vram_mb: 12288,
        free_vram_mb: 6144,
        queue_depth: 0,
        active_jobs: 0,
        loaded_models: [],
        message: '',
      },
    }],
  };
}

describe('GPU coordination settings', () => {
  it('shows measured capacity and requires explicit exclusive control before release', async () => {
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
    appState.settingsSection = 'GPU Coordination';
    appState.resourceCoordination = coordination();
    const saved = coordination();
    saved.settings.mode = 'managed';
    saved.endpoints[0]!.authorization = {
      exclusive_control: true,
      allow_release: true,
      authorized_at: 2,
    };
    const client = {
      saveResourceCoordination: vi.fn().mockResolvedValue(saved),
      resourceCoordinationEvents: vi.fn().mockResolvedValue({ items: [] }),
    } as unknown as ApiClient;
    const view = new SettingsView(vi.fn(), vi.fn(), dialogs, appState, client);
    const node = view.node();

    expect(node.textContent).toContain('6144 MB free of 12288 MB');
    expect(node.textContent).toContain('reclaiming the media provider after a local image job');
    const toggles = node.querySelectorAll('input[type="checkbox"]');
    expect(toggles).toHaveLength(2);
    (toggles[1] as HTMLInputElement).click();
    expect(appState.resourceCoordination.endpoints[0]!.authorization.allow_release).toBe(false);
    (toggles[0] as HTMLInputElement).checked = true;
    (toggles[0] as HTMLInputElement).dispatchEvent(new Event('change'));
    const rerendered = view.node();
    const enabledToggles = rerendered.querySelectorAll('input[type="checkbox"]');
    (enabledToggles[1] as HTMLInputElement).checked = true;
    (enabledToggles[1] as HTMLInputElement).dispatchEvent(new Event('change'));
    (rerendered.querySelector('[data-testid="resource-coordination-mode"]') as HTMLSelectElement).value = 'managed';
    (rerendered.querySelector('[data-testid="resource-coordination-mode"]') as HTMLSelectElement)
      .dispatchEvent(new Event('change'));
    (rerendered.querySelector('[data-testid="resource-coordination-save"]') as HTMLButtonElement).click();
    await vi.waitFor(() => expect(client.saveResourceCoordination).toHaveBeenCalled());

    expect(client.saveResourceCoordination).toHaveBeenCalledWith(expect.objectContaining({
      settings: expect.objectContaining({ mode: 'managed' }),
      endpoints: [expect.objectContaining({
        authorization: expect.objectContaining({ exclusive_control: true, allow_release: true }),
      })],
    }));
  });
});
