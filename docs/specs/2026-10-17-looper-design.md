# Looper — design

A live looper inside Pick, in the spirit of a Boss RC-505 (the heart of Reinhardt Buhr's street sets)
and of Loopy Pro: a favourite song is rebuilt layer by layer — the groove first, then bass, guitars,
keys, voice — each layer playing round in time, and the song's parts (intro, verse, chorus…) made by
switching layers on and off. Everything is played by the feet: a MIDI pedal, or the keyboard.

## What it builds on

- The mixer's audio context and output, so the loops sound with everything else Pick plays.
- The live guitar of Steems (NAM amp and cabinet) and its take recorder, whose samples arrive stamped
  with the audio clock — what lets a loop be cut exactly at a bar.
- The singing microphone (Settings → Mikrofon pro zpěv: device and input).
- The interface's measured latency (the takes' latency), taken off every recording.
- A song open in Steems: its tempo and its parts' names. Without one, the tempo is set by hand.
- The MIDI pedal (practice.js): looper actions learned like any other.

## Phase 1 — the looper (done)

- Five tracks, each one button: empty → record → play → overdub → play. A recording starts on the
  next bar and ends on a bar: by itself after the bars chosen for the track (1, 2, 4, 8, 16), or at
  the bar after the second press ("auto"). An overdub adds a layer over one pass of the loop, or ends
  at the next bar when pressed again.
- Pressing another track while one records hands over on the same bar — no gap between them.
- From a stop, the first recording counts one bar in.
- Undo / redo the last layer; clear a track; each track's volume and mute.
- Sources: the guitar through Pick's amp, or the microphone (heard back on request).
- Click: on/off and volume; accented first beat.
- Parts (scenes): Intro, Verse, Chorus… from the song, or A, B, C. Each part remembers which tracks
  play; a part chosen switches at the next bar. A track recorded joins the part playing.
- Keys: 1–5 the tracks' buttons, Space start/stop all, Z undo (Shift+Z redo), ← → parts, Tab the next
  track. The pedal: the selected track's button, next track, undo, start/stop, next/previous part,
  and each track's button on its own.
- Opened from Tools → Looper, from the Steems deck (with the song's tempo and parts), and from Loop
  Studio.

## Phase 2 — MIDI and drums (done)

- A MIDI track: notes from the MIDI keyboard into a loop, quantised (1/8, 1/16, swing), played with a
  SoundFont instrument of Pick's; overdub merges notes; the notes editable.
- Drums: the pads of the keyboard on a General MIDI kit, a step sequencer per part, and ready grooves
  (rock, pop, shuffle, half-time) at the song's tempo.
- As built: notes are kept as played and rendered into the loop's buffer (OfflineAudioContext), so the
  grid and the sound can change after recording; the kit and four synths are Web Audio (looper-sound.js),
  Pick's SoundFont instruments when the library is installed. The step grid is a track's layer (1–4 bars,
  swing, nine grooves). Single notes are edited in a piano roll (add, move, lengthen, delete; the grid of
  the track's quantise; the layer re-rendered after each change).

## Phase 3 — the song (done)

- An arrangement: parts in order with repeats (Intro ×1, Verse ×2, Chorus ×2…) played by itself or
  stepped by the pedal.
- Saving the looper project with the song (the layers as files), opening it again.
- The whole arrangement bounced to a Steems project (a track per looper track) and to WAV.
- The original song as a guide under the loops, its stems switchable.
- As built: an arrangement step is a part and its bars (from the song's sections, Loop Studio's plan, or by
  hand); the part switches are scheduled on the bar like a part chosen by hand. Projects live in
  Looper/<name>/ (project.json + a WAV per recorded layer; notes and grids re-rendered). Export renders the
  arrangement offline (each track's loops from the start, its gain switched per step): a WAV of the mix, or a
  Steems project of the tracks. The guide plays the open song from the part's section (by name), re-sought
  at each switch.

## Limits

- One recording at a time (as on the RC-505).
- A new layer starts sounding a moment (the latency plus one batch, ~0.1 s) after its loop starts
  again — only on its first pass.
