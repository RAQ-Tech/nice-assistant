import { api, type ApiClient } from './api';
import { errorMessage } from './dom';
import { HandsFreeListener, type ListenOptions } from './hands_free_listener';
import { preferredMimeType, recordingFilename } from './recording_formats';
import { machine, state, type ClientStateMachine } from './state';
import { TURN_DETECTION_DEFAULTS, createLevelMeter, type LevelMeter, type TurnDetectorOptions } from './turn_detection';
import { TranscriptSegments } from './transcript_segments';
import type { AppState } from './types';

export { LEVEL_SAMPLE_MS, type ListenOptions } from './hands_free_listener';

// Asked for by name: a phone's defaults are not a laptop's, and a speaker can
// still ring for a moment after the persona stops.
const MICROPHONE: MediaTrackConstraints = { echoCancellation: true, noiseSuppression: true, autoGainControl: true };

/** Why a recording produced nothing to send: nobody spoke, or what was heard had no words in it. */
export type NothingSaid = 'silence' | 'empty';

export interface RecordingEvents {
  onNothing?: (reason: NothingSaid) => void;
  /** How loud the microphone is right now, from 0 to 1, while listening hands-free. */
  onLevel?: (level: number) => void;
}

export class RecordingController {
  private recorder: MediaRecorder | null = null;
  private stream: MediaStream | null = null;
  private chunks: Blob[] = [];
  private mimeType = '';
  private onChange: () => void = () => undefined;
  private onTranscript: (text: string) => Promise<void> = async () => undefined;
  private events: RecordingEvents = {};
  private listener: HandsFreeListener | null = null;
  private readonly segments = new TranscriptSegments();
  private starting = false;
  /** What a held button asked for while the microphone was still opening. */
  private whenStarted: 'stop' | 'cancel' | null = null;

  constructor(
    private readonly appState: AppState = state,
    private readonly stateMachine: ClientStateMachine = machine,
    private readonly client: ApiClient = api,
    private readonly openMeter: (stream: MediaStream) => LevelMeter = createLevelMeter,
    private readonly now: () => number = () => Date.now(),
    private readonly detection: TurnDetectorOptions = {},
  ) {}

  /**
   * How long silence must last before a hands-free turn is sent.
   *
   * A person's own choice, because the pause that ends a turn is the pause
   * that cuts somebody off, and how long people pause to think is not the
   * product's to know. The pause that cuts a recording for early
   * transcription is unchanged and stays shorter.
   */
  private get sendPauseMs(): number {
    if (this.detection.silenceMs !== undefined) return this.detection.silenceMs;
    const chosen = Number(this.appState.settings?.stt_send_pause_ms);
    return Number.isFinite(chosen) && chosen > 0 ? chosen : TURN_DETECTION_DEFAULTS.silenceMs;
  }

  /** Whether to transcribe at pauses instead of waiting for the whole turn. */
  private get streaming(): boolean {
    return Boolean(this.appState.settings?.stt_streaming);
  }

  configure(onChange: () => void, onTranscript: (text: string) => Promise<void>, events: RecordingEvents = {}): void {
    this.onChange = onChange;
    this.onTranscript = onTranscript;
    this.events = events;
  }

  get recording(): boolean {
    return this.recorder?.state === 'recording';
  }

  /** Whether anybody has spoken since hands-free listening began. */
  get heardSpeech(): boolean {
    return this.listener?.heardSpeech ?? false;
  }

