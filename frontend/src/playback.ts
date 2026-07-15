import { api, type ApiClient } from './api';
import { machine, state, type ClientStateMachine } from './state';
import type { AppState } from './types';
import { Visualizer } from './visualization';

export class PlaybackController {
  private onChange: () => void = () => undefined;

  constructor(
    private readonly audio: HTMLAudioElement,
    private readonly visualizer: Visualizer,
    private readonly appState: AppState = state,
    private readonly stateMachine: ClientStateMachine = machine,
    private readonly client: ApiClient = api,
  ) {
    audio.addEventListener('ended', this.finish);
    audio.addEventListener('error', this.finish);
  }

  setChangeHandler(handler: () => void): void {
    this.onChange = handler;
  }

  async synthesize(text: string, messageId: string, chatId: string, personaId: string | null): Promise<void> {
    const settings = this.appState.settings;
    if (!settings || settings.tts_provider === 'disabled' || !this.appState.voiceResponsesEnabled || !text.trim()) return;
    const result = await this.client.synthesize({
      text,
      chat_id: chatId,
      persona_id: personaId,
      format: settings.tts_format || 'wav',
    });
    this.appState.messageAudioById[messageId] = result.audio_url;
    await this.play(messageId, result.audio_url);
  }

  async play(messageId: string, url: string): Promise<void> {
    this.stop(false);
    this.visualizer.connectAudio();
    this.audio.src = url;
    this.appState.currentAudioMessageId = messageId;
    this.stateMachine.transition('speaking');
    this.onChange();
    try {
      await this.audio.play();
    } catch (error) {
      this.finish();
      throw error;
    }
  }

  stop(render = true): void {
    this.audio.pause();
    this.audio.currentTime = 0;
    this.appState.currentAudioMessageId = null;
    if (this.appState.phase === 'speaking') this.stateMachine.transition('idle');
    if (render) this.onChange();
  }

  private readonly finish = (): void => {
    this.appState.currentAudioMessageId = null;
    if (this.appState.phase === 'speaking') this.stateMachine.transition('idle');
    this.onChange();
  };
}
