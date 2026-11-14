// SonoBus in the jam: the guitars go outside the browser (SonoBus, sonobus.net — ASIO on Windows, Core Audio
// on the Mac, the least delay there is), everything else stays in Pick Jam — the song, the text, the tabs, the
// cameras, the chat. Both join the same private group: the server hands its name and password (jam.py sonobus())
// to the host and to the friend who is let in; the button opens SonoBus with them filled in.
// With "Kytary přes SonoBus" on, Pick Jam stops sending and playing the guitars, so they are not heard twice.
window.JamSonoBus = (() => {
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
  const KEY = "pick-jam-sonobus";
  const on = () => { try{ return localStorage.getItem(KEY) === "1"; }catch(e){ return false; } };
  const save = (v) => { try{ localStorage.setItem(KEY, v ? "1" : "0"); }catch(e){} };
  const win = /Windows/i.test(navigator.userAgent);
  // sb: {group, password, app, web, server}; o.onToggle(on) — the page mutes or brings back its own guitars
  function box(sb, o = {}){
    const el = document.createElement("div");
    el.className = "sonobus";
    if(!sb){ el.hidden = true; return el; }
    el.innerHTML = `<label class="sb-on"><input type="checkbox" ${on() ? "checked" : ""}> <b>Kytary přes SonoBus</b> <span class="sb-hint">(nejmenší zpoždění, mimo prohlížeč)</span></label>
      <div class="sb-body">
        <div class="sb-row"><a class="sb-go" href="${esc(sb.app)}">Otevřít SonoBus</a><button type="button" class="sb-copy">Kopírovat odkaz</button><a class="sb-dl" href="https://sonobus.net/#download" target="_blank" rel="noopener">Stáhnout SonoBus</a></div>
        <div class="sb-note">Skupina <code>${esc(sb.group)}</code> · heslo <code>${esc(sb.password)}</code> · server <code>${esc(sb.server)}</code></div>
        <ol class="sb-note">
          <li>Nainstaluj SonoBus (zdarma) a jednou klikni na <b>Otevřít SonoBus</b> — vyplní se skupina i heslo, dej <b>Connect</b>. Nejde-li otevřít, klikni <b>Kopírovat odkaz</b> a pak SonoBus spusť — vyplní se sám.</li>
          <li>${win ? "V SonoBus → nastavení zvuku zvol <b>ASIO</b> a ovladač své zvukovky (Focusrite, Behringer… nebo ASIO4ALL), buffer 64 nebo 128." : "V SonoBus → nastavení zvuku zvol svou zvukovku, buffer 64 nebo 128."} Vstup = kytara, výstup = sluchátka.</li>
          <li>V SonoBus u kamaráda nastav <b>Jitter buffer</b> co nejmenší, co ještě nepraská (začni na „Auto“).</li>
          <li>Píseň, text, taby, kamera i chat běží dál tady. Tady jen kytary ztichnou, aby nebyly slyšet dvakrát.</li></ol>
      </div>`;
    const cb = el.querySelector("input");
    const paint = () => { el.classList.toggle("on", cb.checked); };
    cb.onchange = () => { save(cb.checked); paint(); if(o.onToggle) o.onToggle(cb.checked); };
    el.querySelector(".sb-copy").onclick = () => navigator.clipboard.writeText(sb.web).then(() => { if(o.toast) o.toast("Odkaz zkopírovaný — teď spusť SonoBus, vyplní se sám."); });
    paint();
    return el;
  }
  const CSS = `.sonobus{border:1px solid rgba(127,127,127,.35);border-radius:8px;padding:8px 10px;margin:6px 0;font-size:12.5px}
    .sonobus .sb-on{display:flex;flex-wrap:wrap;gap:4px 6px;align-items:center;cursor:pointer}.sonobus .sb-hint{opacity:.65}
    .sonobus .sb-body{display:none;margin-top:6px}.sonobus.on .sb-body{display:block}
    .sonobus .sb-row{display:flex;gap:6px;flex-wrap:wrap;align-items:center}
    .sonobus .sb-go{background:#2f7d4f;color:#fff;padding:5px 10px;border-radius:6px;text-decoration:none;font-weight:600}
    .sonobus .sb-copy{padding:4px 8px}.sonobus .sb-note{opacity:.8;margin:6px 0 0;line-height:1.4}.sonobus ol.sb-note{padding-left:18px}
    .sonobus code{background:rgba(127,127,127,.18);padding:0 4px;border-radius:4px;user-select:all}`;
  if(!document.getElementById("sonobus-css")){ const s = document.createElement("style"); s.id = "sonobus-css"; s.textContent = CSS; document.head.appendChild(s); }
  return {box, on};
})();
