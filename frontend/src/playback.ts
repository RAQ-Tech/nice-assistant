import { api, type ApiClient } from './api';
import { speechText } from './speech_text';
import { machine, state, type ClientStateMachine } from './state';
import type { AppState } from './types';
import { Visualizer } from './visualization';

export class PlaybackController {
  private onChange: () => void = () => undefined;
  private sequence = 0;
  private activePlaybackToken: number | null = null;

  constructor(
    private readonly audio: HTMLAudioElement,
    private readonly visualizer: Visualizer,
    private readonly appState: AppState = state,
    private readonly stateMachine: ClientStateMachine = machine,
    private readonly client: ApiClient = api,
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
    const token = this.begin(messageId);
    const result = await this.client.synthesize({
      text: cleanedText,
      chat_id: chatId,
      persona_id: personaId,
      format: settings.tts_format || 'wav',
    });
    if (token !== this.sequence) return;
    this.appState.messageAudioById[messageId] = result.audio_url;
    await this.playPrepared(messageId, result.audio_url, token);
  }

  async play(messageId: string, url: string): Promise<void> {
    const token = this.begin(messageId);
    await this.playPrepared(messageId, url, token);
  }

  stop(render = true): void {
    this.sequence += 1;
    this.activePlaybackToken = null;
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
      if (token !== this.sequence || this.appState.phase !== 'idle') {
        if (this.activePlaybackToken === token) {
          this.activePlaybackToken = null;
          this.haltAudio();
        }
        return;
      }
      this.stateMachine.transition('speaking');
      this.onChange();
    } catch (error) {
      if (token !== this.sequence) return;
      this.activePlaybackToken = null;
      this.haltAudio();
      if (this.appState.phase === 'speaking') this.stateMachine.transition('idle');
      this.onChange();
      throw error;
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
