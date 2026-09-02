import { el, type Child } from './dom';
import type { SettingsDialogs } from './settings_contracts';

/**
 * One page per thing.
 *
 * The model page proved the shape: the thing's name is the headline, its
 * settings sit under it with almost no prose, arrows walk to the next one, and
 * help waits on hover instead of filling the page. Everything here exists so a
 * persona, a conversation model, or a background role can have the same page
 * without a second copy of the layout.
 *
 * Help is a `title`. It appears on hover, assistive technology reads it as the
 * control's description, and it takes up no room. A page gets at most one
 * visible hint - `pageHint` - for the one thing hover cannot carry. The
 * builders in `settings_controls.ts` are the earlier shape, an information icon
 * beside every row; the pictures pages still use them, and they retire as those
 * pages move over.
 */

let controlSequence = 0;

export interface FieldOptions {
  /** Shown on hover, and read as the control's description. Never visible otherwise. */
  hover?: string | undefined;
  testId?: string | undefined;
  step?: string | undefined;
  rows?: number | undefined;
  disabled?: boolean | undefined;
  /** Runs once the value is committed - after `change`, on the change event. */
  commit?: (() => void) | undefined;
}

function fieldRow(label: string, id: string, control: HTMLElement, options: FieldOptions): HTMLElement {
  return el('div', { class: 'setting-row', title: options.hover }, [
    el('label', { textContent: label, htmlFor: id }),
    control,
  ]);
}

export function textField(
  label: string,
  value: string,
  change: (value: string) => void,
  options: FieldOptions & { type?: string | undefined } = {},
): HTMLElement {
  const id = `page-control-${++controlSequence}`;
  return fieldRow(label, id, el('input', {
    id,
    class: 'search-input',
    type: options.type ?? 'text',
    step: options.step,
    value,
    disabled: options.disabled,
    'data-testid': options.testId,
    oninput: (event: Event) => change((event.currentTarget as HTMLInputElement).value),
    onchange: options.commit,
  }), options);
}

export function numberField(
  label: string,
  value: string,
  change: (value: string) => void,
  options: FieldOptions = {},
): HTMLElement {
  return textField(label, value, change, { ...options, type: 'number' });
}

export function longField(
  label: string,
  value: string,
  change: (value: string) => void,
  options: FieldOptions = {},
): HTMLElement {
  const id = `page-control-${++controlSequence}`;
  return fieldRow(label, id, el('textarea', {
    id,
    class: 'search-input',
    rows: options.rows ?? 3,
    value,
    disabled: options.disabled,
    'data-testid': options.testId,
    oninput: (event: Event) => change((event.currentTarget as HTMLTextAreaElement).value),
    onchange: options.commit,
  }), options);
}

export function choiceField(
  label: string,
  value: string,
  values: readonly string[],
  change: (value: string) => void,
  options: FieldOptions & { display?: ((value: string) => string) | undefined } = {},
): HTMLElement {
  const id = `page-control-${++controlSequence}`;
  const display = options.display ?? ((item: string) => item);
  return fieldRow(label, id, el('select', {
    id,
    class: 'chip-select',
    disabled: options.disabled,
    'data-testid': options.testId,
    onchange: (event: Event) => {
      change((event.currentTarget as HTMLSelectElement).value);
      options.commit?.();
    },
  }, values.map((item) => el('option', { value: item, selected: item === value, textContent: display(item) }))), options);
}

export function switchField(
  label: string,
  checked: boolean,
  change: (checked: boolean) => void,
  options: FieldOptions = {},
): HTMLElement {
  const id = `page-control-${++controlSequence}`;
  return el('div', { class: 'setting-row setting-toggle-row', title: options.hover }, [
    el('label', { class: 'checkbox-row', htmlFor: id }, [
      el('input', {
        id,
        type: 'checkbox',
        checked,
        disabled: options.disabled,
        'data-testid': options.testId,
        onchange: (event: Event) => {
          change((event.currentTarget as HTMLInputElement).checked);
          options.commit?.();
        },
      }),
      label,
    ]),
  ]);
}

/** The one line a page may say out loud. */
export function pageHint(text: string, testId?: string): HTMLElement {
  return el('p', { class: 'meta page-hint', 'data-testid': testId, textContent: text });
}

