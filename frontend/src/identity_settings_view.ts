import type { ApiClient } from './api';
import { el, errorMessage, formatBytes, formatDate } from './dom';
import type { AppState, IdentityReference, IdentityValidationSettings, VisualIdentityProfile } from './types';

interface IdentityDialogs {
  prompt(title: string, message: string, initial?: string): Promise<string | null>;
  confirm(title: string, message: string, confirmText?: string): Promise<boolean>;
}

export class IdentitySettingsView {
  private selectedFile: File | null = null;
  private provenance: IdentityReference['provenance'] = 'user_upload';
  private attested = false;
  private candidateMediaId = '';
  private referenceMediaId = '';
  private providerResult = '';

  constructor(
    private readonly renderApp: () => void,
    private readonly appState: AppState,
    private readonly client: ApiClient,
    private readonly dialogs: IdentityDialogs,
  ) {}

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
    const settings = this.appState.identitySettings;
    const personaId = this.appState.identitySelectedPersonaId;
    const profile = personaId ? this.appState.identityProfiles[personaId] : null;
    return [
      el('div', {
        class: 'meta',
        textContent:
          'Nice Assistant owns consent, reference provenance, deletion, and validation history. The verifier receives two images for a stateless comparison; no face is enrolled into the verifier by this integration.',
      }),
      el('div', {
        class: 'meta',
        textContent:
          'Generated media is never identified as the persona unless a real comparison passes the configured threshold. An unavailable verifier leaves the image unverified.',
      }),
      this.providerCard(settings),
      this.personaSelector(),
      profile ? this.profileCard(profile) : el('div', { class: 'meta', textContent: 'Select a persona to manage visual identity.' }),
    ];
  }

  private providerCard(settings: IdentityValidationSettings | null): HTMLElement {
    if (!settings) {
      return el('div', {
        class: 'persona-card',
        textContent: this.appState.identityBusy ? 'Loading verifier settings…' : 'Verifier settings are unavailable.',
      });
    }
    return el('div', { class: 'persona-card identity-provider-card' }, [
      el('h4', { textContent: 'LAN identity verifier' }),
      field('Provider', select(settings.provider, ['disabled', 'compreface'], (value) => {
        settings.provider = value as IdentityValidationSettings['provider'];
      })),
      field('Service URL', input(settings.base_url, (value) => { settings.base_url = value; }, 'url')),
      field('API key', input(settings.api_key, (value) => { settings.api_key = value; }, 'password')),
      field('Timeout seconds', input(String(settings.timeout_seconds), (value) => {
        settings.timeout_seconds = boundedNumber(value, 1, 120, settings.timeout_seconds);
      }, 'number')),
      el('div', { class: 'chips' }, [
        el('button', {
          class: 'send-btn',
          textContent: this.appState.identityBusy ? 'Saving…' : 'Save verifier',
          disabled: this.appState.identityBusy,
          'data-testid': 'identity-provider-save',
          onclick: () => void this.saveProvider(),
        }),
        el('button', {
          class: 'pill-btn',
          textContent: this.appState.identityBusy ? 'Checking…' : 'Check verifier',
          disabled: this.appState.identityBusy,
          onclick: () => void this.checkProvider(),
        }),
        this.providerResult ? el('span', { class: 'provider-check-message', textContent: this.providerResult }) : null,
      ]),
    ]);
  }

  private personaSelector(): HTMLElement {
    return field(
      'Persona',
      select(
        this.appState.identitySelectedPersonaId ?? '',
        this.appState.personas.map((persona) => persona.id),
        (value) => {
          this.appState.identitySelectedPersonaId = value || null;
          void this.refresh();
        },
        (value) => this.appState.personas.find((persona) => persona.id === value)?.name ?? value,
      ),
    );
  }

  private profileCard(profile: VisualIdentityProfile): HTMLElement {
    const consentActive = profile.consent_status === 'granted';
    const persona = this.appState.personas.find((item) => item.id === profile.persona_id);
    const validations = this.appState.identityValidations[profile.persona_id] ?? [];
    const events = this.appState.identityEvents[profile.persona_id] ?? [];
    return el('div', { class: 'identity-profile-stack' }, [
      el('div', { class: 'persona-card' }, [
        el('div', { class: 'task-model-head' }, [
          el('div', {}, [
            el('strong', { textContent: `${persona?.name ?? 'Persona'} visual identity` }),
            el('div', {
              class: 'meta',
              textContent: `${title(profile.status)} · consent ${title(profile.consent_status)} · ${profile.approved_reference_count} approved references`,
            }),
          ]),
          el('span', {
            class: `provider-status ${profile.validation_ready ? 'ok' : 'fail'}`,
            textContent: profile.validation_ready ? 'Validation ready' : 'Not validation ready',
          }),
        ]),
        textareaRow('Appearance description (generation guidance only)', profile.appearance_description, (value) => {
          profile.appearance_description = value;
        }),
        field('Acceptance threshold', input(String(profile.acceptance_threshold), (value) => {
          profile.acceptance_threshold = boundedNumber(value, 0, 1, profile.acceptance_threshold);
        }, 'number')),
        field('Automatic generation attempt limit', input(String(profile.max_generation_attempts), (value) => {
          profile.max_generation_attempts = Math.round(boundedNumber(value, 1, 10, profile.max_generation_attempts));
        }, 'number')),
        field('Failed validation behavior', select(profile.failure_policy, ['block_claim', 'show_unverified'], (value) => {
          profile.failure_policy = value as VisualIdentityProfile['failure_policy'];
        }, title)),
        el('div', { class: 'chips' }, [
          el('button', {
            class: 'send-btn',
            textContent: 'Save profile',
            disabled: this.appState.identityBusy,
            onclick: () => void this.saveProfile(profile),
          }),
          !consentActive
            ? el('button', {
                class: 'pill-btn',
                textContent: 'Grant consent',
                disabled: this.appState.identityBusy,
                onclick: () => void this.grantConsent(profile.persona_id),
              })
            : el('button', {
                class: 'pill-btn danger',
                textContent: 'Withdraw consent + delete references',
                disabled: this.appState.identityBusy,
                onclick: () => void this.withdrawConsent(profile.persona_id),
              }),
        ]),
      ]),
      this.referenceManager(profile, consentActive),
      this.validationManager(profile, validations),
      el('div', { class: 'persona-card' }, [
        el('h4', { textContent: 'Identity audit' }),
        events.length
          ? el('div', { class: 'identity-audit-list' }, events.slice(0, 30).map((event) =>
              el('div', { class: 'manager-row' }, [
                el('strong', { textContent: title(event.action) }),
                el('span', { class: 'meta', textContent: formatDate(event.created_at) }),
              ]),
            ))
          : el('div', { class: 'meta', textContent: 'No identity events have been recorded.' }),
      ]),
    ]);
  }

  private referenceManager(profile: VisualIdentityProfile, consentActive: boolean): HTMLElement {
    return el('div', { class: 'persona-card' }, [
      el('h4', { textContent: 'Reviewable identity references' }),
      el('div', {
        class: 'meta',
        textContent:
          'Uploads are decoded and re-encoded as metadata-free JPEGs. New references remain pending until you explicitly approve them.',
      }),
      el('label', { class: 'setting-row' }, [
        el('span', { textContent: 'Reference image' }),
        el('input', {
          type: 'file',
          accept: 'image/png,image/jpeg,image/webp',
          disabled: !consentActive,
          onchange: (event: Event) => { this.selectedFile = (event.currentTarget as HTMLInputElement).files?.[0] ?? null; },
        }),
      ]),
      field('Provenance', select(this.provenance, ['user_upload', 'imported'], (value) => {
        this.provenance = value as IdentityReference['provenance'];
      }, title)),
      el('label', { class: 'checkbox-row' }, [
        el('input', {
          type: 'checkbox',
          checked: this.attested,
          disabled: !consentActive,
          onchange: (event: Event) => { this.attested = (event.currentTarget as HTMLInputElement).checked; },
        }),
        'I have consent and the right to use this image as a persistent persona identity reference.',
      ]),
      el('div', { class: 'chips' }, [
        el('button', {
          class: 'send-btn',
          textContent: 'Upload pending reference',
          disabled: !consentActive || this.appState.identityBusy,
          onclick: () => void this.uploadReference(profile.persona_id),
        }),
        input(this.referenceMediaId, (value) => { this.referenceMediaId = value; }, 'text', 'Protected media ID'),
        el('button', {
          class: 'pill-btn',
          textContent: 'Copy media into pending references',
          disabled: !consentActive || this.appState.identityBusy,
          onclick: () => void this.referenceFromMedia(profile.persona_id),
        }),
      ]),
      ...profile.references.map((reference) => this.referenceCard(reference)),
      profile.references.length ? null : el('div', { class: 'meta', textContent: 'No references have been added.' }),
    ]);
  }

  private referenceCard(reference: IdentityReference): HTMLElement {
    return el('div', { class: 'identity-reference-card', 'data-testid': `identity-reference-${reference.id}` }, [
      reference.content_url
        ? el('img', { class: 'identity-reference-thumb', src: reference.content_url, alt: 'Persona identity reference' })
        : el('div', { class: 'identity-reference-thumb missing', textContent: 'Deleted' }),
      el('div', { class: 'identity-reference-detail' }, [
        el('strong', { textContent: `${title(reference.review_status)}${reference.is_primary ? ' · primary' : ''}` }),
        el('div', {
          class: 'meta',
          textContent: `${title(reference.provenance)} · ${reference.width}×${reference.height} · ${formatBytes(reference.byte_size)}`,
        }),
        reference.rejection_reason ? el('div', { class: 'provider-check-message', textContent: reference.rejection_reason }) : null,
        el('div', { class: 'chips' }, [
          reference.review_status === 'pending'
            ? el('button', { class: 'pill-btn', textContent: 'Approve', onclick: () => void this.approveReference(reference.id) })
            : null,
          reference.review_status === 'pending'
            ? el('button', { class: 'pill-btn', textContent: 'Reject', onclick: () => void this.rejectReference(reference.id) })
            : null,
          el('button', { class: 'icon-btn danger', textContent: 'Delete', onclick: () => void this.deleteReference(reference.id) }),
        ]),
      ]),
    ]);
  }

  private validationManager(
    profile: VisualIdentityProfile,
    validations: AppState['identityValidations'][string],
  ): HTMLElement {
    return el('div', { class: 'persona-card' }, [
      el('h4', { textContent: 'Candidate validation and correction' }),
      el('div', {
        class: 'meta',
        textContent:
          'Validate an existing protected image by media ID. A failed result remains rejected. To make it a future reference, copy it into pending references and approve it explicitly.',
      }),
      el('div', { class: 'chips' }, [
        input(this.candidateMediaId, (value) => { this.candidateMediaId = value; }, 'text', 'Protected media ID'),
        el('button', {
          class: 'send-btn',
          textContent: this.appState.identityBusy ? 'Validating…' : 'Validate identity',
          disabled: !profile.validation_ready || this.appState.identityBusy,
          onclick: () => void this.validateMedia(profile.persona_id),
        }),
        el('button', { class: 'pill-btn', textContent: 'Refresh history', onclick: () => void this.refresh() }),
      ]),
      ...validations.map((validation) =>
        el('div', { class: 'manager-row identity-validation-row' }, [
          el('div', {}, [
            el('strong', { textContent: `${title(validation.claim_status)} · ${title(validation.status)}` }),
            el('div', {
              class: 'meta',
              textContent: `${formatDate(validation.created_at)} · media ${validation.candidate_media_id}${validation.score === null ? '' : ` · ${(validation.score * 100).toFixed(1)}% / ${(validation.threshold * 100).toFixed(1)}%`}`,
            }),
            validation.error ? el('div', { class: 'provider-check-message', textContent: validation.error.message }) : null,
          ]),
          el('span', {
            class: `provider-status ${validation.claim_status === 'verified' ? 'ok' : 'fail'}`,
            textContent: validation.claim_status === 'verified'
              ? 'Verified persona'
              : (validation.claim_status === 'rejected' ? 'Not the persona' : 'Unverified'),
          }),
        ]),
      ),
      validations.length ? null : el('div', { class: 'meta', textContent: 'No candidate images have been validated.' }),
    ]);
  }

  private async saveProvider(): Promise<void> {
    const settings = this.appState.identitySettings;
    if (!settings) return;
    await this.run(async () => {
      this.appState.identitySettings = await this.client.updateIdentitySettings(settings);
      this.providerResult = 'Verifier settings saved.';
    });
  }

  private async checkProvider(): Promise<void> {
    await this.run(async () => {
      const result = await this.client.checkIdentityProvider();
      this.providerResult = `${String(result.status)}: ${String(result.message ?? '')}`;
    });
  }

  private async saveProfile(profile: VisualIdentityProfile): Promise<void> {
    await this.run(async () => {
      this.appState.identityProfiles[profile.persona_id] = await this.client.updateVisualIdentity(profile.persona_id, profile);
    });
  }

  private async grantConsent(personaId: string): Promise<void> {
    const confirmed = await this.dialogs.confirm(
      'Grant visual identity consent',
      'Confirm that you have consent and the right to persist and compare identity reference images for this persona.',
      'Grant consent',
    );
    if (!confirmed) return;
    await this.run(async () => { this.appState.identityProfiles[personaId] = await this.client.grantIdentityConsent(personaId); });
  }

  private async withdrawConsent(personaId: string): Promise<void> {
    const confirmed = await this.dialogs.confirm(
      'Withdraw visual identity consent',
      'This deletes all stored reference files, tombstones their records, and cancels active identity validations. This cannot restore the images.',
      'Withdraw and delete',
    );
    if (!confirmed) return;
    await this.run(async () => { this.appState.identityProfiles[personaId] = await this.client.withdrawIdentityConsent(personaId); });
  }

  private async uploadReference(personaId: string): Promise<void> {
    if (!this.selectedFile || !this.attested) {
      this.appState.settingsError = 'Choose an image and attest consent before uploading.';
      this.renderApp();
      return;
    }
    await this.run(async () => {
      await this.client.uploadIdentityReference(personaId, this.selectedFile as File, this.provenance);
      this.selectedFile = null;
      this.attested = false;
      await this.reloadPersona(personaId);
    });
  }

  private async referenceFromMedia(personaId: string): Promise<void> {
    if (!this.referenceMediaId.trim() || !this.attested) {
      this.appState.settingsError = 'Enter a protected media ID and attest consent first.';
      this.renderApp();
      return;
    }
    await this.run(async () => {
      await this.client.identityReferenceFromMedia(personaId, this.referenceMediaId.trim());
      this.referenceMediaId = '';
      this.attested = false;
      await this.reloadPersona(personaId);
    });
  }

  private async approveReference(referenceId: string): Promise<void> {
    const personaId = this.appState.identitySelectedPersonaId;
    if (!personaId) return;
    await this.run(async () => { await this.client.approveIdentityReference(referenceId); await this.reloadPersona(personaId); });
  }

  private async rejectReference(referenceId: string): Promise<void> {
    const personaId = this.appState.identitySelectedPersonaId;
    if (!personaId) return;
    const reason = await this.dialogs.prompt('Reject reference', 'Record a safe reason for the identity audit.', 'Does not represent this persona.');
    if (reason === null) return;
    await this.run(async () => { await this.client.rejectIdentityReference(referenceId, reason); await this.reloadPersona(personaId); });
  }

  private async deleteReference(referenceId: string): Promise<void> {
    const personaId = this.appState.identitySelectedPersonaId;
    if (!personaId) return;
    const confirmed = await this.dialogs.confirm(
      'Delete identity reference',
      'Delete the stored file and retain only its deletion audit?',
      'Delete reference',
    );
    if (!confirmed) return;
    await this.run(async () => { await this.client.deleteIdentityReference(referenceId); await this.reloadPersona(personaId); });
  }

  private async validateMedia(personaId: string): Promise<void> {
    const mediaId = this.candidateMediaId.trim();
    if (!mediaId) return;
    await this.run(async () => {
      const accepted = await this.client.validateIdentityMedia(personaId, mediaId);
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
    throw new Error('Identity validation is still running. Refresh the history to check it later.');
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
}

function field(label: string, control: HTMLElement): HTMLElement {
  return el('label', { class: 'setting-row' }, [el('span', { textContent: label }), control]);
}

function textareaRow(label: string, value: string, change: (value: string) => void): HTMLElement {
  return field(label, el('textarea', {
    value,
    onchange: (event: Event) => change((event.currentTarget as HTMLTextAreaElement).value),
  }));
}

function input(value: string, change: (value: string) => void, type = 'text', placeholder = ''): HTMLInputElement {
  return el('input', {
    type,
    value,
    placeholder,
    onchange: (event: Event) => change((event.currentTarget as HTMLInputElement).value),
  }) as HTMLInputElement;
}

function select(
  value: string,
  options: readonly string[],
  change: (value: string) => void,
  label: (value: string) => string = (item) => item,
): HTMLSelectElement {
  return el(
    'select',
    { onchange: (event: Event) => change((event.currentTarget as HTMLSelectElement).value) },
    options.map((option) => el('option', { value: option, selected: option === value, textContent: label(option) })),
  ) as HTMLSelectElement;
}

function boundedNumber(value: string, minimum: number, maximum: number, fallback: number): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? Math.min(maximum, Math.max(minimum, parsed)) : fallback;
}

function title(value: string): string {
  return value.replaceAll('_', ' ').replace(/\b\w/g, (letter) => letter.toUpperCase());
}
