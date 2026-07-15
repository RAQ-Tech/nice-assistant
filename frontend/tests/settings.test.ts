import { describe, expect, it } from 'vitest';

import { modelSettings, normalizeSettings, settingsWire } from '../src/settings';

describe('typed settings', () => {
  it('normalizes the canonical settings envelope and preserves typed preferences', () => {
    const settings = normalizeSettings({
      global_default_model: 'llama3.2',
      default_memory_mode: 'auto',
      stt_provider: 'openai',
      tts_provider: 'local',
      tts_format: 'wav',
      openai_api_key: '••••abcd',
      onboarding_done: true,
      preferences: {
        image_quality: 'hd',
        video_model: 'not-real',
        general_show_viz: true,
        image_prompt_generation: true,
      },
    });
    expect(settings.default_memory_mode).toBe('saved');
    expect(settings.image_quality).toBe('high');
    expect(settings.video_model).toBe('sora-2');
    expect(settings.general_show_viz).toBe(true);
    expect(settingsWire(settings).preferences.general_show_viz).toBe(true);
    expect(settingsWire(settings).preferences).not.toHaveProperty('image_prompt_generation');
  });

  it('uses per-model generation overrides without losing safe defaults', () => {
    const settings = normalizeSettings({
      global_default_model: null,
      default_memory_mode: 'saved',
      stt_provider: 'disabled',
      tts_provider: 'disabled',
      tts_format: 'wav',
      openai_api_key: null,
      onboarding_done: true,
      preferences: { model_overrides: { demo: { temperature: '1.1', context_window_tokens: '8192' } } },
    });
    expect(modelSettings(settings, 'demo')).toMatchObject({ temperature: 1.1, context_window_tokens: 8192 });
    expect(modelSettings(settings, 'demo').num_predict).toBe(512);
  });

  it.each([
    ['local/comfyui', 'comfyui'],
    ['local/automatic1111', 'automatic1111'],
  ])('restores legacy local provider alias %s', (provider, backend) => {
    const settings = normalizeSettings({
      global_default_model: null,
      default_memory_mode: 'saved',
      stt_provider: 'disabled',
      tts_provider: 'disabled',
      tts_format: 'wav',
      openai_api_key: null,
      onboarding_done: true,
      preferences: { image_provider: provider },
    });

    expect(settings.image_provider).toBe('local');
    expect(settings.image_local_backend).toBe(backend);
    expect(settingsWire(settings).preferences.image_provider).toBe('local');
    expect(settingsWire(settings).preferences.image_local_backend).toBe(backend);
  });
});
