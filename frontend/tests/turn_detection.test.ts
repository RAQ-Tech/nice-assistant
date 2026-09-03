import { describe, expect, it, vi } from 'vitest';

import type { ApiClient } from '../src/api';
import { LEVEL_SAMPLE_MS, RecordingController } from '../src/recording';
import { ClientStateMachine, createState } from '../src/state';
import { EndOfTurnDetector, TURN_DETECTION_DEFAULTS } from '../src/turn_detection';
import type { LevelMeter } from '../src/turn_detection';
import type { Settings } from '../src/types';

const LOUD = 0.2;
const QUIET = 0.001;
const BETWEEN = (TURN_DETECTION_DEFAULTS.speechLevel + TURN_DETECTION_DEFAULTS.silenceLevel) / 2;

describe('EndOfTurnDetector', () => {
  it('never ends a turn on silence nobody has spoken into', () => {
    const detector = new EndOfTurnDetector();
    detector.begin(0);

    // Somebody tapped and then paused to think. They have not finished; they
    // have not started.
    for (let at = 0; at < 10_000; at += 100) {
      expect(detector.observe(QUIET, at)).toBe('waiting');
    }
    expect(detector.heard).toBe(false);
  });

  it('ends the turn once speech is followed by a long enough pause', () => {
    const detector = new EndOfTurnDetector();
    detector.begin(0);
    expect(detector.observe(LOUD, 100)).toBe('speaking');

    expect(detector.observe(QUIET, 200)).toBe('speaking');
    expect(detector.observe(QUIET, 200 + TURN_DETECTION_DEFAULTS.silenceMs - 1)).toBe('speaking');
    expect(detector.observe(QUIET, 200 + TURN_DETECTION_DEFAULTS.silenceMs)).toBe('ended');
  });

  it('survives the pause inside a sentence', () => {
    const detector = new EndOfTurnDetector();
    detector.begin(0);
    detector.observe(LOUD, 0);
    // A breath, then more words.
    expect(detector.observe(QUIET, 300)).toBe('speaking');
    expect(detector.observe(LOUD, 600)).toBe('speaking');
    // The pause timer restarted, so the earlier quiet does not count towards it.
    expect(detector.observe(QUIET, 700)).toBe('speaking');
    expect(detector.observe(QUIET, 700 + TURN_DETECTION_DEFAULTS.silenceMs - 1)).toBe('speaking');
  });

  it('does not let a level hovering at the threshold end a sentence', () => {
    const detector = new EndOfTurnDetector();
    detector.begin(0);
    detector.observe(LOUD, 0);

    // Between the two thresholds: neither speech nor silence, so it starts no
    // pause timer. Without the gap between them this would flap.
    for (let at = 100; at < 100 + TURN_DETECTION_DEFAULTS.silenceMs * 3; at += 100) {
      expect(detector.observe(BETWEEN, at)).toBe('speaking');
    }
  });

  it('still ends a turn when the voice trails off through the middle band', () => {
    const detector = new EndOfTurnDetector();
    detector.begin(0);
    detector.observe(LOUD, 0);
    detector.observe(QUIET, 100);
    // Fading rather than cutting out: the pause already started, and drifting
    // back up into the ambiguous band does not cancel it.
    expect(detector.observe(BETWEEN, 100 + TURN_DETECTION_DEFAULTS.silenceMs)).toBe('ended');
  });

  it('gives up rather than holding the microphone open forever', () => {
    const detector = new EndOfTurnDetector({ maxRecordingMs: 1000 });
    detector.begin(0);
    expect(detector.observe(LOUD, 900)).toBe('speaking');
    // A stop nobody asked for beats a microphone that never closes.
    expect(detector.observe(LOUD, 1000)).toBe('too_long');
  });
});

class FakeMeter implements LevelMeter {
  closed = false;
  constructor(private readonly levels: number[]) {}

  level(): number {
    return this.levels.length > 1 ? (this.levels.shift() as number) : (this.levels[0] ?? 0);
  }

  close(): void {
    this.closed = true;
  }
}

