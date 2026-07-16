import { describe, expect, it } from 'vitest';

import { clearIdentitySetupContext, ClientStateMachine, createState } from '../src/state';

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

  it('clears request-scoped identity setup context at the session boundary', () => {
    const state = createState();
    state.mediaCatalogIdentitySetupIntent = {
      capability_request_id: 'request-1',
      chat_id: 'chat-1',
      persona_id: 'persona-1',
      prompt: 'private prompt',
      required_features: ['identity_control'],
      block_code: 'identity_workflow_unavailable',
    };

    clearIdentitySetupContext(state);

    expect(state.mediaCatalogIdentitySetupIntent).toBeNull();
  });
});
