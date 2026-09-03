import type { ApiClient } from './api';
import { el, errorMessage, formatDate } from './dom';
import { IdentityMediaPicker } from './identity_media_picker';
import { identityAuditCard, identityComparisonPolicyCard, identityImageButton } from './identity_settings_components';
import { PictureLibraryView } from './picture_library_view';
import { actionRow, choiceField, groupTitle, numberField, textField } from './settings_page';
import { titleCase as title } from './settings_ui';
import type { AppState, IdentityValidationSettings, Persona, VisualIdentityProfile } from './types';

/**
 * A persona's pictures, on the persona's own page.
 *
 * Persona Pictures used to be a section of its own: the same face card the
 * persona page shows, then the preferred recipes and kept pictures, then the
 * comparison tools folded. One persona, two pages, and the face on both. Now
 * the recipes and the kept pictures sit under the face on the persona's page,
 * and what comparison needs - the optional verifier, the outcome policy,
 * manual comparison and the activity record - lives inside the face's own
 * fold. Comparison is advisory measurement (ADR 0031), so it stays behind the
 * fold rather than in front of somebody setting a persona up.
 */
export class PersonaPicturesView {
  private providerResult = '';
  private readonly loaded = new Set<string>();
  private readonly mediaPicker: IdentityMediaPicker;
  private readonly library: PictureLibraryView;

  constructor(
    private readonly renderApp: () => void,
    private readonly appState: AppState,
    private readonly client: ApiClient,
  ) {
    this.mediaPicker = new IdentityMediaPicker(renderApp, client, (url) => this.openImage(url));
    this.library = new PictureLibraryView(appState, client, renderApp);
  }

  /** What the page needs beyond the face: the verifier settings once, and this persona's comparisons, history and kept pictures. */
  async load(personaId: string): Promise<void> {
    if (this.loaded.has(personaId)) return;
    this.loaded.add(personaId);
    try {
      if (!this.appState.identitySettings) this.appState.identitySettings = await this.client.identitySettings();
      await this.reload(personaId);
    } catch (error) {
      this.loaded.delete(personaId);
      this.appState.settingsError = errorMessage(error, 'Unable to load the persona’s pictures.');
    }
    this.renderApp();
  }

  private async reload(personaId: string): Promise<void> {
    const [profile, validations, history] = await Promise.all([
      this.client.visualIdentity(personaId),
      this.client.identityValidations(personaId),
      this.client.identityHistory(personaId),
    ]);
    this.appState.identityProfiles[personaId] = profile;
    this.appState.identityValidations[personaId] = validations.items;
    this.appState.identityEvents[personaId] = history.items;
    // The kept pictures are this persona's own; they load with the persona
    // instead of waiting behind a button.
    void this.library.refresh(personaId);
  }

  /** Under the face: the recipes that suit this persona, and the pictures kept for reuse. */
  node(persona: Persona): HTMLElement {
    const profile = this.appState.identityProfiles[persona.id] ?? null;
    return this.library.node(persona.id, profile, () => this.reload(persona.id));
  }

  /** Inside the face's fold: what comparison needs. */
  foldContent(persona: Persona): HTMLElement[] {
    const profile = this.appState.identityProfiles[persona.id] ?? null;
    const validations = this.appState.identityValidations[persona.id] ?? [];
    const events = this.appState.identityEvents[persona.id] ?? [];
    return [
      this.providerBlock(this.appState.identitySettings),
      profile && profile.consent_status === 'granted'
        ? identityComparisonPolicyCard(profile, this.appState.identityBusy, () => void this.saveProfile(profile))
        : null,
      profile ? this.validationManager(persona, profile, validations) : null,
      identityAuditCard(events),
    ].filter((node): node is HTMLElement => node !== null);
  }

