import { api, ApiError } from './api';
import { AuthView } from './auth_view';
import { ChatController } from './chat';
import { ChatRenderer, modelNickname } from './chat_rendering';
import { CapabilityController } from './capabilities';
import { DEFAULT_PERSONA_AVATAR } from './constants';
import { captureFocus, el, errorMessage, formatDate, restoreFocus } from './dom';
import { MediaController } from './media';
import { PlaybackController } from './playback';
import { RecordingController } from './recording';
import { Router } from './routing';
import { normalizeSettings, settingsWire } from './settings';
import { SettingsView, type Dialogs } from './settings_view';
import { machine, state } from './state';
import type { Chat, ModalState, RouteState, Session } from './types';
import { Visualizer } from './visualization';

const root = requiredElement<HTMLElement>('app');
const audio = requiredElement<HTMLAudioElement>('ttsAudio');
const visualizer = new Visualizer(audio);
const playback = new PlaybackController(audio, visualizer);
const media = new MediaController();
const chat = new ChatController(playback);
const recording = new RecordingController();
const dialogs = createDialogs();
const router = new Router((route) => void handleRoute(route));
const settingsView = new SettingsView(render, closeSettings, dialogs);
const authView = new AuthView(authenticated, render);
const chatRenderer = new ChatRenderer(media, playback, render);
const capabilities = new CapabilityController(render);

chat.configure({
  onChange: render,
  onNavigate: (chatId) => router.chat(chatId),
});
media.setChangeHandler(render);
playback.setChangeHandler(render);
recording.configure(render, (text) => chat.send(text));

window.addEventListener('nice:unauthorized', () => void signedOut('Your session ended. Please sign in again.'));
window.addEventListener('unhandledrejection', (event) => {
  event.preventDefault();
  reportUnhandled(event.reason);
});
window.addEventListener('error', (event) => reportUnhandled(event.error ?? event.message));
window.addEventListener('pointerdown', noteActivity, { passive: true });
window.addEventListener('keydown', handleKeydown);
window.visualViewport?.addEventListener('resize', syncViewportHeight);
window.addEventListener('resize', syncViewportHeight);
syncViewportHeight();
router.start();
void initialize();

async function initialize(): Promise<void> {
  try {
    await authenticated(await api.session(), false);
  } catch (error) {
    if (!(error instanceof ApiError && error.status === 401)) {
      state.authError = errorMessage(error, 'Unable to contact Nice Assistant.');
    }
    render();
  }
}

async function authenticated(session: Session, runOnboarding = true): Promise<void> {
  state.session = session;
  const [models, workspaces, personas, chats, wire, memories, taskModels, taskRuns, mediaCatalog] = await Promise.all([
    api.models().catch(() => ({ models: [] })),
    api.workspaces(),
    api.personas(),
    api.chats(),
    api.settings(),
    api.memories(),
    api.taskModels().catch(() => ({ items: [] })),
    api.taskModelRuns(undefined, 20).catch(() => ({ items: [] })),
    api.mediaCatalog().catch(() => null),
  ]);
  state.models = models.models;
  state.workspaces = workspaces.items;
  state.personas = personas.items;
  state.chats = chats.items;
  state.settings = normalizeSettings(wire);
  state.memories = memories.items;
  state.taskModels = taskModels.items;
  state.taskModelRuns = taskRuns.items;
  state.mediaCatalog = mediaCatalog;
  state.showSystemMessages = state.settings.general_show_system_messages;
  state.showThinkingByDefault = state.settings.general_show_thinking;
  state.voiceResponsesEnabled = state.settings.general_voice_responses;
  state.showViz = state.settings.general_show_viz;
  document.documentElement.dataset.theme = state.settings.general_theme;
  state.lastActivityAt = Date.now();
  armSessionTimer();

  const needsOnboarding = !state.settings.onboarding_done || !state.workspaces.length || !state.personas.length;
  if (needsOnboarding && runOnboarding) {
    machine.transition('onboarding');
    render();
    await onboarding();
  } else if (state.phase === 'signed_out' || state.phase === 'onboarding') {
    machine.transition('idle');
  }

  state.selectedPersonaId = state.selectedPersonaId ?? state.personas[0]?.id ?? null;
  state.newChatPersonaId = state.newChatPersonaId ?? state.selectedPersonaId;
  await applyCurrentRoute();
  render();
}

