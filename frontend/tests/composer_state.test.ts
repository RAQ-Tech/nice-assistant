import { describe, expect, it } from 'vitest';

import { composerState } from '../src/composer_state';

describe('composerState', () => {
  it('keeps typed and push-to-talk interruption available during speech playback', () => {
    expect(composerState('speaking')).toEqual({ busy: false, inputLocked: false });
  });

  it('keeps active turn and transcription phases locked', () => {
    expect(composerState('queued')).toEqual({ busy: true, inputLocked: false });
    expect(composerState('thinking')).toEqual({ busy: true, inputLocked: false });
    expect(composerState('transcribing')).toEqual({ busy: true, inputLocked: true });
  });
});
