import { afterEach, describe, expect, it, vi } from 'vitest';

import type { ApiClient } from '../src/api';
import { describeVoice, VoicePicker } from '../src/voice_picker';

const KOKORO = ['bf_emma', 'af_heart', 'jf_alpha', 'am_adam', 'zm_yunxi'];

class FakeAudio {
  static played: string[] = [];
  paused = false;
  constructor(readonly src: string) {}
  play() {
    FakeAudio.played.push(this.src);
    return Promise.resolve();
  }
  pause() {
    this.paused = true;
  }
  addEventListener() {}
}

function setup(voices: string[] | Error = KOKORO) {
  const client = {
    voices: vi.fn(async () => {
      if (voices instanceof Error) throw voices;
      return { voices };
    }),
    voiceSample: vi.fn(async () => new Blob(['audio'], { type: 'audio/mpeg' })),
  } as unknown as ApiClient;
  const render = vi.fn();
  const picker = new VoicePicker(client, render);
  const field = (value = 'af_heart', extra = {}) =>
    picker.field({ label: 'Voice', value, speed: '1.1', baseUrl: 'http://kokoro.lan:8880', change: vi.fn(), testId: 'voice', ...extra });
  return { client, render, picker, field };
}

async function settled(render: ReturnType<typeof vi.fn>) {
  await vi.runAllTimersAsync();
  await vi.waitFor(() => expect(render).toHaveBeenCalled());
}

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  FakeAudio.played = [];
});

describe('naming a voice', () => {
  it('writes out what the code means', () => {
    expect(describeVoice('af_heart')).toEqual({ group: 'American English', label: 'Heart · female' });
    expect(describeVoice('bm_george')).toEqual({ group: 'British English', label: 'George · male' });
    // A blend, or a name from some other service, keeps its own name.
    expect(describeVoice('af_bella(2)+af_heart(1)')).toEqual({ group: 'Other voices', label: 'af_bella(2)+af_heart(1)' });
  });
});

describe('choosing a voice', () => {
  it('lists what the service has, English first, with the current one chosen', async () => {
    vi.useFakeTimers();
    const { render, field } = setup();
    field();
    await settled(render);

    const select = field().querySelector('select')!;
    expect([...select.querySelectorAll('optgroup')].map((group) => group.label))
      .toEqual(['American English', 'British English', 'Japanese', 'Mandarin Chinese']);
    expect(select.value).toBe('af_heart');
    expect(select.selectedOptions[0]?.textContent).toBe('Heart · female');
  });

  it('keeps a voice the service no longer lists, rather than quietly changing it', async () => {
    vi.useFakeTimers();
    const { render, field } = setup();
    field('af_bella(2)+af_heart(1)');
    await settled(render);
    expect(field('af_bella(2)+af_heart(1)').querySelector('select')!.value).toBe('af_bella(2)+af_heart(1)');
  });

  it('falls back to typing a name when the service cannot list them', async () => {
    vi.useFakeTimers();
    const { render, field } = setup(new Error('unreachable'));
    field();
    await settled(render);
    const node = field();
    expect(node.querySelector('select')).toBeNull();
    expect(node.querySelector<HTMLInputElement>('input')!.value).toBe('af_heart');
  });

  it('offers a persona the usual voice as its first choice', async () => {
    vi.useFakeTimers();
    const { render, field } = setup();
    field('');
    await settled(render);
    const select = field('', { inherit: { label: 'Same as Spoken replies', voice: 'bf_emma' } }).querySelector('select')!;
    expect(select.value).toBe('');
    expect(select.options[0]?.textContent).toBe('Same as Spoken replies');
  });
});

describe('hearing a voice', () => {
  it('plays the sample in the chosen voice, at the chosen speed', async () => {
    vi.stubGlobal('Audio', FakeAudio);
    vi.stubGlobal('URL', { ...URL, createObjectURL: () => 'blob:sample', revokeObjectURL: vi.fn() });
    const { client, field } = setup();
    field().querySelector<HTMLButtonElement>('[data-testid="voice-sample"]')!.click();

    await vi.waitFor(() => expect(FakeAudio.played).toEqual(['blob:sample']));
    expect(client.voiceSample).toHaveBeenCalledWith('af_heart', '1.1');
    // While it plays, the same button stops it.
    expect(field().querySelector('[data-testid="voice-sample"]')!.getAttribute('aria-label')).toBe('Stop the sample');
  });

  it('plays the usual voice when a persona has none of its own', async () => {
    vi.stubGlobal('Audio', FakeAudio);
    vi.stubGlobal('URL', { ...URL, createObjectURL: () => 'blob:sample', revokeObjectURL: vi.fn() });
    const { client, field } = setup();
    field('', { inherit: { label: 'Same', voice: 'bf_emma' } }).querySelector<HTMLButtonElement>('[data-testid="voice-sample"]')!.click();
    await vi.waitFor(() => expect(client.voiceSample).toHaveBeenCalledWith('bf_emma', '1.1'));
  });

  it('says so when the sample cannot be made', async () => {
    const { client, field } = setup();
    vi.mocked(client.voiceSample).mockRejectedValueOnce(new Error('The local speech service is unavailable.'));
    field().querySelector<HTMLButtonElement>('[data-testid="voice-sample"]')!.click();
    await vi.waitFor(() => expect(field().querySelector('.voice-problem')?.textContent).toBe('The local speech service is unavailable.'));
  });
});
