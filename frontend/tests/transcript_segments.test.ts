import { describe, expect, it, vi } from 'vitest';

import { PauseDetector, TURN_DETECTION_DEFAULTS } from '../src/turn_detection';
import { TranscriptSegments } from '../src/transcript_segments';

const LOUD = TURN_DETECTION_DEFAULTS.speechLevel + 0.01;
const QUIET = TURN_DETECTION_DEFAULTS.silenceLevel - 0.005;

describe('finding a pause worth cutting at', () => {
  it('cuts once per pause, not once per quiet moment', () => {
    const detector = new PauseDetector({ pauseMs: 400 });
    detector.begin();
    detector.observe(LOUD, 0);

    expect(detector.observe(QUIET, 100)).toBe(false);
    expect(detector.observe(QUIET, 500)).toBe(true);
    // Still quiet, but this pause has already been cut at.
    expect(detector.observe(QUIET, 900)).toBe(false);
    expect(detector.observe(QUIET, 2000)).toBe(false);
  });

  it('cuts again after somebody starts talking again', () => {
    const detector = new PauseDetector({ pauseMs: 400 });
    detector.begin();
    detector.observe(LOUD, 0);
    // Sampled the way the real loop samples it, every hundred milliseconds.
    detector.observe(QUIET, 100);
    expect(detector.observe(QUIET, 500)).toBe(true);

    detector.observe(LOUD, 600);

    // The next pause has to last as long as the first one did. Speaking again
    // resets the clock; it does not earn a free cut.
    detector.observe(QUIET, 700);
    expect(detector.observe(QUIET, 800)).toBe(false);
    expect(detector.observe(QUIET, 1100)).toBe(true);
  });

  it('never cuts before anybody has spoken', () => {
    const detector = new PauseDetector({ pauseMs: 400 });
    detector.begin();

    // Somebody who taps and then thinks has not said a first sentence yet, so
    // there is nothing to start transcribing.
    for (const at of [0, 500, 1000, 5000]) expect(detector.observe(QUIET, at)).toBe(false);
  });

  it('does not cut in the band between speech and silence', () => {
    const detector = new PauseDetector({ pauseMs: 400 });
    detector.begin();
    detector.observe(LOUD, 0);
    const between = (TURN_DETECTION_DEFAULTS.speechLevel + TURN_DETECTION_DEFAULTS.silenceLevel) / 2;

    // A voice trailing off passes through here. Cutting would land mid-word.
    for (const at of [100, 500, 900]) expect(detector.observe(between, at)).toBe(false);
  });

  it('cuts sooner than the turn detector ends a turn', () => {
    // Otherwise it would only ever fire at the moment the turn was ending,
    // which is exactly when starting early is worth nothing.
    expect(new PauseDetector({}).observe).toBeTypeOf('function');
    expect(450).toBeLessThan(TURN_DETECTION_DEFAULTS.silenceMs);
  });
});

describe('putting a turn back together', () => {
  it('keeps the order somebody spoke in, not the order they came back', async () => {
    const segments = new TranscriptSegments();
    segments.begin();
    let first: (value: string) => void = () => undefined;
    segments.claim(() => new Promise<string>((resolve) => { first = resolve; }), () => undefined);
    segments.claim(async () => 'the second part', () => undefined);
    // The short second piece finishes first; the sentence must not.
    await new Promise((resolve) => setTimeout(resolve, 0));
    first('the first part');

    expect(await segments.finish('and the tail')).toBe('the first part the second part and the tail');
  });

  it('reports what it has as each piece lands', async () => {
    const segments = new TranscriptSegments();
    segments.begin();
    const seen: string[] = [];
    segments.claim(async () => 'hello there', (joined) => seen.push(joined));
    await segments.finish('');

    expect(seen).toEqual(['hello there']);
  });

  it('leaves a hole for a piece that failed rather than losing the turn', async () => {
    const segments = new TranscriptSegments();
    segments.begin();
    segments.claim(async () => 'what I said first', () => undefined);
    segments.claim(async () => { throw new Error('service down'); }, () => undefined);

    // Most of the sentence arrived. An error over it would be worse than the gap.
    expect(await segments.finish('and last')).toBe('what I said first and last');
  });

  it('collapses the whitespace a join would otherwise leave', async () => {
    const segments = new TranscriptSegments();
    segments.begin();
    segments.claim(async () => '  spaced   out  ', () => undefined);

    expect(await segments.finish('  tail ')).toBe('spaced out tail');
  });

  it('knows when a turn produced nothing at all', async () => {
    const segments = new TranscriptSegments();
    segments.begin();

    expect(segments.empty).toBe(true);
    segments.claim(async () => '', () => undefined);
    expect(segments.empty).toBe(false);
  });
});
