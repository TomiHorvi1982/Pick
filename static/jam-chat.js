// Pick Jam — the chat between the two of you (in Pick's jam panel and on the friend's page): the messages,
// whether the other one is here (or has the jam in the background), "píše…", and ticks — sent ✓, delivered ✓✓,
// read (blue, with the time). The messages go through the room on Pick's server, so they are still there after
// a reload; read means the other one had the chat in front of them.
(function(){
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
  const linkify = (s) => esc(s).replace(/\bhttps?:\/\/[^\s<]+/g, (u) => `<a href="${u}" target="_blank" rel="noopener">${u}</a>`);
  const hm = (t) => new Date(t * 1000).toLocaleTimeString("cs-CZ", {hour: "2-digit", minute: "2-digit"});
  const day = (t) => { const d = new Date(t * 1000), n = new Date(); return d.toDateString() === n.toDateString() ? hm(t) : d.toLocaleDateString("cs-CZ", {day: "numeric", month: "numeric"}) + " " + hm(t); };
  const CSS = `
  .jc{display:flex;flex-direction:column;gap:6px;min-height:0}
  .jc-h{display:flex;align-items:center;gap:7px;font-size:12px}
  .jc-dot{width:8px;height:8px;border-radius:50%;background:#666;flex:none}.jc-dot.on{background:#6ee7a0;box-shadow:0 0 6px #6ee7a0}.jc-dot.away{background:#ffd166;box-shadow:none}
  .jc-h b{font-size:13px}.jc-st{color:#9a9ab0}
  .jc-list{overflow-y:auto;display:flex;flex-direction:column;gap:4px;padding:6px;border-radius:10px;background:rgba(0,0,0,.25);border:1px solid rgba(255,255,255,.07);min-height:90px;max-height:var(--jc-h,220px);overscroll-behavior:contain}
  .jc-empty{color:#9a9ab0;font-size:12px;text-align:center;margin:auto;padding:10px}
  .jc-m{display:flex;flex-direction:column;align-items:flex-start;max-width:85%}
  .jc-m.mine{align-self:flex-end;align-items:flex-end}
  .jc-b{padding:6px 10px;border-radius:12px;background:#2a2a3a;color:#ececf3;white-space:pre-wrap;word-break:break-word;font-size:13px;line-height:1.35}
  .jc-m.mine .jc-b{background:var(--accent,var(--acc,#8b5cf6));color:#0b0b12}
  .jc-b a{color:inherit;text-decoration:underline}
  .jc-meta{font-size:10.5px;color:#8a8aa0;margin:1px 4px 0;display:flex;gap:4px;align-items:center}
  .jc-tick{letter-spacing:-2px;font-weight:700}.jc-tick.read{color:#5ab4ff}.jc-tick.wait{letter-spacing:0}
  .jc-typ{font-size:12px;color:#9a9ab0;min-height:16px;font-style:italic}
  .jc-typ i{display:inline-block;animation:jcdot 1.2s infinite}.jc-typ i:nth-child(2){animation-delay:.2s}.jc-typ i:nth-child(3){animation-delay:.4s}
  @keyframes jcdot{0%,60%,100%{opacity:.25}30%{opacity:1}}
  .jc-f{display:flex;gap:6px;align-items:flex-end}
  .jc-f textarea{flex:1;min-width:0;resize:none;background:#0e0e14;color:#ececf3;border:1px solid rgba(255,255,255,.12);border-radius:10px;padding:7px 9px;font:13px/1.35 system-ui,-apple-system,"Segoe UI",sans-serif;max-height:110px}
  .jc-f button{background:var(--accent,var(--acc,#8b5cf6));color:#0b0b12;border:0;border-radius:10px;padding:7px 12px;font-weight:800;cursor:pointer}
  .jc-f button:disabled{opacity:.4;cursor:default}
  `;
  { const s = document.createElement("style"); s.textContent = CSS; document.head.appendChild(s); }

  // o: {root, me: "host" | "guest", send(obj), visible(): bool, onUnread(n), onNew(msg), name}
  function JamChat(o){
    const other = o.me === "host" ? "guest" : "host";
    const S = {msgs: [], peer: false, away: false, seen: null, typUntil: 0, peerRead: null, myRead: null, sentTyping: 0, lastUnread: -1};
    const root = o.root;
    root.classList.add("jc");
    root.innerHTML = `<div class="jc-h"><span class="jc-dot"></span><b>${esc(o.name || "Kamarád")}</b><span class="jc-st"></span></div>
      <div class="jc-list"></div><div class="jc-typ"></div>
      <form class="jc-f"><textarea rows="1" maxlength="2000" placeholder="Napiš zprávu… (Enter odešle)"></textarea><button type="submit" title="Odeslat">➤</button></form>`;
    const list = root.querySelector(".jc-list"), ta = root.querySelector("textarea"), typ = root.querySelector(".jc-typ");
    const idx = (id) => S.msgs.findIndex((m) => m.id === id);
    const newId = () => Date.now().toString(36) + Math.random().toString(36).slice(2, 8);

    function head(){
      const dot = root.querySelector(".jc-dot"), st = root.querySelector(".jc-st");
      dot.className = "jc-dot" + (S.peer ? (S.away ? " away" : " on") : "");
      st.textContent = S.peer ? (S.away ? "online · Jam má v pozadí" : "online") : S.seen ? `offline · naposledy ${day(S.seen)}` : "offline";
      const typing = S.peer && Date.now() < S.typUntil;
      typ.innerHTML = typing ? `${esc(o.name || "Kamarád")} píše<i>.</i><i>.</i><i>.</i>` : "";
    }
    function paint(){
      const near = list.scrollHeight - list.scrollTop - list.clientHeight < 40;
      const readAt = S.peerRead ? idx(S.peerRead.id) : -1;
      let lastMineRead = -1;
      S.msgs.forEach((m, i) => { if(m.from === o.me && i <= readAt) lastMineRead = i; });
      list.innerHTML = S.msgs.length ? S.msgs.map((m, i) => {
        const mine = m.from === o.me;
        let tick = "";
        if(mine){
          tick = i <= readAt ? `<span class="jc-tick read" title="Přečteno">✓✓</span>` : m.dlv ? `<span class="jc-tick" title="Doručeno">✓✓</span>`
            : m.t ? `<span class="jc-tick" title="Odesláno — ${esc(o.name || "kamarád")} teď není připojený">✓</span>` : `<span class="jc-tick wait" title="Odesílám…">🕓</span>`;
          if(i === lastMineRead && S.peerRead) tick += ` <span>Přečteno ${hm(S.peerRead.t)}</span>`;
        }
        return `<div class="jc-m ${mine ? "mine" : ""}"><div class="jc-b">${linkify(m.text)}</div><div class="jc-meta">${m.t ? day(m.t) : ""} ${tick}</div></div>`;
      }).join("") : `<div class="jc-empty">Zatím žádné zprávy. Napiš třeba, jakou píseň dáte.</div>`;
      if(near || S.stick){ list.scrollTop = list.scrollHeight; S.stick = false; }
      head(); check();
    }
    function unread(){
      const r = S.myRead ? idx(S.myRead) : -1;
      return S.msgs.filter((m, i) => i > r && m.from === other).length;
    }
    // read: the newest message, once the chat is in front of me
    function check(){
      const n = unread();
      if(n && o.visible()){
        const last = S.msgs[S.msgs.length - 1];
        S.myRead = last.id; o.send({type: "read", id: last.id});
      }
      const u = unread();
      if(u !== S.lastUnread){ S.lastUnread = u; o.onUnread && o.onUnread(u); }
    }
    function send(){
      const text = ta.value.trim();
      if(!text) return;
      const m = {id: newId(), from: o.me, text, t: 0, dlv: false};
      S.msgs.push(m); S.stick = true; ta.value = ""; grow();
      o.send({type: "chat", id: m.id, text});
      o.send({type: "typing", on: false}); S.sentTyping = 0;
      S.myRead = m.id;                       // everything before what I write I have seen
      paint();
    }
    function grow(){ ta.style.height = "auto"; ta.style.height = Math.min(110, ta.scrollHeight + 2) + "px"; }
    root.querySelector("form").onsubmit = (e) => { e.preventDefault(); send(); ta.focus(); };
    ta.onkeydown = (e) => { e.stopPropagation(); if(e.key === "Enter" && !e.shiftKey && !e.isComposing){ e.preventDefault(); send(); } };
    ta.onkeyup = ta.onkeypress = (e) => e.stopPropagation();    // Pick's shortcuts (space = play…) stay out of typing
    ta.oninput = () => { grow(); const now = Date.now(); if(ta.value.trim() && now - S.sentTyping > 2000){ S.sentTyping = now; o.send({type: "typing", on: true}); }
      if(!ta.value.trim() && S.sentTyping){ S.sentTyping = 0; o.send({type: "typing", on: false}); } };
    ta.onblur = () => { if(S.sentTyping){ S.sentTyping = 0; o.send({type: "typing", on: false}); } };
    const away = () => o.send({type: "away", on: document.visibilityState !== "visible"});
    document.addEventListener("visibilitychange", () => { away(); check(); });
    window.addEventListener("focus", check);
    const tick = setInterval(() => { head(); check(); }, 1000);

    function onMsg(m){
      switch(m.type){
        case "hello": {
          const pending = S.msgs.filter((x) => !x.t && x.from === o.me);
          S.msgs = (m.chat || []).map((x) => ({...x}));
          pending.forEach((x) => { if(idx(x.id) < 0){ S.msgs.push(x); o.send({type: "chat", id: x.id, text: x.text}); } });
          const rd = m.read || {};
          S.peerRead = rd[other] || null;
          S.myRead = rd[o.me] ? rd[o.me].id : null;
          S.peer = !!m.peer; S.seen = m.seen || null; S.away = !!m.away;
          S.stick = true; away(); paint(); return true;
        }
        case "peer": S.peer = !!m.on; S.away = false; S.typUntil = 0; if(!m.on && m.seen) S.seen = m.seen; head(); return true;
        case "chat": if(idx(m.id) < 0){ S.msgs.push({...m}); S.typUntil = 0; paint(); o.onNew && o.onNew(m); } return true;
        case "chat-ok": { const x = S.msgs[idx(m.id)]; if(x){ x.t = m.t; x.dlv = !!m.dlv; paint(); } return true; }
        case "chat-dlv": (m.ids || []).forEach((id) => { const x = S.msgs[idx(id)]; if(x) x.dlv = true; }); paint(); return true;
        case "read": S.peerRead = {id: m.id, t: m.t}; S.msgs.forEach((x, i) => { if(x.from === o.me && i <= idx(m.id)) x.dlv = true; }); paint(); return true;
        case "typing": S.typUntil = m.on ? Date.now() + 4000 : 0; head(); return true;
        case "away": S.away = !!m.on; head(); return true;
      }
      return false;
    }
    paint();
    return {onMsg, check, unread, focus: () => ta.focus(), destroy: () => clearInterval(tick), state: S};
  }
  window.JamChat = JamChat;
})();