async function onboarding(): Promise<void> {
  state.onboardingRunning = true;
  try {
    let workspace = state.workspaces[0];
    if (!workspace) {
      const name = await dialogs.prompt('Welcome to Nice Assistant', 'Name your first workspace.', 'Main Workspace');
      if (!name?.trim()) throw new Error('First-run setup needs a workspace.');
      workspace = await api.createWorkspace(name.trim());
      state.workspaces.push(workspace);
    }
    let persona = state.personas[0];
    if (!persona) {
      const name = await dialogs.prompt('Create first persona', 'Give your assistant a persona name.', 'Assistant');
      if (!name?.trim()) throw new Error('First-run setup needs a persona.');
      const prompt = await dialogs.prompt('Default personality', 'Set the initial persona instruction.', 'Be helpful and concise.');
      persona = await api.createPersona({
        workspace_id: workspace.id,
        workspace_ids: [workspace.id],
        name: name.trim(),
        system_prompt: prompt?.trim() || 'Be helpful and concise.',
        default_model: state.models[0] ?? null,
      });
      state.personas.push(persona);
    }
    state.selectedPersonaId = persona.id;
    state.newChatPersonaId = persona.id;
    if (state.settings) {
      state.settings.onboarding_done = true;
      await api.updateSettings(settingsWire(state.settings));
    }
    machine.transition('idle');
  } catch (error) {
    state.uiError = errorMessage(error, 'Unable to complete first-run setup.');
    machine.transition('error');
  } finally {
    state.onboardingRunning = false;
    render();
  }
}

async function handleRoute(route: RouteState): Promise<void> {
  state.route = route;
  if (!state.session) return;
  await applyCurrentRoute();
  render();
}

async function applyCurrentRoute(): Promise<void> {
  if (!state.session) return;
  if (state.route.kind === 'settings') {
    state.showSettings = true;
    state.settingsSection = state.route.section ?? 'General';
    return;
  }
  state.showSettings = false;
  if (state.route.kind === 'chat' && state.route.chatId) {
    await chat.open(state.route.chatId);
    return;
  }
  const first = state.currentChat ?? state.chats[0];
  if (first && state.phase === 'idle') {
    await chat.open(first.id);
    router.chat(first.id, true);
  }
}

function render(): void {
  const focus = captureFocus(root);
  visualizer.setEnabled(state.showViz);
  root.replaceChildren();
  if (!state.session) {
    root.append(authView.node());
    restoreFocus(root, focus, Boolean(state.modal));
    return;
  }
  if (state.showSettings) root.append(settingsView.node());
  else root.append(shell());
  if (state.showNewChatPersonaModal) root.append(newChatModal());
  if (state.personaAvatarPreview) root.append(imageOverlay(state.personaAvatarPreview, 'Persona avatar', () => { state.personaAvatarPreview = ''; render(); }));
  if (state.chatImagePreview) root.append(imageOverlay(state.chatImagePreview, 'Generated image', () => { state.chatImagePreview = ''; render(); }));
  if (state.chatVideoPreview) root.append(videoOverlay(state.chatVideoPreview));
  if (state.modal) root.append(modalNode(state.modal));
  restoreFocus(root, focus, Boolean(state.modal));
  requestAnimationFrame(restoreMessageScroll);
}

