import { describe, expect, it, vi } from 'vitest';

import { ApiError, type ApiClient } from '../src/api';
import { AuthView } from '../src/auth_view';
import { normalizeSettings } from '../src/settings';
import { DEFAULT_PERSONA_DESCRIPTION, SetupView } from '../src/setup_view';
import { ClientStateMachine, createState } from '../src/state';
import type { Persona, Session } from '../src/types';

const session: Session = { user_id: 'user-1', expires_at: 4_000_000_000, ttl_seconds: 1800, is_admin: true, persistent: true };

async function settle(): Promise<void> {
  for (let i = 0; i < 5; i += 1) await Promise.resolve();
}

function mounted(view: { node(): HTMLElement }): () => HTMLElement {
  const host = document.createElement('div');
  document.body.replaceChildren(host);
  const draw = () => {
    host.replaceChildren(view.node());
    return host;
  };
  return draw;
}

function type(host: HTMLElement, selector: string, value: string): void {
  const input = host.querySelector<HTMLInputElement | HTMLTextAreaElement>(selector)!;
  input.value = value;
  input.dispatchEvent(new Event('input'));
}

function submit(host: HTMLElement): void {
  host.querySelector('form')!.dispatchEvent(new Event('submit', { cancelable: true }));
}

describe('signing in', () => {
  function signIn(signup: { open: boolean; first_account: boolean }, overrides: Partial<ApiClient> = {}) {
    const appState = createState();
    const authenticated = vi.fn(async (_session: Session) => undefined);
    const client = {
      signupStatus: vi.fn(async () => signup),
      login: vi.fn(async () => session),
      createUser: vi.fn(async () => ({ id: 'user-1' })),
      ...overrides,
    } as unknown as ApiClient;
    let draw: () => HTMLElement = () => document.body;
    const view = new AuthView(authenticated, () => draw(), appState, client);
    draw = mounted(view);
    // The first look asks the server whether an account can be made here.
    draw();
    return { appState, authenticated, client, draw };
  }

  it('is a form a password manager recognises', async () => {
    const { draw } = signIn({ open: false, first_account: false });
    await settle();
    const host = draw();
    const username = host.querySelector<HTMLInputElement>('input[name="username"]')!;
    const password = host.querySelector<HTMLInputElement>('input[name="password"]')!;
    expect(host.querySelector('form')).not.toBeNull();
    expect(username.autocomplete).toBe('username');
    expect(password.autocomplete).toBe('current-password');
    expect(password.type).toBe('password');
    expect(host.querySelector('label[for="username"]')).not.toBeNull();
    expect(host.querySelector('label[for="password"]')).not.toBeNull();
    // Nothing is offered that the server would refuse.
    expect(host.querySelector('[data-testid="auth-switch"]')).toBeNull();
  });

  it('keeps the device signed in unless told otherwise', async () => {
    const { draw, client, authenticated } = signIn({ open: false, first_account: false });
    await settle();
    const host = draw();
    type(host, '#username', ' owner ');
    type(host, '#password', 'correct horse');
    submit(host);
    await settle();
    expect(client.login).toHaveBeenCalledWith('owner', 'correct horse', true);
    expect(authenticated).toHaveBeenCalledWith(session);
  });

  it('leaves what was typed in place when the password is wrong', async () => {
    const { draw, appState } = signIn({ open: false, first_account: false }, {
      login: vi.fn(async () => { throw new ApiError('invalid credentials', 401, 'authentication_required'); }),
    });
    await settle();
    let host = draw();
    type(host, '#username', 'owner');
    type(host, '#password', 'not the one');
    submit(host);
    await settle();
    host = draw();
    expect(appState.authError).toBe('Wrong username or password.');
    expect(host.querySelector<HTMLInputElement>('#username')!.value).toBe('owner');
    expect(host.querySelector<HTMLInputElement>('#password')!.value).toBe('not the one');
    expect(host.querySelector<HTMLButtonElement>('[data-testid="auth-login"]')!.disabled).toBe(false);
  });

  it('makes the first account and signs straight in with it', async () => {
    const { draw, client, authenticated } = signIn({ open: true, first_account: true });
    await settle();
    const host = draw();
    expect(host.querySelector('h1')!.textContent).toBe('Welcome');
    const password = host.querySelector<HTMLInputElement>('#password')!;
    expect(password.autocomplete).toBe('new-password');
    expect(password.minLength).toBe(8);
    type(host, '#username', 'owner');
    type(host, '#password', 'correct horse');
    submit(host);
    await settle();
    expect(client.createUser).toHaveBeenCalledWith('owner', 'correct horse');
    expect(client.login).toHaveBeenCalledWith('owner', 'correct horse', true);
    expect(authenticated).toHaveBeenCalled();
  });

  it('offers a new account only where the server would make one', async () => {
    const { draw } = signIn({ open: true, first_account: false });
    await settle();
    let host = draw();
    host.querySelector<HTMLButtonElement>('[data-testid="auth-switch"]')!.click();
    host = draw();
    expect(host.querySelector('[data-testid="auth-create"]')).not.toBeNull();
    expect(host.querySelector<HTMLInputElement>('#password')!.autocomplete).toBe('new-password');
  });
});

