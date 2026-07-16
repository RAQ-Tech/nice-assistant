import type { AppState, ClientPhase } from './types';

const LEGAL_PHASES: Record<ClientPhase, ReadonlySet<ClientPhase>> = {
  signed_out: new Set(['onboarding', 'idle', 'error']),
  onboarding: new Set(['idle', 'signed_out', 'error']),
  idle: new Set(['loading_chat', 'queued', 'recording', 'speaking', 'signed_out', 'error']),
  loading_chat: new Set(['idle', 'signed_out', 'error']),
  queued: new Set(['thinking', 'idle', 'signed_out', 'error']),
  thinking: new Set(['idle', 'speaking', 'signed_out', 'error']),
  recording: new Set(['transcribing', 'idle', 'signed_out', 'error']),
  transcribing: new Set(['queued', 'idle', 'signed_out', 'error']),
  speaking: new Set(['idle', 'recording', 'signed_out', 'error']),
  error: new Set(['idle', 'signed_out', 'loading_chat', 'onboarding']),
};

export function createState(): AppState {
  return {
    session: null,
    phase: 'signed_out',
    phaseBeforeError: 'signed_out',
    chats: [],
    currentChat: null,
    messages: [],
    capabilityRequests: [],
    personas: [],
    workspaces: [],
    settings: null,
    models: [],
    memories: [],
    taskModels: [],
    taskModelRuns: [],
    taskModelChecks: {},
    taskModelBusy: {},
    mediaCatalog: null,
    mediaReadiness: null,
    mediaCatalogBusy: false,
    mediaPlanPreview: null,
    mediaCatalogIdentitySetupIntent: null,
    identitySettings: null,
    identityProfiles: {},
    identityValidations: {},
    identityEvents: {},
    identitySelectedPersonaId: null,
    identityBusy: false,
    route: { kind: 'home' },
    statusText: 'Signed out',
    uiError: '',
    authError: '',
    settingsError: '',
    drawerOpen: false,
    chatSearch: '',
    stickMessagesToBottom: true,
    showJumpBottom: false,
    showSettings: false,
    settingsSection: 'General',
    modal: null,
    selectedPersonaId: null,
    selectedModel: null,
    selectedMemoryMode: null,
    draftMessage: '',
    showChatControlsMenu: false,
    showSystemMessages: false,
    showThinkingByDefault: false,
    thinkingExpanded: {},
    showViz: false,
    voiceResponsesEnabled: true,
    currentAudioMessageId: null,
    messageAudioById: {},
    messageAudioErrors: {},
    pendingRequest: null,
    recordingStartedAt: 0,
    settingsSaving: false,
    settingsSavedAt: 0,
    providerChecks: {},
    providerChecksRunning: {},
    backupItems: [],
    backupsLoading: false,
    backupActionRunning: false,
    resourceCoordination: null,
    resourceCoordinationEvents: [],
    resourceCoordinationBusy: false,
    memorySections: {
      active: false,
      pending: false,
      activeGlobal: false,
      activeWorkspace: false,
      activePersona: false,
      activeChat: false,
      pendingGlobal: false,
      pendingWorkspace: false,
      pendingPersona: false,
      pendingChat: false,
      history: false,
    },
    personaAvatarPreview: '',
    chatImagePreview: '',
    chatVideoPreview: '',
    revealedImages: {},
    showNewChatPersonaModal: false,
    newChatPersonaId: null,
    onboardingRunning: false,
    sessionTimer: null,
    lastActivityAt: Date.now(),
  };
}

export function clearIdentitySetupContext(appState: AppState): void {
  appState.mediaCatalogIdentitySetupIntent = null;
}

export function clearIdentitySetupContextForChat(appState: AppState, chatId: string): void {
  const intent = appState.mediaCatalogIdentitySetupIntent;
  if (intent && intent.chat_id !== chatId) clearIdentitySetupContext(appState);
}

export class ClientStateMachine {
  constructor(private readonly state: AppState) {}

  transition(next: ClientPhase, statusText?: string): void {
    if (next === this.state.phase) {
      if (statusText) this.state.statusText = statusText;
      return;
    }
    if (!LEGAL_PHASES[this.state.phase].has(next)) {
      throw new Error(`Illegal client transition: ${this.state.phase} -> ${next}`);
    }
    if (next === 'error') this.state.phaseBeforeError = this.state.phase;
    this.state.phase = next;
    this.state.statusText = statusText ?? phaseLabel(next);
  }

  recover(): void {
    const target = this.state.session ? 'idle' : 'signed_out';
    if (this.state.phase !== target) this.transition(target);
  }
}

export function phaseLabel(phase: ClientPhase): string {
  return {
    signed_out: 'Signed out',
    onboarding: 'Setup',
    idle: 'Idle',
    loading_chat: 'Loading',
    queued: 'Queued',
    thinking: 'Thinking',
    recording: 'Listening',
    transcribing: 'Transcribing',
    speaking: 'Speaking',
    error: 'Error',
  }[phase];
}

export const state = createState();
export const machine = new ClientStateMachine(state);
