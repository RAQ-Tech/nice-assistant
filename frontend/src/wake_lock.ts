import type { AppState, ClientPhase } from './types';

/**
 * Keep the screen on while a conversation is happening.
 *
 * A phone propped on the counter dims and locks mid-sentence otherwise, and a
 * locked phone stops listening. The lock is asked for while somebody is being
 * listened to, waited on, or spoken to, and is let go a minute after that ends -
 * long enough to read the reply or answer it, not so long that a forgotten
 * phone stays lit all evening.
 *
 * The browser drops the lock whenever the page is hidden, so it is asked for
 * again on return if the conversation is still going. Where the browser has no
 * wake lock, or refuses it, nothing else changes.
 */

const TALKING: ReadonlySet<ClientPhase> = new Set(['recording', 'transcribing', 'queued', 'thinking', 'speaking']);
export const LINGER_MS = 60_000;

interface WakeLockSentinelLike {
  released: boolean;
  release(): Promise<void>;
}

let sentinel: WakeLockSentinelLike | null = null;
let requesting = false;
let wanted = false;
let lingerTimer: number | null = null;
let watchingVisibility = false;

async function acquire(): Promise<void> {
  const wakeLock = (navigator as Navigator & { wakeLock?: { request(type: 'screen'): Promise<WakeLockSentinelLike> } }).wakeLock;
  if (!wakeLock || requesting || (sentinel && !sentinel.released) || document.visibilityState !== 'visible') return;
  requesting = true;
  try {
    sentinel = await wakeLock.request('screen');
    // Asked for, then no longer wanted while the request was in flight.
    if (!wanted) void release();
  } catch {
    sentinel = null;
  } finally {
    requesting = false;
  }
}

async function release(): Promise<void> {
  const held = sentinel;
  sentinel = null;
  if (held && !held.released) await held.release().catch(() => undefined);
}

export function keepAwakeWhileTalking(appState: AppState): void {
  if (!watchingVisibility) {
    watchingVisibility = true;
    document.addEventListener('visibilitychange', () => {
      if (wanted && document.visibilityState === 'visible') void acquire();
    });
  }
  const talking = TALKING.has(appState.phase) || Boolean(appState.currentAudioMessageId);
  if (talking) {
    if (lingerTimer !== null) {
      window.clearTimeout(lingerTimer);
      lingerTimer = null;
    }
    wanted = true;
    void acquire();
    return;
  }
  if (!wanted || lingerTimer !== null) return;
  lingerTimer = window.setTimeout(() => {
    lingerTimer = null;
    wanted = false;
    void release();
  }, LINGER_MS);
}
