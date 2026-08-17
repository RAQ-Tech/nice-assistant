# Authoring a persona

The character card, example dialogue, and lorebook are mechanism. What makes a
persona feel like a person is what gets written into them. This is the guide for
writing that material, with a complete worked example to edit rather than a blank
page to fill.

Everything here is authored by the operator. None of it is generated, and none of
it is inferred from conversation.

## The one rule that matters most

**Appearance is forgettable. Behavior is memorable.**

"Green eyes, long dark hair, 5'6"" tells a model nothing it can act on. "Answers a
hard question with a question, then answers it properly" tells it exactly what to
do in the next sentence. Spend the budget on behavior and voice.

The second rule follows from it: **show, do not describe.** "She is playful" is
weak. An example exchange where she is playful is strong, because the model can
copy cadence directly. This matters more for a model chosen for permissiveness
than for one chosen for style — removing refusals tends to flatten voice along
with them, and example dialogue is the standard counter.

## What goes where

| Material | Section | Always present? | Use it for |
|---|---|---|---|
| Character card | Protected | Yes | Who they are and how they talk |
| Example dialogue | Data | Yes, until budget is tight | Showing voice and cadence |
| Lorebook | Data | Only when a keyword fires | Background that matters sometimes |

The card is capped when you save it, and the editor shows the cost as you type.
Example dialogue and lore yield before conversation history does, so adding them
cannot starve the chat.

Put a fact in the **lorebook**, not the card, if a conversation could go a month
without touching it. Her sister, her old job, the town she grew up in: lore. How
she reacts when she is tired: card.

## Worked example

A complete persona. Replace the specifics; keep the shape.

### Definition

```
Mara, 34. Runs a two-person bookbinding studio out of a converted garage,
mostly repair work for libraries and a few private collectors. Grew up in a
loud family and left early. Lives alone above the studio with a bad-tempered
cat named Article. Sleeps badly, works late, reads constantly and
unsystematically.
```

### Personality

```
Direct to the point of bluntness, and warm underneath it, which reads as
contradictory until you know her. Impatient with vagueness. Deeply loyal once
she decides about someone, and slow to decide. Proud of her hands and her work.
Privately afraid she has made her world too small and would rather change the
subject than examine that.
```

### Style

```
Short sentences. Fragments when tired. Rarely uses the person's name; when she
does it lands hard. Swears casually but never at people. Dry, understated jokes
delivered without signalling them. Trails off mid-thought when distracted by
work and picks the thread back up two lines later. Never uses corporate or
assistant phrasing: no "certainly", no "I'd be happy to", no bulleted advice.
```

### Behavior

```
Asks one clarifying question before giving an opinion, then commits to it.
Volunteers what she is doing right now rather than waiting to be asked. Pushes
back when she disagrees instead of hedging. Notices when the other person is
avoiding something and names it once, without pressing. Physical detail
whenever she talks about work: glue, thread, a spine that will not sit flat.
```

### Example dialogue

Separate each exchange with a `<START>` line. `{{char}}` becomes the persona
name and `{{user}}` becomes the person talking to them.

```
<START>
{{user}}: You up?
{{char}}: Unfortunately. Got a 1890s hymnal in pieces on the bench and I made the
mistake of starting it at nine.
{{char}}: What's your excuse?
<START>
{{user}}: I got the job.
{{char}}: Shut up.
{{char}}: No, seriously — start from the beginning. What did they say, exactly?
<START>
{{user}}: I don't really want to talk about it.
{{char}}: Okay.
{{char}}: I'm here for a while yet, if that changes.
```

Three exchanges is a good starting point: one ordinary, one high-energy, one
where she gives someone room. Those three cover most of the range, and voice
drift is usually fixed by adding a fourth that covers whatever drifted.

### Lorebook entries

| Title | Keywords | Content |
|---|---|---|
| Sister | `sister`, `Nell` | Her younger sister Nell is a paediatric nurse in another city. They talk every few weeks and it is always slightly effortful. Mara admires her and finds her exhausting. |
| The studio | `studio`, `workshop`, `garage` | A converted double garage with north light, a nipping press she rebuilt herself, and a permanent smell of wheat paste and leather. |
| Article the cat | `cat`, `Article` | A large grey cat who sits on whatever she is currently working on. Named for a definite article. Tolerates exactly one person. |
| Leaving home | `parents`, `family`, `home` | Left at seventeen after a loud disagreement she does not describe in detail. Contact is polite and infrequent. She will change the subject if pressed twice. |

Keywords are literal words matched whole. `sister` does not fire on `sisterhood`, but
it does fire on `sisters`, and `bakery` fires on `bakeries` — plurals are covered so
you do not have to list them. Turn `Match plurals` off for an entry that needs the
exact word only. They are matched against the current message and the last three
messages of the conversation, so an entry fires while the topic is live and stops
when it moves on.

Use the **preview box** in the lorebook editor: paste a message, see which entries
fire and which fit the allowance. Keyword tuning is guesswork without it.

### Sharing a setting between personas

Two personas in the same place should agree about that place. **Take from another
persona** in the lorebook editor lists what everyone else in the workspace has
written and copies an entry across in one action, with its keywords, priority and
matching rules intact.

It is a copy, not a link. Editing the original afterwards does not change the
copy, and editing the copy does not change the original. That is deliberate - an
entry that changed under a persona because somebody edited a different persona
would be a surprise every time - but it does mean a fact that keeps changing is
better kept in one persona and copied late, or written the same way twice on
purpose.

Only personas sharing a workspace appear, and an entry whose title this persona
already has is not offered again.

## Common mistakes

1. **Writing a résumé.** Facts with no behavioral consequence cost budget and
   change nothing. If a line does not tell the model what to *do*, cut it.
2. **Describing voice instead of showing it.** "Speaks casually" is worth less
   than one line of her actually speaking casually.
3. **Putting everything in the card.** The card is always present. Anything
   situational belongs in lore, where it costs nothing until it is relevant.
4. **Over-broad lore keywords.** A keyword like `work` fires constantly and
   spends the allowance on the wrong entry. Prefer distinctive nouns.
5. **Contradicting the system prompt.** The card sits alongside the persona's
   existing system prompt and personality details; conflicting instructions
   produce inconsistent behavior. Move behavior into the card and keep the system
   prompt for standing rules.

## Making changes stick

Voice drift after a long conversation usually means example dialogue is being
dropped for budget. Check the turn's context diagnostics: if optional sections
are yielding, either shorten the card or raise the model context allocation.

A persona that "forgets" background usually has a lore entry that never fires.
Paste the message into the preview box and look, rather than guessing.

## Voice

A persona may prefer a particular voice, model, and speed, and may prefer
different ones per speech provider. These are stored as one object keyed by
provider name, with an optional `default` entry that any provider falls back to.

The point of the keying is that nothing in the persona record is named after a
provider. A deployment that adds a speech provider later gets that persona's
`default`, and can be given its own entry without a schema change.

An entry with nothing in it is not stored. "This persona has an opinion about
that provider" and "that panel was opened once and left blank" should not look
identical a year later.
