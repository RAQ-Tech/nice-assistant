import { api, type ApiClient } from './api';
import { clientId } from './client_id';
import { errorMessage } from './dom';
import { waitForJob } from './media';
import { SETTINGS_DEFAULTS, modelSettings } from './settings';
import { clearIdentitySetupContextForChat, machine, state, type ClientStateMachine } from './state';
import type { TurnInput } from './api';
import type { AppState, CapabilityRequest, Chat, Job, Message, TurnAccepted, TurnEvent } from './types';
import type { PlaybackController } from './playback';
import { ReplySpeaker } from './reply_speaker';

// Tries at picking a dropped reply stream back up before waiting for the result instead.
const STREAM_RESUME_ATTEMPTS = 3;
const TERMINAL_TURN_EVENTS = new Set(['turn.completed', 'turn.failed', 'turn.cancelled']);

export class ChatController {
  private onChange: () => void = () => undefined;
  private onStreamChange: () => void = () => undefined;
  private onNavigate: (chatId: string) => void = () => undefined;
  private streamAbort: AbortController | null = null;
  private speaker: ReplySpeaker | null = null;
  private greetFor: string | null = null;
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
    /** For each streamed word: may wait for the next frame rather than redraw for every one. */
    onStreamChange?: () => void;
  }): void {
    this.onChange = options.onChange;
    this.onNavigate = options.onNavigate;
    this.onStreamChange = options.onStreamChange ?? options.onChange;
  }

  async open(chatId: string): Promise<void> {
    // Arriving at a conversation covers its pictures again. A reveal is for the
    // moment somebody asked to look, not for every time they come back.
    if (this.appState.currentChat?.id !== chatId) {
      this.appState.revealedImages = {};
      this.appState.coverNewestPending = true;
      this.appState.coveredImageSrc = null;
    }
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
      if (this.greetFor === chatId) {
        this.greetFor = null;
        if (!detail.messages.length) void this.greet(detail.chat);
      }
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

  /** Have the persona speak first once this new conversation is open (ADR 0052). */
  greetWhenOpened(chatId: string): void {
    this.greetFor = chatId;
  }

  /** Send a message, with a photo when one was chosen (ADR 0046). */
  async send(rawText: string, photo: string | null = null): Promise<void> {
    const text = rawText.trim();
    if ((!text && !photo) || !this.readyForTurn()) return;
    const chat = this.appState.currentChat ?? (await this.create(this.appState.selectedPersonaId));
    await this.runTurn(chat, text, (input) => this.client.createTurn(chat.id, { ...input, photo }), photo);
  }

  /**
   * Answer the latest exchange again: try a failed reply again, ask for a
   * different reply, or - with `rawText` - send what the message is corrected
   * to. The server refuses anything but the latest exchange.
   */
  async again(messageId: string, rawText?: string): Promise<void> {
    const chat = this.appState.currentChat;
    const start = this.appState.messages.findLastIndex((message) => message.role === 'user');
    const said = this.appState.messages[start];
    const corrected = rawText?.trim() || null;
    const text = corrected ?? said?.text ?? '';
    // Nothing said yet: the reply is the persona's greeting, and it greets again.
    const greeting = !said && this.appState.messages.some((message) => message.role === 'assistant');
    if (!chat || (!greeting && !text) || !this.readyForTurn()) return;
    this.appState.editingMessageId = null;
    this.appState.messages = this.appState.messages.slice(0, Math.max(start, 0));
    await this.runTurn(chat, greeting ? null : text, (input) => this.client.answerAgain(chat.id, messageId, { ...input, text: corrected }));
  }

  /** Delete a message and the reply it belongs with. */
  async deleteExchange(messageId: string): Promise<void> {
    const chat = this.appState.currentChat;
    if (!chat) return;
    try {
      await this.client.deleteMessage(chat.id, messageId);
      await this.reconcileChat(chat.id);
    } catch (error) {
      this.appState.uiError = errorMessage(error, 'That message could not be deleted.');
    }
    this.onChange();
  }

  /** Catch up with anything that happened while the page was not being looked at. */
  async refresh(): Promise<void> {
    const chat = this.appState.currentChat;
    if (!chat || this.appState.pendingRequest || this.appState.phase !== 'idle') return;
    try {
      await this.reconcileChat(chat.id);
      this.onChange();
    } catch {
      // Still the last known conversation; the next action reconciles it.
    }
  }

  /** The persona's first words. If they cannot be had, nothing is said: the chat still invites the first word. */
  private async greet(chat: Chat): Promise<void> {
    if (!this.readyForTurn()) return;
    await this.runTurn(chat, null, (input) => this.client.greet(chat.id, input));
  }

  private readyForTurn(): boolean {
    // Speech while the reply is still being written is not barge-in: the
    // stop control ends the turn and its speech together. Once the reply is
    // complete, typing over the voice stops it, as before.
    if (this.appState.phase === 'speaking' && !this.appState.pendingRequest) this.playback.stop(false);
    if (this.appState.pendingRequest) return false;
    if (this.appState.phase === 'error') this.stateMachine.recover();
    return ['idle', 'transcribing'].includes(this.appState.phase);
  }

  /** One reply, streamed and spoken. `text` null is the persona speaking first, with nothing said. */
  private async runTurn(
    chat: Chat,
    text: string | null,
    accept: (input: TurnInput) => Promise<TurnAccepted>,
    photo: string | null = null,
  ): Promise<void> {
    const settings = this.requiredSettings();
    const persona = this.appState.personas.find((item) => item.id === chat.persona_id);
    const model = this.appState.selectedModel ?? chat.model_override ?? persona?.default_model ?? settings.global_default_model;
    const memoryMode = this.appState.selectedMemoryMode ?? chat.memory_mode ?? settings.default_memory_mode;
    const now = Math.floor(Date.now() / 1000);
    const optimisticUser: Message | null = text === null ? null : {
      id: clientId('local-user'),
      role: 'user',
      text,
      created_at: now,
      ...(photo ? { photos: [{ id: 'local-photo', url: photo, width: 0, height: 0 }] } : {}),
    };
    const typing: Message = {
      id: clientId('typing'),
      role: 'assistant',
      text: '',
      created_at: now,
      isTyping: true,
    };
    this.appState.messages.push(...(optimisticUser ? [optimisticUser] : []), typing);
    if (optimisticUser) {
      this.appState.draftMessage = '';
      this.appState.draftPhoto = null;
    }
    this.appState.uiError = '';
    this.stateMachine.transition('queued');
    this.onChange();
    let ownedJobId: string | null = null;
    let ownedAbort: AbortController | null = null;
    let reachedServer = false;
    try {
      const accepted = await accept({
        text: text ?? '',
        model: model || null,
        memory_mode: memoryMode,
        model_settings: modelSettings(settings, model || ''),
      });
      reachedServer = true;
      if (optimisticUser && accepted.turn.user_message_id) optimisticUser.id = accepted.turn.user_message_id;
      typing.id = `typing-${accepted.turn.id}`;
      const abort = new AbortController();
      ownedJobId = accepted.job.id;
      ownedAbort = abort;
      this.streamAbort = abort;
      // Each finished sentence is spoken as it is written (ADR 0042); a
      // format that cannot start early speaks the completed reply below.
      this.speaker?.stop();
      this.speaker = this.playback.beginPieces(typing.id)
        ? new ReplySpeaker(this.playback, this.client, chat.id, chat.persona_id, settings.tts_format || SETTINGS_DEFAULTS.tts_format)
        : null;
      this.appState.pendingRequest = {
        jobId: accepted.job.id,
        turnId: accepted.turn.id,
        progress: accepted.job.progress || 'Queued',
        cancel: async () => {
          abort.abort();
          this.speaker?.stop();
          await this.client.cancelJob(accepted.job.id);
        },
      };
      void this.reconcileAcceptedTitle(chat.id);
      await this.consumeTurn(accepted.turn.id, accepted.job.id, typing, abort.signal);
      const job = await this.client.job(accepted.job.id);
      if (job.status !== 'completed') throw new Error(job.error || `Turn ${job.status}`);
      const detail = await this.reconcileChat(chat.id);
      this.releaseRequest(ownedJobId, ownedAbort);
      // A reply already being spoken keeps its phase; playback ends it.
      if (this.appState.phase !== 'speaking') this.stateMachine.transition('idle');
      this.onChange();
      const followupJobIds = Array.isArray(job.result?.followup_job_ids)
        ? job.result.followup_job_ids.filter((item): item is string => typeof item === 'string' && Boolean(item))
        : typeof job.result?.followup_job_id === 'string' && job.result.followup_job_id
          ? [job.result.followup_job_id]
          : [];
      followupJobIds.forEach((followupJobId) => void this.reconcileFollowup(chat.id, followupJobId));
      const assistant = [...detail.messages].reverse().find((message) => message.role === 'assistant');
      const speaker = this.speaker;
      this.speaker = null;
      if (assistant?.text.trim()) {
        try {
          if (speaker) await speaker.finish(assistant.text, assistant.id);
          if (!speaker?.spokeAnything) await this.playback.synthesize(assistant.text, assistant.id, chat.id, chat.persona_id);
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
      this.speaker?.stop();
      this.speaker = null;
      const aborted = ownedAbort?.signal.aborted || (error instanceof DOMException && error.name === 'AbortError');
      this.appState.messages = this.appState.messages.filter((message) => message !== typing);
      // Never accepted, so nothing was kept: put the words back where they
      // were typed rather than lose them with the failure.
      if (!reachedServer && text && !this.appState.draftMessage) this.appState.draftMessage = text;
      if (!reachedServer && photo && !this.appState.draftPhoto) this.appState.draftPhoto = photo;
      try {
        await this.reconcileChat(chat.id);
      } catch {
        // Preserve the original turn outcome when metadata reconciliation is unavailable.
      }
      // A greeting that could not be made is not a failure anybody needs to see.
      if (!aborted && text !== null) {
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

  /**
   * Follow the reply as it is written, through a dropped connection.
   *
   * A phone that locks or a Wi-Fi blip used to end the live text and the
   * sentence-by-sentence speech, and a connection that closed cleanly midway
   * was taken for a finished turn and reported as failed. The stream is
   * resumed from the last event it delivered instead - the server keeps a
   * short replay for exactly this - and only after a few failed attempts does
   * this fall back to waiting for the finished result.
   */
  private async consumeTurn(turnId: string, jobId: string, typing: Message, signal: AbortSignal): Promise<void> {
    let lastEventId: number | undefined;
    let finished = false;
    for (let attempt = 0; attempt <= STREAM_RESUME_ATTEMPTS && !finished; attempt += 1) {
      if (attempt) await new Promise((resolve) => window.setTimeout(resolve, 400 * attempt));
      try {
        await this.client.streamTurn(
          turnId,
          (event) => {
            if (event.id !== null) lastEventId = event.id;
            if (TERMINAL_TURN_EVENTS.has(event.event)) finished = true;
            this.applyTurnEvent(event, typing);
            this.onStreamChange();
          },
          signal,
          lastEventId,
        );
      } catch (error) {
        if (signal.aborted || finished) throw error;
      }
      if (finished || signal.aborted) break;
      // The stream ended without saying how the turn did. If the turn is over
      // anyway, there is nothing to resume.
      const current = await this.client.job(jobId).catch(() => null);
      if (current && ['completed', 'failed', 'cancelled'].includes(current.status)) return;
    }
    if (finished || signal.aborted) return;
    const job = await waitForJob(this.client, jobId, (current) => {
      if (this.appState.pendingRequest) this.appState.pendingRequest.progress = current.progress;
      this.onChange();
    });
    if (job.status !== 'completed') throw new Error(job.error || `Turn ${job.status}`);
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
      this.speaker?.observe(typing.text);
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
      let seen = '';
      while (this.appState.currentChat?.id === chatId) {
        const current = await this.client.capabilityRequest(requestId);
        this.appState.capabilityRequests = [
          ...this.appState.capabilityRequests.filter((item) => item.id !== current.id),
          current,
        ].sort((left, right) => left.requested_at - right.requested_at);
        // The whole conversation used to be fetched and redrawn twice a second
        // while a picture rendered. Only a change in the picture's state can
        // change what the conversation shows.
        const state = JSON.stringify([current.status, current.started_at, current.completed_at, current.attachment]);
        if (state !== seen) {
          seen = state;
          const detail = await this.client.chat(chatId);
          if (this.appState.currentChat?.id !== chatId) break;
          this.appState.currentChat = detail.chat;
          this.appState.messages = detail.messages;
          this.onChange();
        }
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