function shell(): HTMLElement {
  const personaId = state.selectedPersonaId ?? state.currentChat?.persona_id ?? state.personas[0]?.id ?? null;
  const persona = state.personas.find((item) => item.id === personaId);
  const workspaceId = state.currentChat?.workspace_id ?? persona?.workspace_id ?? persona?.workspace_ids[0];
  const workspaceName = state.workspaces.find((item) => item.id === workspaceId)?.name ?? 'Workspace';
  const model = state.selectedModel ?? state.currentChat?.model_override ?? persona?.default_model ?? state.settings?.global_default_model ?? state.models[0] ?? '';
  const memoryMode = state.selectedMemoryMode ?? state.currentChat?.memory_mode ?? state.settings?.default_memory_mode ?? 'saved';
  const messages = [
    ...(state.showSystemMessages ? chatRenderer.syntheticSystemMessage(personaId) : []),
    ...state.messages,
  ];
  const messageNodes = messages.flatMap((message) => {
    const node = chatRenderer.message(message, personaId);
    if (!node) return [];
    const requests = state.capabilityRequests
      .filter(
        (request) =>
          request.permission_mode === 'confirm' && request.assistant_message_id === message.id,
      )
      .map((request) => capabilities.node(request));
    return [node, ...requests];
  });
  const unanchoredRequests = state.capabilityRequests
    .filter(
      (request) =>
        request.permission_mode === 'confirm' &&
        !messages.some((message) => message.id === request.assistant_message_id),
    )
    .map((request) => capabilities.node(request));
  const pane = el(
    'section',
    {
      id: 'messagesPane',
      class: 'message-pane glass',
      onscroll: onMessageScroll,
      'data-testid': 'message-pane',
    },
    [...messageNodes, ...unanchoredRequests],
  );
  return el('div', { class: 'app-shell' }, [
    drawer(),
    el('main', { class: 'main-pane glass' }, [
      topbar(persona?.name ?? 'Persona', persona?.avatar_url || DEFAULT_PERSONA_AVATAR, workspaceName, model),
      state.showChatControlsMenu ? chatControls(model, memoryMode) : null,
      pane,
      state.showJumpBottom
        ? el('button', { id: 'jumpBtn', class: 'jump-bottom show', textContent: '↓ Latest', onclick: () => scrollBottom(true) })
        : null,
      state.uiError
        ? el('div', { class: 'error-banner' }, [
            state.uiError,
            el('button', { class: 'icon-btn', textContent: 'Dismiss', onclick: () => { state.uiError = ''; if (state.phase === 'error') machine.recover(); render(); } }),
          ])
        : null,
      el('div', { class: 'record-indicator', textContent: state.phase === 'recording' ? `● Recording… ${Math.floor((Date.now() - state.recordingStartedAt) / 1000)}s` : 'Ready' }),
      composer(),
    ]),
    el('div', { class: `viz-wrap ${state.showViz ? 'show' : ''}` }, visualizer.node()),
  ]);
}

function drawer(): HTMLElement {
  const rows = state.chats
    .filter((item) => (item.title ?? '').toLowerCase().includes(state.chatSearch.toLowerCase()))
    .map((item) =>
      el('div', { class: `chat-row ${item.id === state.currentChat?.id ? 'active' : ''}`, onclick: () => { state.drawerOpen = false; router.chat(item.id); } }, [
        el('div', { class: 'title', textContent: item.title || 'Untitled chat' }),
        el('div', { class: 'meta', textContent: formatDate(item.updated_at || item.created_at) }),
        el('div', { class: 'chat-actions' }, [
          el('button', { class: 'icon-btn', textContent: '✎', title: 'Rename chat', onclick: (event: Event) => { event.stopPropagation(); void renameChat(item); } }),
          el('button', { class: 'icon-btn', textContent: '🗑', title: 'Hide chat', onclick: (event: Event) => { event.stopPropagation(); void hideChat(item); } }),
        ]),
      ]),
    );
  return el('aside', { class: `drawer glass ${state.drawerOpen ? 'open' : ''}` }, [
    el('div', { class: 'drawer-head' }, [
      el('strong', { textContent: 'Chats' }),
      el('button', { class: 'icon-btn', textContent: '✕', onclick: () => { state.drawerOpen = false; render(); } }),
    ]),
    el('button', { class: 'send-btn', textContent: '+ New Chat', 'data-testid': 'new-chat', onclick: () => { state.showNewChatPersonaModal = true; state.newChatPersonaId = state.selectedPersonaId ?? state.personas[0]?.id ?? null; render(); } }),
    el('input', { class: 'search-input', placeholder: 'Search chats…', value: state.chatSearch, oninput: (event: Event) => { state.chatSearch = (event.currentTarget as HTMLInputElement).value; render(); } }),
    el('div', { class: 'drawer-list' }, rows.length ? rows : el('div', { class: 'meta', textContent: 'No chats yet.' })),
  ]);
}

