import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ANSWER_WAIT_MS, ConversationLoop, REOPEN_DELAY_MS } from '../src/conversation_loop';
import type { RecordingController } from '../src/recording';
import { ClientStateMachine, createState, state } from '../src/state';
import { talkButton } from '../src/talk_button';
import type { Settings } from '../src/types';

/**
 * A hands-free conversation keeps going: once the persona has answered, the
 * microphone opens again, until somebody stops.
 */

function conversation() {
  const appState = createState();
  appState.phase = 'idle';
  const machine = new ClientStateMachine(appState);
  const recording = { start: vi.fn(async () => undefined) };
  const loop = new ConversationLoop(recording, appState, machine);
  return { appState, machine, recording, loop };
}

describe('a hands-free conversation', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('listens again once a reply has been spoken', () => {
    const { machine, recording, loop } = conversation();
    loop.begin();
    machine.transition('speaking');
    loop.answered();
    // Not while the persona is still speaking: the microphone would hear it.
    vi.advanceTimersByTime(REOPEN_DELAY_MS * 3);
    expect(recording.start).not.toHaveBeenCalled();

    machine.transition('idle');
    vi.advanceTimersByTime(REOPEN_DELAY_MS);
    expect(recording.start).toHaveBeenCalledWith(true, { idleMs: ANSWER_WAIT_MS });
  });

  it('listens again straight away when the reply was not spoken', () => {
    const { recording, loop } = conversation();
    loop.begin();
    loop.answered();
    vi.advanceTimersByTime(REOPEN_DELAY_MS);
    expect(recording.start).toHaveBeenCalledTimes(1);
  });

  it('keeps going when the reply finished speaking before it was reported answered', () => {
    const { appState, machine, recording, loop } = conversation();
    loop.begin();
    machine.transition('speaking');
    machine.transition('idle');
    vi.advanceTimersByTime(REOPEN_DELAY_MS);
    appState.phase = 'recording';
    loop.answered();
    expect(loop.running).toBe(true);
    expect(recording.start).toHaveBeenCalledTimes(1);
  });

  it('ends when nobody answers', () => {
    const { machine, recording, loop } = conversation();
    loop.begin();
    loop.nothingSaid('silence');
    expect(loop.running).toBe(false);
    machine.transition('speaking');
    machine.transition('idle');
    vi.advanceTimersByTime(REOPEN_DELAY_MS * 3);
    expect(recording.start).not.toHaveBeenCalled();
  });

  it('gives a wordless noise another chance, but not endlessly', () => {
    const { recording, loop } = conversation();
    loop.begin();
    loop.nothingSaid('empty');
    vi.advanceTimersByTime(REOPEN_DELAY_MS);
    expect(recording.start).toHaveBeenCalledTimes(1);
    loop.nothingSaid('empty');
    expect(loop.running).toBe(false);
  });

  it('ends when the turn fails', () => {
    const { machine, loop } = conversation();
    loop.begin();
    machine.transition('queued');
    machine.transition('error');
    expect(loop.running).toBe(false);
  });

  it('leaves the microphone to whatever happened in the meantime', () => {
    const { appState, recording, loop } = conversation();
    loop.begin();
    loop.answered();
    // Somebody tapped to talk before the conversation reopened it.
    appState.phase = 'recording';
    vi.advanceTimersByTime(REOPEN_DELAY_MS);
    expect(recording.start).not.toHaveBeenCalled();
  });

  it('does nothing once it has been ended', () => {
    const { recording, loop } = conversation();
    loop.begin();
    loop.answered();
    loop.end();
    vi.advanceTimersByTime(REOPEN_DELAY_MS);
    expect(recording.start).not.toHaveBeenCalled();
  });
});

describe('the talk button in a conversation', () => {
  function listeningButton(heardSpeech: boolean) {
    state.settings = { stt_provider: 'local', stt_hands_free: true } as Settings;
    state.phase = 'recording';
    const recording = { start: vi.fn(), stop: vi.fn(), cancel: vi.fn(), heardSpeech } as unknown as RecordingController;
    const loop = { running: true, begin: vi.fn(), end: vi.fn() } as unknown as ConversationLoop;
    const node = talkButton(false, () => undefined, recording, loop) as HTMLButtonElement;
    return { node, recording, loop };
  }

  afterEach(() => {
    state.phase = 'idle';
  });

  it('sends what was said when tapped mid-answer', () => {
    const { node, recording, loop } = listeningButton(true);
    node.click();
    expect(recording.stop).toHaveBeenCalled();
    expect(loop.end).not.toHaveBeenCalled();
  });

  it('ends the conversation when tapped before anything was said', () => {
    const { node, recording, loop } = listeningButton(false);
    node.click();
    // Not silence sent off to be transcribed: "never mind".
    expect(recording.cancel).toHaveBeenCalled();
    expect(recording.stop).not.toHaveBeenCalled();
    expect(loop.end).toHaveBeenCalled();
  });

  it('says what is happening, and only there', () => {
    const { node } = listeningButton(false);
    expect(node.textContent).toBe('Listening…');
    expect(node.classList.contains('listening')).toBe(true);

    state.phase = 'thinking';
    const loop = { running: true } as unknown as ConversationLoop;
    const thinking = talkButton(true, () => undefined, {} as RecordingController, loop) as HTMLButtonElement;
    expect(thinking.textContent).toBe('Thinking…');
    expect(thinking.disabled).toBe(true);
  });

  it('tells a held button to be let go', () => {
    state.settings = { stt_provider: 'local', stt_hands_free: false } as Settings;
    state.phase = 'recording';
    const node = talkButton(false, () => undefined, {} as RecordingController) as HTMLButtonElement;
    expect(node.textContent).toBe('Release to send');
  });
});
