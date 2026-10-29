import type { ApiClient } from './api';
import { el, errorMessage } from './dom';
import { iconButton } from './icons';

/**
 * Choosing a voice by listening to it.
 *
 * A voice was a box to type `af_heart` into, with nothing to say what any name
 * sounded like or which names existed. This lists the voices the speech
 * service on this network actually reports, grouped by language with their
 * names written out, and plays one fixed sentence in whichever is chosen. The
 * server makes each sample once and keeps it (ADR 0051).
 *
 * When the service cannot list its voices - it is down, or the address is still
 * being typed - the box to type a name comes back, so a voice can still be set.
 */

const LANGUAGES: Record<string, string> = {
  a: 'American English',
  b: 'British English',
  e: 'Spanish',
  f: 'French',
  h: 'Hindi',
  i: 'Italian',
  j: 'Japanese',
  p: 'Brazilian Portuguese',
  z: 'Mandarin Chinese',
};
const SOUNDS: Record<string, string> = { f: 'female', m: 'male' };
const ENGLISH_FIRST = ['American English', 'British English'];
const OTHER = 'Other voices';
/** Long enough to let somebody finish typing an address before asking it for voices. */
const SETTLE_MS = 600;

/** `af_heart` is Heart, a female American English voice; anything else keeps its own name. */
export function describeVoice(id: string): { group: string; label: string } {
  const match = /^([a-z])([fm])_([a-z0-9]+)$/.exec(id);
  const language = match ? LANGUAGES[match[1]!] : undefined;
  if (!match || !language) return { group: OTHER, label: id };
  const name = match[3]!.charAt(0).toUpperCase() + match[3]!.slice(1);
  return { group: language, label: `${name} · ${SOUNDS[match[2]!]}` };
}

function groupOrder(group: string): string {
  const english = ENGLISH_FIRST.indexOf(group);
  if (english >= 0) return `0${english}`;
  return group === OTHER ? '2' : `1${group}`;
}

export interface VoiceFieldOptions {
  label: string;
  value: string;
  speed: string;
  baseUrl: string;
  change: (voice: string) => void;
  /** An empty choice, and the voice it stands for: a persona that has no voice of its own. */
  inherit?: { label: string; voice: string } | undefined;
  hover?: string | undefined;
  testId?: string | undefined;
}

let sequence = 0;

export class VoicePicker {
  private voices: string[] | null = null;
  private wantedFor: string | null = null;
  private loadedFor: string | null = null;
  private settle: number | null = null;
  private playing: { voice: string; audio: HTMLAudioElement; url: string } | null = null;
  private fetching = '';
  private problem = '';

  constructor(
    private readonly client: ApiClient,
    private readonly render: () => void,
  ) {}

  field(options: VoiceFieldOptions): HTMLElement {
    this.want(options.baseUrl);
    const id = `voice-picker-${++sequence}`;
    const listed = this.loadedFor === options.baseUrl ? this.voices : null;
    const control = listed && listed.length
      ? this.select(id, listed, options)
      : el('input', {
          id,
          class: 'search-input',
          value: options.value,
          placeholder: options.inherit?.label ?? '',
          'data-testid': options.testId,
          oninput: (event: Event) => options.change((event.currentTarget as HTMLInputElement).value.trim()),
        });
    const heard = options.value || options.inherit?.voice || '';
    const playing = this.playing?.voice === heard;
    return el('div', { class: 'setting-row', title: options.hover }, [
      el('label', { textContent: options.label, htmlFor: id }),
      el('div', { class: 'voice-choice' }, [
        control,
        iconButton(playing ? 'stop' : 'play', playing ? 'Stop the sample' : 'Hear this voice', {
          disabled: !heard,
          'aria-busy': this.fetching === heard ? 'true' : false,
          'data-testid': options.testId ? `${options.testId}-sample` : 'voice-sample',
          onclick: () => void this.toggle(heard, options.speed),
        }),
      ]),
      this.problem ? el('p', { class: 'meta voice-problem', textContent: this.problem }) : null,
    ]);
  }

  private select(id: string, voices: string[], options: VoiceFieldOptions): HTMLSelectElement {
    const groups = new Map<string, HTMLOptionElement[]>();
    const known = new Set(voices);
    // A voice set before - a blend, or one this service no longer has - stays chosen and visible.
    const listed = options.value && !known.has(options.value) ? [options.value, ...voices] : voices;
    for (const voice of listed) {
      const { group, label } = describeVoice(voice);
      const option = el('option', { value: voice, selected: voice === options.value, textContent: label });
      groups.set(group, [...(groups.get(group) ?? []), option]);
    }
    const ordered = [...groups.keys()].sort((left, right) => groupOrder(left).localeCompare(groupOrder(right)));
    return el('select', {
      id,
      class: 'chip-select',
      'data-testid': options.testId,
      onchange: (event: Event) => {
        this.stop();
        options.change((event.currentTarget as HTMLSelectElement).value);
      },
    }, [
      options.inherit ? el('option', { value: '', selected: !options.value, textContent: options.inherit.label }) : null,
      ...ordered.map((group) => el('optgroup', { label: group }, groups.get(group) ?? [])),
    ]);
  }

  private want(baseUrl: string): void {
    if (this.wantedFor === baseUrl) return;
    this.wantedFor = baseUrl;
    if (this.settle !== null) window.clearTimeout(this.settle);
    // The first time, at once; after that, once the address has stopped changing.
    const delay = this.loadedFor === null ? 0 : SETTLE_MS;
    this.settle = window.setTimeout(() => {
      this.settle = null;
      void this.load(baseUrl);
    }, delay);
  }

  private async load(baseUrl: string): Promise<void> {
    let voices: string[] = [];
    try {
      voices = (await this.client.voices(baseUrl)).voices;
    } catch {
      voices = [];
    }
    if (this.wantedFor !== baseUrl) return;
    this.voices = voices;
    this.loadedFor = baseUrl;
    this.render();
  }

  private async toggle(voice: string, speed: string): Promise<void> {
    const wasPlaying = this.playing?.voice === voice;
    this.stop(false);
    if (wasPlaying || !voice) {
      this.render();
      return;
    }
    this.problem = '';
    this.fetching = voice;
    this.render();
    try {
      const url = URL.createObjectURL(await this.client.voiceSample(voice, speed));
      const audio = new Audio(url);
      this.playing = { voice, audio, url };
      audio.addEventListener('ended', () => this.stop(), { once: true });
      await audio.play();
    } catch (error) {
      this.stop(false);
      this.problem = errorMessage(error, 'That voice could not be played.');
    } finally {
      this.fetching = '';
      this.render();
    }
  }

  private stop(render = true): void {
    if (this.playing) {
      this.playing.audio.pause();
      URL.revokeObjectURL(this.playing.url);
      this.playing = null;
      if (render) this.render();
    }
  }
}
