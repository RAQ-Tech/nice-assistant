import { CALIBRATION_MS, levelsForRoom, type SpeechLevels } from './room_levels';
import { EndOfTurnDetector, PauseDetector, type LevelMeter, type TurnDetectorOptions } from './turn_detection';

// How often the microphone level is sampled while listening hands-free. Fine
// enough to notice the end of a sentence, coarse enough not to be a busy loop.
export const LEVEL_SAMPLE_MS = 100;

/** What listening hands-free tells the recorder. */
export interface ListenerEvents {
  /** Somebody spoke and has now been quiet long enough: the turn is over. */
  ended(): void;
  /** A turn ran to the one-minute ceiling. */
  tooLong(): void;
  /** The idle limit passed and nobody had spoken. */
  nobodySpoke(): void;
  /** A pause worth transcribing at, while the rest is still being said. */
  pause(): void;
  /** How loud the microphone is right now, from 0 to 1. */
  level(value: number): void;
}

export interface ListenOptions {
  /** Stop quietly if nobody has spoken for this long: nobody answering is not an error. */
  idleMs?: number | undefined;
}

export interface ListenerOptions extends ListenOptions {
  detection: TurnDetectorOptions;
  levels: SpeechLevels;
  sendPauseMs: number;
  cutAtPauses: boolean;
}

/**
 * Watching one hands-free turn's microphone level, and deciding when it ended.
 *
 * The first moment is spent listening to the room, so a noisy one does not
 * read as somebody talking forever; what was said meanwhile still counts once
 * the room is known. The deciding itself is `EndOfTurnDetector`'s, unchanged:
 * this only feeds it, at levels set for the room. See ADRs 0038 and 0044.
 */
export class HandsFreeListener {
  private detector: EndOfTurnDetector;
  private readonly pauses: PauseDetector;
  private levels: SpeechLevels;
  private readonly startedAt: number;
  /** What the microphone heard while the room was being measured, until it has been. */
  private calibrating: { level: number; at: number }[] | null = [];
  private timer: ReturnType<typeof setInterval> | null;
  private over = false;

  constructor(
    private readonly meter: LevelMeter,
    private readonly now: () => number,
    private readonly options: ListenerOptions,
    private readonly events: ListenerEvents,
  ) {
    this.levels = options.levels;
    this.startedAt = now();
    this.detector = this.detectorFor(this.levels);
    this.pauses = new PauseDetector(options.detection);
    this.pauses.begin();
    this.timer = setInterval(() => this.sample(), LEVEL_SAMPLE_MS);
  }

  /** Whether anybody has spoken yet, counting the moments spent measuring the room. */
  get heardSpeech(): boolean {
    if (this.detector.heard) return true;
    return Boolean(this.calibrating?.some((sample) => sample.level >= this.options.levels.speechLevel));
  }

  /** A cut happened at the last pause and nobody has spoken since. */
  get cutPending(): boolean {
    return this.pauses.cutPending;
  }

  stop(): void {
    this.over = true;
    if (this.timer !== null) clearInterval(this.timer);
    this.timer = null;
    this.meter.close();
    this.events.level(0);
  }

  private detectorFor(levels: SpeechLevels): EndOfTurnDetector {
    const detector = new EndOfTurnDetector({ ...this.options.detection, ...levels, silenceMs: this.options.sendPauseMs });
    detector.begin(this.startedAt);
    return detector;
  }

  private sample(): void {
    if (this.over) return;
    const level = this.meter.level();
    const at = this.now();
    this.events.level(Math.min(1, level / (this.levels.speechLevel * 2)));
    const calibrating = this.calibrating;
    if (!calibrating) {
      this.consider(level, at);
      return;
    }
    calibrating.push({ level, at });
    if (at - this.startedAt < CALIBRATION_MS) return;
    this.calibrating = null;
    this.levels = levelsForRoom(calibrating.map((sample) => sample.level), this.options.levels);
    this.detector = this.detectorFor(this.levels);
    this.pauses.setLevels(this.levels);
    for (const sample of calibrating) {
      if (this.over) return;
      this.consider(sample.level, sample.at);
    }
  }

  private consider(level: number, at: number): void {
    const verdict = this.detector.observe(level, at);
    if (verdict === 'too_long') return this.finish(() => this.events.tooLong());
    if (verdict === 'ended') return this.finish(() => this.events.ended());
    const idleMs = this.options.idleMs;
    if (idleMs !== undefined && !this.detector.heard && at - this.startedAt >= idleMs) {
      return this.finish(() => this.events.nobodySpoke());
    }
    // A pause is where the earlier part of a long answer can start being
    // transcribed while the rest is still being said. It never ends a turn;
    // that decision stays with the detector above, unchanged.
    if (this.options.cutAtPauses && this.pauses.observe(level, at)) this.events.pause();
  }

  private finish(report: () => void): void {
    this.over = true;
    report();
  }
}
