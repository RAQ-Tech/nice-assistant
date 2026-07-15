import { describe, expect, it } from 'vitest';

import { ClientStateMachine, createState } from '../src/state';

describe('ClientStateMachine', () => {
  it('models the voice-ready conversation lifecycle explicitly', () => {
    const state = createState();
    const machine = new ClientStateMachine(state);
    machine.transition('idle');
    machine.transition('recording');
    machine.transition('transcribing');
    machine.transition('queued');
    machine.transition('thinking');
    machine.transition('speaking');
    machine.transition('idle');
    expect(state.phase).toBe('idle');
    expect(state.statusText).toBe('Idle');
  });

  it('rejects transitions that would hide an impossible client state', () => {
    const state = createState();
    const machine = new ClientStateMachine(state);
    machine.transition('idle');
    expect(() => machine.transition('thinking')).toThrow('idle -> thinking');
  });
});
