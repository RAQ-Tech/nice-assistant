import { api, type ApiClient } from './api';
import { errorMessage } from './dom';
import { machine, state, type ClientStateMachine } from './state';
import {
  EndOfTurnDetector,
  PauseDetector,
  createLevelMeter,
  type LevelMeter,
  type TurnDetectorOptions,
} from './turn_detection';
import { TranscriptSegments } from './transcript_segments';
import type { AppState } from './types';

// How often the microphone level is sampled while listening hands-free. Fine
// enough to notice the end of a sentence, coarse enough not to be a busy loop.
export const LEVEL_SAMPLE_MS = 100;

const MIME_TYPES = [
  'audio/webm;codecs=opus',
  'audio/webm',
  'audio/mp4',
  'audio/ogg;codecs=opus',
  'audio/ogg',
];

export class RecordingController {
  private recorder: MediaRecorder | null = null;
  private stream: MediaStream | null = null;
  private chunks: Blob[] = [];
  private mimeType = '';
  private onChange: () => void = () => undefined;
  private onTranscript: (text: string) => Promise<void> = async () => undefined;
  private meter: LevelMeter | null = null;
  private listener: ReturnType<typeof setInterval> | null = null;
  private readonly detector: EndOfTurnDetector;
  private readonly pauses: PauseDetector;
  private readonly segments = new TranscriptSegments();

  constructor(
    private readonly appState: AppState = state,
    private readonly stateMachine: ClientStateMachine = machine,
    private readonly client: ApiClient = api,
    private readonly openMeter: (stream: MediaStream) => LevelMeter = createLevelMeter,
    private readonly now: () => number = () => Date.now(),
    detection: TurnDetectorOptions = {},
  ) {
    this.detector = new EndOfTurnDetector(detection);
    this.pauses = new PauseDetector(detection);
  }

  /** Whether to transcribe at pauses instead of waiting for the whole turn. */
  private get streaming(): boolean {
    return Boolean(this.appState.settings?.stt_streaming);
  }

  configure(onChange: () => void, onTranscript: (text: string) => Promise<void>): void {
    this.onChange = onChange;
    this.onTranscript = onTranscript;
  }

  get recording(): boolean {
    return this.recorder?.state === 'recording';
  }

