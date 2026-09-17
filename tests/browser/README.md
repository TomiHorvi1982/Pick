# Browser scenarios

Scenarios for the `run-for-me` Playwright driver. With the app running on
127.0.0.1:8765:

    node ~/.claude/skills/run-for-me/scripts/qa-drive.mjs \
      "http://127.0.0.1:8765" --headless \
      --scenario-json "$(cat tests/browser/<name>.json)"

Exit 0 is a pass. Drop `--headless` to watch it happen.

| file | covers |
|------|--------|
| `core.json` | shell, search, sections, playlists, player bar |
| `artist-panel.json` | the now-playing rail: profile, photos, concerts, videos, albums |
| `panels.json` | both side rails: drag, clamp, remember, reset |
| `mixer.json` | Steems: channels, waveforms, metronome lane, tempo and key |
| `playlist-video.json` | playlist detail and the video list |
