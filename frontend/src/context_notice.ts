/**
 * Turns a turn's `degraded_reason` into something the person in the conversation can act
 * on. The platform records the reason truthfully; this decides how to say it.
 *
 * Reasons are produced by `ContextService.plan`, joined with "; " when more than one
 * applies. `history_floor_dropped` carries the section names that yielded.
 */

const REPLY_TRUNCATED = 'reply_truncated';

const SECTION_NAMES: Record<string, string> = {
  summary: 'the conversation summary',
  memory: 'saved memories',
  lore: "this persona's background notes",
  example_dialogue: "this persona's voice examples",
};

function listPhrase(parts: string[]): string {
  if (parts.length <= 1) return parts[0] ?? '';
  if (parts.length === 2) return `${parts[0]} and ${parts[1]}`;
  return `${parts.slice(0, -1).join(', ')}, and ${parts[parts.length - 1]}`;
}

function describeOne(reason: string): string | null {
  const trimmed = reason.trim();
  if (!trimmed) return null;

  if (trimmed.startsWith('history_floor_dropped:')) {
    const dropped = trimmed
      .slice('history_floor_dropped:'.length)
      .split(',')
      .map((name) => SECTION_NAMES[name.trim()])
      .filter((label): label is string => Boolean(label));
    if (!dropped.length) return 'Some saved context was left out to keep recent messages.';
    return `${listPhrase(dropped)} left out to keep recent messages in view.`;
  }
  if (trimmed === 'summary_provider_failed') {
    return 'the conversation summary could not be updated, so an older one was used.';
  }
  if (trimmed === 'summary_catchup_limited') {
    return 'the conversation summary is still catching up on earlier messages.';
  }
  if (trimmed === REPLY_TRUNCATED) {
    return 'this reply reached its length limit and stopped early.';
  }
  return null;
}

/** A single sentence, or null when the turn ran normally or the reason is unrecognized. */
export function describeContextDegradation(reason: string | null | undefined): string | null {
  if (!reason) return null;
  const parts = reason
    .split(';')
    .map((piece) => describeOne(piece))
    .filter((piece): piece is string => Boolean(piece));
  if (!parts.length) return null;
  const sentence = listPhrase(parts);
  return `Replied with less context than usual: ${sentence}`;
}

/**
 * Whether a reply was cut off rather than finished.
 *
 * Worth separating from the rest because it is the one a person notices without
 * being told - the sentence just stops - and the only one where the marker
 * answers a question they are already asking.
 */
export function replyWasTruncated(reason: string | null | undefined): boolean {
  return Boolean(reason && reason.split(';').some((piece) => piece.trim() === REPLY_TRUNCATED));
}

/** Whether raising the model context allocation is the fix worth suggesting. */
export function degradationSuggestsMoreContext(reason: string | null | undefined): boolean {
  return Boolean(reason && reason.includes('history_floor_dropped:'));
}
