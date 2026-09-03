import { api, type ApiClient } from './api';
import { speechText } from './speech_text';
import { machine, state, type ClientStateMachine } from './state';
import { MediaSourceSink, streamableMimeType, type AudioStreamSink } from './streaming_audio';
import type { AppState } from './types';
import { Visualizer } from './visualization';

export const AUDIO_ID_HEADER = 'X-Nice-Assistant-Audio-Id';

export class PlaybackController {
  private onChange: () => void = () => undefined;
  private sequence = 0;
  private activePlaybackToken: number | null = null;
  private synthesis: AbortController | null = null;
  /** A reply being spoken in pieces into one stream (ADR 0042). */
  private pieces: { token: number; sink: AudioStreamSink; started: boolean; mimeType: string; messageId: string } | null = null;

  constructor(
    private readonly audio: HTMLAudioElement,
    private readonly visualizer: Visualizer,
    private readonly appState: AppState = state,
    private readonly stateMachine: ClientStateMachine = machine,
    private readonly client: ApiClient = api,
    private readonly createSink: () => AudioStreamSink = () => new MediaSourceSink(),
  ) {
    audio.addEventListener('ended', this.finishActive);
    audio.addEventListener('error', this.failActive);
  }

  setChangeHandler(handler: () => void): void {
    this.onChange = handler;
  }

  async synthesize(text: string, messageId: string, chatId: string, personaId: string | null): Promise<void> {
    const settings = this.appState.settings;
    const cleanedText = speechText(text);
    if (!settings || settings.tts_provider === 'disabled' || !this.appState.voiceResponsesEnabled || !cleanedText) return;
    const format = settings.tts_format || 'wav';
    const mimeType = streamableMimeType(format);
    if (mimeType) {
      // Start speaking when the first audio exists rather than when the last
      // one does. A format that cannot be played incrementally, or a browser
      // that will not, falls through to the completed file below.
      const streamed = await this.speakWhileArriving(cleanedText, messageId, chatId, personaId, format, mimeType);
      if (streamed) return;
    }
    const token = this.begin(messageId);
    const request = new AbortController();
    this.synthesis = request;
    let result;
    try {
      result = await this.client.synthesize({
        text: cleanedText,
        chat_id: chatId,
        persona_id: personaId,
        format: settings.tts_format || 'wav',
      }, request.signal);
    } catch (error) {
      // An interruption is the expected way this ends, not a failure worth
      // reporting: the person stopped it on purpose.
      if (request.signal.aborted) return;
      throw error;
    } finally {
      if (this.synthesis === request) this.synthesis = null;
    }
    if (token !== this.sequence) return;
    this.appState.messageAudioById[messageId] = result.audio_url;
    await this.playPrepared(messageId, result.audio_url, token);
  }

