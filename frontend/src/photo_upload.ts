import { el, errorMessage } from './dom';
import { icon } from './icons';
import { state } from './state';

/**
 * A photo from the phone or the computer, made small enough to send.
 *
 * A phone photo is several megabytes and twelve megapixels; a model that can
 * see looks at a fraction of that. Drawing it onto a canvas at the size it
 * will be seen also leaves behind everything a photo carries besides its
 * pixels, such as where it was taken - the server strips that again, but it
 * need not travel at all. Upright, because the browser applies the photo's
 * orientation as it decodes it.
 */

// What the server keeps as its longest side; nothing larger is worth sending.
const LONGEST_SIDE = 1568;
// Comfortably inside the server's one-megabyte request limit once wrapped.
const MAX_DATA_URL_CHARS = 900_000;

export async function shrinkPhoto(file: Blob): Promise<string> {
  if (!file.type.startsWith('image/')) throw new Error('That is not a photo.');
  const bitmap = await createImageBitmap(file, { imageOrientation: 'from-image' });
  try {
    const scale = Math.min(1, LONGEST_SIDE / Math.max(bitmap.width, bitmap.height));
    const canvas = document.createElement('canvas');
    canvas.width = Math.max(1, Math.round(bitmap.width * scale));
    canvas.height = Math.max(1, Math.round(bitmap.height * scale));
    const context = canvas.getContext('2d');
    if (!context) throw new Error('This browser cannot prepare a photo.');
    context.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    for (const quality of [0.85, 0.75, 0.6, 0.45]) {
      const url = canvas.toDataURL('image/jpeg', quality);
      if (url.length <= MAX_DATA_URL_CHARS) return url;
    }
    throw new Error('That photo is too detailed to send. Try a smaller one.');
  } finally {
    bitmap.close();
  }
}

/** The model this conversation answers with, and whether it can be shown a photo. */
export function canSendPhoto(model: string, visionModels: readonly string[]): boolean {
  return Boolean(model) && visionModels.includes(model);
}

/** The camera button: a phone offers its camera and library, a computer its files. */
export function photoPicker(render: () => void): HTMLElement {
  return el('label', { class: 'icon-btn composer-attach', title: 'Send a photo', 'data-testid': 'photo-attach' }, [
    icon('camera'),
    el('input', {
      type: 'file',
      accept: 'image/*',
      class: 'composer-photo-input',
      'aria-label': 'Send a photo',
      'data-testid': 'photo-input',
      onchange: async (event: Event) => {
        const input = event.currentTarget as HTMLInputElement;
        const file = input.files?.[0];
        input.value = '';
        if (!file) return;
        try {
          state.draftPhoto = await shrinkPhoto(file);
        } catch (error) {
          state.uiError = errorMessage(error, 'That photo could not be used.');
        }
        render();
      },
    }),
  ]);
}
