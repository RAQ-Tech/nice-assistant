import { describe, expect, it } from 'vitest';

import { speakableText, speechText } from '../src/speech_text';

describe('what a reply sounds like', () => {
  it('does not read an acted-out aside aloud', () => {
    expect(speechText('*smiles* It is good to see you. *laughs softly* Really.')).toBe('It is good to see you. Really.');
    expect(speechText('That is lovely.\n*leans back in her chair*\nTell me more.')).toBe('That is lovely. Tell me more.');
  });

  it('still says a word emphasised inside a sentence', () => {
    // *really* here is emphasis, not a stage direction; leaving it out would
    // change what was said.
    expect(speechText('I *really* want to hear about it.')).toBe('I really want to hear about it.');
    expect(speechText('That was **brilliant**, honestly.')).toBe('That was brilliant , honestly.');
  });

  it('never says a bullet point marker or reads it as an aside', () => {
    expect(speechText('* first thing\n* second thing')).toBe('first thing second thing');
  });

  it('never speaks a model thinking, closed or not', () => {
    expect(speechText('<think>They want a joke.</think>Here is one.')).toBe('Here is one.');
    expect(speechText('Here is one. <think>Wait, was that')).toBe('Here is one.');
  });
});

describe('what can be spoken while the reply is still arriving', () => {
  it('holds back reasoning until it closes, so none of it is spoken in the meantime', () => {
    // The bug this fixes: a sentence-sized piece of an unfinished <think> block
    // used to be sent to the voice with only the tag stripped.
    expect(speakableText('<think>The user is asking about the weather. I should')).toBe('');
    expect(speakableText('<think>The user is asking about the weather.</think>It is sunny today. ')).toBe('It is sunny today. ');
  });

  it('holds back an aside on the line still being written', () => {
    expect(speakableText('It is good to see you. *smiles and')).toBe('It is good to see you. ');
    expect(speakableText('It is good to see you. *smiles* How have you been? ')).toBe(
      'It is good to see you.  How have you been? ',
    );
  });

  it('only ever adds to what it already returned', () => {
    const reply = 'Hello there. *waves* <think>be warm</think>It has been a while. *grins* Shall we?';
    let previous = '';
    for (let length = 0; length <= reply.length; length += 1) {
      const spoken = speakableText(reply.slice(0, length));
      // A sentence once spoken can never be unsaid, so nothing here may shrink
      // or change what came before.
      expect(spoken.startsWith(previous)).toBe(true);
      previous = spoken;
    }
    expect(speakableText(reply, true).startsWith(previous)).toBe(true);
  });

  it('lets a lone asterisk through once the reply is finished', () => {
    expect(speakableText('Remember the footnote. *Prices may vary')).toBe('Remember the footnote. ');
    expect(speakableText('Remember the footnote. *Prices may vary', true)).toBe('Remember the footnote. *Prices may vary');
  });
});
