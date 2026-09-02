import type { ApiClient } from './api';
import { el, errorMessage, formatDate } from './dom';
import { IdentityMediaPicker } from './identity_media_picker';
import { PersonaFaceView } from './persona_face_view';
import { PictureLibraryView } from './picture_library_view';
import {
  identityAuditCard,
  identityComparisonPolicyCard,
  identityImageButton,
  identityPersonaSelector,
} from './identity_settings_components';
import {
  advancedSettings,
  boundedNumber,
  selectControl as select,
  settingField as field,
  settingsHeading,
  settingsIntro,
  textControl as input,
  titleCase as title,
} from './settings_ui';
import type {
  AppState,
  IdentityValidationSettings,
  VisualIdentityProfile,
} from './types';

interface IdentityDialogs {
  prompt(title: string, message: string, initial?: string): Promise<string | null>;
  confirm(title: string, message: string, confirmText?: string): Promise<boolean>;
}

/**
 * Persona Pictures: one persona's face, its preferred recipes and kept
 * pictures, and - folded - the comparison service, manual comparison and the
 * activity record.
 *
 * The face is the same card the persona's own page shows, so there is one
 * face and not two. What is left here is what comparison needs: a verifier
 * is advisory measurement (ADR 0031), so its plumbing and its thresholds sit
 * behind the fold rather than in front of somebody setting a persona up.
 */
export class IdentitySettingsView {
  private providerResult = '';
  private readonly mediaPicker: IdentityMediaPicker;
  private readonly library: PictureLibraryView;
  private readonly face: PersonaFaceView;
  private advancedOpen = false;

  constructor(
    private readonly renderApp: () => void,
    private readonly appState: AppState,
    private readonly client: ApiClient,
    private readonly dialogs: IdentityDialogs,
    private readonly openIdentitySetup: (personaId: string) => void = () => undefined,
  ) {
    this.mediaPicker = new IdentityMediaPicker(renderApp, client, (url) => this.openImage(url));
    this.library = new PictureLibraryView(appState, client, renderApp);
    this.face = new PersonaFaceView(
      appState,
      client,
      renderApp,
      dialogs,
      openIdentitySetup,
      null,
      (personaId) => this.reloadPersona(personaId),
    );
  }

  async refresh(): Promise<void> {
    this.appState.identitySelectedPersonaId ??= this.appState.personas[0]?.id ?? null;
    const personaId = this.appState.identitySelectedPersonaId;
    this.appState.identityBusy = true;
    try {
      this.appState.identitySettings = await this.client.identitySettings();
      if (personaId) await this.reloadPersona(personaId);
      this.appState.settingsError = '';
    } catch (error) {
      this.appState.settingsError = errorMessage(error, 'Visual identity settings could not be loaded.');
    } finally {
      this.appState.identityBusy = false;
      this.renderApp();
    }
  }

  nodes(): HTMLElement[] {
    const personaId = this.appState.identitySelectedPersonaId;
    const persona = this.appState.personas.find((item) => item.id === personaId) ?? null;
    const profile = personaId ? this.appState.identityProfiles[personaId] : null;
    return [
      settingsIntro(
        "Everything about a persona's pictures",
        'The face it is drawn from, the recipes that suit it, and the pictures kept for reuse. Comparison, when a verifier is configured, is measurement after the fact and lives under the fold.',
      ),
      this.personaSelector(),
      persona
        ? this.face.node(persona)
        : el('div', { class: 'settings-empty-state', textContent: 'Choose a persona to manage its appearance.' }),
      this.library.node(personaId ?? '', profile ?? null, () => this.reloadPersona(personaId ?? '')),
      profile ? this.advanced(profile) : null,
    ].filter((node): node is HTMLElement => node !== null);
  }

  private personaSelector(): HTMLElement {
    return identityPersonaSelector(
      this.appState.identitySelectedPersonaId ?? '',
      this.appState.personas.map((persona) => persona.id),
      (value) => this.personaName(value),
      (value) => {
        this.appState.identitySelectedPersonaId = value || null;
        this.mediaPicker.close();
        void this.refresh();
      },
    );
  }