describe('meeting a companion', () => {
  const persona: Persona = {
    id: 'persona-1', workspace_id: 'home', workspace_ids: ['home'], name: 'Nova', avatar_url: null,
    allow_image_sends: true, system_prompt: DEFAULT_PERSONA_DESCRIPTION, personality_details: null,
    traits: {}, default_model: 'seeing', voice_preferences: {}, created_at: 1,
  };
  const chat = {
    id: 'chat-1', workspace_id: 'home', persona_id: persona.id, model_override: 'seeing',
    memory_mode: 'saved' as const, title: 'New chat', hidden_in_ui: false, created_at: 2, updated_at: 2,
  };

  function setUp(models: string[], visionModels: string[], overrides: Partial<ApiClient> = {}) {
    const appState = createState();
    appState.session = session;
    appState.settings = normalizeSettings({
      global_default_model: null, default_memory_mode: 'saved', stt_provider: 'disabled', tts_provider: 'disabled',
      tts_format: 'wav', openai_api_key: null, onboarding_done: false, preferences: {},
    });
    appState.models = models;
    appState.visionModels = visionModels;
    const machine = new ClientStateMachine(appState);
    machine.transition('onboarding');
    const client = {
      createWorkspace: vi.fn(async () => ({ id: 'home', name: 'Home', created_at: 1 })),
      createPersona: vi.fn(async () => persona),
      updateSettings: vi.fn(async (wire: unknown) => wire),
      createChat: vi.fn(async () => chat),
      models: vi.fn(async () => ({ models: ['demo'], vision_models: [] })),
      ...overrides,
    } as unknown as ApiClient;
    const openChat = vi.fn();
    let draw: () => HTMLElement = () => document.body;
    const view = new SetupView(appState, client, machine, () => draw(), openChat);
    draw = mounted(view);
    return { appState, client, openChat, draw };
  }

  it('suggests a model that can see, and ends in a conversation', async () => {
    const { appState, client, openChat, draw } = setUp(['demo', 'seeing'], ['seeing']);
    const host = draw();
    expect(host.querySelector<HTMLSelectElement>('#setup-model')!.value).toBe('seeing');
    expect(host.querySelector<HTMLTextAreaElement>('#setup-description')!.value).toBe(DEFAULT_PERSONA_DESCRIPTION);
    type(host, '#setup-name', ' Nova ');
    submit(host);
    await settle();
    expect(client.createWorkspace).toHaveBeenCalledWith('Home');
    expect(client.createPersona).toHaveBeenCalledWith(expect.objectContaining({ name: 'Nova', default_model: 'seeing' }));
    expect(client.updateSettings).toHaveBeenCalledWith(expect.objectContaining({ onboarding_done: true, global_default_model: 'seeing' }));
    expect(client.createChat).toHaveBeenCalledWith(expect.objectContaining({ persona_id: persona.id, model: 'seeing' }));
    expect(appState.personas).toEqual([persona]);
    expect(appState.phase).toBe('idle');
    expect(openChat).toHaveBeenCalledWith(chat.id);
  });

  it('does not make a twin when trying again after a later step failed', async () => {
    const createChat = vi.fn()
      .mockRejectedValueOnce(new Error('The server is busy.'))
      .mockResolvedValueOnce(chat);
    const { appState, client, openChat, draw } = setUp(['demo'], [], { createChat } as Partial<ApiClient>);
    let host = draw();
    type(host, '#setup-name', 'Nova');
    submit(host);
    await settle();
    host = draw();
    // Still on the page, with the reason and with nothing half-made shown as done.
    expect(appState.personas).toEqual([]);
    expect(host.querySelector('[role="alert"]')!.textContent).toBe('The server is busy.');
    expect(host.querySelector<HTMLInputElement>('#setup-name')!.value).toBe('Nova');
    submit(host);
    await settle();
    expect(client.createPersona).toHaveBeenCalledTimes(1);
    expect(openChat).toHaveBeenCalledWith(chat.id);
  });

  it('says what to check when Ollama has no models, and looks again', async () => {
    const { appState, client, draw } = setUp([], []);
    let host = draw();
    expect(host.querySelector('[data-testid="setup-no-models"]')).not.toBeNull();
    host.querySelector<HTMLButtonElement>('.setup-ollama button')!.click();
    await settle();
    host = draw();
    expect(client.models).toHaveBeenCalled();
    expect(appState.models).toEqual(['demo']);
    expect(host.querySelector<HTMLSelectElement>('#setup-model')!.value).toBe('demo');
  });
});