function recordingSetup(levels: number[], handsFreeSupported = true) {
  const appState = createState();
  appState.phase = 'idle';
  appState.settings = { stt_provider: 'openai', stt_hands_free: true } as Settings;
  const track = { stop: vi.fn() };
  const stream = { getTracks: () => [track] } as unknown as MediaStream;
  vi.stubGlobal('navigator', { mediaDevices: { getUserMedia: vi.fn().mockResolvedValue(stream) } });

  class FakeRecorder {
    static isTypeSupported = () => false;
    state = 'inactive';
    mimeType = 'audio/webm';
    private handlers: Record<string, ((event: unknown) => void)[]> = {};
    addEventListener(name: string, handler: (event: unknown) => void) {
      (this.handlers[name] ??= []).push(handler);
    }
    start() { this.state = 'recording'; }
    stop() {
      this.state = 'inactive';
      // A recorder that stops always has something to hand over.
      for (const handler of this.handlers.dataavailable ?? []) handler({ data: new Blob(['x']) });
      for (const handler of this.handlers.stop ?? []) handler({});
    }
  }
  vi.stubGlobal('MediaRecorder', FakeRecorder);

  const client = { transcribe: vi.fn().mockResolvedValue({ text: '' }) } as unknown as ApiClient;
  const meter = new FakeMeter(levels);
  let clock = 0;
  const controller = new RecordingController(
    appState,
    new ClientStateMachine(appState),
    client,
    () => (handsFreeSupported ? meter : { level: () => 0, close: () => undefined }),
    () => clock,
  );
  controller.configure(() => undefined, async () => undefined);
  return { appState, client, controller, meter, tick: (ms: number) => { clock += ms; } };
}

describe('Hands-free recording', () => {
  it('stops itself once the speaker has finished', async () => {
    vi.useFakeTimers();
    try {
      const { controller, meter, tick } = recordingSetup([LOUD, LOUD, QUIET]);
      await controller.start(true);
      expect(controller.recording).toBe(true);

      for (let step = 0; step < 20; step += 1) {
        tick(LEVEL_SAMPLE_MS);
        await vi.advanceTimersByTimeAsync(LEVEL_SAMPLE_MS);
      }

      expect(controller.recording).toBe(false);
      // The level meter is released with the microphone, not left running.
      expect(meter.closed).toBe(true);
    } finally {
      vi.useRealTimers();
      vi.unstubAllGlobals();
    }
  });

  it('waits as long as the chosen sending pause before it sends', async () => {
    vi.useFakeTimers();
    try {
      const { appState, controller, tick } = recordingSetup([LOUD, LOUD, QUIET]);
      appState.settings = { ...appState.settings, stt_send_pause_ms: 2500 } as Settings;
      await controller.start(true);

      for (let step = 0; step < 20; step += 1) {
        tick(LEVEL_SAMPLE_MS);
        await vi.advanceTimersByTimeAsync(LEVEL_SAMPLE_MS);
      }
      // Well past the default pause, short of the chosen one: still theirs.
      expect(controller.recording).toBe(true);
      for (let step = 0; step < 10; step += 1) {
        tick(LEVEL_SAMPLE_MS);
        await vi.advanceTimersByTimeAsync(LEVEL_SAMPLE_MS);
      }
      expect(controller.recording).toBe(false);
    } finally {
      vi.useRealTimers();
      vi.unstubAllGlobals();
    }
  });

  it('does not transcribe the silence that ended the turn when transcribing at pauses', async () => {
    vi.useFakeTimers();
    try {
      const { appState, client, controller, tick } = recordingSetup([LOUD, LOUD, QUIET]);
      appState.settings = { ...appState.settings, stt_streaming: true, stt_send_pause_ms: 1500 } as Settings;
      await controller.start(true);

      for (let step = 0; step < 30; step += 1) {
        tick(LEVEL_SAMPLE_MS);
        await vi.advanceTimersByTimeAsync(LEVEL_SAMPLE_MS);
      }

      expect(controller.recording).toBe(false);
      // The words went at the pause; the silence after them is not sent to
      // be transcribed, so the wait after the last word did not grow by it.
      expect(client.transcribe).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
      vi.unstubAllGlobals();
    }
  });

  it('keeps listening through silence when nothing has been said', async () => {
    vi.useFakeTimers();
    try {
      const { controller, tick } = recordingSetup([QUIET]);
      await controller.start(true);

      for (let step = 0; step < 30; step += 1) {
        tick(LEVEL_SAMPLE_MS);
        await vi.advanceTimersByTimeAsync(LEVEL_SAMPLE_MS);
      }

      // Ending here would be the product deciding somebody had spoken when
      // they had not.
      expect(controller.recording).toBe(true);
      controller.cancel();
    } finally {
      vi.useRealTimers();
      vi.unstubAllGlobals();
    }
  });

  it('never watches the level when the button is being held', async () => {
    vi.useFakeTimers();
    try {
      const { controller, meter, tick } = recordingSetup([QUIET]);
      await controller.start();

      for (let step = 0; step < 30; step += 1) {
        tick(LEVEL_SAMPLE_MS);
        await vi.advanceTimersByTimeAsync(LEVEL_SAMPLE_MS);
      }

      // Held recording is the dependable one; nothing about it is guessed at.
      expect(controller.recording).toBe(true);
      expect(meter.closed).toBe(false);
      controller.cancel();
    } finally {
      vi.useRealTimers();
      vi.unstubAllGlobals();
    }
  });
});
