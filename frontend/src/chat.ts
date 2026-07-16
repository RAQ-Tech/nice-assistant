import { api, type ApiClient } from './api';
import { clientId } from './client_id';
import { errorMessage } from './dom';
import { waitForJob } from './media';
import { modelSettings } from './settings';
import { clearIdentitySetupContextForChat, machine, state, type ClientStateMachine } from './state';
import type { AppState, CapabilityRequest, Chat, Job, Message, TurnEvent } from './types';
import type { PlaybackController } from './playback';

export class ChatController {
  private onChange: () => void = () => undefined;
  private onNavigate: (chatId: string) => void = () => undefined;
  private streamAbort: AbortController | null = null;
  private readonly capabilityPolls = new Set<string>();

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
      clearIdentitySetupContextForChat(this.appState, detail.chat.id);
      this.appState.currentChat = detail.chat;
      this.appState.messages = detail.messages;
      this.appState.capabilityRequests = capabilities.items;
      this.appState.selectedPersonaId = detail.chat.persona_id;
      this.appState.selectedModel = detail.chat.model_override;
      this.appState.selectedMemoryMode = detail.chat.memory_mode;
      this.appState.uiError = '';
      this.stateMachine.transition('idle');
      this.resumeCapabilities(detail.chat.id, capabilities.items);
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
    clearIdentitySetupContextForChat(this.appState, chat.id);
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
    if (!text) return;
    if (this.appState.phase === 'speaking') this.playback.stop(false);
    if (this.appState.pendingRequest) return;
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
    let ownedJobId: string | null = null;
    let ownedAbort: AbortController | null = null;
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
      ownedJobId = accepted.job.id;
      ownedAbort = abort;
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
      void this.reconcileAcceptedTitle(chat.id);
      await this.consumeTurn(accepted.turn.id, accepted.job.id, typing, abort.signal);
      const job = await this.client.job(accepted.job.id);
      if (job.status !== 'completed') throw new Error(job.error || `Turn ${job.status}`);
      const detail = await this.reconcileChat(chat.id);
      this.releaseRequest(ownedJobId, ownedAbort);
      this.stateMachine.transition('idle');
      this.onChange();
      const followupJobIds = Array.isArray(job.result?.followup_job_ids)
        ? job.result.followup_job_ids.filter((item): item is string => typeof item === 'string' && Boolean(item))
        : typeof job.result?.followup_job_id === 'string' && job.result.followup_job_id
          ? [job.result.followup_job_id]
          : [];
      followupJobIds.forEach((followupJobId) => void this.reconcileFollowup(chat.id, followupJobId));
      const assistant = [...detail.messages].reverse().find((message) => message.role === 'assistant');
      if (assistant?.text.trim()) {
        try {
          await this.playback.synthesize(assistant.text, assistant.id, chat.id, personaId);
        } catch (error) {
          const message = errorMessage(error, 'Audio could not be played.');
          this.appState.messageAudioErrors[assistant.id] = message;
          try {
            await this.client.clientEvent('tts.playback_error', message);
          } catch {
            // The reply and its compact playback error remain usable if telemetry is unavailable.
          }
          this.onChange();
        }
      }
    } catch (error) {
      const aborted = ownedAbort?.signal.aborted || (error instanceof DOMException && error.name === 'AbortError');
      this.appState.messages = this.appState.messages.filter((message) => message !== typing);
      try {
        await this.reconcileChat(chat.id);
      } catch {
        // Preserve the original turn outcome when metadata reconciliation is unavailable.
      }
      if (!aborted) {
        this.appState.uiError = errorMessage(error, 'The conversation turn failed.');
        this.stateMachine.transition('error');
      } else if (this.appState.phase !== 'idle') {
        this.stateMachine.transition('idle');
      }
    } finally {
      this.releaseRequest(ownedJobId, ownedAbort);
      this.onChange();
    }
  }

  async cancel(): Promise<void> {
    const pending = this.appState.pendingRequest;
    if (!pending) return;
    try {
      await pending.cancel();
      this.streamAbort?.abort();
      if (this.appState.pendingRequest?.jobId === pending.jobId) this.appState.pendingRequest = null;
      if (['queued', 'thinking'].includes(this.appState.phase)) this.stateMachine.transition('idle');
    } catch (error) {
      this.appState.uiError = errorMessage(error, 'The current reply could not be canceled.');
    }
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

  private async reconcileChat(chatId: string) {
    const detail = await this.client.chat(chatId);
    if (this.appState.currentChat?.id === chatId) {
      this.appState.currentChat = detail.chat;
      this.appState.messages = detail.messages;
    }
    this.mergeChat(detail.chat);
    try {
      const capabilities = await this.client.capabilityRequests(chatId);
      if (this.appState.currentChat?.id === chatId) {
        this.appState.capabilityRequests = capabilities.items;
        this.resumeCapabilities(chatId, capabilities.items);
      }
    } catch {
      // Capability reconciliation is nonessential to the delivered conversation reply.
    }
    return detail;
  }

  private async reconcileAcceptedTitle(chatId: string): Promise<void> {
    try {
      const detail = await this.client.chat(chatId);
      if (this.appState.currentChat?.id === chatId) this.appState.currentChat = detail.chat;
      this.mergeChat(detail.chat);
      this.onChange();
    } catch {
      // Success, cancellation, and failure paths make another reconciliation attempt.
    }
  }

  private async reconcileFollowup(chatId: string, jobId: string): Promise<void> {
    try {
      await waitForJob(this.client, jobId);
      await this.reconcileChat(chatId);
      this.onChange();
    } catch {
      // Title and capability follow-up must never invalidate an already delivered reply.
    }
  }

  private releaseRequest(jobId: string | null, abort: AbortController | null): void {
    if (jobId && this.appState.pendingRequest?.jobId === jobId) this.appState.pendingRequest = null;
    if (abort && this.streamAbort === abort) this.streamAbort = null;
  }

  private resumeCapabilities(chatId: string, requests: CapabilityRequest[]): void {
    requests
      .filter((request) => request.attachment && ['queued', 'running'].includes(request.status))
      .forEach((request) => {
        if (this.capabilityPolls.has(request.id)) return;
        this.capabilityPolls.add(request.id);
        void this.pollCapability(chatId, request.id);
      });
  }

  private async pollCapability(chatId: string, requestId: string): Promise<void> {
    try {
      while (this.appState.currentChat?.id === chatId) {
        const current = await this.client.capabilityRequest(requestId);
        this.appState.capabilityRequests = [
          ...this.appState.capabilityRequests.filter((item) => item.id !== current.id),
          current,
        ].sort((left, right) => left.requested_at - right.requested_at);
        const detail = await this.client.chat(chatId);
        if (this.appState.currentChat?.id !== chatId) break;
        this.appState.currentChat = detail.chat;
        this.appState.messages = detail.messages;
        this.onChange();
        if (!['queued', 'running'].includes(current.status)) break;
        await new Promise((resolve) => window.setTimeout(resolve, 500));
      }
    } catch {
      // Durable server state is reloaded on the next chat open; polling failure is not a generation failure.
    } finally {
      this.capabilityPolls.delete(requestId);
    }
  }

  private requiredSettings() {
    if (!this.appState.settings) throw new Error('Settings are unavailable.');
    return this.appState.settings;
  }
}