function topbar(personaName: string, avatar: string, workspace: string, model: string): HTMLElement {
  return el('div', { class: 'topbar' }, [
    el('button', { class: 'icon-btn', textContent: '☰', onclick: () => { state.drawerOpen = !state.drawerOpen; render(); } }),
    el('div', { class: 'header-meta' }, [
      el('img', { class: 'topbar-avatar', src: avatar, alt: `${personaName} avatar`, onclick: () => { state.personaAvatarPreview = avatar; render(); } }),
      el('div', { class: 'header-title', textContent: state.currentChat?.title || 'New conversation' }),
      el('div', { class: 'chips' }, [
        el('button', { class: 'chip', textContent: personaName }),
        el('button', { class: 'chip', textContent: workspace }),
        el('button', { class: 'chip', textContent: modelNickname(model || 'model') }),
      ]),
    ]),
    el('div', { class: `status-pill ${statusClass()}`, textContent: state.statusText, 'data-testid': 'client-phase' }),
    el('button', { class: 'icon-btn', textContent: '⋯', title: 'Chat controls', onclick: () => { state.showChatControlsMenu = !state.showChatControlsMenu; render(); } }),
    el('button', { class: `viz-btn ${state.showViz ? 'active' : ''}`, textContent: state.showViz ? '✦ Visualizer On' : '✧ Visualizer', onclick: () => { state.showViz = !state.showViz; if (state.settings) state.settings.general_show_viz = state.showViz; render(); } }),
    el('button', { class: 'icon-btn', textContent: '⚙', title: 'Settings', 'data-testid': 'open-settings', onclick: () => router.settings(state.settingsSection) }),
    el('button', { class: 'icon-btn', textContent: '↪', title: 'Log out', 'data-testid': 'logout', onclick: () => void logout() }),
  ]);
}

function chatControls(model: string, memoryMode: string): HTMLElement {
  return el('div', { class: 'chat-controls-menu glass' }, [
    el('label', { textContent: 'Model' }),
    el(
      'select',
      { class: 'chip-select', value: model, onchange: (event: Event) => { state.selectedModel = (event.currentTarget as HTMLSelectElement).value; } },
      state.models.map((item) => el('option', { value: item, selected: item === model, textContent: modelNickname(item) })),
    ),
    el('label', { textContent: 'Memory mode' }),
    el('select', { class: 'chip-select', value: memoryMode, onchange: (event: Event) => { state.selectedMemoryMode = (event.currentTarget as HTMLSelectElement).value === 'off' ? 'off' : 'saved'; } }, [
      el('option', { value: 'saved', selected: memoryMode !== 'off', textContent: 'Memory: saved' }),
      el('option', { value: 'off', selected: memoryMode === 'off', textContent: 'Memory: off' }),
    ]),
    el('button', { class: 'pill-btn', textContent: state.showSystemMessages ? 'Hide system/tool' : 'Show system/tool', onclick: () => { state.showSystemMessages = !state.showSystemMessages; render(); } }),
    el('button', { class: 'pill-btn', textContent: state.showThinkingByDefault ? 'Hide thinking' : 'Show thinking', onclick: () => { state.showThinkingByDefault = !state.showThinkingByDefault; render(); } }),
    el('button', { class: 'pill-btn', textContent: state.voiceResponsesEnabled ? 'Voice replies: On' : 'Voice replies: Off', onclick: () => { state.voiceResponsesEnabled = !state.voiceResponsesEnabled; render(); } }),
    state.currentAudioMessageId ? el('button', { class: 'pill-btn', textContent: 'Stop audio', onclick: () => playback.stop() }) : null,
  ]);
}

function composer(): HTMLElement {
  const busy = ['queued', 'thinking', 'transcribing', 'speaking'].includes(state.phase);
  const inputLocked = ['transcribing', 'speaking'].includes(state.phase);
  return el('div', { class: 'composer' }, [
    el('textarea', {
      id: 'chatInput',
      rows: 1,
      class: 'composer-input',
      value: state.draftMessage,
      placeholder: 'Ask anything… (Shift+Enter for new line)',
      disabled: inputLocked,
      'data-testid': 'chat-input',
      oninput: (event: Event) => {
        const target = event.currentTarget as HTMLTextAreaElement;
        state.draftMessage = target.value;
        autoResize(target);
      },
      onkeydown: (event: KeyboardEvent) => {
        if (event.key === 'Enter' && !event.shiftKey) {
          event.preventDefault();
          void chat.send((event.currentTarget as HTMLTextAreaElement).value);
        }
      },
    }),
    state.pendingRequest
      ? el('button', {
          class: 'send-btn',
          textContent: 'Cancel',
          'data-testid': 'chat-cancel',
          onclick: () => void chat.cancel(),
        })
      : el('button', {
          class: 'send-btn',
          textContent: 'Send',
          disabled: busy,
          'data-testid': 'chat-send',
          onclick: () => void chat.send(state.draftMessage),
        }),
    el('button', {
      class: `talk-btn ${state.phase === 'recording' ? 'active' : ''}`,
      textContent: state.phase === 'recording' ? `Recording ${Math.floor((Date.now() - state.recordingStartedAt) / 1000)}s` : state.phase === 'transcribing' ? 'Transcribing…' : 'Hold to Talk',
      disabled: busy && state.phase !== 'recording',
      onpointerdown: () => { playback.stop(false); void recording.start(); },
      onpointerup: () => void recording.stop(),
      onpointercancel: () => void recording.stop(),
      onpointerleave: (event: PointerEvent) => { if (event.buttons === 1) void recording.stop(); },
    }),
  ]);
}