/** Back to the list, and arrows to the neighbours. */
export function pageNav(options: {
  back: string;
  onBack: () => void;
  arrows?: { previous: (() => void) | null; next: (() => void) | null } | undefined;
  busy?: boolean | undefined;
  testId: string;
}): HTMLElement {
  const arrows = options.arrows;
  return el('div', { class: 'page-nav' }, [
    el('button', {
      class: 'pill-btn',
      textContent: `‹ ${options.back}`,
      'data-testid': `${options.testId}-back`,
      onclick: options.onBack,
    }),
    arrows
      ? el('div', { class: 'chips' }, [
          el('button', {
            class: 'pill-btn',
            textContent: '‹ Previous',
            disabled: !arrows.previous || options.busy,
            'data-testid': `${options.testId}-previous`,
            onclick: () => arrows.previous?.(),
          }),
          el('button', {
            class: 'pill-btn',
            textContent: 'Next ›',
            disabled: !arrows.next || options.busy,
            'data-testid': `${options.testId}-next`,
            onclick: () => arrows.next?.(),
          }),
        ])
      : null,
  ]);
}

/**
 * The headline. An input when the name can be changed here, so renaming is
 * direct, styled to read as a title rather than a form.
 */
export function pageHead(options: {
  thumb?: HTMLElement | null | undefined;
  name: string;
  onName?: ((value: string) => void) | undefined;
  nameTitle?: string | undefined;
  line?: string | undefined;
  testId: string;
}): HTMLElement {
  const name = options.onName
    ? el('input', {
        class: 'page-name',
        value: options.name,
        title: options.nameTitle,
        'aria-label': 'Name',
        'data-testid': `${options.testId}-name`,
        oninput: (event: Event) => options.onName?.((event.currentTarget as HTMLInputElement).value),
      })
    : el('h2', { class: 'page-name page-name-static', textContent: options.name, title: options.nameTitle, 'data-testid': `${options.testId}-name` });
  return el('div', { class: 'page-head' }, [
    options.thumb ?? null,
    el('div', { class: 'page-head-titles' }, [
      name,
      options.line ? el('p', { class: 'meta page-line', textContent: options.line }) : null,
    ]),
  ]);
}

export interface ThingChip {
  id: string;
  label: string;
  thumb?: string | null | undefined;
  thumbFallback?: ((event: Event) => void) | undefined;
  note?: string | undefined;
  title?: string | undefined;
  testId?: string | undefined;
  onOpen: () => void;
}

/** A list of plain things, each opening a page of its own. */
export function thingList(items: ThingChip[], emptyText: string, testId?: string): HTMLElement {
  if (!items.length) return el('p', { class: 'meta', 'data-testid': testId, textContent: emptyText });
  return el('div', { class: 'chips thing-list', 'data-testid': testId }, items.map((item) =>
    el('button', {
      class: `pill-btn thing-open ${item.thumb ? 'has-thumb' : ''}`.trim(),
      title: item.title,
      'data-testid': item.testId,
      onclick: item.onOpen,
    }, [
      item.thumb ? el('img', { class: 'thing-open-thumb', src: item.thumb, alt: '', onerror: item.thumbFallback }) : null,
      el('span', { textContent: item.label }),
      item.note ? el('span', { class: 'thing-open-note', textContent: item.note }) : null,
    ])));
}

/** Save, or the plain statement that there is nothing to save. */
export function saveButton(options: { dirty: boolean; busy: boolean; onSave: () => void; testId: string }): HTMLElement {
  return el('button', {
    class: 'send-btn',
    textContent: options.busy ? 'Saving…' : options.dirty ? 'Save' : 'Saved',
    disabled: options.busy || !options.dirty,
    'data-testid': options.testId,
    onclick: options.onSave,
  });
}

export function actionRow(children: Child[]): HTMLElement {
  return el('div', { class: 'chips' }, children);
}

/** Leave only with intact work: stay, discard, or save first. */
export async function leaveGuard(
  dialogs: SettingsDialogs,
  name: string,
  dirty: boolean,
  save: () => Promise<boolean>,
  leave: () => void,
): Promise<void> {
  if (!dirty) return leave();
  const answer = await dialogs.choice(
    'Save these changes?',
    `${name} has unsaved changes.`,
    ['Stay here', 'Leave without saving', 'Save and continue'],
  );
  if (answer === 1) return leave();
  if (answer === 2 && (await save())) leave();
}