  private advanced(profile: VisualIdentityProfile): HTMLElement {
    const enabled = profile.consent_status === 'granted';
    const name = this.personaName(profile.persona_id);
    const validations = this.appState.identityValidations[profile.persona_id] ?? [];
    const events = this.appState.identityEvents[profile.persona_id] ?? [];
    return advancedSettings(
      'Comparison and history',
      `Measuring a finished picture against ${name}’s photo. A verifier cannot make a picture look more like the photo; it can say how close one came.`,
      [
        this.providerCard(this.appState.identitySettings),
        enabled
          ? identityComparisonPolicyCard(profile, this.appState.identityBusy, () => void this.saveProfile(profile))
          : null,
        this.validationManager(profile, validations),
        identityAuditCard(events),
      ],
      {
        open: this.advancedOpen,
        testId: 'identity-advanced-settings',
        onToggle: (open) => { this.advancedOpen = open; },
      },
    );
  }

  private providerCard(settings: IdentityValidationSettings | null): HTMLElement {
    if (!settings) return el('div', { class: 'persona-card', textContent: 'Verifier settings are unavailable.' });
    const enabled = settings.provider === 'compreface';
    return el('div', { class: 'persona-card identity-provider-card' }, [
      settingsHeading(
        'Optional identity comparison service',
        'A verifier compares a finished image with the approved reference. It does not improve generation, so leave it off until reference-aware generation is useful.',
      ),
      field('Comparison service', select(settings.provider, ['disabled', 'compreface'], (value) => {
        settings.provider = value as IdentityValidationSettings['provider'];
        this.renderApp();
      }, (value) => value === 'disabled' ? 'Off' : 'CompreFace'), 'CompreFace is a separately deployed LAN service used only for face comparison.'),
      enabled ? field('CompreFace service address', input(settings.base_url, (value) => { settings.base_url = value; }, 'url'), 'The private-LAN address of the CompreFace service.') : null,
      enabled ? field('CompreFace API key', input(settings.api_key, (value) => { settings.api_key = value; }, 'password'), 'A verification API key created in CompreFace and encrypted by Nice Assistant.') : null,
      enabled ? field('Stop waiting after (seconds)', input(String(settings.timeout_seconds), (value) => {
        settings.timeout_seconds = boundedNumber(value, 1, 120, settings.timeout_seconds);
      }, 'number'), 'Bounds each comparison request so an unavailable verifier cannot hang generation indefinitely.') : null,
      el('div', { class: 'chips' }, [
        el('button', {
          class: 'send-btn',
          textContent: this.appState.identityBusy ? 'Saving…' : 'Save comparison service',
          disabled: this.appState.identityBusy,
          'data-testid': 'identity-provider-save',
          onclick: () => void this.saveProvider(),
        }),
        enabled ? el('button', {
          class: 'pill-btn',
          textContent: this.appState.identityBusy ? 'Checking…' : 'Test connection',
          disabled: this.appState.identityBusy,
          onclick: () => void this.checkProvider(),
        }) : null,
        this.providerResult ? el('span', { class: 'provider-check-message', textContent: this.providerResult }) : null,
      ]),
    ]);
  }

