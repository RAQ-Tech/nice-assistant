/**
 * Deciding that somebody has stopped talking.
 *
 * Holding a button to talk needs none of this: the release is the decision, and
 * it is always right. This is for the hands-free case, where the product has to
 * make that call itself, and where getting it wrong cuts somebody off mid
 * sentence.
 *
 * The rules it works to are deliberately conservative. Silence that was never
 * preceded by speech never ends a turn - somebody who taps and then pauses to
 * think has not finished, and ending there would be the product deciding they
 * had spoken when they had not. The level that counts as speech sits above the
 * level that counts as silence, so a voice hovering near the line cannot make
 * the decision flap. And there is a ceiling, so a detector that never sees
 * silence cannot hold the microphone open forever.
 */

export type TurnState =
  /** Nothing has been heard yet. Keep listening; do not decide anything. */
  | 'waiting'
  /** Somebody is talking. */
  | 'speaking'
  /** They were talking, and have now stopped for long enough to mean it. */
  | 'ended'
  /** This has run long enough that something is wrong with the listening. */
  | 'too_long';

export interface TurnDetectorOptions {
  /** Above this, a level counts as somebody speaking. */
  speechLevel?: number;
  /** Below this, a level counts as silence. Lower than `speechLevel` on purpose. */
  silenceLevel?: number;
  /** How long silence must last, after speech, before the turn is over. */
  silenceMs?: number;
  /** A stop nobody asked for beats a microphone left open indefinitely. */
  maxRecordingMs?: number;
}

export const TURN_DETECTION_DEFAULTS: Required<TurnDetectorOptions> = {
  // Room tone on a quiet machine sits well under 0.01; ordinary speech is an
  // order of magnitude above it. The gap between these two is the hysteresis.
  speechLevel: 0.035,
  silenceLevel: 0.015,
  // Long enough to survive the pause inside a sentence, short enough that the
  // wait after somebody finishes does not feel like being ignored.
  silenceMs: 900,
  maxRecordingMs: 60_000,
};

export class EndOfTurnDetector {
  private readonly options: Required<TurnDetectorOptions>;
  private startedAt = 0;
  private heardSpeech = false;
  private quietSince = 0;

  constructor(options: TurnDetectorOptions = {}) {
    this.options = { ...TURN_DETECTION_DEFAULTS, ...options };
  }

  begin(atMs: number): void {
    this.startedAt = atMs;
    this.heardSpeech = false;
    this.quietSince = 0;
  }

  /** Whether any speech has been heard at all during this turn. */
  get heard(): boolean {
    return this.heardSpeech;
  }

  observe(level: number, atMs: number): TurnState {
    if (atMs - this.startedAt >= this.options.maxRecordingMs) return 'too_long';
    if (level >= this.options.speechLevel) {
      this.heardSpeech = true;
      this.quietSince = 0;
      return 'speaking';
    }
    if (!this.heardSpeech) return 'waiting';
    if (level > this.options.silenceLevel) {
      // Between the two thresholds: neither speech nor silence. It does not
      // start a silence timer, which is what stops a wobble from ending a
      // sentence, and it does not cancel one either - a voice trailing off
      // passes through this band on its way down.
      return this.quietSince && atMs - this.quietSince >= this.options.silenceMs ? 'ended' : 'speaking';
    }
    if (!this.quietSince) this.quietSince = atMs;
    return atMs - this.quietSince >= this.options.silenceMs ? 'ended' : 'speaking';
  }
}

/**
 * Where a long utterance can be cut so its earlier part can be transcribed
 * while its later part is still being spoken.
 *
 * A pause is not an ending. This looks for the gap between sentences - shorter
 * than the silence that ends a turn, long enough that cutting there loses
 * nothing - and reports it once, so a single pause produces one cut rather
 * than one on every sample while the room stays quiet.
 *
 * Deliberately separate from `EndOfTurnDetector`. That one decides whether
 * somebody has finished, which must stay conservative because getting it wrong
 * cuts a person off. This one only decides where to start work early, and
 * getting it wrong costs a little duplicated effort and nothing else.
 */
export class PauseDetector {
  private readonly pauseMs: number;
  private readonly silenceLevel: number;
  private readonly speechLevel: number;
  private heardSpeech = false;
  private quietSince = 0;
  private cutAlready = false;

  constructor(options: { pauseMs?: number; silenceLevel?: number; speechLevel?: number } = {}) {
    // Shorter than the end-of-turn silence on purpose: this has to fire while
    // somebody is still mid-utterance to be worth anything at all.
    this.pauseMs = options.pauseMs ?? 450;
    this.silenceLevel = options.silenceLevel ?? TURN_DETECTION_DEFAULTS.silenceLevel;
    this.speechLevel = options.speechLevel ?? TURN_DETECTION_DEFAULTS.speechLevel;
  }

  begin(): void {
    this.heardSpeech = false;
    this.quietSince = 0;
    this.cutAlready = false;
  }

  /** A cut happened and nobody has spoken since: whatever follows is silence. */
  get cutPending(): boolean {
    return this.cutAlready;
  }

  /** True exactly once per pause, and never before anybody has spoken. */
  observe(level: number, atMs: number): boolean {
    if (level >= this.speechLevel) {
      this.heardSpeech = true;
      this.quietSince = 0;
      this.cutAlready = false;
      return false;
    }
    if (!this.heardSpeech || level > this.silenceLevel) return false;
    if (!this.quietSince) this.quietSince = atMs;
    if (this.cutAlready || atMs - this.quietSince < this.pauseMs) return false;
    this.cutAlready = true;
    return true;
  }
}

/** Something that reports how loud the microphone is, right now. */
export interface LevelMeter {
  level(): number;
  close(): void;
}

/**
 * A level meter over a live microphone stream.
 *
 * The number is the root mean square of the waveform, which is loudness rather
 * than peak: a single click does not read as speech, and a quiet steady voice
 * does not read as silence.
 */
export function createLevelMeter(stream: MediaStream): LevelMeter {
  const AudioContextClass = (globalThis as {
    AudioContext?: typeof AudioContext;
    webkitAudioContext?: typeof AudioContext;
  }).AudioContext ?? (globalThis as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
  if (!AudioContextClass) return { level: () => 0, close: () => undefined };
  const context = new AudioContextClass();
  const analyser = context.createAnalyser();
  analyser.fftSize = 1024;
  const source = context.createMediaStreamSource(stream);
  source.connect(analyser);
  const samples = new Float32Array(analyser.fftSize);
  return {
    level(): number {
      analyser.getFloatTimeDomainData(samples);
      let sum = 0;
      for (const sample of samples) sum += sample * sample;
      return Math.sqrt(sum / samples.length);
    },
    close(): void {
      try {
        source.disconnect();
        void context.close();
      } catch {
        // A context the browser already tore down is not a problem worth
        // reporting to somebody who just finished talking.
      }
    },
  };
}
