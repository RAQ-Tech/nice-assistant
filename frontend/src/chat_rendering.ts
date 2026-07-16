import { api, type ApiClient } from './api';
import { DEFAULT_PERSONA_AVATAR } from './constants';
import { copyText, downloadUrl, el, markdown } from './dom';
import { extractImageUrl, extractVideoUrl, imagePromptFromMessage, stripVideoLinks } from './media';
import { state } from './state';
import type { AppState, Message } from './types';
import type { MediaController } from './media';
import type { PlaybackController } from './playback';

export class ChatRenderer {
  constructor(
    private readonly media: MediaController,
    private readonly playback: PlaybackController,
    private readonly renderApp: () => void,
    private readonly appState: AppState = state,
    private readonly client: ApiClient = api,
  ) {}

  message(message: Message, personaId: string | null): HTMLElement | null {
    const isUser = message.role === 'user';
    if (!this.appState.showSystemMessages && ['system', 'tool'].includes(message.role)) return null;
    const { thinking, visibleText } = splitThinking(message.text);
    const hasThinking = Boolean(thinking) && message.role === 'assistant';
    const showThinking = this.appState.showThinkingByDefault || Boolean(this.appState.thinkingExpanded[message.id]);
    const persona = this.appState.personas.find((item) => item.id === (personaId ?? this.appState.selectedPersonaId));
    const personaName = persona?.name ?? 'assistant';
    const roleLabel = isUser ? 'You' : message.role === 'assistant' ? personaName : message.role;
    const source = visibleText || message.text;
    const imageUrl = extractImageUrl(source);
    const videoUrl = extractVideoUrl(source);
    const displayText = videoUrl ? stripVideoLinks(source) : source;
    const body = el('div', { html: markdown(displayText) });
    this.bindImagePreviews(body);
    const audioUrl = this.appState.messageAudioById[message.id];
    const audioError = this.appState.messageAudioErrors[message.id];
    return el('div', { class: `msg-wrap ${isUser ? 'user' : ''}`, 'data-testid': `message-${message.role}` }, [
      !isUser && message.role === 'assistant'
        ? el('img', {
            class: 'msg-avatar',
            src: persona?.avatar_url || DEFAULT_PERSONA_AVATAR,
            alt: `${personaName} avatar`,
            onclick: () => {
              this.appState.personaAvatarPreview = persona?.avatar_url || DEFAULT_PERSONA_AVATAR;
              this.renderApp();
            },
          })
        : null,
      el('article', { class: `msg ${isUser ? 'user' : 'assistant'}` }, [
        el('small', { textContent: roleLabel }),
        message.isTyping
          ? el('button', {
              class: `typing-indicator ${this.appState.pendingRequest ? 'clickable' : ''}`,
              textContent: this.appState.pendingRequest?.progress || 'typing…',
              disabled: !this.appState.pendingRequest,
              onclick: () => void this.appState.pendingRequest?.cancel(),
            })
          : null,
        hasThinking && showThinking
          ? el('details', { class: 'think-block', open: true }, [
              el('summary', { textContent: 'Model thinking' }),
              el('div', { class: 'think-content', html: markdown(thinking) }),
            ])
          : null,
        message.isTyping && !message.text ? null : body,
        audioError ? el('p', { class: 'message-audio-error', textContent: audioError }) : null,
        videoUrl
          ? el('button', { class: 'msg-video-preview', onclick: () => { this.appState.chatVideoPreview = videoUrl; this.renderApp(); } }, [
              el('video', { class: 'msg-inline-video', src: videoUrl, preload: 'metadata', muted: true, playsInline: true }),
              el('span', { class: 'msg-video-play', textContent: '▶' }),
              el('span', { class: 'msg-video-label', textContent: 'Play video' }),
            ])
          : null,
        message.isTyping
          ? null
          : el('div', { class: 'msg-actions' }, [
              message.role === 'assistant'
                ? el('button', { class: 'icon-btn', textContent: '🖼', title: 'Generate an image from this reply', onclick: () => void this.generateImage(message) })
                : null,
              hasThinking
                ? el('button', {
                    class: 'icon-btn',
                    textContent: showThinking ? '🙈' : '💭',
                    title: showThinking ? 'Hide thinking' : 'Show thinking',
                    onclick: () => {
                      this.appState.thinkingExpanded[message.id] = !showThinking;
                      this.renderApp();
                    },
                  })
                : null,
              el('button', { class: 'icon-btn', textContent: '⧉', title: 'Copy', onclick: () => void copyText(visibleText || message.text) }),
              imageUrl ? el('button', { class: 'icon-btn', textContent: '⬇', title: 'Save image', onclick: () => downloadUrl(imageUrl, `nice-assistant-image-${Date.now()}.png`) }) : null,
              videoUrl ? el('button', { class: 'icon-btn', textContent: '⬇', title: 'Save video', onclick: () => downloadUrl(videoUrl, `nice-assistant-video-${Date.now()}.mp4`) }) : null,
              audioUrl
                ? el('button', { class: 'icon-btn', textContent: '⟲', title: 'Replay response audio', onclick: () => void this.replayAudio(message.id, audioUrl) })
                : null,
              this.appState.currentAudioMessageId === message.id
                ? el('button', { class: 'icon-btn', textContent: '■', title: 'Stop audio', onclick: () => this.playback.stop() })
                : null,
              el('button', { class: 'icon-btn', textContent: '🧠', title: 'Save to chat memory', onclick: () => void this.saveMemory(message) }),
            ]),
      ]),
    ]);
  }