  private validationManager(
    profile: VisualIdentityProfile,
    validations: AppState['identityValidations'][string],
  ): HTMLElement {
    const name = this.personaName(profile.persona_id);
    return el('div', { class: 'persona-card' }, [
      settingsHeading(
        'Manual comparison',
        'Choose one of your generated images and compare its face with the approved reference without changing the image.',
      ),
      el('p', {
        class: 'meta',
        textContent: profile.validation_ready
          ? `Choose a generated image to compare with ${name}’s approved reference.`
          : 'Manual comparison becomes available after an approved reference and comparison service are configured.',
      }),
      el('button', {
        class: 'pill-btn',
        textContent: 'Choose an image to compare',
        disabled: !profile.validation_ready || this.appState.identityBusy,
        'data-testid': 'identity-validation-gallery-open',
        onclick: () => void this.mediaPicker.open('validation'),
      }),
      this.mediaPicker.isOpen('validation')
        ? this.mediaPicker.node({
            mode: 'validation',
            actionLabel: 'Compare image',
            actionDisabled: this.appState.identityBusy,
            onUse: (item) => this.validateMedia(profile.persona_id, item.id),
          })
        : null,
      validations.length ? el('div', { class: 'identity-validation-list' }, validations.map((validation) =>
        el('div', { class: 'identity-validation-card' }, [
          identityImageButton(
            this.client.mediaUrl(validation.candidate_media_id),
            `Open compared image for ${name}`,
            `Compared with ${name}`,
            'identity-validation-thumb',
            (url) => this.openImage(url),
          ),
          el('div', {}, [
            el('strong', { textContent: validation.claim_status === 'verified' ? 'Looks like the persona' : title(validation.claim_status) }),
            el('div', {
              class: 'meta',
              textContent: `${formatDate(validation.created_at)}${validation.score === null ? '' : ` · ${(validation.score * 100).toFixed(1)}% match`}`,
            }),
            validation.error ? el('div', { class: 'provider-check-message', textContent: validation.error.message }) : null,
          ]),
        ]),
      )) : el('div', { class: 'meta', textContent: 'No images have been compared manually.' }),
    ]);
  }

  private async saveProvider(): Promise<void> {
    const settings = this.appState.identitySettings;
    if (!settings) return;
    await this.run(async () => {
      this.appState.identitySettings = await this.client.updateIdentitySettings(settings);
      this.providerResult = settings.provider === 'disabled' ? 'Comparison is off.' : 'Comparison service saved.';
      const personaId = this.appState.identitySelectedPersonaId;
      if (personaId) await this.reloadPersona(personaId);
    });
  }

  private openImage(url: string): void {
    this.appState.chatImagePreview = url;
    this.renderApp();
  }

  private async checkProvider(): Promise<void> {
    await this.run(async () => {
      const result = await this.client.checkIdentityProvider();
      this.providerResult = `${title(String(result.status))}: ${String(result.message ?? '')}`;
    });
  }

  private async saveProfile(profile: VisualIdentityProfile): Promise<void> {
    await this.run(async () => {
      try {
        this.appState.identityProfiles[profile.persona_id] = await this.client.updateVisualIdentity(profile.persona_id, profile);
      } catch (error) {
        // A refused save means the copy on screen is behind. Reload it, or the
        // next attempt is refused for the same reason forever.
        await this.reloadPersona(profile.persona_id).catch(() => undefined);
        throw error;
      }
    });
  }

  private async validateMedia(personaId: string, mediaId: string): Promise<void> {
    await this.run(async () => {
      const accepted = await this.client.validateIdentityMedia(personaId, mediaId);
      this.mediaPicker.close();
      await this.waitJob(accepted.job.id);
      await this.reloadPersona(personaId);
    });
  }

  private async waitJob(jobId: string): Promise<void> {
    const deadline = Date.now() + 120_000;
    while (Date.now() < deadline) {
      const job = await this.client.job(jobId);
      if (['completed', 'failed', 'cancelled'].includes(job.status)) return;
      await new Promise((resolve) => window.setTimeout(resolve, 250));
    }
    throw new Error('The comparison is still running. Reopen this section later to see the result.');
  }

  private async reloadPersona(personaId: string): Promise<void> {
    const [profile, validations, history] = await Promise.all([
      this.client.visualIdentity(personaId),
      this.client.identityValidations(personaId),
      this.client.identityHistory(personaId),
    ]);
    this.appState.identityProfiles[personaId] = profile;
    this.appState.identityValidations[personaId] = validations.items;
    this.appState.identityEvents[personaId] = history.items;
    // The kept-pictures library is this persona's own; it loads with the
    // persona instead of waiting behind a button.
    void this.library.refresh(personaId);
  }

  private async run(action: () => Promise<void>): Promise<void> {
    this.appState.identityBusy = true;
    this.appState.settingsError = '';
    this.renderApp();
    try {
      await action();
    } catch (error) {
      this.appState.settingsError = errorMessage(error, 'Visual identity operation failed.');
    } finally {
      this.appState.identityBusy = false;
      this.renderApp();
    }
  }

  private personaName(personaId: string): string {
    return this.appState.personas.find((persona) => persona.id === personaId)?.name ?? 'Persona';
  }
}
