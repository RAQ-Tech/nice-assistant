import type { ApiClient } from './api';
import { el, errorMessage } from './dom';
import { settingsWire } from './settings';
import type { ClientStateMachine } from './state';
import type { AppState, Persona } from './types';

/** Who a new persona is until somebody says otherwise: a companion, not a help desk. */
export const DEFAULT_PERSONA_DESCRIPTION =
  'Warm, curious and easy to talk to. Speaks naturally, like a close friend, and keeps replies conversational rather than formal.';

/**
 * Meeting your companion, the first time.
 *
 * This replaced three dialogs - name a "workspace", name a persona, set an
 * "instruction" that defaulted to "Be helpful and concise" - which asked about
 * the product's internals and made a help desk. It is one page that asks what
 * a person would actually decide: who they are talking to, what that persona
 * is like, and which model it thinks with, after checking that Ollama is there
 * to think at all. The workspace is made quietly. It ends in a conversation.
 *
 * Nothing else can be reached until it is done, so there is no chat with a
 * placeholder persona and a Send button that does nothing.
 */
export class SetupView {
  private name = '';
  private description = DEFAULT_PERSONA_DESCRIPTION;
  private model: string | null = null;
  private made: { user: string; persona: Persona } | null = null;
  private busy = false;
  private checking = false;

  constructor(
    private readonly appState: AppState,
    private readonly client: ApiClient,
    private readonly machine: ClientStateMachine,
    private readonly render: () => void,
    private readonly openChat: (chatId: string) => void,
  ) {}

  node(): HTMLElement {
    const models = this.appState.models;
    const seeing = new Set(this.appState.visionModels);
    // A model that can see, if there is one: it can do everything the others can.
    const chosen = this.model ?? models.find((model) => seeing.has(model)) ?? models[0] ?? '';
    return el('div', { class: 'main-pane glass setup-pane', 'data-testid': 'setup-view' }, [
      el('form', {
        class: 'setup-form',
        onsubmit: (event: Event) => {
          event.preventDefault();
          void this.finish(chosen);
        },
      }, [
        el('h1', { class: 'auth-title', textContent: 'Meet your companion' }),
        el('p', { class: 'meta', textContent: 'Who you will be talking to. All of it can be changed later on their page.' }),
        this.appState.uiError ? el('div', { class: 'error-banner', role: 'alert', textContent: this.appState.uiError }) : null,
        el('label', { for: 'setup-name', textContent: 'Their name' }),
        el('input', {
          id: 'setup-name',
          class: 'search-input',
          placeholder: 'For example, Nova',
          required: true,
          value: this.name,
          'data-testid': 'setup-name',
          oninput: (event: Event) => { this.name = (event.currentTarget as HTMLInputElement).value; },
        }),
        el('label', { for: 'setup-description', textContent: 'What they are like' }),
        el('textarea', {
          id: 'setup-description',
          class: 'search-input setup-description',
          rows: 3,
          value: this.description,
          'data-testid': 'setup-description',
          oninput: (event: Event) => { this.description = (event.currentTarget as HTMLTextAreaElement).value; },
        }),
        el('label', { for: 'setup-model', textContent: 'The model they think with' }),
        models.length
          ? el('select', {
              id: 'setup-model',
              class: 'chip-select',
              'data-testid': 'setup-model',
              onchange: (event: Event) => { this.model = (event.currentTarget as HTMLSelectElement).value; },
            }, models.map((model) => el('option', {
              value: model,
              selected: model === chosen,
              textContent: seeing.has(model) ? `${model} · can see photos` : model,
            })))
          : el('div', { class: 'setup-ollama', 'data-testid': 'setup-no-models' }, [
              el('p', { textContent: 'No models found yet.' }),
              el('p', { class: 'meta', textContent: 'Check that Ollama is running, has a model pulled, and that OLLAMA_BASE_URL points at it. You can also carry on and choose one later.' }),
              el('button', {
                type: 'button',
                class: 'pill-btn',
                textContent: this.checking ? 'Checking…' : 'Check again',
                disabled: this.checking,
                onclick: () => void this.checkAgain(),
              }),
            ]),
        el('button', {
          type: 'submit',
          class: 'send-btn setup-submit',
          textContent: this.busy ? 'Getting ready…' : 'Start talking',
          disabled: this.busy,
          'data-testid': 'setup-start',
        }),
      ]),
    ]);
  }

  private async checkAgain(): Promise<void> {
    this.checking = true;
    this.render();
    try {
      const found = await this.client.models();
      this.appState.models = found.models;
      this.appState.visionModels = found.vision_models ?? [];
    } catch {
      // Still nothing; the page already says what to check.
    } finally {
      this.checking = false;
      this.render();
    }
  }

  private async finish(model: string): Promise<void> {
    const name = this.name.trim();
    if (!name || this.busy || !this.appState.settings) return;
    this.busy = true;
    this.appState.uiError = '';
    this.render();
    try {
      const workspace = this.appState.workspaces[0] ?? (await this.client.createWorkspace('Home'));
      if (!this.appState.workspaces.length) this.appState.workspaces.push(workspace);
      // Made once per account: trying again after a later step failed must not make a twin.
      const user = this.appState.session?.user_id ?? '';
      if (this.made?.user !== user) {
        this.made = {
          user,
          persona: await this.client.createPersona({
            workspace_id: workspace.id,
            workspace_ids: [workspace.id],
            name,
            system_prompt: this.description.trim() || DEFAULT_PERSONA_DESCRIPTION,
            default_model: model || null,
          }),
        };
      }
      const persona = this.made.persona;
      this.appState.settings.onboarding_done = true;
      if (model) this.appState.settings.global_default_model = model;
      await this.client.updateSettings(settingsWire(this.appState.settings));
      const chat = await this.client.createChat({
        workspace_id: workspace.id,
        persona_id: persona.id,
        model: model || null,
        memory_mode: this.appState.settings.default_memory_mode,
        title: 'New chat',
      });
      // Only now does the persona count as there: the page stays until it all worked.
      this.appState.personas.push(persona);
      this.appState.chats = [chat, ...this.appState.chats];
      this.appState.selectedPersonaId = persona.id;
      this.appState.newChatPersonaId = persona.id;
      this.machine.recover();
      this.openChat(chat.id);
    } catch (error) {
      this.appState.uiError = errorMessage(error, 'Setup could not finish. Nothing you typed was lost.');
    } finally {
      this.busy = false;
      this.render();
    }
  }
}