  syntheticSystemMessage(personaId: string | null): Message[] {
    const persona = this.appState.personas.find((item) => item.id === personaId);
    if (!persona?.system_prompt) return [];
    return [{ id: `system-${persona.id}`, role: 'system', text: persona.system_prompt, created_at: persona.created_at }];
  }

  private bindImagePreviews(root: HTMLElement): void {
    root.querySelectorAll<HTMLImageElement>('.msg-inline-image').forEach((image) => {
      const key = image.src;
      if (!this.appState.revealedImages[key]) {
        image.classList.add('image-blurred');
        image.title = 'Tap to reveal image';
      } else image.title = 'Open image preview';
      image.addEventListener('click', () => {
        if (!this.appState.revealedImages[key]) {
          this.appState.revealedImages[key] = true;
          image.classList.remove('image-blurred');
          image.title = 'Open image preview';
          return;
        }
        this.appState.chatImagePreview = key;
        this.renderApp();
      });
    });
  }

  private async generateImage(message: Message): Promise<void> {
    await this.media.generateImage(imagePromptFromMessage(message), this.appState.currentChat?.id ?? null);
  }

  private async replayAudio(messageId: string, audioUrl: string): Promise<void> {
    try {
      await this.playback.play(messageId, audioUrl);
    } catch {
      this.appState.messageAudioErrors[messageId] = 'Audio could not be played.';
      this.renderApp();
    }
  }

  private async saveMemory(message: Message): Promise<void> {
    const chatId = this.appState.currentChat?.id;
    if (!chatId) return;
    await this.client.createMemory('chat', chatId, message.text);
    this.appState.memories = (await this.client.memories()).items;
    this.renderApp();
  }
}

export function splitThinking(text: string): { thinking: string; visibleText: string } {
  const match = /<think>([\s\S]*?)<\/think>/i.exec(text);
  if (!match) return { thinking: '', visibleText: text };
  return {
    thinking: match[1]?.trim() ?? '',
    visibleText: text.replace(match[0], '').trim(),
  };
}

export function modelNickname(model: string): string {
  const leaf = model.split('/').pop() ?? model;
  return leaf.replace(/:latest$/, '').replace(/[-_]/g, ' ');
}
