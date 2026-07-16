import { api, type ApiClient } from './api';
import { clientId } from './client_id';
import { errorMessage } from './dom';
import { waitForJob } from './media';
import { modelSettings } from './settings';
import { machine, state, type ClientStateMachine } from './state';
import type { AppState, Chat, Job, Message, TurnEvent } from './types';
import type { PlaybackController } from './playback';

export class ChatController {
  private onChange: () => void = () => undefined;
  private onNavigate: (chatId: string) => void = () => undefined;
  private streamAbort: AbortController | null = null;

  constructor(
    private readonly playback: PlaybackController,
    private readonly appState: AppState = state,
    private readonly stateMachine: ClientStateMachine = machine,
    private readonly client: ApiClient = api,
  ) {}

  configure(options: {
    onChange: () => void;
    onNavigate: (chatId: string) => void;
  }): void {
    this.onChange = options.onChange;
    this.onNavigate = options.onNavigate;
  }

  async open(chatId: string): Promise<void> {
    if (this.appState.currentChat?.id === chatId && this.appState.messages.length) return;
    if (this.appState.phase === 'error') this.stateMachine.recover();
    if (this.appState.phase !== 'idle') return;
    this.stateMachine.transition('loading_chat');
    this.onChange();
    try {
      const [detail, capabilities] = await Promise.all([
        this.client.chat(chatId),
        this.client.capabilityRequests(chatId),
      ]);
      this.appState.currentChat = detail.chat;
      this.appState.messages = detail.messages;
      this.appState.capabilityRequests = capabilities.items;
      this.appState.selectedPersonaId = detail.chat.persona_id;
      this.appState.selectedModel = detail.chat.model_override;
      this.appState.selectedMemoryMode = detail.chat.memory_mode;
      this.appState.uiError = '';
      this.stateMachine.transition('idle');
    } catch (error) {
      this.appState.uiError = errorMessage(error, 'Unable to open this chat.');
      this.stateMachine.transition('error');
    } finally {
      this.onChange();
    }
  }

  async create(personaId?: string | null): Promise<Chat> {
    const persona = this.appState.personas.find((item) => item.id === (personaId ?? this.appState.selectedPersonaId));
    const settings = this.requiredSettings();
    const chat = await this.client.createChat({
      workspace_id: persona?.workspace_id ?? this.appState.workspaces[0]?.id ?? null,
      persona_id: persona?.id ?? null,
      model: persona?.default_model ?? (settings.global_default_model || null),
      memory_mode: settings.default_memory_mode,
      title: 'New chat',
    });
    this.appState.chats = [chat, ...this.appState.chats.filter((item) => item.id !== chat.id)];
    this.appState.currentChat = chat;
    this.appState.messages = [];
    this.appState.capabilityRequests = [];
    this.appState.selectedPersonaId = chat.persona_id;
    this.appState.selectedModel = chat.model_override;
    this.appState.selectedMemoryMode = chat.memory_mode;
    this.onNavigate(chat.id);
    this.onChange();
    return chat;
  }

