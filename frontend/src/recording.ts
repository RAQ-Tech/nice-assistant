import { api, type ApiClient } from './api';
import { errorMessage } from './dom';
import { machine, state, type ClientStateMachine } from './state';
import type { AppState } from './types';

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

  constructor(
    private readonly appState: AppState = state,
    private readonly stateMachine: ClientStateMachine = machine,
    private readonly client: ApiClient = api,
  ) {}

  configure(onChange: () => void, onTranscript: (text: string) => Promise<void>): void {
    this.onChange = onChange;
    this.onTranscript = onTranscript;
  }

  get recording(): boolean {
    return this.recorder?.state === 'recording';
  }

  async start(): Promise<void> {
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
      this.appState.recordingStartedAt = Date.now();
      this.stateMachine.transition('recording');
    } catch (error) {
      this.cleanup();
      this.appState.uiError = errorMessage(error, 'Microphone access was denied or unavailable.');
      this.stateMachine.transition('error');
    }
    this.onChange();
  }

  async stop(): Promise<void> {
    const recorder = this.recorder;
    if (!recorder || recorder.state !== 'recording') return;
    const blob = await new Promise<Blob>((resolve) => {
      recorder.addEventListener(
        'stop',
        () => resolve(new Blob(this.chunks, { type: recorder.mimeType || this.mimeType || 'audio/webm' })),
        { once: true },
      );
      recorder.stop();
    });
    this.cleanup();
    if (!blob.size) {
      this.stateMachine.transition('idle');
      this.onChange();
      return;
    }
    this.stateMachine.transition('transcribing');
    this.onChange();
    try {
      const result = await this.client.transcribe(blob, recordingFilename(blob.type));
      const transcript = result.text.trim();
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
    this.cleanup();
    if (this.appState.phase === 'recording') this.stateMachine.transition('idle');
    this.onChange();
  }

  private cleanup(): void {
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
