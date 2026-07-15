import { api, type ApiClient } from './api';
import { el, errorMessage } from './dom';
import { state } from './state';
import type { AppState, Session } from './types';

export class AuthView {
  constructor(
    private readonly authenticated: (session: Session) => Promise<void>,
    private readonly renderApp: () => void,
    private readonly appState: AppState = state,
    private readonly client: ApiClient = api,
  ) {}

  node(): HTMLElement {
    return el('div', { class: 'main-pane glass auth-pane', 'data-testid': 'auth-view' }, [
      el('h2', { textContent: 'Nice Assistant Login' }),
      this.appState.authError ? el('div', { class: 'error-banner', textContent: this.appState.authError }) : null,
      this.appState.settingsSavedAt ? el('div', { class: 'success-banner', textContent: 'Account created. Please sign in.' }) : null,
      el('input', { id: 'username', class: 'search-input', placeholder: 'username', autocomplete: 'username', 'data-testid': 'auth-username' }),
      el('input', { id: 'password', class: 'search-input', placeholder: 'password', type: 'password', autocomplete: 'current-password', 'data-testid': 'auth-password' }),
      el('div', { class: 'chips' }, [
        el('button', { class: 'pill-btn', textContent: 'Create account', 'data-testid': 'auth-create', onclick: () => void this.createAccount() }),
        el('button', { class: 'send-btn', textContent: 'Login', 'data-testid': 'auth-login', onclick: () => void this.login() }),
      ]),
    ]);
  }

  private credentials(): { username: string; password: string } {
    return {
      username: (document.querySelector<HTMLInputElement>('#username')?.value ?? '').trim(),
      password: document.querySelector<HTMLInputElement>('#password')?.value ?? '',
    };
  }

  private async createAccount(): Promise<void> {
    const { username, password } = this.credentials();
    this.appState.authError = '';
    try {
      await this.client.createUser(username, password);
      this.appState.settingsSavedAt = Date.now();
    } catch (error) {
      this.appState.authError = errorMessage(error, 'Unable to create account.');
    }
    this.renderApp();
  }

  private async login(): Promise<void> {
    const { username, password } = this.credentials();
    this.appState.authError = '';
    try {
      await this.authenticated(await this.client.login(username, password));
    } catch (error) {
      this.appState.authError = errorMessage(error, 'Wrong username and/or password.');
      this.renderApp();
    }
  }
}
