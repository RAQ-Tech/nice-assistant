import type { ChatController } from './chat';
import { composerState, heardSoFar } from './composer_state';
import type { ConversationLoop } from './conversation_loop';
import { el } from './dom';
import { icon } from './icons';
import { photoPicker } from './photo_upload';
import type { RecordingController } from './recording';
import { state } from './state';
import { talkButton } from './talk_button';

export interface ComposerDeps {
  chat: ChatController;
  loop: ConversationLoop;
  recording: RecordingController;
  stopSpeaking: () => void;
  render: () => void;
  /** Whether this conversation's model can be shown a photo (ADR 0046). */
  canSendPhotos: boolean;
}

/**
 * Where a message is typed, said, or corrected.
 *
 * Correcting the latest message happens here rather than in the bubble: its
 * words come back into the box, a line above says so with a way out, and
 * sending answers the corrected words instead of the old ones. Escape, or
 * Cancel, leaves the conversation as it was.
 */
export function composerView(personaName: string, deps: ComposerDeps): HTMLElement {
  const { busy, inputLocked } = composerState(state.phase);
  const cancellableTurn = state.pendingRequest && ['queued', 'thinking'].includes(state.phase);
  const heard = heardSoFar(state.phase, state.partialTranscript);
  const editing = state.editingMessageId;
  const submit = (text: string): void => {
    // Typing instead of talking ends a hands-free conversation.
    deps.loop.end();
    if (editing) void deps.chat.again(editing, text);
    else void deps.chat.send(text, state.draftPhoto);
  };
  const stopEditing = (): void => {
    state.editingMessageId = null;
    state.draftMessage = '';
    deps.render();
  };
  return el('div', { class: 'composer' }, [
    el('div', { class: 'composer-field' }, [
      editing
        ? el('div', { class: 'composer-editing', 'data-testid': 'composer-editing' }, [
            'Editing your message',
            el('button', { class: 'link-btn', textContent: 'Cancel', onclick: stopEditing }),
          ])
        : null,
      state.draftPhoto
        ? el('div', { class: 'composer-photo', 'data-testid': 'composer-photo' }, [
            el('img', { src: state.draftPhoto, alt: 'The photo to send' }),
            el('button', {
              class: 'icon-btn',
              type: 'button',
              title: 'Remove the photo',
              'aria-label': 'Remove the photo',
              onclick: () => { state.draftPhoto = null; deps.render(); },
            }, [icon('close')]),
          ])
        : null,
      el('div', { class: 'composer-row' }, [
        deps.canSendPhotos && !editing ? photoPicker(deps.render) : null,
        el('textarea', {
        id: 'chatInput',
        rows: 1,
        class: `composer-input ${heard ? 'composer-hearing' : ''}`,
        value: heard ?? state.draftMessage,
        readOnly: Boolean(heard),
        placeholder: `Message ${personaName}…`,
        title: 'Enter sends. Shift+Enter starts a new line.',
        disabled: inputLocked && !heard,
        'data-testid': 'chat-input',
        oninput: (event: Event) => {
          const target = event.currentTarget as HTMLTextAreaElement;
          state.draftMessage = target.value;
          autoResize(target);
        },
        onkeydown: (event: KeyboardEvent) => {
          if (event.key === 'Escape' && editing) stopEditing();
          if (event.key !== 'Enter' || event.shiftKey) return;
          event.preventDefault();
          submit((event.currentTarget as HTMLTextAreaElement).value);
        },
      }),
      ]),
    ]),
    cancellableTurn
      ? el('button', {
          class: 'send-btn',
          textContent: 'Cancel',
          'data-testid': 'chat-cancel',
          onclick: () => { deps.loop.end(); void deps.chat.cancel(); },
        })
      : el('button', {
          class: 'send-btn',
          textContent: 'Send',
          disabled: busy,
          'data-testid': 'chat-send',
          onclick: () => submit(state.draftMessage),
        }),
    talkButton(busy, deps.stopSpeaking, deps.recording, deps.loop),
  ]);
}

/** Put a message's words back in the box to be corrected. */
export function startEditing(messageId: string, text: string, render: () => void): void {
  state.editingMessageId = messageId;
  state.draftMessage = text;
  render();
  requestAnimationFrame(() => {
    const input = document.querySelector<HTMLTextAreaElement>('#chatInput');
    if (!input) return;
    input.focus();
    input.setSelectionRange(input.value.length, input.value.length);
    autoResize(input);
  });
}

export function autoResize(target: HTMLTextAreaElement): void {
  target.style.height = 'auto';
  target.style.height = `${Math.min(180, target.scrollHeight)}px`;
}
