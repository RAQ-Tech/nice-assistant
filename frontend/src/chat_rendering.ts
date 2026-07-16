import { api, type ApiClient } from './api';
import { DEFAULT_PERSONA_AVATAR } from './constants';
import { copyText, downloadUrl, el, markdown } from './dom';
import { extractImageUrl, extractVideoUrl, imagePromptFromMessage, speechText, stripVideoLinks } from './media';
import { state } from './state';
import type { AppState, ChatAttachment, CapabilityRequest, Message } from './types';
import type { MediaController } from './media';
import type { PlaybackController } from './playback';

export class ChatRenderer {
  constructor(
    private readonly media: MediaController,
    private readonly playback: PlaybackController,
    private readonly renderApp: () => void,
    private readonly appState: AppState = state,
    private readonly client: ApiClient = api,
    private readonly editMemoryProposal: (content: string) => Promise<string | null> = async (content) => content,
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
    const attachmentNodes = (message.attachments ?? [])
      .filter((attachment) => this.appState.capabilityRequests.find(
        (request) => request.id === attachment.capability_request_id,
      )?.status !== 'pending_confirmation')
      .map((attachment) => this.attachmentNode(attachment));
    const mediaRoot = el('div', { class: 'chat-attachments' }, attachmentNodes);
    this.bindImagePreviews(body);
    this.bindImagePreviews(mediaRoot);
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
        attachmentNodes.length ? mediaRoot : null,
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
              message.role === 'assistant' && message.text.trim()
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
              el('button', { class: 'icon-btn', textContent: '🧠', title: 'Propose a memory fact', onclick: () => void this.saveMemory(message) }),
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
      const blur = Boolean(this.appState.settings?.chat_blur_images);
      if (blur && !this.appState.revealedImages[key]) {
        image.classList.add('image-blurred');
        image.title = 'Tap to reveal image';
      } else image.title = 'Open image preview';
      image.addEventListener('click', () => {
        if (blur && !this.appState.revealedImages[key]) {
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

  private attachmentNode(attachment: ChatAttachment): HTMLElement {
    const request = this.appState.capabilityRequests.find((item) => item.id === attachment.capability_request_id);
    const label = attachment.kind === 'image' ? 'picture' : 'video';
    if (attachment.status === 'completed' && attachment.content_url) {
      const media = attachment.kind === 'image'
        ? el('img', {
            class: 'msg-inline-image attachment-image',
            src: attachment.content_url,
            alt: 'Generated picture',
          })
        : el('button', {
            class: 'msg-video-preview',
            onclick: () => {
              this.appState.chatVideoPreview = attachment.content_url ?? '';
              this.renderApp();
            },
          }, [
            el('video', { class: 'msg-inline-video', src: attachment.content_url, preload: 'metadata', muted: true, playsInline: true }),
            el('span', { class: 'msg-video-play', textContent: '▶' }),
            el('span', { class: 'msg-video-label', textContent: 'Play video' }),
          ]);
      return el('section', { class: 'chat-attachment attachment-completed', 'data-testid': 'chat-attachment' }, [
        media,
        attachment.identity_state === 'unconditioned'
          ? el('p', { class: 'attachment-identity', textContent: 'No identity reference was applied · unverified' })
          : attachment.identity_state === 'verified'
            ? el('p', { class: 'attachment-identity', textContent: 'Identity match verified' })
            : attachment.identity_state === 'unverified'
              ? el('p', { class: 'attachment-identity', textContent: 'Identity match unverified' })
              : null,
        this.attachmentDetails(request),
      ]);
    }
    if (attachment.status === 'failed') {
      return el('section', { class: 'chat-attachment attachment-failed', 'data-testid': 'chat-attachment' }, [
        el('span', { textContent: attachment.safe_error || `That ${label} could not be made.` }),
        attachment.retry_available
          ? el('button', {
              class: 'pill-btn attachment-action',
              textContent: 'Retry',
              'data-testid': 'retry-chat-attachment',
              onclick: () => void this.retryAttachment(attachment),
            })
          : null,
        this.attachmentDetails(request),
      ]);
    }
    if (attachment.status === 'cancelled') {
      return el('section', { class: 'chat-attachment attachment-cancelled', 'data-testid': 'chat-attachment' }, [
        el('span', { textContent: `${capitalize(label)} canceled.` }),
        attachment.retry_available
          ? el('button', { class: 'pill-btn attachment-action', textContent: 'Retry', onclick: () => void this.retryAttachment(attachment) })
          : null,
      ]);
    }
    if (attachment.status === 'retried') {
      return el('section', { class: 'chat-attachment attachment-retried', textContent: `${capitalize(label)} retry started.` });
    }
    return el('section', { class: 'chat-attachment attachment-progress', 'data-testid': 'chat-attachment' }, [
      el('span', { class: 'attachment-spinner', 'aria-hidden': 'true' }),
      el('span', { textContent: attachment.status === 'running' ? `Making that ${label}…` : `${capitalize(label)} queued…` }),
      request && ['queued', 'running'].includes(request.status)
        ? el('button', {
            class: 'pill-btn attachment-action',
            textContent: 'Cancel',
            'data-testid': 'cancel-chat-attachment',
            onclick: () => void this.cancelAttachment(request),
          })
        : null,
      this.attachmentDetails(request),
    ]);
  }

  private attachmentDetails(request: CapabilityRequest | undefined): HTMLElement | null {
    if (!request) return null;
    const selected = request.media_plan?.selected_resources ?? [];
    const provider = selected.find((item) => item.resource_type === 'model');
    return el('details', { class: 'attachment-details' }, [
      el('summary', { textContent: 'Details' }),
      request.arguments.prompt
        ? el('p', { textContent: String(request.arguments.prompt) })
        : null,
      provider
        ? el('p', { class: 'meta', textContent: `Made with ${provider.name} (${provider.backend})` })
        : null,
    ]);
  }

  private async cancelAttachment(request: CapabilityRequest): Promise<void> {
    try {
      await this.client.cancelCapability(request.id);
      await this.refreshCurrentChat();
    } catch {
      this.appState.uiError = 'That picture could not be canceled.';
    }
    this.renderApp();
  }

  private async retryAttachment(attachment: ChatAttachment): Promise<void> {
    try {
      await this.client.retryCapability(attachment.capability_request_id);
      await this.refreshCurrentChat();
    } catch {
      this.appState.uiError = 'That picture could not be retried.';
    }
    this.renderApp();
  }

  private async refreshCurrentChat(): Promise<void> {
    const chatId = this.appState.currentChat?.id;
    if (!chatId) return;
    const [detail, capabilities] = await Promise.all([
      this.client.chat(chatId),
      this.client.capabilityRequests(chatId),
    ]);
    if (this.appState.currentChat?.id !== chatId) return;
    this.appState.currentChat = detail.chat;
    this.appState.messages = detail.messages;
    this.appState.capabilityRequests = capabilities.items;
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
    const proposal = await this.editMemoryProposal(speechText(message.text));
    const content = proposal?.trim();
    if (!content) return;
    try {
      await this.client.proposeMemory('chat', chatId, content, message.id);
      this.appState.memories = (await this.client.memories()).items;
      this.appState.statusText = 'Memory fact proposed for review';
    } catch {
      this.appState.uiError = 'That memory fact could not be proposed.';
    }
    this.renderApp();
  }
}

function capitalize(value: string): string {
  return value.charAt(0).toUpperCase() + value.slice(1);
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
