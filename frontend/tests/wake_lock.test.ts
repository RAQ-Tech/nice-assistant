import { afterEach, describe, expect, it, vi } from 'vitest';

import { createState } from '../src/state';
import { keepAwakeWhileTalking, LINGER_MS } from '../src/wake_lock';

function fakeWakeLock() {
  const sentinels: { released: boolean; release: ReturnType<typeof vi.fn> }[] = [];
  const request = vi.fn(async () => {
    const sentinel = { released: false, release: vi.fn(async () => { sentinel.released = true; }) };
    sentinels.push(sentinel);
    return sentinel;
  });
  Object.defineProperty(navigator, 'wakeLock', { value: { request }, configurable: true });
  return { request, sentinels };
}

afterEach(() => {
  vi.useRealTimers();
  Reflect.deleteProperty(navigator, 'wakeLock');
});

describe('keeping the screen on', () => {
  it('holds it while talking, and lets go a minute after', async () => {
    vi.useFakeTimers();
    const lock = fakeWakeLock();
    const appState = createState();

    appState.phase = 'recording';
    keepAwakeWhileTalking(appState);
    await vi.waitFor(() => expect(lock.request).toHaveBeenCalledWith('screen'));

    appState.phase = 'thinking';
    keepAwakeWhileTalking(appState);
    expect(lock.request).toHaveBeenCalledTimes(1);

    appState.phase = 'idle';
    keepAwakeWhileTalking(appState);
    await vi.advanceTimersByTimeAsync(LINGER_MS - 1000);
    expect(lock.sentinels[0]?.release).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1000);
    expect(lock.sentinels[0]?.release).toHaveBeenCalled();
  });

  it('a new turn before the minute is up keeps the same lock', async () => {
    vi.useFakeTimers();
    const lock = fakeWakeLock();
    const appState = createState();
    appState.phase = 'speaking';
    keepAwakeWhileTalking(appState);
    await vi.waitFor(() => expect(lock.request).toHaveBeenCalledTimes(1));
    appState.phase = 'idle';
    keepAwakeWhileTalking(appState);
    await vi.advanceTimersByTimeAsync(LINGER_MS / 2);
    appState.phase = 'recording';
    keepAwakeWhileTalking(appState);
    await vi.advanceTimersByTimeAsync(LINGER_MS);
    expect(lock.sentinels[0]?.release).not.toHaveBeenCalled();
    expect(lock.request).toHaveBeenCalledTimes(1);
  });

  it('changes nothing where the browser has no wake lock', () => {
    const appState = createState();
    appState.phase = 'recording';
    expect(() => keepAwakeWhileTalking(appState)).not.toThrow();
  });
});
