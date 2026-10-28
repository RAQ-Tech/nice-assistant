import { api, ApiError, type ApiClient } from './api';
import { el, errorMessage } from './dom';
import { state } from './state';
import type { AppState, Session } from './types';

type Signup = { open: boolean; first_account: boolean };

/**
 * Signing in, or creating the account to sign in with.
 *
 * A real form, so a password manager offers to save and fill it: labelled
 * fields with the names and autocomplete values it looks for, Enter to submit,
 * and the words that were typed left in place when something is wrong. The
 * form disappears only once the sign-in has worked, which is how a browser
 * knows it did. Nothing typed here is kept anywhere but in the page.
 *
 * Creating an account offers itself only where it would work - the very first
 * account, or a server that allows more - and signs straight in, rather than
 * clearing the form and asking for the same words again.
 */
export class AuthView {
  private signup: Signup | null = null;
  private loading = false;
  private creating = false;
  private busy = false;
  private username = '';
  private password = '';
  private remember = true;

  constructor(
    private readonly authenticated: (session: Session) => Promise<void>,
    private readonly renderApp: () => void,
    private readonly appState: AppState = state,
    private readonly client: ApiClient = api,
  ) {}

  node(): HTMLElement {
    if (!this.signup && !this.loading) void this.loadSignup();
    const first = Boolean(this.signup?.first_account);
    const creating = first || this.creating;
    return el('div', { class: 'main-pane glass auth-pane', 'data-testid': 'auth-view' }, [
      el('form', {
        class: 'auth-form',
        method: 'post',
        'data-testid': 'auth-form',
        onsubmit: (event: Event) => {
          event.preventDefault();
          void (creating ? this.createAccount() : this.login());
        },
      }, [
        el('h1', { class: 'auth-title', textContent: first ? 'Welcome' : creating ? 'Create an account' : 'Sign in' }),
        first
          ? el('p', { class: 'meta', textContent: 'Create the first account. It looks after this assistant, its settings and its backups.' })
          : null,
        this.appState.authError ? el('div', { class: 'error-banner', role: 'alert', textContent: this.appState.authError }) : null,
        el('label', { for: 'username', textContent: 'Username' }),
        el('input', {
          id: 'username',
          name: 'username',
          class: 'search-input',
          autocomplete: 'username',
          autocapitalize: 'none',
          spellcheck: false,
          required: true,
          value: this.username,
          'data-testid': 'auth-username',
          oninput: (event: Event) => { this.username = (event.currentTarget as HTMLInputElement).value; },
        }),
        el('label', { for: 'password', textContent: 'Password' }),
        el('input', {
          id: 'password',
          name: 'password',
          type: 'password',
          class: 'search-input',
          autocomplete: creating ? 'new-password' : 'current-password',
          required: true,
          minLength: creating ? 8 : undefined,
          value: this.password,
          'data-testid': 'auth-password',
          oninput: (event: Event) => { this.password = (event.currentTarget as HTMLInputElement).value; },
        }),
        creating ? el('p', { class: 'meta auth-hint', textContent: 'At least 8 characters.' }) : null,
        el('label', { class: 'checkbox-row' }, [
          el('input', {
            type: 'checkbox',
            name: 'remember',
            checked: this.remember,
            'data-testid': 'auth-remember',
            onchange: (event: Event) => { this.remember = (event.currentTarget as HTMLInputElement).checked; },
          }),
          'Keep me signed in on this device',
        ]),
        el('button', {
          type: 'submit',
          class: 'send-btn auth-submit',
          textContent: creating ? 'Create account' : 'Sign in',
          disabled: this.busy,
          'data-testid': creating ? 'auth-create' : 'auth-login',
        }),
        !first && this.signup?.open
          ? el('button', {
              type: 'button',
              class: 'link-btn',
              textContent: creating ? 'I already have an account' : 'Create an account',
              'data-testid': 'auth-switch',
              onclick: () => {
                this.creating = !this.creating;
                this.appState.authError = '';
                this.renderApp();
              },
            })
          : null,
      ]),
    ]);
  }

  private async loadSignup(): Promise<void> {
    this.loading = true;
    try {
      this.signup = await this.client.signupStatus();
    } catch {
      // Without an answer, signing in is still possible and nothing else is offered.
      this.signup = { open: false, first_account: false };
    } finally {
      this.loading = false;
    }
    this.renderApp();
  }

  private async createAccount(): Promise<void> {
    await this.attempt(async () => {
      await this.client.createUser(this.username.trim(), this.password);
      return this.client.login(this.username.trim(), this.password, this.remember);
    }, 'That account could not be created.');
  }

  private async login(): Promise<void> {
    await this.attempt(() => this.client.login(this.username.trim(), this.password, this.remember), 'Wrong username or password.');
  }

  private async attempt(signIn: () => Promise<Session>, fallback: string): Promise<void> {
    this.busy = true;
    this.appState.authError = '';
    this.renderApp();
    try {
      const session = await signIn();
      // Signed in: the form goes, and with it the words typed into it.
      this.password = '';
      this.signup = null;
      this.creating = false;
      await this.authenticated(session);
    } catch (error) {
      this.appState.authError = error instanceof ApiError && error.status === 401
        ? 'Wrong username or password.'
        : errorMessage(error, fallback);
    } finally {
      this.busy = false;
      this.renderApp();
    }
  }
}
