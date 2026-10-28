import type { ListenOptions, NothingSaid, RecordingController } from './recording';
import { machine, state, type ClientStateMachine } from './state';
import type { AppState, ClientPhase } from './types';

/** How long a microphone the conversation reopened waits for somebody to answer. */
export const ANSWER_WAIT_MS = 30_000;
/** A breath between the persona's last word and the microphone opening, so its ring is not heard. */
export const REOPEN_DELAY_MS = 300;
/** Recordings in a row with no words in them before the conversation stops listening. */
const EMPTY_LIMIT = 2;

/**
 * A hands-free conversation: once the persona has answered, listen again.
 *
 * Tapping to talk with hands-free listening on starts one. From then on every
 * reply ends by opening the microphone, so the next thing said is the next
 * turn, until somebody stops: by tapping before they have said anything, by
 * typing instead, by cancelling or stopping the voice, or by not answering at
 * all for half a minute.
 *
 * Half-duplex, on purpose. The microphone is closed while the persona speaks
 * and opens once it has finished. Listening while it spoke would hear it - a
 * laptop's speaker is a foot from its microphone - and on a phone an open
 * microphone moves the persona's voice to the earpiece, quietly. Talking over
 * the persona is still possible: a tap stops it and listens, as before.
 * Noticing on its own that somebody has started talking over it is later work.
 */
export class ConversationLoop {
  private active = false;
  private empties = 0;
  private reopen: ReturnType<typeof setTimeout> | null = null;

  constructor(
    private readonly recording: { start(handsFree?: boolean, options?: ListenOptions): Promise<void> },
    private readonly appState: AppState = state,
    stateMachine: ClientStateMachine = machine,
  ) {
    stateMachine.onTransition((from, to) => this.transitioned(from, to));
  }

  get running(): boolean {
    return this.active;
  }

  /** Somebody tapped to talk hands-free. */
  begin(): void {
    this.active = true;
    this.empties = 0;
  }

  /** Stop listening again after replies. Whatever is happening right now carries on. */
  end(): void {
    this.active = false;
    if (this.reopen !== null) clearTimeout(this.reopen);
    this.reopen = null;
  }

  /** What was said has been sent and answered, and any speech has begun. */
  answered(): void {
    if (!this.active) return;
    this.empties = 0;
    // While the persona is still speaking, the end of its speech reopens the
    // microphone; see `transitioned`. A short reply may already have finished
    // and been listened past, which is the same thing arrived early.
    if (this.appState.phase === 'speaking' || this.appState.phase === 'recording') return;
    if (this.appState.phase === 'idle') this.listen();
    else this.end();
  }

  /** A recording ended with nothing to send. */
  nothingSaid(reason: NothingSaid): void {
    if (!this.active) return;
    // Nobody spoke, so the conversation is over. A cough or a door - heard,
    // but with no words in it - gets another chance, but not endlessly.
    this.empties += 1;
    if (reason === 'silence' || this.empties >= EMPTY_LIMIT) {
      this.end();
      return;
    }
    this.listen();
  }

  private transitioned(from: ClientPhase, to: ClientPhase): void {
    if (!this.active) return;
    if (to === 'error' || to === 'signed_out') this.end();
    else if (from === 'speaking' && to === 'idle') this.listen();
  }

  private listen(): void {
    if (this.reopen !== null) return;
    this.reopen = setTimeout(() => {
      this.reopen = null;
      // Something else may have happened meanwhile - a tap, a typed message -
      // and that is what wins.
      if (!this.active || this.appState.phase !== 'idle') return;
      void this.recording.start(true, { idleMs: ANSWER_WAIT_MS });
    }, REOPEN_DELAY_MS);
  }
}

/**
 * Join the microphone to the conversation: what is said is sent and answered,
 * a recording with nothing in it is reported, and the talk button's ring
 * follows the voice.
 */
export function connectConversation(
  recording: RecordingController,
  loop: ConversationLoop,
  send: (text: string) => Promise<void>,
  onChange: () => void,
): void {
  recording.configure(
    onChange,
    async (text) => {
      await send(text);
      loop.answered();
    },
    {
      onNothing: (reason) => loop.nothingSaid(reason),
      // Straight onto the button rather than through a render: ten times a
      // second is too often to rebuild the page to move one ring.
      onLevel: (level) =>
        document.querySelector<HTMLElement>('[data-testid="talk-button"]')?.style.setProperty('--mic-level', level.toFixed(2)),
    },
  );
}