  async send(rawText: string): Promise<void> {
    const text = rawText.trim();
    if (!text || this.appState.pendingRequest) return;
    if (this.appState.phase === 'error') this.stateMachine.recover();
    if (!['idle', 'transcribing'].includes(this.appState.phase)) return;
    const chat = this.appState.currentChat ?? (await this.create(this.appState.selectedPersonaId));
    const settings = this.requiredSettings();
    const personaId = this.appState.selectedPersonaId ?? chat.persona_id;
    const persona = this.appState.personas.find((item) => item.id === personaId);
    const workspaceId = chat.workspace_id ?? persona?.workspace_id ?? null;
    const model = this.appState.selectedModel ?? chat.model_override ?? persona?.default_model ?? settings.global_default_model;
    const memoryMode = this.appState.selectedMemoryMode ?? chat.memory_mode ?? settings.default_memory_mode;
    const optimisticUser: Message = {
      id: clientId('local-user'),
      role: 'user',
      text,
      created_at: Math.floor(Date.now() / 1000),
    };
    const typing: Message = {
      id: clientId('typing'),
      role: 'assistant',
      text: '',
      created_at: optimisticUser.created_at,
      isTyping: true,
    };
    this.appState.messages.push(optimisticUser, typing);
    this.appState.draftMessage = '';
    this.appState.uiError = '';
    this.stateMachine.transition('queued');
    this.onChange();
    try {
      const accepted = await this.client.createTurn(chat.id, {
        text,
        workspace_id: workspaceId,
        persona_id: personaId,
        model: model || null,
        memory_mode: memoryMode,
        model_settings: modelSettings(settings, model || ''),
      });
      optimisticUser.id = accepted.turn.user_message_id;
      typing.id = `typing-${accepted.turn.id}`;
      const abort = new AbortController();
      this.streamAbort = abort;
      this.appState.pendingRequest = {
        jobId: accepted.job.id,
        turnId: accepted.turn.id,
        progress: accepted.job.progress || 'Queued',
        cancel: async () => {
          abort.abort();
          await this.client.cancelJob(accepted.job.id);
        },
      };
      await this.consumeTurn(accepted.turn.id, accepted.job.id, typing, abort.signal);
      const job = await this.client.job(accepted.job.id);
      if (job.status !== 'completed') throw new Error(job.error || `Turn ${job.status}`);
      const [detail, capabilities] = await Promise.all([
        this.client.chat(chat.id),
        this.client.capabilityRequests(chat.id),
      ]);
      this.appState.currentChat = detail.chat;
      this.appState.messages = detail.messages;
      this.appState.capabilityRequests = capabilities.items;
      this.mergeChat(detail.chat);
      this.stateMachine.transition('idle');
      const assistant = [...detail.messages].reverse().find((message) => message.role === 'assistant');
      if (assistant?.text.trim()) {
        try {
          await this.playback.synthesize(assistant.text, assistant.id, chat.id, personaId);
        } catch (error) {
          this.appState.uiError = errorMessage(error, 'The reply completed, but its audio could not be played.');
          await this.client.clientEvent('tts.playback_error', this.appState.uiError);
        }
      }
    } catch (error) {
      const aborted = error instanceof DOMException && error.name === 'AbortError';
      this.appState.messages = this.appState.messages.filter((message) => message !== typing);
      if (!aborted) {
        this.appState.uiError = errorMessage(error, 'The conversation turn failed.');
        this.stateMachine.transition('error');
      } else if (this.appState.phase !== 'idle') {
        this.stateMachine.transition('idle');
      }
    } finally {
      this.streamAbort = null;
      this.appState.pendingRequest = null;
      this.onChange();
    }
  }

  async cancel(): Promise<void> {
    const pending = this.appState.pendingRequest;
    if (!pending) return;
    await pending.cancel();
    this.streamAbort?.abort();
    this.appState.pendingRequest = null;
    if (['queued', 'thinking'].includes(this.appState.phase)) this.stateMachine.transition('idle');
    this.onChange();
  }

  async hide(chat: Chat): Promise<void> {
    await this.client.hideChat(chat.id);
    this.appState.chats = this.appState.chats.filter((item) => item.id !== chat.id);
    if (this.appState.currentChat?.id === chat.id) {
      this.appState.currentChat = null;
      this.appState.messages = [];
      this.appState.capabilityRequests = [];
    }
    this.onChange();
  }

  async rename(chat: Chat, title: string): Promise<void> {
    const updated = await this.client.updateChat(chat.id, { title: title.trim() });
    this.mergeChat(updated);
    if (this.appState.currentChat?.id === updated.id) this.appState.currentChat = updated;
    this.onChange();
  }

  private async consumeTurn(turnId: string, jobId: string, typing: Message, signal: AbortSignal): Promise<void> {
    try {
      await this.client.streamTurn(
        turnId,
        (event) => {
          this.applyTurnEvent(event, typing);
          this.onChange();
        },
        signal,
      );
    } catch (error) {
      if (signal.aborted) throw error;
      const job = await waitForJob(this.client, jobId, (current) => {
        if (this.appState.pendingRequest) this.appState.pendingRequest.progress = current.progress;
        this.onChange();
      });
      if (job.status !== 'completed') throw new Error(job.error || `Turn ${job.status}`);
    }
  }

  private applyTurnEvent(event: TurnEvent, typing: Message): void {
    if (event.event === 'turn.snapshot') {
      const accumulated = event.data.accumulated_text;
      if (typeof accumulated === 'string') typing.text = accumulated;
      const status = event.data.status;
      if (status === 'running' && this.appState.phase === 'queued') this.stateMachine.transition('thinking');
    } else if (event.event === 'turn.started') {
      if (this.appState.phase === 'queued') this.stateMachine.transition('thinking');
      if (this.appState.pendingRequest) this.appState.pendingRequest.progress = 'Thinking…';
    } else if (event.event === 'assistant.delta') {
      const delta = event.data.text;
      if (typeof delta === 'string') typing.text += delta;
      typing.isTyping = true;
    } else if (event.event === 'turn.failed') {
      const error = event.data.error;
      if (typeof error === 'object' && error !== null && 'message' in error) {
        throw new Error(String((error as { message: unknown }).message));
      }
    }
  }

  private mergeChat(chat: Chat): void {
    this.appState.chats = [chat, ...this.appState.chats.filter((item) => item.id !== chat.id)].sort(
      (left, right) => right.updated_at - left.updated_at,
    );
  }

  private requiredSettings() {
    if (!this.appState.settings) throw new Error('Settings are unavailable.');
    return this.appState.settings;
  }
}
