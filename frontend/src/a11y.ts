import { speechText } from './speech_text';
import type { AppState } from './types';

/**
 * What a screen reader is told, and where focus goes, after each render.
 *
 * Rendering rebuilds the whole page, so nothing inside it can be a live region:
 * a region recreated on every render is either never read or read every time.
 * These two sit outside the page, made once, and are written to only when
 * something new has happened - a reply finished, or something went wrong.
 */

let polite: HTMLElement | null = null;
let urgent: HTMLElement | null = null;

function region(level: 'polite' | 'assertive'): HTMLElement {
  const node = document.createElement('div');
  node.className = 'sr-only';
  node.setAttribute('aria-live', level);
  node.setAttribute('aria-atomic', 'true');
  if (level === 'assertive') node.setAttribute('role', 'alert');
  document.body.append(node);
  return node;
}

export function announce(text: string, isUrgent = false): void {
  const target = isUrgent ? (urgent ??= region('assertive')) : (polite ??= region('polite'));
  if (!target.isConnected) document.body.append(target);
  // Emptied first, so the same words twice are still two announcements.
  target.textContent = '';
  window.setTimeout(() => { target.textContent = text; }, 50);
}

let lastError = '';
let awaitingAfter: string | null | undefined;

/**
 * Say what changed since the last render, once.
 *
 * A reply is read when the turn that asked for it has finished, not word by
 * word as it streams and not when a chat full of old replies is opened. Errors
 * on the sign-in and setup pages are already alerts on those pages; these are
 * the ones in the conversation, where the banner is redrawn too often to be one.
 */
export function announceChanges(appState: AppState): void {
  const inConversation = Boolean(appState.session && appState.personas.length);
  const error = inConversation ? appState.uiError : '';
  if (error && error !== lastError) announce(error, true);
  lastError = error;

  const latest = [...appState.messages].reverse().find((message) => message.role === 'assistant' && !message.isTyping);
  if (appState.pendingRequest) {
    if (awaitingAfter === undefined) awaitingAfter = latest?.id ?? null;
    return;
  }
  if (awaitingAfter === undefined) return;
  const before = awaitingAfter;
  awaitingAfter = undefined;
  const words = latest && latest.id !== before ? speechText(latest.text).trim() : '';
  if (!words) return;
  const persona = appState.personas.find((item) => item.id === appState.currentChat?.persona_id);
  announce(persona ? `${persona.name}: ${words}` : words);
}

const FOCUSABLE = 'button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])';

/** A dialog that has just opened takes focus, so the keyboard is where the eyes are. */
export function focusOpenDialog(root: HTMLElement): void {
  const dialogs = root.querySelectorAll<HTMLElement>('[role="dialog"]');
  const dialog = dialogs.item(dialogs.length - 1);
  if (!dialog || dialog.contains(document.activeElement)) return;
  const first = dialog.querySelector<HTMLElement>('input:not([disabled]), textarea:not([disabled])') ?? dialog.querySelector<HTMLElement>(FOCUSABLE);
  if (first) first.focus({ preventScroll: true });
  else {
    dialog.tabIndex = -1;
    dialog.focus({ preventScroll: true });
  }
}
