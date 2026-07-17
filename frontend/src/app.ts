import { api, ApiError } from './api';
import { AuthView } from './auth_view';
import { ChatController } from './chat';
import { ChatDrawer } from './chat_drawer';
import { ChatRenderer, modelNickname } from './chat_rendering';
import { CapabilityController } from './capabilities';
import { composerState } from './composer_state';
import { DEFAULT_PERSONA_AVATAR } from './constants';
import { captureFocus, el, errorMessage, restoreFocus } from './dom';
import { MediaController } from './media';
import { PlaybackController } from './playback';
import { RecordingController } from './recording';
import { Router } from './routing';
import { normalizeSettings, settingsWire } from './settings';
import { SettingsView, type Dialogs } from './settings_view';
import { clearIdentitySetupContext, machine, state } from './state';
import type { ModalState, RouteState, Session } from './types';
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
const settingsView = new SettingsView(render, closeSettings, dialogs, state, api, (section) => router.settings(section));
const authView = new AuthView(authenticated, render);
const chatRenderer = new ChatRenderer(
  media,
  playback,
  render,
  state,
  api,
  (content) => dialogs.prompt(
    'Propose a memory fact',
    'Edit this into one specific fact. It will stay pending until you approve it in Memory settings.',
    content,
  ),
);
const capabilities = new CapabilityController(render, state, machine, api, (intent) => settingsView.startIdentitySetup(intent));
const chatDrawer = new ChatDrawer(state, api, chat, dialogs, {
  render,
  openChat: (chatId) => router.chat(chatId),
  openNewChat: () => {
    state.showNewChatPersonaModal = true;
    state.newChatPersonaId = state.selectedPersonaId ?? state.personas[0]?.id ?? null;
    render();
  },
  goHome: () => router.home(true),
});

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
  const [models, workspaces, personas, chats, wire, memories, taskModels, taskRuns, mediaCatalog, mediaReadiness] = await Promise.all([
    api.models().catch(() => ({ models: [] })),
    api.workspaces(),
    api.personas(),
    api.chats(),
    api.settings(),
    api.memories(),
    api.taskModels().catch(() => ({ items: [] })),
    api.taskModelRuns(undefined, 20).catch(() => ({ items: [] })),
    api.mediaCatalog().catch(() => null),
    api.mediaReadiness().catch(() => null),
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
  state.mediaReadiness = mediaReadiness;
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
  if (state.chatImagePreview) root.append(imageOverlay(state.chatImagePreview, 'Image', () => { state.chatImagePreview = ''; render(); }));
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
          request.permission_mode === 'confirm'
          && request.status === 'pending_confirmation'
          && request.capability_key !== 'media.generate_image'
          && request.assistant_message_id === message.id,
      )
      .map((request) => capabilities.node(request));
    return [node, ...requests];
  });
  const unanchoredRequests = state.capabilityRequests
    .filter(
      (request) =>
        request.permission_mode === 'confirm' &&
        request.status === 'pending_confirmation' &&
        request.capability_key !== 'media.generate_image' &&
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
    chatDrawer.node(),
    el('main', { class: 'main-pane glass' }, [
      topbar(persona?.name ?? 'Persona', persona?.avatar_url || DEFAULT_PERSONA_AVATAR),
      state.showChatControlsMenu ? chatControls(workspaceName, model, memoryMode) : null,
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

function topbar(personaName: string, avatar: string): HTMLElement {
  return el('div', { class: 'topbar' }, [
    el('button', { class: 'icon-btn', textContent: '☰', onclick: () => { state.drawerOpen = !state.drawerOpen; render(); } }),
    el('div', { class: 'header-meta' }, [
      el('button', {
        class: 'image-preview-trigger topbar-avatar-trigger',
        type: 'button',
        title: `View ${personaName}'s full-size avatar`,
        'aria-label': `View ${personaName}'s full-size avatar`,
        onclick: () => { state.personaAvatarPreview = avatar; render(); },
      }, [
        el('img', { class: 'topbar-avatar', src: avatar, alt: `${personaName} avatar` }),
      ]),
      el('div', { class: 'header-title', textContent: state.currentChat?.title || 'New conversation' }),
      el('span', { class: 'chip persona-chip', textContent: personaName }),
    ]),
    el('span', { class: 'sr-only', textContent: state.statusText, 'data-testid': 'client-phase' }),
    el('button', { class: 'icon-btn', textContent: '⋯', title: 'Chat controls and details', 'aria-expanded': state.showChatControlsMenu, onclick: () => { state.showChatControlsMenu = !state.showChatControlsMenu; render(); } }),
    el('button', { class: 'icon-btn', textContent: '⚙', title: 'Settings', 'data-testid': 'open-settings', onclick: () => router.settings(state.settingsSection) }),
    el('button', { class: 'icon-btn', textContent: '↪', title: 'Log out', 'data-testid': 'logout', onclick: () => void logout() }),
  ]);
}

function chatControls(workspace: string, model: string, memoryMode: string): HTMLElement {
  return el('div', { class: 'chat-controls-menu glass' }, [
    el('button', { class: 'pill-btn', textContent: state.voiceResponsesEnabled ? 'Voice replies: On' : 'Voice replies: Off', onclick: () => { state.voiceResponsesEnabled = !state.voiceResponsesEnabled; render(); } }),
    el('button', {
      class: `pill-btn ${state.settings?.chat_blur_images ? 'active' : ''}`,
      textContent: state.settings?.chat_blur_images ? 'Blur images: On' : 'Blur images: Off',
      'aria-pressed': Boolean(state.settings?.chat_blur_images),
      'data-testid': 'toggle-chat-image-blur',
      onclick: () => void toggleChatImageBlur(),
    }),
    state.currentAudioMessageId ? el('button', { class: 'pill-btn', textContent: 'Stop audio', onclick: () => playback.stop() }) : null,
    el('details', { class: 'chat-control-details' }, [
      el('summary', { textContent: 'Chat details' }),
      el('p', { class: 'meta', textContent: `Workspace: ${workspace}` }),
      el('label', { textContent: 'Model' }),
      el(
        'select',
        { class: 'chip-select', value: model, onchange: (event: Event) => { state.selectedModel = (event.currentTarget as HTMLSelectElement).value; } },
        state.models.map((item) => el('option', { value: item, selected: item === model, textContent: modelNickname(item) })),
      ),
      el('label', { textContent: 'Memory mode' }),
      el('select', { class: 'chip-select', value: memoryMode, onchange: (event: Event) => { state.selectedMemoryMode = (event.currentTarget as HTMLSelectElement).value === 'off' ? 'off' : 'saved'; } }, [
        el('option', { value: 'saved', selected: memoryMode !== 'off', textContent: 'Use saved memory' }),
        el('option', { value: 'off', selected: memoryMode === 'off', textContent: 'Do not use saved memory' }),
      ]),
      el('div', { class: `status-pill ${statusClass()}`, textContent: state.statusText }),
      el('button', { class: 'pill-btn', textContent: state.showSystemMessages ? 'Hide system/tool' : 'Show system/tool', onclick: () => { state.showSystemMessages = !state.showSystemMessages; render(); } }),
      el('button', { class: 'pill-btn', textContent: state.showThinkingByDefault ? 'Hide thinking' : 'Show thinking', onclick: () => { state.showThinkingByDefault = !state.showThinkingByDefault; render(); } }),
      el('button', { class: `pill-btn ${state.showViz ? 'active' : ''}`, textContent: state.showViz ? 'Visualizer: On' : 'Visualizer: Off', onclick: () => { state.showViz = !state.showViz; if (state.settings) state.settings.general_show_viz = state.showViz; render(); } }),
    ]),
  ]);
}

async function toggleChatImageBlur(): Promise<void> {
  if (!state.settings || state.settingsSaving) return;
  const previous = state.settings.chat_blur_images;
  state.settings.chat_blur_images = !previous;
  state.settingsSaving = true;
  render();
  try {
    state.settings = normalizeSettings(await api.updateSettings(settingsWire(state.settings)));
  } catch (error) {
    state.settings.chat_blur_images = previous;
    state.uiError = errorMessage(error, 'The image blur preference could not be saved.');
  } finally {
    state.settingsSaving = false;
    render();
  }
}

function composer(): HTMLElement {
  const { busy, inputLocked } = composerState(state.phase);
  const cancellableTurn = state.pendingRequest && ['queued', 'thinking'].includes(state.phase);
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
    cancellableTurn
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
  return el('div', { class: 'modal-backdrop media-preview-backdrop', 'data-testid': 'image-preview', onclick: close }, [
    el('div', {
      class: 'media-preview-frame',
      role: 'dialog',
      'aria-modal': 'true',
      'aria-label': `${alt} preview`,
      onclick: (event: Event) => event.stopPropagation(),
    }, [
      el('button', {
        class: 'icon-btn media-preview-close',
        textContent: '✕',
        title: 'Close preview',
        'aria-label': 'Close preview',
        onclick: close,
      }),
      el('img', {
        class: 'media-preview-image',
        src: url,
        alt,
        title: 'Close preview',
        onclick: close,
      }),
    ]),
  ]);
}

function videoOverlay(url: string): HTMLElement {
  const close = () => { state.chatVideoPreview = ''; render(); };
  return el('div', { class: 'modal-backdrop media-preview-backdrop', 'data-testid': 'video-preview', onclick: close }, [
    el('div', {
      class: 'media-preview-frame video-preview-frame',
      role: 'dialog',
      'aria-modal': 'true',
      'aria-label': 'Video preview',
      onclick: (event: Event) => event.stopPropagation(),
    }, [
      el('button', {
        class: 'icon-btn media-preview-close',
        textContent: '✕',
        title: 'Close preview',
        'aria-label': 'Close preview',
        onclick: close,
      }),
      el('video', { class: 'video-preview-media', src: url, controls: true, autoplay: true }),
    ]),
  ]);
}

function closeSettings(): void {
  clearIdentitySetupContext(state);
  state.showSettings = false;
  if (state.currentChat) router.chat(state.currentChat.id);
  else router.home();
}

async function signedOut(message = ''): Promise<void> {
  playback.stop(false);
  recording.cancel();
  chatDrawer.reset();
  state.session = null;
  state.currentChat = null;
  state.messages = [];
  state.capabilityRequests = [];
  clearIdentitySetupContext(state);
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