  private providerBlock(settings: IdentityValidationSettings | null): HTMLElement {
    if (!settings) return el('p', { class: 'meta', textContent: 'Comparison settings are unavailable.' });
    const enabled = settings.provider === 'compreface';
    return el('div', { class: 'identity-provider-card', 'data-testid': 'identity-provider' }, [
      groupTitle(
        'Optional identity comparison service',
        'A verifier compares a finished picture with the approved photo. It cannot improve generation, so leave it off until reference-aware generation is useful.',
      ),
      choiceField('Comparison service', settings.provider, ['disabled', 'compreface'], (value) => {
        settings.provider = value as IdentityValidationSettings['provider'];
        this.renderApp();
      }, {
        display: (value) => (value === 'disabled' ? 'Off' : 'CompreFace'),
        hover: 'CompreFace is a separately deployed service on this network, used only to compare faces.',
      }),
      enabled
        ? textField('CompreFace service address', settings.base_url, (value) => { settings.base_url = value; }, {
            type: 'url',
            hover: 'Its address on this network.',
          })
        : null,
      enabled
        ? textField('CompreFace API key', settings.api_key, (value) => { settings.api_key = value; }, {
            type: 'password',
            hover: 'A verification key created in CompreFace, encrypted at rest by Nice Assistant.',
          })
        : null,
      enabled
        ? numberField('Stop waiting after (seconds)', String(settings.timeout_seconds), (value) => {
            const parsed = Number(value);
            settings.timeout_seconds = Number.isFinite(parsed) ? Math.max(1, Math.min(120, parsed)) : settings.timeout_seconds;
          }, { hover: 'Bounds each comparison, so an unavailable verifier cannot hold a picture up for ever.' })
        : null,
      actionRow([
        el('button', {
          class: 'send-btn',
          textContent: this.appState.identityBusy ? 'Saving…' : 'Save comparison service',
          disabled: this.appState.identityBusy,
          'data-testid': 'identity-provider-save',
          onclick: () => void this.saveProvider(),
        }),
        enabled
          ? el('button', {
              class: 'pill-btn',
              textContent: this.appState.identityBusy ? 'Checking…' : 'Test connection',
              disabled: this.appState.identityBusy,
              onclick: () => void this.checkProvider(),
            })
          : null,
        this.providerResult ? el('span', { class: 'provider-check-message', textContent: this.providerResult }) : null,
      ]),
    ]);
  }

  private validationManager(
    persona: Persona,
    profile: VisualIdentityProfile,
    validations: AppState['identityValidations'][string],
  ): HTMLElement {
    return el('div', { class: 'identity-validation-manager' }, [
      groupTitle('Manual comparison', 'Compare one of your generated pictures with the approved photo. The picture is not changed.'),
      el('p', {
        class: 'meta',
        textContent: profile.validation_ready
          ? `Choose a generated picture to compare with ${persona.name}’s approved photo.`
          : 'Needs an approved photo and a comparison service.',
      }),
      el('button', {
        class: 'pill-btn',
        textContent: 'Choose a picture to compare',
        disabled: !profile.validation_ready || this.appState.identityBusy,
        'data-testid': 'identity-validation-gallery-open',
        onclick: () => void this.mediaPicker.open('validation'),
      }),
      this.mediaPicker.isOpen('validation')
        ? this.mediaPicker.node({
            mode: 'validation',
            actionLabel: 'Compare picture',
            actionDisabled: this.appState.identityBusy,
            onUse: (item) => this.validateMedia(persona.id, item.id),
          })
        : null,
      validations.length
        ? el('div', { class: 'identity-validation-list' }, validations.map((validation) =>
            el('div', { class: 'identity-validation-card' }, [
              identityImageButton(
                this.client.mediaUrl(validation.candidate_media_id),
                `Open compared picture for ${persona.name}`,
                `Compared with ${persona.name}`,
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
            ])))
        : el('p', { class: 'meta', textContent: 'No pictures have been compared by hand.' }),
    ]);
  }

  private async saveProvider(): Promise<void> {
    const settings = this.appState.identitySettings;
    if (!settings) return;
    await this.run(async () => {
      this.appState.identitySettings = await this.client.updateIdentitySettings(settings);
      this.providerResult = settings.provider === 'disabled' ? 'Comparison is off.' : 'Comparison service saved.';
      await Promise.all([...this.loaded].map((personaId) => this.reload(personaId)));
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
        await this.reload(profile.persona_id).catch(() => undefined);
        throw error;
      }
    });
  }

  private async validateMedia(personaId: string, mediaId: string): Promise<void> {
    await this.run(async () => {
      const accepted = await this.client.validateIdentityMedia(personaId, mediaId);
      this.mediaPicker.close();
      await this.waitJob(accepted.job.id);
      await this.reload(personaId);
    });
  }

  private async waitJob(jobId: string): Promise<void> {
    const deadline = Date.now() + 120_000;
    while (Date.now() < deadline) {
      const job = await this.client.job(jobId);
      if (['completed', 'failed', 'cancelled'].includes(job.status)) return;
      await new Promise((resolve) => window.setTimeout(resolve, 250));
    }
    throw new Error('The comparison is still running. Come back to this fold later to see the result.');
  }

  private async run(action: () => Promise<void>): Promise<void> {
    this.appState.identityBusy = true;
    this.appState.settingsError = '';
    this.renderApp();
    try {
      await action();
    } catch (error) {
      this.appState.settingsError = errorMessage(error, 'The comparison settings could not be changed.');
    } finally {
      this.appState.identityBusy = false;
      this.renderApp();
    }
  }
}