function newChatModal(): HTMLElement {
  return el('div', { class: 'modal-backdrop' }, [
    el('div', { class: 'modal-card glass', role: 'dialog', 'aria-modal': 'true' }, [
      el('h3', { textContent: 'Start a new chat' }),
      el('label', { textContent: 'Persona' }),
      el(
        'select',
        { class: 'chip-select', value: state.newChatPersonaId ?? '', 'data-testid': 'new-chat-persona', onchange: (event: Event) => { state.newChatPersonaId = (event.currentTarget as HTMLSelectElement).value || null; } },
        state.personas.map((persona) => el('option', { value: persona.id, selected: persona.id === state.newChatPersonaId, textContent: persona.name })),
      ),
      el('div', { class: 'modal-actions' }, [
        el('button', { class: 'pill-btn', textContent: 'Cancel', onclick: () => { state.showNewChatPersonaModal = false; render(); } }),
        el('button', { class: 'send-btn', textContent: 'Create chat', 'data-testid': 'new-chat-confirm', onclick: () => void createNewChat() }),
      ]),
    ]),
  ]);
}

async function createNewChat(): Promise<void> {
  state.showNewChatPersonaModal = false;
  await chat.create(state.newChatPersonaId);
  render();
}

function modalNode(modal: ModalState): HTMLElement {
  return el('div', { class: 'modal-backdrop' }, [
    el('div', { class: 'modal-card glass', role: 'dialog', 'aria-modal': 'true', 'aria-label': modal.title }, [
      el('h3', { textContent: modal.title }),
      modal.message ? el('div', { class: 'meta modal-message', textContent: modal.message }) : null,
      modal.inputValue !== undefined
        ? el('input', { class: 'search-input', value: modal.inputValue, placeholder: modal.inputPlaceholder ?? '', autofocus: true, oninput: (event: Event) => { if (state.modal) state.modal.inputValue = (event.currentTarget as HTMLInputElement).value; } })
        : null,
      el('div', { class: 'modal-actions' }, modal.actions.map((action) =>
        el('button', { class: action.kind === 'primary' ? 'send-btn' : action.kind === 'danger' ? 'send-btn danger' : 'pill-btn', textContent: action.label, onclick: () => void action.run(state.modal?.inputValue ?? '') }),
      )),
    ]),
  ]);
}

function imageOverlay(url: string, alt: string, close: () => void): HTMLElement {
  return el('div', { class: 'preview-backdrop', onclick: close }, [
    el('div', { class: 'preview-card', onclick: (event: Event) => event.stopPropagation() }, [
      el('button', { class: 'icon-btn avatar-preview-close', textContent: '✕', onclick: close }),
      el('img', { class: 'preview-image', src: url, alt }),
    ]),
  ]);
}

function videoOverlay(url: string): HTMLElement {
  return el('div', { class: 'preview-backdrop', onclick: () => { state.chatVideoPreview = ''; render(); } }, [
    el('div', { class: 'preview-card video-preview-card', onclick: (event: Event) => event.stopPropagation() }, [
      el('button', { class: 'icon-btn avatar-preview-close', textContent: '✕', onclick: () => { state.chatVideoPreview = ''; render(); } }),
      el('video', { class: 'preview-video', src: url, controls: true, autoplay: true }),
    ]),
  ]);
}

async function renameChat(item: Chat): Promise<void> {
  const title = await dialogs.prompt('Rename chat', 'Choose a new title.', item.title ?? '');
  if (title?.trim()) await chat.rename(item, title);
}

async function hideChat(item: Chat): Promise<void> {
  if (!(await dialogs.confirm('Hide chat', `Hide ${item.title || 'this chat'}?`, 'Hide'))) return;
  await chat.hide(item);
  router.home(true);
}

function closeSettings(): void {
  state.showSettings = false;
  if (state.currentChat) router.chat(state.currentChat.id);
  else router.home();
}

