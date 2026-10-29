import { describe, expect, it, vi } from 'vitest';

import type { ApiClient } from '../src/api';
import { LEVEL_SAMPLE_MS, RecordingController } from '../src/recording';
import { ClientStateMachine, createState } from '../src/state';
import { levelsForRoom } from '../src/room_levels';
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

describe('levels for the room', () => {
  it('leaves a quiet room judged exactly as before', () => {
    expect(levelsForRoom([0.002, 0.004, 0.003])).toEqual({
      speechLevel: TURN_DETECTION_DEFAULTS.speechLevel,
      silenceLevel: TURN_DETECTION_DEFAULTS.silenceLevel,
    });
  });

  it('lifts both levels above a noisy room, keeping speech above silence', () => {
    const levels = levelsForRoom([0.03, 0.031, 0.03]);
    expect(levels.silenceLevel).toBeGreaterThan(0.03);
    expect(levels.speechLevel).toBeGreaterThan(levels.silenceLevel);
  });

  it('never sets the bar above ordinary speech, even measured mid-sentence', () => {
    const levels = levelsForRoom([0.3, 0.25, 0.28]);
    expect(levels.speechLevel).toBeLessThan(0.15);
    expect(levels.silenceLevel).toBeLessThan(levels.speechLevel);
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

  it('ends a turn in a noisy room instead of running to the ceiling', async () => {
    vi.useFakeTimers();
    try {
      // A fan sits above the default silence level, so with fixed levels the
      // room itself never sounded like silence and the turn never ended.
      const ROOM = 0.03;
      const VOICE = 0.18;
      const { controller, tick } = recordingSetup([ROOM, ROOM, ROOM, ROOM, ROOM, VOICE, VOICE, VOICE, ROOM]);
      await controller.start(true);
      for (let step = 0; step < 25; step += 1) {
        tick(LEVEL_SAMPLE_MS);
        await vi.advanceTimersByTimeAsync(LEVEL_SAMPLE_MS);
      }
      expect(controller.recording).toBe(false);
    } finally {
      vi.useRealTimers();
      vi.unstubAllGlobals();
    }
  });

  it('counts what was said while the room was being measured', async () => {
    vi.useFakeTimers();
    try {
      // A quick "yes" straight after the tap, then quiet.
      const { controller, tick } = recordingSetup([QUIET, LOUD, LOUD, QUIET]);
      await controller.start(true);
      for (let step = 0; step < 20; step += 1) {
        tick(LEVEL_SAMPLE_MS);
        await vi.advanceTimersByTimeAsync(LEVEL_SAMPLE_MS);
      }
      expect(controller.recording).toBe(false);
    } finally {
      vi.useRealTimers();
      vi.unstubAllGlobals();
    }
  });

  it('stops quietly when a reopened microphone hears nobody', async () => {
    vi.useFakeTimers();
    try {
      const { appState, client, controller, tick } = recordingSetup([QUIET]);
      const nothing = vi.fn();
      controller.configure(() => undefined, async () => undefined, { onNothing: nothing });
      await controller.start(true, { idleMs: 2000 });
      for (let step = 0; step < 25; step += 1) {
        tick(LEVEL_SAMPLE_MS);
        await vi.advanceTimersByTimeAsync(LEVEL_SAMPLE_MS);
      }
      expect(controller.recording).toBe(false);
      expect(nothing).toHaveBeenCalledWith('silence');
      // Nobody answering is not an error, and there is nothing to transcribe.
      expect(appState.uiError).toBe('');
      expect(appState.phase).toBe('idle');
      expect(client.transcribe).not.toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
      vi.unstubAllGlobals();
    }
  });

  it('opens the microphone once however quickly it is asked twice', async () => {
    try {
      const { controller } = recordingSetup([QUIET]);
      await Promise.all([controller.start(true), controller.start(true)]);
      expect(navigator.mediaDevices.getUserMedia).toHaveBeenCalledTimes(1);
      controller.cancel();
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it('asks for an echo-cancelled microphone', async () => {
    try {
      const { controller } = recordingSetup([QUIET]);
      await controller.start();
      expect(navigator.mediaDevices.getUserMedia).toHaveBeenCalledWith({
        audio: expect.objectContaining({ echoCancellation: true, noiseSuppression: true }),
      });
      controller.cancel();
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it('honours a held button let go before the microphone opened', async () => {
    try {
      const { controller } = recordingSetup([QUIET]);
      const starting = controller.start();
      await controller.stop();
      await starting;
      // Otherwise a quick tap left the microphone recording with nothing to stop it.
      expect(controller.recording).toBe(false);
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it('honours a brush that was discarded before the microphone opened', async () => {
    try {
      const { controller, appState } = recordingSetup([QUIET]);
      const starting = controller.start();
      controller.cancel();
      await starting;
      // The cancel used to be dropped, and the microphone opened and kept recording.
      expect(controller.recording).toBe(false);
      expect(appState.phase).not.toBe('recording');
    } finally {
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