  /**
   * Begin listening.
   *
   * `handsFree` is what makes the product decide when the turn is over. Held
   * recording never uses it: the release is the decision, and it is always
   * right. See ADR 0038.
   */
  async start(handsFree = false, options: ListenOptions = {}): Promise<void> {
    // Asking for the microphone takes a moment. A second start in that moment
    // - a double tap, or a conversation reopening it as somebody taps - would
    // open it twice and leave one copy running.
    if (this.recording || this.starting) return;
    if (!this.appState.settings || this.appState.settings.stt_provider === 'disabled') {
      this.appState.uiError = 'Talking is not set up yet. Choose what transcribes it under Settings, Transcription.';
      this.onChange();
      return;
    }
    if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
      this.appState.uiError = window.isSecureContext === false
        ? 'The microphone needs a secure (https) address. See the mobile microphone note in the README.'
        : 'This browser cannot record microphone audio.';
      this.onChange();
      return;
    }
    this.starting = true;
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({ audio: MICROPHONE });
      this.mimeType = preferredMimeType();
      this.openRecorder();
      this.segments.begin();
      this.appState.partialTranscript = '';
      this.appState.recordingStartedAt = this.now();
      this.stateMachine.transition('recording');
      if (handsFree) this.listenForTheEnd(options.idleMs);
    } catch (error) {
      this.cleanup();
      this.appState.uiError = errorMessage(error, 'Microphone access was denied or unavailable.');
      this.stateMachine.transition('error');
    } finally {
      this.starting = false;
    }
    this.onChange();
    // A held button let go while the microphone was still being opened.
    const then = this.whenStarted;
    this.whenStarted = null;
    if (then === 'cancel') this.cancel();
    else if (then === 'stop') await this.stop();
  }

  /**
   * Watch the microphone level and stop when the speaker has finished.
   *
   * A turn that ran to the ceiling is stopped too, and says so: a microphone
   * left open because nothing ever sounded like silence is worse than a stop
   * nobody asked for.
   */
  private listenForTheEnd(idleMs?: number): void {
    if (!this.stream) return;
    const base = { ...TURN_DETECTION_DEFAULTS, ...this.detection };
    this.listener = new HandsFreeListener(
      this.openMeter(this.stream),
      this.now,
      {
        detection: this.detection,
        levels: { speechLevel: base.speechLevel, silenceLevel: base.silenceLevel },
        sendPauseMs: this.sendPauseMs,
        cutAtPauses: this.streaming,
        idleMs,
      },
      {
        ended: () => void this.stop(),
        tooLong: () => {
          this.appState.uiError = 'Listening stopped after a minute. Hold the microphone button to record instead.';
          void this.stop();
        },
        nobodySpoke: () => {
          this.cancel();
          this.events.onNothing?.('silence');
        },
        pause: () => void this.cutSegment(),
        level: (value) => this.events.onLevel?.(value),
      },
    );
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
    if (this.starting) {
      this.whenStarted = 'stop';
      return;
    }
    const recorder = this.recorder;
    if (!recorder || recorder.state !== 'recording') return;
    // A cut at the last pause already sent everything said; what the
    // recorder holds since then is the silence that ended the turn, and
    // transcribing silence would make the wait grow by the whole pause.
    const silentTail = this.streaming && Boolean(this.listener?.cutPending);
    this.stopListening();
    const blob = await this.closeRecorder(recorder);
    this.cleanup();
    if (!blob.size && this.segments.empty) {
      this.appState.partialTranscript = '';
      this.stateMachine.transition('idle');
      this.onChange();
      this.events.onNothing?.('silence');
      return;
    }
    this.stateMachine.transition('transcribing');
    this.onChange();
    try {
      // Whatever was cut at pauses is already transcribed or on its way; this
      // is only the tail, and it is the sole part anybody waited for.
      const tail = silentTail ? '' : (await this.client.transcribe(blob, recordingFilename(blob.type))).text;
      const transcript = await this.segments.finish(tail);
      this.appState.partialTranscript = '';
      if (transcript) await this.onTranscript(transcript);
      else {
        if (this.appState.phase === 'transcribing') this.stateMachine.transition('idle');
        this.events.onNothing?.('empty');
      }
    } catch (error) {
      this.appState.uiError = errorMessage(error, 'Unable to transcribe this recording.');
      this.stateMachine.transition('error');
    } finally {
      this.onChange();
    }
  }

  cancel(): void {
    if (this.starting) {
      this.whenStarted = 'cancel';
      return;
    }
    if (this.recorder?.state === 'recording') this.recorder.stop();
    this.appState.partialTranscript = '';
    this.stopListening();
    this.cleanup();
    if (this.appState.phase === 'recording') this.stateMachine.transition('idle');
    this.onChange();
  }

  private stopListening(): void {
    this.listener?.stop();
    this.listener = null;
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
