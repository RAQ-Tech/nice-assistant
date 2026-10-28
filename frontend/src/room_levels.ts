import { TURN_DETECTION_DEFAULTS, type TurnDetectorOptions } from './turn_detection';

/** The two levels a turn is judged by. */
export type SpeechLevels = Pick<Required<TurnDetectorOptions>, 'speechLevel' | 'silenceLevel'>;

/** How long the room is listened to, as soon as the microphone opens, before anything is judged. */
export const CALIBRATION_MS = 400;

// How far above the room speech and silence sit. Below the defaults nothing
// moves - a quiet room is judged exactly as before - and the ceilings keep a
// measurement taken while somebody was already talking from setting the bar
// above ordinary speech.
const SPEECH_OVER_ROOM = 2.5;
const SILENCE_OVER_ROOM = 1.6;
const SPEECH_LEVEL_CEILING = 0.12;
const SILENCE_LEVEL_CEILING = 0.07;

/**
 * The levels for this room, from what the microphone heard just after it opened.
 *
 * Fixed levels assume a quiet room. In a noisy one - a fan, a television, a
 * kitchen - the room itself sits above the silence level, so silence is never
 * heard and a hands-free turn never ends until the one-minute ceiling. The
 * quietest moment of the first instant is taken as the room, because a room
 * is the floor under speech, never above it.
 */
export function levelsForRoom(samples: readonly number[], base: SpeechLevels = TURN_DETECTION_DEFAULTS): SpeechLevels {
  if (!samples.length) return { speechLevel: base.speechLevel, silenceLevel: base.silenceLevel };
  const room = Math.min(...samples);
  return {
    speechLevel: Math.min(SPEECH_LEVEL_CEILING, Math.max(base.speechLevel, room * SPEECH_OVER_ROOM)),
    silenceLevel: Math.min(SILENCE_LEVEL_CEILING, Math.max(base.silenceLevel, room * SILENCE_OVER_ROOM)),
  };
}