  /**
   * Play a reply as the provider produces it.
   *
   * Returns false when the stream could not be started at all, so the caller
   * can fall back to the completed file rather than leave somebody in silence.
   * Once playback has begun a failure is not silently retried: the audio the
   * person already heard would be spoken twice.
   */
  private async speakWhileArriving(
    text: string,
    messageId: string,
    chatId: string,
    personaId: string | null,
    format: string,
    mimeType: string,
  ): Promise<boolean> {
    const token = this.begin(messageId);
    const request = new AbortController();
    this.synthesis = request;
    const sink = this.createSink();
    let started = false;
    try {
      const response = await this.client.streamSpeech(
        { text, chat_id: chatId, persona_id: personaId, format },
        request.signal,
      );
      const body = response.body;
      if (!body) return false;
      const audioId = response.headers.get(AUDIO_ID_HEADER);
      const url = await sink.open(mimeType);
      if (token !== this.sequence) return true;
      started = true;
      // The element is given the growing source before any piece has arrived,
      // so it begins the moment there is enough to begin with.
      void this.playPrepared(messageId, url, token);
      const reader = body.getReader();
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        if (token !== this.sequence) return true;
        await sink.append(value);
      }
      await sink.end();
      // The completed recording is still stored, so replay uses the file
      // rather than asking the provider to speak it again.
      if (audioId) this.appState.messageAudioById[messageId] = `/api/v1/audio/${audioId}`;
      return true;
    } catch (error) {
      if (request.signal.aborted) return true;
      return started;
    } finally {
      sink.close();
      if (this.synthesis === request) this.synthesis = null;
    }
  }

  /**
   * Start a reply that will be spoken a sentence at a time, while it is
   * still being written. False when this format cannot start early, so the
   * caller speaks the completed reply instead, exactly as before.
   */
  beginPieces(messageId: string): boolean {
    const settings = this.appState.settings;
    if (!settings || settings.tts_provider === 'disabled' || !this.appState.voiceResponsesEnabled) return false;
    const mimeType = streamableMimeType(settings.tts_format || 'wav');
    if (!mimeType) return false;
    const token = this.begin(messageId);
    this.pieces = { token, sink: this.createSink(), started: false, mimeType, messageId };
    return true;
  }

  /**
   * Speak one piece into the stream as it arrives. False once the stream is
   * over - stopped by the person, or failed - so nothing more is queued.
   */
  async appendPiece(fetchPiece: (signal: AbortSignal) => Promise<Response>): Promise<boolean> {
    const current = this.pieces;
    if (!current || current.token !== this.sequence) return false;
    const request = new AbortController();
    this.synthesis = request;
    try {
      const response = await fetchPiece(request.signal);
      const body = response.body;
      if (!body) return false;
      if (!current.started) {
        const url = await current.sink.open(current.mimeType);
        if (current.token !== this.sequence) return false;
        current.started = true;
        // The element is given the growing source before any piece has
        // arrived, so it begins the moment there is enough to begin with.
        void this.playPrepared(current.messageId, url, current.token);
      }
      const reader = body.getReader();
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        if (current.token !== this.sequence) return false;
        await current.sink.append(value);
      }
      return current.token === this.sequence;
    } catch {
      return false;
    } finally {
      if (this.synthesis === request) this.synthesis = null;
    }
  }

  /** No more pieces. The element plays out what it has; replay uses the stored whole. */
  async endPieces(audioUrl: string | null, messageId: string): Promise<void> {
    const current = this.pieces;
    if (!current) return;
    this.pieces = null;
    if (current.token !== this.sequence) return;
    // The reply's final id may differ from the one it was typed under.
    if (this.appState.currentAudioMessageId === current.messageId) this.appState.currentAudioMessageId = messageId;
    if (audioUrl) this.appState.messageAudioById[messageId] = audioUrl;
    if (current.started) await current.sink.end();
    current.sink.close();
    this.onChange();
  }

  async play(messageId: string, url: string): Promise<void> {
    const token = this.begin(messageId);
    await this.playPrepared(messageId, url, token);
  }

  stop(render = true): void {
    this.sequence += 1;
    this.activePlaybackToken = null;
    this.pieces?.sink.close();
    this.pieces = null;
    // Muting the output while the provider keeps generating is what barge-in
    // is not. Aborting tells the server nobody is waiting, and it stops.
    this.synthesis?.abort();
    this.synthesis = null;
    this.haltAudio();
    if (this.appState.phase === 'speaking') this.stateMachine.transition('idle');
    if (render) this.onChange();
  }

  private begin(messageId: string): number {
    this.stop(false);
    delete this.appState.messageAudioErrors[messageId];
    return this.sequence;
  }

  private async playPrepared(messageId: string, url: string, token: number): Promise<void> {
    if (token !== this.sequence) return;
    this.visualizer.connectAudio();
    this.audio.src = url;
    this.activePlaybackToken = token;
    this.appState.currentAudioMessageId = messageId;
    try {
      await this.audio.play();
      // A reply spoken while it is written starts from thinking, not idle.
      if (token !== this.sequence || !['idle', 'thinking'].includes(this.appState.phase)) {
        if (this.activePlaybackToken === token) {
          this.activePlaybackToken = null;
          this.haltAudio();
        }
        return;
      }
      this.stateMachine.transition('speaking');
      this.onChange();
    } catch {
      if (token !== this.sequence) return;
      this.activePlaybackToken = null;
      this.haltAudio();
      if (this.appState.phase === 'speaking') this.stateMachine.transition('idle');
      this.appState.messageAudioErrors[messageId] = 'Audio is ready. Use replay to listen.';
      this.onChange();
    }
  }

  private haltAudio(): void {
    this.audio.pause();
    this.audio.currentTime = 0;
    this.appState.currentAudioMessageId = null;
  }

  private readonly finishActive = (): void => {
    if (this.activePlaybackToken === null) return;
    this.sequence += 1;
    this.activePlaybackToken = null;
    this.haltAudio();
    if (this.appState.phase === 'speaking') this.stateMachine.transition('idle');
    this.onChange();
  };

  private readonly failActive = (): void => {
    const messageId = this.appState.currentAudioMessageId;
    if (messageId) this.appState.messageAudioErrors[messageId] = 'Audio could not be played.';
    this.finishActive();
  };
}
