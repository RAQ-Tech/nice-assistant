import { describe, expect, it } from 'vitest';

import { degradationSuggestsMoreContext, describeContextDegradation, replyWasTruncated } from '../src/context_notice';

describe('context degradation notice', () => {
  it('says nothing when the turn ran normally', () => {
    expect(describeContextDegradation(null)).toBeNull();
    expect(describeContextDegradation(undefined)).toBeNull();
    expect(describeContextDegradation('')).toBeNull();
  });

  it('names the material that was left out, in plain language', () => {
    expect(describeContextDegradation('history_floor_dropped:summary')).toBe(
      'Replied with less context than usual: the conversation summary left out to keep recent messages in view.',
    );
  });

  it('joins several dropped sections readably', () => {
    const notice = describeContextDegradation('history_floor_dropped:summary,memory,lore');
    expect(notice).toContain('the conversation summary, saved memories, and');
    expect(notice).toContain("this persona's background notes");
  });

  it('explains a summary provider failure without blaming the reader', () => {
    expect(describeContextDegradation('summary_provider_failed')).toBe(
      'Replied with less context than usual: the conversation summary could not be updated, so an older one was used.',
    );
  });

  it('combines reasons when a turn hit more than one', () => {
    const notice = describeContextDegradation('summary_provider_failed; history_floor_dropped:memory');
    expect(notice).toContain('could not be updated');
    expect(notice).toContain('saved memories');
  });

  it('stays silent rather than guessing at an unrecognized reason', () => {
    expect(describeContextDegradation('some_future_reason')).toBeNull();
    expect(describeContextDegradation('history_floor_dropped:unknown_section')).toBe(
      'Replied with less context than usual: Some saved context was left out to keep recent messages.',
    );
  });

  it('suggests more context only when the budget was the cause', () => {
    expect(degradationSuggestsMoreContext('history_floor_dropped:summary')).toBe(true);
    expect(degradationSuggestsMoreContext('summary_provider_failed')).toBe(false);
    expect(degradationSuggestsMoreContext(null)).toBe(false);
  });

  it('says when a reply stopped because it ran out of room', () => {
    // The sentence just stops. Without this the reader cannot tell that from a
    // persona choosing to trail off, which is the whole reason it is recorded.
    expect(describeContextDegradation('reply_truncated')).toBe(
      'Replied with less context than usual: this reply reached its length limit and stopped early.',
    );
    expect(replyWasTruncated('reply_truncated')).toBe(true);
    expect(replyWasTruncated('summary_provider_failed; reply_truncated')).toBe(true);
  });

  it('does not claim truncation for anything else', () => {
    expect(replyWasTruncated(null)).toBe(false);
    expect(replyWasTruncated('summary_provider_failed')).toBe(false);
    expect(replyWasTruncated('history_floor_dropped:summary')).toBe(false);
  });
});
