import type { Dialogs } from './settings_view';
import type { ModalState } from './types';

/**
 * The dialog vocabulary, built over whatever owns the modal slot.
 *
 * Each method is one shape of question: free text, yes/no, plain notice,
 * consent with a remembered opt-out, or a choice between named actions. The
 * host hands in how to show a modal and how to re-render; everything about
 * which buttons exist and what Escape means lives here in one place.
 */
export function createDialogs(
  host: { modal: ModalState | null },
  render: () => void,
): Dialogs {
  const close = () => {
    host.modal = null;
    render();
  };
  return {
    prompt(title, message, initial = '') {
      return new Promise((resolve) => {
        host.modal = {
          title,
          message,
          inputValue: initial,
          actions: [
            { label: 'Cancel', run: () => { close(); resolve(null); } },
            { label: 'Continue', kind: 'primary', run: (value) => { close(); resolve(value); } },
          ],
        };
        render();
      });
    },
    confirm(title, message, confirmText = 'Confirm') {
      return new Promise((resolve) => {
        host.modal = {
          title,
          message,
          actions: [
            { label: 'Cancel', run: () => { close(); resolve(false); } },
            { label: confirmText, kind: 'danger', run: () => { close(); resolve(true); } },
          ],
        };
        render();
      });
    },
    info(title, message) {
      host.modal = { title, message, actions: [{ label: 'Close', kind: 'primary', run: close }] };
      render();
    },
    // Consent with a remembered opt-out: cancel, ok, and a checkbox whose
    // meaning the caller owns. Escape cancels, and cancelling never remembers.
    consent(title, message, confirmText, checkboxLabel) {
      return new Promise((resolve) => {
        host.modal = {
          title,
          message,
          checkboxLabel,
          checkboxValue: false,
          actions: [
            { label: 'Cancel', run: () => { close(); resolve({ ok: false, remember: false }); } },
            {
              label: confirmText,
              kind: 'primary',
              run: () => {
                const remember = host.modal?.checkboxValue ?? false;
                close();
                resolve({ ok: true, remember });
              },
            },
          ],
        };
        render();
      });
    },
    // A choice between named actions. The first option doubles as the escape
    // hatch, so pressing Escape can never mean "discard" by accident.
    choice(title, message, options) {
      return new Promise((resolve) => {
        host.modal = {
          title,
          message,
          actions: options.map((label, index) => ({
            label,
            ...(index === options.length - 1 ? { kind: 'primary' as const } : {}),
            run: () => { close(); resolve(index); },
          })),
        };
        render();
      });
    },
  };
}
