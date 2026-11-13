// Pick DJ in the friend's page — the library on the host's PC and the jam (library.py / jam.py put
// this, dj-core.js and dj.js into its page). The decks there play the songs
// that are already separated — the copy of the card has their stems — taken
// through the library's own login: /g/songs, /g/stem (Opus), /g/beats. There
// is no live separation on the PC (no Demucs in the container).
(() => {
  const enc = encodeURIComponent;
  PickDJ.setSource({
    async items() {
      const r = await fetch("/g/songs", {cache: "no-store"});
      if (!r.ok) throw new Error(r.status === 403 ? "nejsi přihlášený" : String(r.status));
      return DJCore.fromLibrarySongs((await r.json()).songs);
    },
    stemUrl: (name, stem) => `/g/stem?name=${enc(name)}&stem=${enc(stem)}`,
    beatsUrl: (name) => `/g/beats?name=${enc(name)}`,
    live: false,
  });

  // The DJ button appears in the header once someone is logged in (the page itself shows the login first).
  async function mountWhenLoggedIn() {
    if (document.getElementById("dj-quick")) return;
    try {
      const me = await (await fetch("/g/me", {cache: "no-store"})).json();
      if (me && me.user) {                               // the library between jams, and the jam itself
        const b = PickDJ.mountButton(document.getElementById("reBtn"));
        if (b) { b.style.marginLeft = "0"; b.style.marginRight = "6px"; }
        return;
      }
    } catch (e) {}
    setTimeout(mountWhenLoggedIn, 3000);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", mountWhenLoggedIn);
  else mountWhenLoggedIn();
})();
