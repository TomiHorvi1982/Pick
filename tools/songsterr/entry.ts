// Pick: Songsterr tab -> alphaTab score in the browser, from Songsterr Downloader's converter (MIT, Ryan Roberts,
// github.com/Metaphysics0/songsterr-downloader). Built by tools/songsterr/build.sh into static/songsterr-alphatab.js.
import { SongsterrToAlphaTabConverter } from '$lib/converter/songsterr-to-alphatab.converter';
import { fetchAllPartRevisionsWithFallback } from '$lib/songsterr/revision-fetcher';

type Meta = { songId: number; revisionId: number; image: string; title: string; artist: string; tracks: any[] };

// The parts are fetched by the viewer's browser straight from Songsterr's CDN (it allows any origin).
async function load(meta: Meta) {
  const got = await fetchAllPartRevisionsWithFallback(meta as any);
  if (!got.revisions.length) throw new Error('Songsterr tab se nepodařilo stáhnout.');
  const conv = new SongsterrToAlphaTabConverter() as any;
  const { score, settings, warnings } = conv.buildScore({ meta, revisions: got.revisions });
  score.finish(settings);
  return { score, warnings: [...got.warnings, ...warnings], missing: got.failures.map((f: any) => f.partId) };
}

(globalThis as any).SongsterrTabs = { load };