async function signedOut(message = ''): Promise<void> {
  playback.stop(false);
  recording.cancel();
  state.session = null;
  state.currentChat = null;
  state.messages = [];
  state.capabilityRequests = [];
  state.mediaCatalog = null;
  state.mediaPlanPreview = null;
  state.resourceCoordination = null;
  state.resourceCoordinationEvents = [];
  state.pendingRequest = null;
  state.authError = message;
  state.modal = null;
  if (state.phase !== 'signed_out') machine.transition('signed_out');
  router.home(true);
  render();
}

async function logout(): Promise<void> {
  try {
    await api.logout();
  } finally {
    await signedOut();
  }
}

function createDialogs(): Dialogs {
  return {
    prompt(title, message, initial = '') {
      return new Promise((resolve) => {
        state.modal = {
          title,
          message,
          inputValue: initial,
          actions: [
            { label: 'Cancel', run: () => { state.modal = null; resolve(null); render(); } },
            { label: 'Continue', kind: 'primary', run: (value) => { state.modal = null; resolve(value); render(); } },
          ],
        };
        render();
      });
    },
    confirm(title, message, confirmText = 'Confirm') {
      return new Promise((resolve) => {
        state.modal = {
          title,
          message,
          actions: [
            { label: 'Cancel', run: () => { state.modal = null; resolve(false); render(); } },
            { label: confirmText, kind: 'danger', run: () => { state.modal = null; resolve(true); render(); } },
          ],
        };
        render();
      });
    },
    info(title, message) {
      state.modal = { title, message, actions: [{ label: 'Close', kind: 'primary', run: () => { state.modal = null; render(); } }] };
      render();
    },
  };
}

function statusClass(): string {
  if (state.phase === 'recording') return 'status-recording';
  if (state.phase === 'speaking') return 'status-speaking';
  if (['queued', 'thinking', 'transcribing'].includes(state.phase)) return 'status-thinking';
  return 'status-idle';
}

function onMessageScroll(event: Event): void {
  const pane = event.currentTarget as HTMLElement;
  state.showJumpBottom = pane.scrollTop + pane.clientHeight < pane.scrollHeight - 130;
  state.stickMessagesToBottom = !state.showJumpBottom;
}

function restoreMessageScroll(): void {
  if (state.stickMessagesToBottom) scrollBottom(false);
}

function scrollBottom(smooth: boolean): void {
  const pane = document.querySelector<HTMLElement>('#messagesPane');
  pane?.scrollTo({ top: pane.scrollHeight, behavior: smooth ? 'smooth' : 'auto' });
  state.stickMessagesToBottom = true;
  state.showJumpBottom = false;
}

function autoResize(target: HTMLTextAreaElement): void {
  target.style.height = 'auto';
  target.style.height = `${Math.min(180, target.scrollHeight)}px`;
}

function handleKeydown(event: KeyboardEvent): void {
  if (event.key !== 'Escape') return;
  if (state.modal) {
    const cancel = state.modal.actions.find((action) => action.label === 'Cancel') ?? state.modal.actions[0];
    if (cancel) void cancel.run(state.modal.inputValue ?? '');
  } else if (state.showNewChatPersonaModal) {
    state.showNewChatPersonaModal = false;
    render();
  } else if (state.chatImagePreview || state.chatVideoPreview || state.personaAvatarPreview) {
    state.chatImagePreview = '';
    state.chatVideoPreview = '';
    state.personaAvatarPreview = '';
    render();
  } else if (state.showSettings) closeSettings();
}

function noteActivity(): void {
  state.lastActivityAt = Date.now();
}

function armSessionTimer(): void {
  if (state.sessionTimer !== null) window.clearInterval(state.sessionTimer);
  state.sessionTimer = window.setInterval(() => {
    if (!state.session || !state.settings?.general_auto_logout) return;
    if (Date.now() - state.lastActivityAt >= state.session.ttl_seconds * 1000) void logout();
  }, 15_000);
}

function syncViewportHeight(): void {
  const height = window.visualViewport?.height ?? window.innerHeight;
  document.documentElement.style.setProperty('--app-viewport-height', `${Math.round(height)}px`);
}

function reportUnhandled(reason: unknown): void {
  const message = errorMessage(reason, 'The browser encountered an unexpected error.');
  state.uiError = message;
  machine.transition('error', 'Error');
  void api.clientEvent('browser.unhandled', message);
  render();
}

function requiredElement<T extends HTMLElement>(id: string): T {
  const node = document.getElementById(id);
  if (!(node instanceof HTMLElement)) throw new Error(`Missing #${id}`);
  return node as T;
}
