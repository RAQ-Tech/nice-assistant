import { el } from './dom';
import { infoTip } from './settings_ui';

let controlSequence = 0;

function labelLine(label: string, controlId: string, help?: string): HTMLElement {
  return el('div', { class: 'setting-label-line' }, [
    el('label', { textContent: label, htmlFor: controlId }),
    help ? infoTip(help, `About ${label}`) : null,
  ]);
}

export function inputField(
  label: string,
  value: string,
  change: (value: string) => void,
  type = 'text',
  rerender = true,
  help?: string,
): HTMLElement {
  const id = `settings-control-${++controlSequence}`;
  return el('div', { class: 'setting-row' }, [
    labelLine(label, id, help),
    el('input', {
      id,
      class: 'search-input',
      type,
      value,
      oninput: (event: Event) => change((event.currentTarget as HTMLInputElement).value),
      onchange: rerender ? (event: Event) => change((event.currentTarget as HTMLInputElement).value) : undefined,
    }),
  ]);
}

export function textareaField(
  label: string,
  value: string,
  change: (value: string) => void,
  rerender = true,
  help?: string,
): HTMLElement {
  const id = `settings-control-${++controlSequence}`;
  return el('div', { class: 'setting-row' }, [
    labelLine(label, id, help),
    el('textarea', {
      id,
      class: 'search-input',
      rows: 3,
      value,
      oninput: (event: Event) => change((event.currentTarget as HTMLTextAreaElement).value),
      onchange: rerender ? (event: Event) => change((event.currentTarget as HTMLTextAreaElement).value) : undefined,
    }),
  ]);
}

export function selectField(
  label: string,
  value: string,
  values: readonly string[],
  change: (value: string) => void,
  testId?: string,
  display: (value: string) => string = titleCase,
  rerender = true,
  help?: string,
): HTMLElement {
  const id = `settings-control-${++controlSequence}`;
  return el('div', { class: 'setting-row' }, [
    labelLine(label, id, help),
    el(
      'select',
      {
        id,
        class: 'chip-select',
        value,
        'data-testid': testId,
        onchange: (event: Event) => {
          change((event.currentTarget as HTMLSelectElement).value);
          if (!rerender) return;
        },
      },
      values.map((item) => el('option', { value: item, selected: item === value, textContent: display(item) })),
    ),
  ]);
}

export function toggleField(
  label: string,
  checked: boolean,
  change: (checked: boolean) => void,
  help?: string,
): HTMLElement {
  const id = `settings-control-${++controlSequence}`;
  return el('div', { class: 'setting-row setting-toggle-row' }, [
    el('label', { class: 'checkbox-row', htmlFor: id }, [
      el('input', {
        id,
        type: 'checkbox',
        checked,
        onchange: (event: Event) => change((event.currentTarget as HTMLInputElement).checked),
      }),
      label,
    ]),
    help ? infoTip(help, `About ${label}`) : null,
  ]);
}

function titleCase(value: string): string {
  if (!value) return 'None';
  return value.replace(/[-_]/g, ' ').replace(/\b\w/g, (character) => character.toUpperCase());
}
