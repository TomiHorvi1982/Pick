# Neverlast ladička (vendored)

`neverlast-ladicka.js` is the built single-file bundle of the tuner web
component `<neverlast-ladicka>`, copied from
`/Users/maxbook/Documents/AI Projekty/Aplikace/Ladicka/dist/` (README option B).
`LICENSE-MoChord.txt` must stay next to it: the pitch detector is MoChord's (MIT).

To update: run `npm run build` in the tuner's folder and copy `dist/neverlast-ladicka.js` over this one.

## Changed here (marked "Pick:" in the file)

Copying a new build over this file loses these — make them in the tuner's own source too:

- each input channel is analysed on its own and the one with the clearest note is read
  (mixed together, a hum on a modeller's second USB channel buried the guitar on the first);
- the level gate is 5e-4 instead of 9e-3 (a plain guitar signal over USB can be that quiet);
- a pitch found at the longest period looked at is rejected (mains hum below the range).