  /**
   * Begin listening.
   *
   * `handsFree` is what makes the product decide when the turn is over. Held
   * recording never uses it: the release is the decision, and it is always
   * right. See ADR 0038.
   */
  async start(handsFree = false): Promise<void> {
    if (this.recording) return;
    if (!this.appState.settings || this.appState.settings.stt_provider === 'disabled') {
      this.appState.uiError = 'Speech-to-text is disabled. Enable OpenAI STT in Settings.';
      this.onChange();
      return;
    }
    if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
      this.appState.uiError = 'This browser cannot record microphone audio.';
      this.onChange();
      return;
    }
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      this.mimeType = preferredMimeType();
      this.chunks = [];
      this.recorder = this.mimeType
        ? new MediaRecorder(this.stream, { mimeType: this.mimeType })
        : new MediaRecorder(this.stream);
      this.recorder.addEventListener('dataavailable', (event: BlobEvent) => {
        if (event.data.size) this.chunks.push(event.data);
      });
      this.recorder.start(250);
      this.segments.begin();
      this.appState.partialTranscript = '';
      this.appState.recordingStartedAt = this.now();
      this.stateMachine.transition('recording');
      if (handsFree) this.listenForTheEnd();
    } catch (error) {
      this.cleanup();
      this.appState.uiError = errorMessage(error, 'Microphone access was denied or unavailable.');
      this.stateMachine.transition('error');
    }
    this.onChange();
  }

  /**
   * Watch the microphone level and stop when the speaker has finished.
   *
   * A turn that ran to the ceiling is stopped too, and says so: a microphone
   * left open because nothing ever sounded like silence is worse than a stop
   * nobody asked for.
   */
  private listenForTheEnd(): void {
    const stream = this.stream;
    if (!stream) return;
    this.meter = this.openMeter(stream);
    this.detector.begin(this.now());
    this.pauses.begin();
    this.listener = setInterval(() => {
      const meter = this.meter;
      if (!meter || !this.recording) return;
      const verdict = this.detector.observe(meter.level(), this.now());
      if (verdict === 'too_long') {
        this.appState.uiError = 'Listening stopped after a minute. Hold the microphone button to record instead.';
        void this.stop();
        return;
      }
      if (verdict === 'ended') {
        void this.stop();
        return;
      }
      // A pause is where the earlier part of a long answer can start being
      // transcribed while the rest is still being said. It never ends a turn;
      // that decision stays with the detector above, unchanged.
      if (this.streaming && this.pauses.observe(meter.level(), this.now())) void this.cutSegment();
    }, LEVEL_SAMPLE_MS);
  }

  /**
   * Close the piece just spoken, start the next, and transcribe the closed one.
   *
   * Stopping and restarting the recorder is what makes each piece a complete
   * file rather than a slice of a stream nothing can decode on its own. The
   * few milliseconds lost at the boundary fall inside the pause that caused
   * the cut, so no speech goes with them.
   */
  private async cutSegment(): Promise<void> {
    const recorder = this.recorder;
    if (!recorder || recorder.state !== 'recording' || !this.stream) return;
    const blob = await this.closeRecorder(recorder);
    if (this.recording) this.openRecorder();
    if (!blob.size) return;
    this.segments.claim(
      async () => (await this.client.transcribe(blob, recordingFilename(blob.type))).text,
      (joined) => {
        this.appState.partialTranscript = joined;
        this.onChange();
      },
    );
  }

  private openRecorder(): void {
    if (!this.stream) return;
    this.chunks = [];
    this.recorder = this.mimeType
      ? new MediaRecorder(this.stream, { mimeType: this.mimeType })
      : new MediaRecorder(this.stream);
    this.recorder.addEventListener('dataavailable', (event: BlobEvent) => {
      if (event.data.size) this.chunks.push(event.data);
    });
    this.recorder.start(250);
  }

  private closeRecorder(recorder: MediaRecorder): Promise<Blob> {
    const type = recorder.mimeType || this.mimeType || 'audio/webm';
    const chunks = this.chunks;
    return new Promise<Blob>((resolve) => {
      recorder.addEventListener('stop', () => resolve(new Blob(chunks, { type })), { once: true });
      recorder.stop();
    });
  }

  async stop(): Promise<void> {
    const recorder = this.recorder;
    if (!recorder || recorder.state !== 'recording') return;
    this.stopListening();
    const blob = await new Promise<Blob>((resolve) => {
      recorder.addEventListener(
        'stop',
        () => resolve(new Blob(this.chunks, { type: recorder.mimeType || this.mimeType || 'audio/webm' })),
        { once: true },
      );
      recorder.stop();
    });
    this.cleanup();
    if (!blob.size && this.segments.empty) {
      this.appState.partialTranscript = '';
      this.stateMachine.transition('idle');
      this.onChange();
      return;
    }
    this.stateMachine.transition('transcribing');
    this.onChange();
    try {
      const result = await this.client.transcribe(blob, recordingFilename(blob.type));
      // Whatever was cut at pauses is already transcribed or on its way; this
      // is only the tail, and it is the sole part anybody waited for.
      const transcript = await this.segments.finish(result.text);
      this.appState.partialTranscript = '';
      if (transcript) await this.onTranscript(transcript);
      else if (this.appState.phase === 'transcribing') this.stateMachine.transition('idle');
    } catch (error) {
      this.appState.uiError = errorMessage(error, 'Unable to transcribe this recording.');
      this.stateMachine.transition('error');
    } finally {
      this.onChange();
    }
  }

  cancel(): void {
    if (this.recorder?.state === 'recording') this.recorder.stop();
    this.appState.partialTranscript = '';
    this.stopListening();
    this.cleanup();
    if (this.appState.phase === 'recording') this.stateMachine.transition('idle');
    this.onChange();
  }

  private stopListening(): void {
    if (this.listener !== null) clearInterval(this.listener);
    this.listener = null;
    this.meter?.close();
    this.meter = null;
  }

  private cleanup(): void {
    this.stopListening();
    for (const track of this.stream?.getTracks() ?? []) track.stop();
    this.stream = null;
    this.recorder = null;
    this.chunks = [];
    this.appState.recordingStartedAt = 0;
  }
}

export function preferredMimeType(): string {
  if (typeof MediaRecorder === 'undefined' || typeof MediaRecorder.isTypeSupported !== 'function') return '';
  return MIME_TYPES.find((value) => MediaRecorder.isTypeSupported(value)) ?? '';
}

export function recordingFilename(mimeType: string): string {
  if (mimeType.includes('mp4')) return 'audio.mp4';
  if (mimeType.includes('ogg')) return 'audio.ogg';
  return 'audio.webm';
}
