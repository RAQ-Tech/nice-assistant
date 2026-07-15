import { describe, expect, it, vi } from 'vitest';

import type { ApiClient } from '../src/api';
import { normalizeSettings } from '../src/settings';
import { SettingsView, type Dialogs } from '../src/settings_view';
import { createState } from '../src/state';
import type { TaskModelProfile } from '../src/types';

function profile(role: TaskModelProfile['role'] = 'title_generation'): TaskModelProfile {
  return {
    role,
    title: role === 'title_generation' ? 'Chat titles' : 'Capability planning',
    description: 'A separately configured platform task.',
    enabled: true,
    provider: 'ollama',
    model: null,
    fallback_provider: null,
    fallback_model: null,
    max_input_tokens: 512,
    max_output_tokens: 64,
    timeout_seconds: 30,
    temperature: 0.1,
    fallback_policy: role === 'title_generation' ? 'deterministic' : 'skip',
    updated_at: 1,
  };
}

const dialogs = {
  prompt: vi.fn(),
  confirm: vi.fn(),
  info: vi.fn(),
} as unknown as Dialogs;

describe('Task model settings', () => {
  it('renders separate role controls and saves the selected task model', async () => {
    const appState = createState();
    appState.settings = normalizeSettings({
      global_default_model: null,
      default_memory_mode: 'saved',
      stt_provider: 'disabled',
      tts_provider: 'local',
      tts_format: 'wav',
      openai_api_key: null,
      onboarding_done: true,
      preferences: {},
    });
    appState.settingsSection = 'Task Models';
    appState.models = ['persona-model', 'task-model'];
    appState.taskModels = [profile()];
    const updated = { ...profile(), model: 'task-model' };
    const client = {
      updateTaskModel: vi.fn().mockResolvedValue(updated),
      checkTaskModel: vi.fn().mockResolvedValue({
        role: 'title_generation',
        ready: true,
        status: 'ready',
        message: 'Task model is ready.',
        primary_ready: true,
        fallback_ready: false,
        effective_model: 'task-model',
        fallback_effective_model: null,
      }),
    } as unknown as ApiClient;
    const view = new SettingsView(vi.fn(), vi.fn(), dialogs, appState, client);
    const node = view.node();

    expect(node.textContent).toContain('separate from persona behavior');
    expect(node.textContent).toContain('workflows, LoRAs, and identity controls');
    const modelSelect = [...node.querySelectorAll('select')].find((select) =>
      select.parentElement?.textContent?.includes('Primary model'),
    ) as HTMLSelectElement;
    modelSelect.value = 'task-model';
    modelSelect.dispatchEvent(new Event('change'));
    (node.querySelector('[data-testid="task-model-save-title_generation"]') as HTMLButtonElement).click();
    await new Promise((resolve) => window.setTimeout(resolve, 0));

    expect(client.updateTaskModel).toHaveBeenCalledWith(expect.objectContaining({ model: 'task-model' }));
    expect(appState.taskModelChecks.title_generation?.ready).toBe(true);
  });

  it('preserves an unsaved model choice when a late refresh completes', async () => {
    const appState = createState();
    appState.settings = normalizeSettings({
      global_default_model: null,
      default_memory_mode: 'saved',
      stt_provider: 'disabled',
      tts_provider: 'local',
      tts_format: 'wav',
      openai_api_key: null,
      onboarding_done: true,
      preferences: {},
    });
    appState.settingsSection = 'General';
    appState.models = ['task-model'];
    appState.taskModels = [profile()];
    let finishRefresh!: (value: { items: TaskModelProfile[] }) => void;
    const pendingRefresh = new Promise<{ items: TaskModelProfile[] }>((resolve) => {
      finishRefresh = resolve;
    });
    const updated = { ...profile(), model: 'task-model' };
    const client = {
      taskModels: vi.fn().mockReturnValue(pendingRefresh),
      taskModelRuns: vi.fn().mockResolvedValue({ items: [] }),
      updateTaskModel: vi.fn().mockResolvedValue(updated),
      checkTaskModel: vi.fn().mockResolvedValue({
        role: 'title_generation',
        ready: true,
        status: 'ready',
        message: 'Task model is ready.',
        primary_ready: true,
        fallback_ready: false,
        effective_model: 'task-model',
        fallback_effective_model: null,
      }),
    } as unknown as ApiClient;
    const view = new SettingsView(vi.fn(), vi.fn(), dialogs, appState, client);

    (view.node().querySelector('[data-testid="settings-nav-task-models"]') as HTMLButtonElement).click();
    const taskModelNode = view.node();
    const modelSelect = [...taskModelNode.querySelectorAll('select')].find((select) =>
      select.parentElement?.textContent?.includes('Primary model'),
    ) as HTMLSelectElement;
    modelSelect.value = 'task-model';
    modelSelect.dispatchEvent(new Event('change'));
    finishRefresh({ items: [profile()] });
    await new Promise((resolve) => window.setTimeout(resolve, 0));

    expect(appState.taskModels[0]?.model).toBe('task-model');
    (view.node().querySelector('[data-testid="task-model-save-title_generation"]') as HTMLButtonElement).click();
    await vi.waitFor(() => {
      expect(client.updateTaskModel).toHaveBeenCalledWith(expect.objectContaining({ model: 'task-model' }));
    });
  });

  it('shows content-free task run diagnostics without rendering prompt or result fields', () => {
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
    appState.settingsSection = 'Task Models';
    appState.taskModels = [profile('capability_planning')];
    appState.taskModelRuns = [{
      id: 'run-1',
      role: 'capability_planning',
      chat_id: 'chat-1',
      turn_id: 'turn-1',
      requested_provider: 'ollama',
      requested_model: 'task-model',
      executed_provider: 'ollama',
      executed_model: 'task-model',
      status: 'completed',
      fallback_used: false,
      error: null,
      attempts: [],
      input_tokens_estimated: 120,
      output_tokens_estimated: 8,
      latency_ms: 42,
      started_at: 1,
      completed_at: 2,
    }];
    const node = new SettingsView(vi.fn(), vi.fn(), dialogs, appState, {} as ApiClient).node();
    expect(node.textContent).toContain('Prompts and generated task output are not stored');
    expect(node.textContent).toContain('42 ms');
    expect(node.textContent).not.toContain('Show me a portrait');
  });
});
