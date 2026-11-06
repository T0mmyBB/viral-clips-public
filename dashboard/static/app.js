// app.js — vanilla, nessuna dipendenza: il pannello deve funzionare offline e
// senza build, sullo stesso vincolo del resto del progetto (tutto locale).
//
// Com'e' fatto. Una sola richiesta (`/api/snapshot`) porta tutto quello che
// sa `stato.py`; ogni sezione si ridisegna da li'. I clic passano da un solo
// ascoltatore che legge `data-azione`: i pezzi di pagina si ridisegnano spesso,
// e riagganciare gli ascoltatori a ogni disegno era il modo in cui un pulsante
// smetteva di rispondere senza un errore.

const $ = (sel, el = document) => el.querySelector(sel);
const $$ = (sel, el = document) => [...el.querySelectorAll(sel)];

async function get(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url} -> ${r.status}`);
  return r.json();
}

async function post(url, corpo) {
  const r = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(corpo),
  });
  const dati = await r.json();
  if (!r.ok || dati.errore) throw new Error(dati.errore || `${url} -> ${r.status}`);
  return dati;
}

// ------------------------------------------------------------ piccoli aiuti

function esc(t) {
  const d = document.createElement('div');
  d.textContent = t == null ? '' : String(t);
  return d.innerHTML;
}
// Lo stesso testo dentro un attributo: `textContent` non scappa le virgolette
// doppie, che in un `title="..."` chiuderebbero l'attributo a metà frase.
const attr = t => esc(t).replace(/"/g, '&quot;');

const numero = v => (v == null ? '—' : Math.round(v).toLocaleString('it-IT'));
const soldi = v => `$${Math.round(v || 0).toLocaleString('it-IT')}`;
// Sotto i dieci dollari i centesimi contano — un minimo di $1,50 non è $2 — e
// sopra no: il tetto di una campagna è $450, non $450,00.
const denaro = v => '$' + Number(v).toLocaleString('it-IT', {
  minimumFractionDigits: Math.abs(v) < 10 ? 2 : 0,
  maximumFractionDigits: Math.abs(v) < 10 ? 2 : 0,
});

function tempoFa(iso) {
  if (!iso) return 'mai';
  const min = Math.round((Date.now() - new Date(iso)) / 60000);
  if (min < 1) return 'adesso';
  if (min < 60) return `${min} min fa`;
  const ore = Math.round(min / 60);
  if (ore < 48) return `${ore} h fa`;
  return `${Math.round(ore / 24)} giorni fa`;
}

const GIORNI = ['domenica', 'lunedì', 'martedì', 'mercoledì', 'giovedì', 'venerdì', 'sabato'];
const MESI = ['gen', 'feb', 'mar', 'apr', 'mag', 'giu', 'lug', 'ago', 'set', 'ott', 'nov', 'dic'];
const MESI_LUNGHI = ['gennaio', 'febbraio', 'marzo', 'aprile', 'maggio', 'giugno', 'luglio',
                     'agosto', 'settembre', 'ottobre', 'novembre', 'dicembre'];

// I giorni in parole, perché «giovedì 12» si colloca e «2026-09-12» va contato.
function quandoInParole(iso) {
  const d = new Date(iso);
  const oggi = new Date(); oggi.setHours(0, 0, 0, 0);
  const suo = new Date(d); suo.setHours(0, 0, 0, 0);
  const giorni = Math.round((suo - oggi) / 86400000);
  const ora = String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0');
  const data = `${GIORNI[d.getDay()]} ${d.getDate()} ${MESI[d.getMonth()]}`;
  const fra = giorni === 0 ? 'oggi' : giorni === 1 ? 'domani' : giorni > 1 ? `fra ${giorni} giorni`
    : giorni === -1 ? 'ieri' : `${-giorni} giorni fa`;
  return { data, ora, fra, giorni };
}

const dataBreve = iso => {
  const d = new Date(iso + (iso.length === 10 ? 'T12:00:00' : ''));
  return `${d.getDate()} ${MESI[d.getMonth()]}`;
};

const ICONA = {
  si: '<svg class="segno si" viewBox="0 0 24 24" aria-hidden="true"><path d="m5 12.5 4.5 4.5L19 7.5"/></svg>',
  no: '<svg class="segno no" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="7.5"/></svg>',
};

function toast(testo, { errore = false, azione = null, durata = 6000 } = {}) {
  const box = document.createElement('div');
  box.className = 'toast' + (errore ? ' errore' : '');
  box.setAttribute('role', errore ? 'alert' : 'status');
  box.innerHTML = `<span>${esc(testo)}</span>`;
  if (azione) {
    const b = document.createElement('button');
    b.className = 'btn piccolo';
    b.textContent = azione.etichetta;
    b.onclick = () => { box.remove(); azione.fn(); };
    box.appendChild(b);
  }
  $('#avvisi-toast').appendChild(box);
  setTimeout(() => box.remove(), durata);
}

// ----------------------------------------------------------------- stato

let SNAP = null;
let PANNELLO = [];
let APERTA = null;               // la nicchia aperta in «Campagne»
let CAMPAGNA_APERTA = null;      // la campagna aperta in «Produzione»
let MENU_APERTO = null;
let SOLO_PRONTE = false;         // filtro del catalogo: solo sorgente risolta
const CATALOGO_TUTTO = new Set();
const PIATTAFORME = ['youtube', 'tiktok', 'instagram'];
const SIGLA = { youtube: 'YT', tiktok: 'TT', instagram: 'IG' };

// ----------------------------------------------------------- le sezioni

// Ogni sezione è un indirizzo (#oggi, #campagne…): avanti, indietro e un link
// mandato a sé stessi funzionano come in una pagina qualunque.
const SEZIONI = ['oggi', 'campagne', 'produzione', 'pubblicazione', 'numeri', 'chiedi'];

function sezioneCorrente() {
  const h = location.hash.replace('#', '');
  return SEZIONI.includes(h) ? h : 'oggi';
}

function mostraSezione() {
  const s = sezioneCorrente();
  $$('.sezione').forEach(el => { el.hidden = el.dataset.sezione !== s; });
  $$('#navigazione .voce').forEach(v => {
    const attiva = v.dataset.sezione === s;
    v.classList.toggle('attiva', attiva);
    if (attiva) v.setAttribute('aria-current', 'page'); else v.removeAttribute('aria-current');
  });
  const titolo = $(`#sez-${s} h1`);
  document.title = `${titolo ? titolo.textContent : 'Pannello'} — viral-clips`;
}
window.addEventListener('hashchange', () => { mostraSezione(); $('#contenuto').focus({ preventScroll: true }); window.scrollTo(0, 0); });

// ------------------------------------------------------------ il tema

// Tre stati: come Windows, chiaro, scuro. Resta nel browser: è una preferenza
// di chi guarda, non un dato del progetto.
const TEMI = ['auto', 'chiaro', 'scuro'];
const NOME_TEMA = { auto: 'come Windows', chiaro: 'chiaro', scuro: 'scuro' };
function applicaTema(t) {
  document.documentElement.dataset.tema = t;
  $('#btn-tema').textContent = `Tema: ${NOME_TEMA[t]}`;
}
applicaTema(localStorage.getItem('tema') || 'auto');

// ======================================================================
// OGGI — la cabina di regia della roadmap
// ======================================================================

function disegnaOggi() {
  const oggi = new Date();
  $('#oggi-data').textContent =
    `${GIORNI[oggi.getDay()]} ${oggi.getDate()} ${MESI_LUNGHI[oggi.getMonth()]} ${oggi.getFullYear()}`
    + ' · quello che chiede te, e a che punto è il piano';
  const rm = SNAP.roadmap || {};
  const n = SNAP.numeri || {};
  $('#oggi-corpo').innerHTML = `
    <div class="griglia g-regia">
      ${schedaFase(rm)}
      ${schedaCancello(rm, n)}
    </div>
    <div class="griglia g-2">
      ${schedaDaFare(SNAP.da_fare || [])}
      <div class="colonna">
        ${schedaUscite(SNAP.calendario || [])}
        ${schedaSegnali(SNAP.avvisi_raggruppati || [])}
      </div>
    </div>
    ${schedaPiattaforme(n)}`;
}

// La fase di oggi, le sue voci e le scadenze vicine. Tutto viene da
// docs/roadmap.md: il pannello non ne tiene una copia, e una roadmap cambiata
// si legge al caricamento dopo.
function schedaFase(rm) {
  const fase = (rm.fasi || []).find(f => f.numero === rm.corrente);
  if (!fase) {
    return `<div class="scheda"><h2>Roadmap</h2><div class="vuoto">Nessuna fase in corso secondo
      <code>docs/roadmap.md</code>: oggi cade fuori dai periodi delle fasi.</div></div>`;
  }
  const fatte = fase.voci.filter(v => v.fatto).length;
  const pct = fase.voci.length ? Math.round(100 * fatte / fase.voci.length) : 0;
  const giorniFine = fase.fine
    ? Math.round((new Date(fase.fine + 'T23:59:59') - new Date()) / 86400000) : null;
  const scadenze = (rm.scadenze || []).filter(s => s.giorni >= -3).slice(0, 5).map(s => `
    <div class="scadenza ${s.esito ? 'chiusa' : s.giorni <= 2 ? 'vicina' : ''}">
      <b>${dataBreve(s.data)} · ${s.esito ? esc(s.esito)
        : s.giorni === 0 ? 'oggi' : s.giorni === 1 ? 'domani' : s.giorni < 0 ? 'scaduta' : `fra ${s.giorni} gg`}</b>
      <span>${esc(s.cosa)}</span>
    </div>`).join('');
  return `<div class="scheda">
    <h2>Roadmap <span class="a-destra fine">fase ${fase.numero} di ${(rm.fasi || []).length}</span></h2>
    <div class="fase-testa">
      <h3>Fase ${fase.numero} · ${esc(fase.nome)}</h3>
      <span class="fine">${esc(fase.periodo)}${giorniFine != null
        ? ` · ${giorniFine > 0 ? `ancora ${giorniFine} giorni` : giorniFine === 0 ? 'ultimo giorno' : 'chiusa'}` : ''}</span>
    </div>
    <div class="barra-avanz" role="progressbar" aria-valuenow="${pct}" aria-valuemin="0" aria-valuemax="100"
         aria-label="Voci della fase fatte"><span style="width:${pct}%"></span></div>
    <div class="fine" style="margin-top:6px">${fatte} ${fatte === 1 ? 'voce fatta' : 'voci fatte'} su ${fase.voci.length}</div>
    <ul class="voci-fase">${fase.voci.map(v => `
      <li class="${v.fatto ? 'fatta' : ''}">${v.fatto ? ICONA.si : ICONA.no}
        <span class="t">${esc(v.titolo)}</span></li>`).join('')}</ul>
    ${scadenze ? `<div class="scadenze">${scadenze}</div>` : ''}
  </div>`;
}

// Il cancello del 26 ottobre in due righe: views mediane (basta una
// piattaforma) e primo incasso. Una barra per piattaforma con la tacca del
// bersaglio: la distanza si vede prima di leggere il numero, e il numero c'è.
function schedaCancello(rm, n) {
  const stato = rm.stato_cancello || [];
  const data = rm.data_cancello;
  const giorni = data ? Math.round((new Date(data + 'T12:00:00') - new Date()) / 86400000) : null;
  const views = stato.find(c => c.chiave === 'views_mediane');
  const incasso = stato.find(c => c.chiave === 'incassato');
  const etichettaStato = c => !c ? ''
    : c.raggiunta === true ? '<span class="tag ok">raggiunta</span>'
    : c.raggiunta === false ? '<span class="tag attenzione">non ancora</span>'
    : '<span class="tag">non si sa</span>';
  let righe = '';
  if (views) {
    const piatt = (n.piattaforme || []);
    const scala = Math.max(views.soglia * 1.25, ...piatt.map(p => p.mediana || 0));
    righe = piatt.map(p => {
      const v = p.mediana;
      return `<div class="riga-bullet" title="${attr(`${p.piattaforma}: mediana ${numero(v)} views su ${p.misurati} post misurati`)}">
        <span class="sigla-p">${SIGLA[p.piattaforma] || p.piattaforma}</span>
        <div class="bullet" aria-hidden="true">
          <span class="valore" style="width:${v == null ? 0 : Math.max(1, 100 * v / scala)}%"></span>
          <span class="obiettivo" style="left:${100 * views.soglia / scala}%"></span>
        </div>
        <span class="cifra-p">${v == null ? '—' : numero(v)}</span>
      </div>`;
    }).join('');
  }
  const whop = n.whop || [];
  const somma = k => whop.reduce((t, w) => t + (w[k] || 0), 0);
  return `<div class="scheda">
    <h2>Cancello${data ? ` · ${dataBreve(data)}` : ''}
      <span class="a-destra fine">${giorni != null ? (giorni > 0 ? `fra ${giorni} giorni` : 'oggi') : ''}</span></h2>
    ${views ? `<div class="fase-testa"><b>${esc(views.condizione)}</b> ${etichettaStato(views)}</div>
      <div class="fine">bersaglio ${numero(views.soglia)} views per post, la tacca arancione</div>
      ${righe}` : ''}
    ${incasso ? `<div class="fase-testa" style="margin-top:16px"><b>${esc(incasso.condizione)}</b> ${etichettaStato(incasso)}</div>
      <div class="kpi"><div class="valore">${incasso.valore == null ? '—' : denaro(incasso.valore)}
        <small>incassati</small></div></div>
      <div class="kpi-riga">
        <div><b>${numero(somma('clip'))}</b><span>invii su Whop</span></div>
        <div><b>${numero(somma('approvate'))}</b><span>approvati</span></div>
        <div><b>${numero(somma('in_attesa'))}</b><span>in attesa</span></div>
        <div><b>${numero(somma('respinte'))}</b><span>respinti</span></div>
      </div>
      <p class="nota">${whop.length ? `Letto dal pannello Whop ${tempoFa(whop.map(w => w.quando).sort().pop())}. «In attesa» è il Pending: Whop lo tiene lì finché le views non arrivano alla soglia di pagamento della campagna.`
        : 'Whop non è ancora stato letto: lo legge «Aggiorna social».'}</p>` : ''}
  </div>`;
}

const LIVELLO = { adesso: ['errore', 'Adesso'], oggi: ['accento', 'Oggi'], presto: ['info', 'Presto'] };
const DOVE = { pubblicazione: 'Pubblicazione', produzione: 'Produzione', campagne: 'Campagne',
               numeri: 'Numeri', oggi: null };
const TASTI = { 'aggiorna-radar': ['radar', 'Aggiorna radar'], 'aggiorna-social': ['social', 'Aggiorna social'] };

function schedaDaFare(voci) {
  const corpo = voci.length ? `<div class="compiti">${voci.map(v => {
    const [classe, parola] = LIVELLO[v.livello] || ['', v.livello];
    const bottone = TASTI[v.vai]
      ? `<button class="btn piccolo" data-azione="aggiorna" data-cosa="${TASTI[v.vai][0]}">${TASTI[v.vai][1]}</button>`
      : DOVE[v.vai] ? `<a class="btn piccolo" href="#${v.vai}">${DOVE[v.vai]}</a>` : '<span></span>';
    return `<div class="compito ${v.livello}">
      <span class="tag ${classe}">${parola}</span>
      <div><div class="t">${esc(v.titolo)}</div>${v.dettaglio ? `<div class="d">${esc(v.dettaglio)}</div>` : ''}</div>
      ${bottone}
    </div>`;
  }).join('')}</div>`
    : '<div class="vuoto">Niente che chieda un gesto adesso. Le pubblicazioni partono da sole dal calendario.</div>';
  return `<div class="scheda"><h2>Da fare <span class="a-destra fine">${voci.length ? `${voci.length} voci` : ''}</span></h2>${corpo}</div>`;
}

function schedaUscite(righe) {
  const prossime = righe.filter(r => r.quando && !['pubblicata', 'ferma', 'sospesa', 'campagna finita'].includes(r.stato))
    .sort((a, b) => a.quando.localeCompare(b.quando)).slice(0, 4);
  const ferme = righe.filter(r => r.stato === 'ferma').length;
  const corpo = prossime.length ? `<div class="uscite">${prossime.map(r => {
    const q = quandoInParole(r.quando);
    return `<div class="uscita"><div class="quando"><b>${q.data}</b><span>${q.ora} · ${q.fra}</span></div>
      <div><b>clip ${esc(r.id)}</b> · ${esc(r.campagna)}<div class="fine">${esc(r.titolo || '')}</div></div></div>`;
  }).join('')}</div>` : '<div class="vuoto">Niente in programma.</div>';
  return `<div class="scheda"><h2>Prossime uscite <a class="a-destra" href="#pubblicazione">calendario</a></h2>
    ${corpo}${ferme ? `<p class="nota">${ferme} ${ferme === 1 ? 'clip ferma' : 'clip ferme'}: la campagna non accetta invii Whop, escono da sole quando riapre.</p>` : ''}</div>`;
}

// I segnali: si guardano, non si risolvono con un clic — per questo non stanno
// fra le cose da fare. Gli «0 views» di una stessa campagna e piattaforma
// arrivano già raggruppati dal server: sei righe gialle dicevano una cosa sola.
function schedaSegnali(avvisi) {
  const corpo = avvisi.length ? `<div class="segnali">${avvisi.slice(0, 8).map(a => `
    <div class="segnale">
      <span class="tag ${a.livello === 'errore' ? 'errore' : 'attenzione'}">${a.livello === 'errore' ? 'errore' : 'avviso'}</span>
      <div>${esc(a.messaggio)}<div class="dove">${esc([a.campagna, a.clip].filter(Boolean).join(' / '))}</div></div>
    </div>`).join('')}</div>` : '<div class="vuoto">Nessun segnale: quello che dichiariamo coincide con quello che le fonti trovano.</div>';
  return `<div class="scheda"><h2>Segnali</h2>${corpo}</div>`;
}

function schedaPiattaforme(n) {
  const piatt = n.piattaforme || [];
  if (!piatt.length) return '';
  return `<div class="griglia g-3">${piatt.map(p => `
    <div class="scheda kpi">
      <h2>${p.piattaforma}</h2>
      <div class="valore">${p.mediana == null ? '—' : numero(p.mediana)} <small>views mediane per post</small></div>
      <div class="kpi-riga">
        <div><b>${numero(p.post)}</b><span>post${p.post !== p.misurati ? `, ${p.misurati} misurati` : ''}</span></div>
        <div><b>${numero(p.massimo)}</b><span>il migliore</span></div>
        <div><b>${numero(p.totale)}</b><span>views in tutto</span></div>
      </div>
    </div>`).join('')}</div>`;
}

// ======================================================================
// CAMPAGNE — scegliere cosa lavorare
// ======================================================================

// Il nome della campagna porta alla sua **scheda pubblica**, che si apre senza
// login e senza essere iscritti al provider. `link_whop` è un'altra cosa: è il
// collegamento dentro l'applicazione del provider, serve per inviare, e a chi
// non è iscritto a quel whop rimbalza sulla home senza dirlo.
function nomeCampagna(r, testo) {
  const t = esc(testo != null ? testo : (r.titolo || r.campagna || ''));
  if (!r.link_scheda) return t;
  return `<a href="${attr(r.link_scheda)}" target="_blank" rel="noopener"
    title="Apri la scheda della campagna: tariffe, materiali e regole">${t}</a>`;
}

// Un numero solo quando le piattaforme concordano; l'elenco quando no. Il
// server manda `null` apposta: scegliere la prima riga delle tre vorrebbe dire
// scrivere un tetto vero per TikTok e falso per YouTube.
function cifraPaletto(r, campo, chiave, etichetta, spiega) {
  if (r[campo] != null) {
    return `<span class="paletto" title="${attr(spiega)}"><b>${denaro(r[campo])}</b> ${etichetta}</span>`;
  }
  const t = r.tariffe || [];
  if (!t.length) return '';
  const per = t.map(x => `${SIGLA[x.piattaforma] || x.piattaforma} ${denaro(x[chiave])}`).join(' · ');
  return `<span class="paletto" title="${attr(spiega + ' — qui cambia da piattaforma a piattaforma')}">${etichetta}: ${per}</span>`;
}

// Quante views deve fare un post prima di pagare un centesimo: `minimo / CPM`.
// Sotto quella soglia una clip vale **zero**, non poco: è il vincolo che morde
// durante la rampa. Si legge accanto a $/post, non al posto suo.
function cellaBreakEven(r) {
  const v = r.break_even;
  if (v == null) return '<span class="debole" title="Tariffe per piattaforma non dichiarate: non si può sapere">—</span>';
  const per = r.break_even_per_piattaforma || {};
  const valori = Object.values(per);
  const sparse = valori.length > 1 && Math.min(...valori) !== Math.max(...valori);
  const classe = v <= 2000 ? 'si' : v <= 10000 ? 'attesa' : 'no';
  const dettaglio = sparse
    ? Object.entries(per).map(([p, x]) => `${SIGLA[p] || p} ${Math.round(x).toLocaleString('it-IT')}`).join(' · ') : '';
  // Sotto le 10k si tiene un decimale: 1.533 arrotondato a «2k» si legge come
  // il doppio di 1.000, e qui è la differenza fra una campagna che paga e no.
  const scritto = v === 0 ? '0' : v < 1000 ? Math.round(v)
    : v < 10000 ? (v / 1000).toFixed(1).replace('.', ',') + 'k' : Math.round(v / 1000) + 'k';
  return `<span class="soglia ${classe}" title="${attr((v === 0 ? 'Nessun minimo: paga dalla prima view'
      : 'Views che un post deve fare prima di pagare un centesimo') + (sparse ? ' — cambia da piattaforma a piattaforma: ' + dettaglio : ''))}">${scritto}</span>`;
}

// Le tre piattaforme si mostrano **tutte e tre sempre**, accese o spente: la
// domanda non è «dove posso pubblicare» ma «è solo TikTok o anche gli altri
// due», e un elenco delle sole accettate la lascia senza risposta.
function pillePiattaforme(r) {
  const accettate = r.piattaforme_campagna || [];
  if (!accettate.length) return '';
  const nostre = PIATTAFORME.map(p => {
    const si = accettate.includes(p);
    return `<span class="sigla ${si ? 'si' : 'no'}" title="${p}${si ? '' : ' — non accettata'}">${SIGLA[p]}</span>`;
  }).join('');
  const altre = accettate.filter(p => !PIATTAFORME.includes(p));
  return `<span class="piatt-campagna" title="Dove la campagna accetta di essere pubblicata">${nostre}${
    altre.length ? `<span class="sigla altra">+${esc(altre.join(' '))}</span>` : ''}</span>`;
}

function rigaPaletti(r) {
  const pezzi = [
    cifraPaletto(r, 'tetto_clip', 'tetto', 'max per clip', "Oltre questo guadagno una clip non paga di più"),
    cifraPaletto(r, 'minimo_clip', 'minimo', 'min per essere pagati', 'Sotto questo guadagno la clip non paga affatto'),
  ].filter(Boolean);
  const piatt = pillePiattaforme(r);
  const whop = r.link_whop
    ? `<a class="paletto" href="${attr(r.link_whop)}" target="_blank" rel="noopener"
        title="Il pannello del provider su Whop, da cui si inviano le clip. Serve essere iscritti a quel whop: se non lo sei ti rimanda alla home">whop ↗</a>` : '';
  if (!pezzi.length && !piatt && !whop) return '';
  return `<div class="paletti">${pezzi.join('')}${piatt}${whop}</div>`;
}

// La sorgente come l'ha trovata il risolutore (radar/sorgenti.py): «pronta» è
// un video da clippare preselezionato, «da scegliere» c'è ma non passa i filtri,
// «a mano» non si apre da qui. Assente vuol dire «non ancora vista», non «a mano».
function cellaSorgente(c) {
  const s = c.sorgente;
  if (!s) return '<span class="debole" title="Il risolutore non l\'ha ancora vista: gira con «Aggiorna radar»">—</span>';
  const classe = { automatico: 'ok', con_scelta: 'attenzione', a_mano: 'errore' }[s.esito] || '';
  const sotto = s.titolo ? `${s.titolo}${s.durata_min ? ` · ${s.durata_min} min` : ''}` : s.motivo;
  return `<div class="sorgente-cella"><span class="tag ${classe}" title="${attr(s.motivo || 'video preselezionato')}">${esc(s.etichetta)}</span>
    ${sotto ? `<span class="fine" title="${attr(sotto)}">${esc(sotto)}</span>` : ''}</div>`;
}

// **Il pulsante «scegli» sta dove la campagna si può prendere, e dove non c'è
// la casella dice perché.** Da fuori, una regola applicata togliendo un comando
// è indistinguibile da un guasto. Etichetta e frase le scrive il server
// (`stato.riserva_scegli`): la regola che decide chi entra nelle somme sta in
// un posto solo.
function cellaScegli(c, s) {
  if (!s.e_nicchia) return '';
  if (c.proponibile) {
    return `<button class="btn piccolo" data-azione="scegli" data-id="${attr(c.id)}"
      data-gruppo="${attr(s.gruppo || '')}" data-nicchia="${attr(s.nicchia)}">scegli</button>`;
  }
  return `<span class="niente-scegli" title="${attr(c.perche_non_proponibile)}">${esc(c.etichetta_non_proponibile)}</span>`;
}

function statoLavorabile(c) {
  // Quattro stati: `?` è «nessuno l'ha ancora letta», che non è «scartata».
  const classe = { si: 'ok', lingua: 'attenzione', '?': 'attenzione' }[c.lavorabile] || 'errore';
  const spiega = {
    si: 'La pipeline può lavorarla',
    lingua: 'Forma buona, ma il parlato non è italiano né inglese',
    '?': 'Nessuno ha ancora letto questa campagna: non è stata scartata, aspetta un giudizio',
  }[c.lavorabile] || "Forma incompatibile: quasi sempre un video già montato, dove non c'è niente da estrarre";
  return `<span class="tag ${classe} senza-punto" title="${attr(spiega)}">${esc(c.segno)}</span>`;
}

function rigaCatalogo(c, s, conNicchia = false) {
  return `<tr class="${['si', '?'].includes(c.lavorabile) ? '' : 'spenta'}">
    <td>${statoLavorabile(c)}</td>
    <td class="campagna-cella">${nomeCampagna(c)}${c.scelta ? ' <span class="tag ok">già scelta</span>' : ''}${
      ['marca', 'esclusiva'].includes(c.pagina)
        ? ` <span class="tag errore" title="${attr(c.pagina === 'marca'
            ? 'Il brief vuole una pagina intestata a quel creator' : 'Il brief vieta di tenere altre campagne sulla stessa pagina')}: non la lavoriamo">pagina ${c.pagina}</span>` : ''}
      ${rigaPaletti(c)}${c.nota ? `<div class="fine">${esc(c.nota)}</div>` : ''}</td>
    ${conNicchia ? `<td>${esc(s.nicchia)}</td>` : ''}
    <td class="num">${cellaBreakEven(c)}</td>
    <td class="num"><b>${c.dollari_post != null ? '$' + c.dollari_post.toFixed(2) : '—'}</b></td>
    <td class="num">$${(c.cpm || 0).toFixed(2)}</td>
    <td class="num">${Math.min(c.mesi || 0, 99).toFixed(1)}</td>
    <td class="num">${numero(c.clipper)}</td>
    <td class="num">${soldi(c.residuo)}</td>
    <td>${cellaSorgente(c)}</td>
    <td><span class="tag ${c.iscritto ? 'ok' : ''}" title="${attr(c.iscritto
      ? 'Siamo iscritti al whop di questo provider: si può inviare'
      : "Non siamo iscritti al whop di questo provider: l'iscrizione è un passo per campagna scelta")}">${c.iscritto ? 'iscritti' : 'no'}</span></td>
    <td class="azioni">${cellaScegli(c, s)}</td></tr>`;
}

function intestazioneCatalogo(conNicchia = false) {
  return `<thead><tr>
    <th title="Se la pipeline può lavorarla">ok</th>
    <th>Campagna</th>${conNicchia ? '<th>Nicchia</th>' : ''}
    <th class="num" title="Views che un post deve fare prima di pagare un centesimo">paga da</th>
    <th class="num" title="Views per post × CPM: quanto vale un nostro post">$/post</th>
    <th class="num">CPM</th><th class="num">mesi</th><th class="num">clipper</th><th class="num">residuo</th>
    <th title="Da dove viene il girato, secondo il risolutore">sorgente</th>
    <th title="Se siamo iscritti al whop del provider">whop</th><th></th></tr></thead>`;
}

// «Da prendere adesso»: le campagne che si possono scegliere, sulle nicchie con
// un account, con la sorgente già risolta. Ordinate per soglia di pagamento
// prima che per $/post: per un account giovane una campagna che paga da mille
// views vale più di una da $36 che ne pretende centomila.
// Si prende adesso solo dove l'account c'e' davvero (`aperto`: un gruppo «da
// aprire» ha il nome e non i profili) e dove la campagna ha ancora budget e
// tempo: la roadmap chiede «budget e durata residui», e una campagna a $1 di
// residuo e' in catalogo ma non e' lavoro. Le soglie sono quelle scritte qui.
const RESIDUO_MINIMO = 100;
const MESI_MINIMI = 0.5;

function prendibili() {
  const righe = [];
  for (const s of PANNELLO) {
    if (!s.gruppo || !s.aperto || !s.catalogo) continue;
    for (const sotto of s.catalogo.sottonicchie || []) {
      for (const c of sotto.campagne) {
        if (c.proponibile && c.sorgente && c.sorgente.esito === 'automatico'
            && (c.residuo || 0) >= RESIDUO_MINIMO && (c.mesi || 0) >= MESI_MINIMI) righe.push([c, s]);
      }
    }
  }
  return righe;
}

function schedaDaPrendere() {
  const righe = prendibili();
  righe.sort(([a], [b]) => (a.break_even ?? 1e9) - (b.break_even ?? 1e9) || (b.dollari_post || 0) - (a.dollari_post || 0));
  // La colonna della nicchia serve solo quando le nicchie sono piu' d'una.
  const piuNicchie = new Set(righe.map(([, s]) => s.chiave)).size > 1;
  const corpo = righe.length
    ? `<div class="scorre"><table>${intestazioneCatalogo(piuNicchie)}<tbody>${
        righe.slice(0, 12).map(([c, s]) => rigaCatalogo(c, s, piuNicchie)).join('')}</tbody></table></div>
       ${righe.length > 12 ? `<p class="nota">Le prime 12 di ${righe.length}: le altre sono nel catalogo della nicchia, qui sotto.</p>` : ''}`
    : '<div class="vuoto">Nessuna campagna pronta da prendere sulle nicchie con un account aperto: la sorgente non è risolta, il budget è agli sgoccioli, o sono già tutte scelte.</div>';
  return `<div class="scheda"><h2>Da prendere adesso
      <span class="a-destra fine">${righe.length} sulle nicchie con un account aperto · sorgente pronta, almeno ${soldi(RESIDUO_MINIMO)} e due settimane di budget · per soglia di pagamento</span></h2>
    ${corpo}</div>`;
}

// Una carta per nicchia. Chi ha un account mostra pubblicazioni e views, chi
// non ce l'ha quante campagne ci sono da prendere: una barra a zero su una
// nicchia senza account direbbe «lavoro fermo» dove non è mai cominciato.
function cartaNicchia(s) {
  const cat = s.catalogo || {};
  const nome = s.nicchia && s.nicchia !== 'nessuna' ? s.nicchia : s.chiave;
  const etichetta = s.gruppo ? `account ${s.gruppo}${s.aperto ? '' : ' · da aprire'}`
    : (s.e_nicchia ? 'nessun account' : 'non è una nicchia');
  const classi = ['carta-nicchia', s.chiave === APERTA ? 'aperta' : '', s.gruppo ? 'con-account' : '',
                  s.e_nicchia ? '' : 'non-nicchia'].join(' ');
  const coda = s.gruppo
    ? `<div class="barra-avanz"><span style="width:${s.percentuale}%"></span></div>
       <div class="fine">${s.pubblicate} pubblicazioni su ${s.totale} · ${s.percentuale}%</div>`
    : `<div class="fine">${cat.proponibili || 0} da prendere · ${cat.lavorabili || 0} lavorabili</div>`;
  return `<button class="${classi}" data-azione="nicchia" data-chiave="${attr(s.chiave)}" aria-expanded="${s.chiave === APERTA}">
    <div class="gruppo">${esc(etichetta)}</div>
    <div class="nome">${esc(nome)}</div>
    <div class="cifre">
      <div class="cifra" title="Mediana fra le campagne misurate: il caso normale"><b>$${(s.dollari_post || 0).toFixed(2)}</b><span>per post, mediana</span></div>
      ${s.gruppo
        ? `<div class="cifra"><b>${numero((s.metriche || {}).views)}</b><span>views</span></div>`
        : `<div class="cifra spenta"><b>${cat.totale || 0}</b><span>in catalogo</span></div>`}
    </div>
    ${coda}
  </button>`;
}

function sezioneCatalogo(s) {
  const cat = s.catalogo;
  if (!cat || !cat.totale) {
    return '<div class="vuoto">Nessuna campagna in questa nicchia. Non è un errore: il catalogo cresce quando il radar gira.</div>';
  }
  const tutto = CATALOGO_TUTTO.has(s.chiave);
  const gruppi = cat.sottonicchie.map(sotto => {
    // Le non ancora lette non si nascondono mai: quello che chiedono è una
    // lettura, e una campagna dietro un «mostra tutte» il giudizio non lo riceve.
    let righe = sotto.campagne.filter(c => tutto || ['si', '?'].includes(c.lavorabile));
    if (SOLO_PRONTE) righe = righe.filter(c => c.sorgente && c.sorgente.esito === 'automatico');
    if (!righe.length) return '';
    return `<h4 class="titolo-sotto">${esc(sotto.nome)} <span class="fine">${righe.length} di ${sotto.campagne.length}</span></h4>
      <div class="scorre"><table>${intestazioneCatalogo()}<tbody>${righe.map(c => rigaCatalogo(c, s)).join('')}</tbody></table></div>`;
  }).join('');
  return `<div class="riga-filtri">
      <span>${cat.da_leggere ? `<b class="da-leggere">${cat.da_leggere}</b> da leggere · ` : ''}<b>${cat.proponibili}</b> da prendere ·
        <b>${cat.lavorabili}</b> lavorabili · <b>${cat.lingua}</b> ferme per la lingua ·
        <b>${cat.incompatibili}</b> di forma incompatibile · ${cat.totale} in tutto</span>
      <span class="a-destra">
        <button class="btn piccolo" data-azione="solo-pronte" aria-pressed="${SOLO_PRONTE}">${SOLO_PRONTE ? 'tutte le sorgenti' : 'solo sorgente pronta'}</button>
        <button class="btn piccolo" data-azione="catalogo-tutto" data-chiave="${attr(s.chiave)}">${tutto ? 'solo le lavorabili' : 'mostra tutte'}</button>
      </span>
    </div>
    ${gruppi || '<div class="vuoto">Nessuna riga con questi filtri.</div>'}
    <details class="nota"><summary>Come leggere il catalogo</summary>
      <p><b>ok</b>: <code>si</code> la pipeline può lavorarla · <code>si*</code> lavorabile, ma con troppo pochi
      clipper perché $/post sia una media · <code>lng</code> forma buona, lingua fuori portata · <code>no</code> forma
      incompatibile, quasi sempre un video già montato.</p>
      <p><b>paga da</b> è la soglia che decide se vale la pena <i>adesso</i>: views che un post deve fare prima di pagare
      un centesimo (<code>minimo ÷ CPM</code>). Sotto quella soglia una clip vale zero, non poco. Sul catalogo la mediana è
      1.500 views. <b>$/post</b> = views per post × CPM: si legge accanto alla soglia, non al posto suo.</p>
      <p><b>sorgente</b>: <code>pronta</code> il risolutore ha trovato un video da clippare, <code>da scegliere</code> ci
      sono video ma nessuno passa i filtri (sotto i 10 minuti, forse esempi, già lavorati), <code>a mano</code> non si
      apre da qui (WeTransfer, Dropbox, nessun link). Il titolo della casella dice il motivo.</p>
      <p><b>whop</b> dice se si può <i>inviare</i>: il radar legge tutte le campagne dalla pagina pubblica, ma l'invio
      passa dal whop del provider, e l'iscrizione è un passo per campagna scelta. Dove «scegli» non c'è, la casella dice
      perché: <code>pagina marca</code> e <code>pagina esclusiva</code> sono fuori dal piano, <code>lingua</code> e
      <code>forma</code> la pipeline non le lavora, <code>da leggere</code> aspetta un giudizio, <code>già scelta</code> è
      già in produzione.</p>
    </details>`;
}

function disegnaCampagne() {
  const s = PANNELLO.find(x => x.chiave === APERTA);
  const attive = s ? (s.attive || []).length : 0;
  $('#campagne-corpo').innerHTML = `
    ${schedaDaPrendere()}
    <h2 class="titolo-scheda" style="margin-top:24px">Nicchie <span class="fine" style="text-transform:none;letter-spacing:0;font-weight:500">
      le tre con un account in alto · clic per aprire il catalogo</span></h2>
    <div class="griglia-nicchie">${PANNELLO.map(cartaNicchia).join('')}</div>
    ${s ? `<div class="scheda pannello-nicchia">
      <header>
        <h2>${esc(s.nicchia && s.nicchia !== 'nessuna' ? s.nicchia : s.chiave)}</h2>
        ${s.gruppo ? `<span class="tag ok">account ${esc(s.gruppo)}</span>` : '<span class="tag">nessun account</span>'}
        ${attive ? `<a href="#produzione" class="fine">${attive} in produzione →</a>` : ''}
        <span class="fine">${esc(s.descrizione || s.definizione || '')}</span>
      </header>
      ${sezioneCatalogo(s)}
    </div>` : ''}
    ${schedaSparite(SNAP.sparite || [])}`;
}

// Le campagne giudicate che dal catalogo sono sparite: non sono lavoro
// disponibile, ma nemmeno niente — il giudizio su una campagna costa una
// lettura del brief, e sparire in silenzio è il difetto che il progetto insegue.
function schedaSparite(righe) {
  if (!righe.length) return '';
  const lavorabili = righe.filter(r => r.lavorabile === 'si').length;
  return `<details class="scheda"><summary>${righe.length} campagne sparite dal catalogo
      <span class="fine">· ${lavorabili} erano lavorabili · il giudizio resta scritto, se rientrano non si rilegge il brief</span></summary>
    <div class="scorre" style="margin-top:12px"><table><thead><tr><th>ok</th><th>Nicchia</th><th>Sottonicchia</th><th>Campagna</th></tr></thead>
      <tbody>${righe.map(r => `<tr class="${r.lavorabile === 'si' ? '' : 'spenta'}">
        <td><span class="tag ${r.lavorabile === 'si' ? 'ok' : ''} senza-punto">${esc(r.lavorabile)}</span></td>
        <td>${esc(r.nicchia || '—')}</td><td>${esc(r.sottonicchia || '—')}</td><td>${esc(r.titolo || r.id)}</td></tr>`).join('')}</tbody></table></div>
  </details>`;
}

// ======================================================================
// PRODUZIONE — dalla campagna scelta alle clip pronte
// ======================================================================

const PASSI = ['scelta', 'sorgente', 'preparata', 'decisa', 'renderizzata', 'calendario', 'pubblicata'];

// A che punto è una campagna. Le cartelle con un `publish.json` sono già alla
// pubblicazione; prima ci sono le cartelle di lavoro (`lavorazione`), e prima
// ancora solo la scelta con la sorgente trovata o no.
function passoDi(c) {
  if (c.cartella) {
    if (c.pubblicate > 0) return 'pubblicata';
    if (c.clip_dettaglio.some(cl => cl.quando)) return 'calendario';
    if (c.clip_dettaglio.some(cl => cl.renderizzata)) return 'renderizzata';
    return 'decisa';
  }
  const lav = (c.lavorazione || [])[0];
  if (lav) return lav.renderizzate ? 'renderizzata' : lav.decisa ? 'decisa' : 'preparata';
  const risolta = (c.risolutore && c.risolutore.candidati.some(x => x.preselezionato)) || c.sorgente;
  return risolta ? 'sorgente' : 'scelta';
}

function passi(c) {
  const qui = PASSI.indexOf(passoDi(c));
  return `<div class="passi" aria-label="Stadio: ${PASSI[qui]}">${PASSI.map((p, i) =>
    `<div class="passo ${i < qui ? 'fatto' : i === qui ? 'corrente' : ''}"><i></i>${p}</div>`).join('')}</div>`;
}

// Il sorgente non lo si chiede: lo trova il risolutore (radar/sorgenti.py),
// con i video candidati già misurati, e in mancanza `stato.sorgente_campagna()`
// fra i materiali e nel brief. Il campo da riempire resta solo quando nessuno
// dei due trova niente — e lì è onesto: nessuno sa cosa clippare.
function sceltaSorgente(c) {
  const uuid = c.whop_campaign_uuid;
  const candidati = (c.risolutore && c.risolutore.candidati) || [];
  if (candidati.length) {
    return `<div class="scelta-sorgente" role="radiogroup" aria-label="Video da clippare">${candidati.map((x, i) => `
      <label><input type="radio" name="fonte-${attr(uuid)}" value="${attr(x.url)}" ${x.preselezionato && !candidati.slice(0, i).some(y => y.preselezionato) ? 'checked' : ''}>
        <span class="t" title="${attr(x.url)}">${esc(x.titolo || x.url)}</span>
        <span class="fine">${x.durata_s ? Math.round(x.durata_s / 60) + ' min' : ''}${x.data ? ' · ' + dataBreve(x.data) : ''}${x.gia_lavorato ? ' · già lavorato' : ''}</span>
      </label>`).join('')}</div>`;
  }
  if (c.sorgente) {
    const altre = c.altre_fonti || [];
    return `<div class="scelta-sorgente">
      <label><input type="radio" name="fonte-${attr(uuid)}" value="${attr(c.sorgente)}" checked>
        <span class="t">${esc(c.sorgente)}</span><span class="fine">dai materiali</span></label>
      ${altre.map(f => `<label><input type="radio" name="fonte-${attr(uuid)}" value="${attr(f)}"><span class="t">${esc(f)}</span><span class="fine">dal brief</span></label>`).join('')}
    </div>`;
  }
  return `<input class="fonte" type="text" aria-label="Link del video sorgente"
    placeholder="nessun video trovato nei materiali: incolla qui il link" data-uuid="${attr(uuid || '')}">`;
}

function comandi(c, s) {
  const documenti = (c.documenti || []).map((d, i) =>
    `<a href="${attr(d)}" target="_blank" rel="noopener">documento${c.documenti.length > 1 ? ' ' + (i + 1) : ''} ↗</a>`).join(' · ');
  const passo = passoDi(c);
  if (!c.cartella) {
    const lav = (c.lavorazione || [])[0];
    // Una cartella di lavoro c'è ma la consegna no: la produzione è a metà, e il
    // pulsante giusto dipende da dove.
    if (lav && passo === 'renderizzata') {
      return `<div class="comandi">
        <span class="fine">${lav.renderizzate} clip renderizzate in <code>${esc(lav.slug)}</code></span>
        ${s.gruppo ? `<button class="btn primario piccolo" data-azione="lavora" data-tipo="pubblicazione" data-cartella="${attr(lav.slug)}">Prepara la pubblicazione</button>`
          : '<span class="fine">nessun account: il calendario si fa quando la nicchia avrà il suo</span>'}
        ${documenti ? `<span class="fine">${documenti}</span>` : ''}</div>`;
    }
    if (lav && passo === 'decisa') {
      return `<div class="comandi">
        <span class="fine">clip decise in <code>${esc(lav.slug)}</code></span>
        <button class="btn primario piccolo" data-azione="lavora" data-tipo="render" data-cartella="${attr(lav.slug)}">Renderizza le clip decise</button>
        ${documenti ? `<span class="fine">${documenti}</span>` : ''}</div>`;
    }
    return `<div class="comandi">
      ${sceltaSorgente(c)}
      <button class="btn primario piccolo" data-azione="lavora" data-tipo="tutto" data-uuid="${attr(c.whop_campaign_uuid || '')}">Fai tutto</button>
      <button class="btn piccolo" data-azione="lavora" data-tipo="prepara" data-uuid="${attr(c.whop_campaign_uuid || '')}">Solo prepara</button>
      ${lav ? `<span class="fine">preparata in <code>${esc(lav.slug)}</code>: manca la scelta delle clip, «Fai tutto» la fa leggendo il brief</span>` : ''}
      ${documenti ? `<span class="fine">${documenti}</span>` : ''}
      ${c.risolutore && c.risolutore.motivo ? `<span class="fine">risolutore: ${esc(c.risolutore.motivo)}</span>` : ''}
    </div>`;
  }
  const pronte = c.clip_dettaglio.some(cl => cl.renderizzata);
  const programmate = c.clip_dettaglio.filter(cl => cl.quando).length;
  // «Prepara la pubblicazione» scrive didascalie e calendario. Non pubblica: la
  // regola che il pannello non abbia pulsanti che mandano roba fuori resta.
  const pubblicazione = !pronte ? ''
    : !s.gruppo ? '<span class="fine">nessun account: niente calendario per ora</span>'
    : `<button class="btn piccolo" data-azione="lavora" data-tipo="pubblicazione" data-cartella="${attr(c.cartella)}">${
        programmate ? 'Rifai il calendario' : 'Prepara la pubblicazione'}</button>`;
  return `<div class="comandi">
    <span class="fine">cartella <code>${esc(c.cartella)}</code></span>
    ${pronte ? '' : `<button class="btn primario piccolo" data-azione="lavora" data-tipo="render" data-cartella="${attr(c.cartella)}">Renderizza le clip decise</button>`}
    ${pubblicazione}
    ${programmate ? `<span class="fine">${programmate} clip programmate</span>` : ''}
    ${documenti ? `<span class="fine">${documenti}</span>` : ''}
  </div>`;
}

// Una clip esce su tre piattaforme, quindi la riga della clip ne ha tre: è
// l'unico modo di accorgersi che su una manca. «non misurata» non è zero.
function celleClip(cl) {
  return PIATTAFORME.map(p => {
    const v = cl.piattaforme[p];
    if (!v) return '<td class="spenta">—</td>';
    if (!(v.stato === 'pubblicato' || v.stato === 'done')) return `<td class="spenta">${esc(v.stato)}</td>`;
    const views = v.views == null ? '<span class="spenta">non misurata</span>' : `<b>${numero(v.views)}</b>`;
    const avvisi = (v.avvisi || []).length ? `<div class="avviso-post">${esc(v.avvisi.join(' · '))}</div>` : '';
    const pg = v.pagamento ? `<div><span class="tag ${{ 'sotto soglia': 'attenzione', 'sopra soglia': 'ok', 'nessun minimo': 'ok', 'non pagherà': 'errore' }[v.pagamento.etichetta] || ''}"
      title="${attr(v.pagamento.frase)}">${esc(v.pagamento.etichetta)}</span></div>` : '';
    return `<td>${v.url ? `<a href="${attr(v.url)}" target="_blank" rel="noopener">${views}</a>` : views}${avvisi}${pg}</td>`;
  }).join('');
}

function tabellaClip(c) {
  if (!c.clip_dettaglio.length) {
    return '<div class="vuoto">Nessuna clip ancora: quando esistono compaiono qui, con le metriche delle tre piattaforme.</div>';
  }
  const righe = c.clip_dettaglio.map(cl => `<tr tabindex="0" data-azione="clip" data-cartella="${attr(c.cartella)}" data-id="${attr(cl.id)}">
      <td><b>${esc(cl.id)}</b></td>
      <td>${esc(cl.hook || cl.titolo || '—')}<div class="fine">${cl.durata ? cl.durata + ' s · ' : ''}${cl.pubblicata_su}/3 piattaforme</div></td>
      ${celleClip(cl)}
      <td class="num"><b>${numero(cl.views || 0)}</b></td>
    </tr>`).join('');
  return `<div class="scorre"><table class="clip-tabella"><thead><tr><th>#</th><th>Clip</th>
      <th>YouTube</th><th>TikTok</th><th>Instagram</th><th class="num">Totale</th></tr></thead>
      <tbody>${righe}</tbody></table></div>`;
}

// Il menu della campagna: nessuna voce cancella niente. «Completata» la sposta
// fra le concluse con clip e metriche, «rimetti fra le disponibili» toglie la
// scelta. Una campagna con una cartella non torna disponibile: il lavoro fatto
// resta, e fingere che non ci sia sarebbe l'unico modo di perderlo.
function menuCampagna(c, s) {
  const chiave = c.cartella || c.whop_campaign_uuid;
  const voci = [];
  const base = `data-uuid="${attr(c.whop_campaign_uuid || '')}" data-gruppo="${attr(s.gruppo || '')}" data-nicchia="${attr(s.nicchia || '')}"`;
  if (!c.conclusa) voci.push(`<button data-azione="scelta" data-stato="chiusa" ${base}>Segna come completata</button>`);
  if (c.conclusa) voci.push(`<button data-azione="scelta" data-stato="in-corso" ${base}>Rimetti fra le attive</button>`);
  if (!c.cartella) voci.push(`<button data-azione="libera" ${base}>Rimetti fra le disponibili</button>`);
  if (!voci.length) return '';
  return `<div class="menu"><button class="btn piccolo" data-azione="menu" data-chiave="${attr(chiave)}"
      aria-haspopup="true" aria-expanded="${MENU_APERTO === chiave}" aria-label="Altre azioni">⋯</button>
    <div class="voci" ${MENU_APERTO === chiave ? '' : 'hidden'}>${voci.join('')}</div></div>`;
}

// L'iscrizione su Whop che «scegli» lancia da sola. Si dice solo finche' la
// campagna e' attiva: «iscritta» in verde, qualunque altro esito in rosso,
// perche' una campagna mai entrata su Whop non riceve gli invii e sembra pronta.
function tagIscrizione(c) {
  if (c.conclusa) return '';
  const l = (SNAP.lavoro || []).find(r => r.campagna_id === c.whop_campaign_uuid);
  const i = l && l.iscrizione;
  if (!i) return '';
  const aPosto = ['fatta', "gia' dentro"].includes(i.esito);
  return aPosto
    ? '<span class="tag ok">iscritta su Whop</span>'
    : `<span class="tag errore" title="Esito di ${esc((i.quando || '').slice(0, 16))}: va guardata su Whop">Whop: ${esc(i.esito)}</span>`;
}

function schedaCampagna(c, s) {
  const chiave = c.cartella || c.whop_campaign_uuid;
  const aperta = chiave === CAMPAGNA_APERTA;
  const stato = (c.conclusa ? `<span class="tag">conclusa · ${esc(c.perche_conclusa)}</span>`
    : `<span class="tag accento">${passoDi(c)}</span>`) + ' ' + tagIscrizione(c);
  return `<article class="scheda scheda-campagna">
    <div class="testa" tabindex="0" role="button" data-azione="campagna" data-chiave="${attr(chiave)}" aria-expanded="${aperta}">
      <div>
        <span class="nome-c">${nomeCampagna(c, c.campagna)}</span> ${stato}
        <div class="fine">${[c.provider ? esc(c.provider) : '', c.residuo != null ? 'residuo ' + soldi(c.residuo) : '',
          c.mesi != null ? Math.min(c.mesi, 99).toFixed(1) + ' mesi' : ''].filter(Boolean).join(' · ')}</div>
        ${rigaPaletti(c)}
        ${c.conclusa ? '' : passi(c)}
      </div>
      <div class="numeri">
        ${!c.cartella && (c.lavorazione || []).some(l => l.renderizzate)
          ? `<div><b>${c.lavorazione.reduce((t, l) => t + l.renderizzate, 0)}</b><span>renderizzate</span></div>`
          : `<div><b>${c.clip_distinte}</b><span>clip</span></div>`}
        <div><b>${c.pubblicate}/${c.totale_clip}</b><span>pubblicazioni</span></div>
        <div><b>${numero((c.metriche || {}).views || 0)}</b><span>views</span></div>
        ${menuCampagna(c, s)}
      </div>
    </div>
    ${aperta ? `<div class="corpo">${comandi(c, s)}${c.cartella ? tabellaClip(c)
      : (c.lavorazione || []).some(l => l.renderizzate)
        ? '<div class="fine">Le clip ci sono in <code>output/</code>; con la consegna («Prepara la pubblicazione») compaiono qui una per una, con le metriche.</div>'
        : ''}</div>` : ''}
  </article>`;
}

function disegnaProduzione() {
  const conAttive = PANNELLO.filter(s => (s.attive || []).length);
  const concluse = PANNELLO.flatMap(s => (s.concluse || []).map(c => [c, s]));
  const giro = GIRO || {};
  const banda = giro.in_corso && giro.che && !giro.che.startsWith('aggiornamento')
    ? `<div class="banda-lavoro" role="status"><span class="pulsa" aria-hidden="true"></span>
        <div><b>In corso: ${esc(giro.che)}</b>${giro.passo ? ` · ${esc(giro.passo)}` : ''}
        <div class="fine">partito ${tempoFa(giro.iniziato_il)}: si può continuare a usare il pannello</div></div></div>` : '';
  $('#produzione-corpo').innerHTML = `${banda}
    ${conAttive.length ? conAttive.map(s => `
      <div class="gruppo-nicchia"><h2>${esc(s.nicchia && s.nicchia !== 'nessuna' ? s.nicchia : s.chiave)}</h2>
        <span class="fine">${s.gruppo ? `account ${esc(s.gruppo)}` : 'nessun account: si prepara e si renderizza, il calendario aspetta'}</span></div>
      ${s.attive.map(c => schedaCampagna(c, s)).join('')}`).join('')
      : '<div class="vuoto">Nessuna campagna in lavorazione. Prendine una da <a href="#campagne">Campagne</a>.</div>'}
    ${concluse.length ? `<details class="scheda" style="margin-top:22px"><summary>${concluse.length} campagne concluse
        <span class="fine">· finite perché tutto è stato caricato o perché il budget è esaurito, con clip e metriche</span></summary>
      <div style="margin-top:12px">${concluse.map(([c, s]) => schedaCampagna(c, s)).join('')}</div></details>` : ''}`;
}

// La clip aperta: il video e, sotto, quanto ha fatto su ciascuna piattaforma.
function apriClip(cartella, id) {
  let c = null;
  for (const s of PANNELLO) {
    c = (s.attive || []).concat(s.concluse || []).find(x => x.cartella === cartella);
    if (c) break;
  }
  const cl = c && c.clip_dettaglio.find(x => x.id === id);
  if (!cl) return;
  const righe = PIATTAFORME.map(p => {
    const v = cl.piattaforme[p] || {};
    const pubblicata = v.stato === 'pubblicato' || v.stato === 'done';
    return `<tr><td>${p}</td>
      <td>${pubblicata ? `<a href="${attr(v.url)}" target="_blank" rel="noopener">pubblicata ↗</a>` : `<span class="spenta">${esc(v.stato || 'non prevista')}</span>`}</td>
      <td class="num">${v.views != null ? '<b>' + numero(v.views) + '</b>' : '<span class="spenta">non misurata</span>'}</td>
      <td class="spenta">${esc((v.letto_il || '').replace('T', ' ').slice(0, 16))}</td>
      <td>${v.whop && v.whop !== 'in attesa' ? esc(v.whop) : '<span class="spenta">—</span>'}${v.pagamento ? `<div class="fine">${esc(v.pagamento.frase)}</div>` : ''}</td></tr>`;
  }).join('');
  const punteggi = cl.punteggi ? Object.entries(cl.punteggi).map(([k, v]) =>
    `<span class="punteggio ${esc(v)}">${esc(k)}: ${esc(v)}</span>`).join('') : '';
  $('.finestra-corpo').innerHTML = `
    <button class="chiudi" data-azione="chiudi-clip" aria-label="Chiudi">&times;</button>
    <h3>${esc(cl.hook || cl.titolo || 'clip ' + cl.id)}</h3>
    <div class="fine">${esc(c.campagna)} · clip ${esc(cl.id)}${cl.durata ? ' · ' + cl.durata + ' s' : ''}</div>
    ${cl.file ? `<video controls preload="metadata" src="/clip/${encodeURIComponent(cartella)}/${encodeURIComponent(cl.file)}"></video>`
              : '<div class="vuoto">Il file non è in <code>output/</code>: la clip non è stata renderizzata.</div>'}
    <div class="punteggi">${punteggi}</div>
    <div class="scorre"><table><thead><tr><th>Piattaforma</th><th>Stato</th><th class="num">Views</th><th>Letto il</th><th>Whop</th></tr></thead>
      <tbody>${righe}</tbody></table></div>
    <p class="nota">YouTube si legge in HTTP; TikTok e Instagram dalle pagine degli account, con «Aggiorna social». «non misurata» vuol dire proprio questo — non zero.</p>`;
  $('#finestra-clip').hidden = false;
  $('.finestra-corpo .chiudi').focus();
}

function chiudiClip() {
  const v = $('.finestra-corpo video');
  if (v) v.pause();
  $('#finestra-clip').hidden = true;
}

// ======================================================================
// PUBBLICAZIONE — cosa esce, gli invii a mano, il ritmo
// ======================================================================

// La coda «da inviare a mano» (roadmap, fase 1, voce 5). Un invio Whop che
// fallisce non ferma la pubblicazione: il post esce e l'esito resta scritto.
// Qui c'è quello che serve per farlo in trenta secondi — il post, il pannello
// del provider, il tempo che resta — e il pulsante che prende nota del gesto.
// Il pannello non invia niente: registra che l'hai fatto tu.
function schedaInviiAMano(righe) {
  const aperte = righe.filter(r => !r.finita);
  const finite = righe.filter(r => r.finita);
  const riga = r => {
    const quando = r.minuti == null ? '—'
      : r.in_finestra ? `<span class="finestra-aperta">${Math.max(0, 30 - r.minuti)} min alla chiusura</span>`
      : `pubblicata ${tempoFa(r.pubblicato_il)}`;
    return `<tr class="${r.finita ? 'spenta' : ''}">
      <td><b>clip ${esc(r.clip)}</b> · ${esc(r.piattaforma)}<div class="fine">${esc(r.titolo || '')}</div></td>
      <td>${esc(r.campagna)}${r.finita ? `<div class="fine">${esc(r.finita)}: l'invio non paga più</div>` : ''}</td>
      <td>${quando}</td>
      <td><span class="tag attenzione">${esc(r.esito)}</span></td>
      <td class="azioni">
        <a class="btn piccolo" href="${attr(r.url)}" target="_blank" rel="noopener">Post ↗</a>
        <button class="btn piccolo" data-azione="copia" data-testo="${attr(r.url)}">Copia link</button>
        ${r.whop_app ? `<a class="btn piccolo" href="${attr(r.whop_app)}" target="_blank" rel="noopener" title="Il pannello del provider su Whop, dove si invia la clip">Whop ↗</a>` : ''}
        <button class="btn primario piccolo" data-azione="whop-mano" data-cartella="${attr(r.cartella)}"
          data-clip="${attr(r.clip)}" data-piattaforma="${attr(r.piattaforma)}">Segna inviato</button>
      </td></tr>`;
  };
  const tabella = rr => `<div class="scorre"><table><thead><tr><th>Pubblicazione</th><th>Campagna</th><th>Finestra</th><th>Esito</th><th></th></tr></thead>
    <tbody>${rr.map(riga).join('')}</tbody></table></div>`;
  return `<div class="scheda"><h2>Da inviare a mano <span class="a-destra fine">${aperte.length ? `${aperte.length} in sospeso` : ''}</span></h2>
    ${aperte.length ? tabella(aperte) : '<div class="vuoto">Nessun invio in sospeso: ogni post online è registrato su Whop, oppure la sua campagna non passa da Whop.</div>'}
    ${finite.length ? `<details class="nota"><summary>${finite.length} su campagne finite</summary>${tabella(finite)}</details>` : ''}
    <p class="nota">Il post è già online e la finestra per registrarlo è di <b>30 minuti</b> dalla pubblicazione. Si apre il
      pannello Whop del provider, si incolla il link, e poi «Segna inviato»: il pannello prende nota, non invia niente. Quando
      l'invio automatico fallisce parte anche una notifica di Windows.</p></div>`;
}

// L'ultimo controllo degli invii Whop, sulle clip scadute. «ferma» vuol dire
// che la campagna non accetta invii, e la clip esce da sola quando riapre.
function testoInvii(r) {
  if (!r.invii) return '';
  const e = r.invii.esito;
  const letto = r.invii.letto_il ? ` (letto ${r.invii.letto_il.replace('T', ' ').slice(0, 16)})` : '';
  if (r.stato === 'ferma') return `invii Whop ${e}${letto}: esce da sola quando la campagna riapre`;
  if (e === 'aperti' || e === 'nessuna campagna') return '';
  return `invii Whop ${e}${letto}: non esce finché lo stato non si legge`;
}

function rigaCalendario(r) {
  const stati = { 'in attesa': 'info', 'in ritardo': 'errore', pubblicata: 'ok', 'non programmata': 'attenzione',
                  sospesa: '', ferma: '' };
  const q = r.quando ? quandoInParole(r.quando) : null;
  // Le piattaforme si mostrano tutte e tre sempre, e sotto ognuna la sua spia
  // Whop: l'invio è per coppia (clip, piattaforma), e una spia unica per la
  // clip direbbe «a posto» con un terzo del lavoro da rifare.
  const whopFatte = new Set(r.whop_fatte || []);
  const piatt = Object.entries(r.piattaforme || {}).map(([p, st]) => {
    const fatta = st === 'pubblicato' || st === 'done';
    const suWhop = whopFatte.has(p);
    // «a mano» è un pulsante: chi ha pubblicato dal telefono incolla il link, e
    // da lì la coppia risulta uscita qui e per l'esecutore.
    const sigla = st === 'a mano'
      ? `<button class="tag attenzione senza-punto" data-azione="pubblicato-mano" data-cartella="${attr(r.cartella)}"
          data-clip="${attr(r.id)}" data-piattaforma="${attr(p)}"
          title="La pubblichi tu dal telefono: l'esecutore non la tocca. Clic per registrarla col link del post.">${SIGLA[p] || p} a mano ✎</button>`
      : st === 'sospesa'
      ? `<span class="tag senza-punto" title="Piattaforma sospesa nel registro (pubblicazione_sospesa): non esce finché non la si riapre.">${SIGLA[p] || p} sospesa</span>`
      : `<span class="tag ${fatta ? 'ok' : ''} senza-punto">${SIGLA[p] || p}</span>`;
    return `<span class="piatt-cal">
      ${sigla}
      <span class="tag ${suWhop ? 'ok' : ''} senza-punto" title="${attr(suWhop ? 'registrata su Whop' : 'non ancora su Whop: ' + ((r.whop || {})[p] || 'in attesa'))}">whop</span>
    </span>`;
  }).join('');
  const invii = testoInvii(r);
  return `<tr>
    <td>${q ? `<b>${q.data}</b> ${q.ora}<div class="fine">${q.fra}</div>` : '<span class="fine">nessuna data</span>'}</td>
    <td><span class="tag ${stati[r.stato] ?? ''}">${esc(r.stato)}</span></td>
    <td>${esc(r.campagna)}<div class="fine">clip ${esc(r.id)} — ${esc(r.titolo || '')}</div>
      ${r.sospesa ? `<div class="fine">sospesa: ${esc(r.sospesa)}</div>` : ''}${invii ? `<div class="fine">${esc(invii)}</div>` : ''}</td>
    <td>${piatt}</td></tr>`;
}

function tabellaCalendario(righe, vuoto) {
  if (!righe.length) return `<div class="vuoto">${vuoto}</div>`;
  return `<div class="scorre"><table><thead><tr><th>Quando</th><th>Stato</th><th>Campagna e clip</th><th>Piattaforme</th></tr></thead>
    <tbody>${righe.map(rigaCalendario).join('')}</tbody></table></div>`;
}

function riassuntoCalendario(prossime) {
  // Il riassunto dice **la prossima uscita**, non un totale: è la domanda che
  // ci si fa aprendo questa sezione. Una clip sospesa o ferma non «esce».
  const p = prossime.find(r => r.stato !== 'sospesa' && r.stato !== 'ferma');
  const ritardo = prossime.filter(r => r.stato === 'in ritardo').length;
  const ferme = prossime.filter(r => r.stato === 'ferma').length;
  let testo = p ? `La prossima esce ${quandoInParole(p.quando).fra} — ${quandoInParole(p.quando).data} alle ${quandoInParole(p.quando).ora}, clip ${p.id} di ${p.campagna}, su ${(p.mancanti || []).join(', ')}.`
    : 'Niente in programma.';
  if (ritardo) testo += ` ${ritardo} in ritardo: il momento è passato e non sono uscite dappertutto — il ritmo dell'account può averle rimandate.`;
  if (ferme) testo += ` ${ferme} ${ferme === 1 ? 'ferma' : 'ferme'}: la campagna non accetta invii Whop, escono da sole alla riapertura.`;
  return testo + ' Le pubblicazioni partono da sole alle 15:15, 19:15, 21:15 e 23:15.';
}

// Le clip non uscite di una campagna finita non sono in coda: non usciranno
// più, e l'esecutore non le prova nemmeno. Fuori dalle tabelle, ma dette in
// una riga — sparire senza dirlo non è lo stesso che essere a posto.
function notaFinite(righe) {
  const per = {};
  righe.forEach(r => { (per[r.campagna] ||= { n: 0, perche: r.finita }).n++; });
  const voci = Object.entries(per).map(([c, v]) => `${v.n} di ${c} (${v.perche})`);
  return voci.length ? `<p class="nota">Non escono più, campagna finita: ${esc(voci.join('; '))}.</p>` : '';
}

function disegnaPubblicazione() {
  const righe = SNAP.calendario || [];
  const finite = righe.filter(r => r.stato === 'campagna finita');
  const prossime = righe.filter(r => r.quando && r.stato !== 'pubblicata' && r.stato !== 'campagna finita');
  const fatte = righe.filter(r => r.stato === 'pubblicata');
  const senza = righe.filter(r => !r.quando && r.stato !== 'pubblicata' && r.stato !== 'campagna finita');
  $('#pubblicazione-corpo').innerHTML = `
    ${schedaInviiAMano(SNAP.invii_a_mano || [])}
    <div class="scheda"><h2>Cosa esce, e quando</h2><p class="fine" style="margin:0 0 12px">${esc(riassuntoCalendario(prossime))}</p>
      ${tabellaCalendario(prossime, 'Nessuna clip programmata. «Prepara la pubblicazione» su una campagna renderizzata, in Produzione.')}${notaFinite(finite)}</div>
    <details class="scheda"><summary>Già pubblicate <span class="fine">· ${fatte.length}</span></summary><div style="margin-top:12px">${tabellaCalendario(fatte, 'Ancora nessuna.')}</div></details>
    <details class="scheda"><summary>Senza calendario <span class="fine">· ${senza.length} clip renderizzate a cui nessuno ha dato una data: finché restano qui non esce niente</span></summary>
      <div style="margin-top:12px">${tabellaCalendario(senza, 'Nessuna: tutte hanno una data.')}</div></details>
    <h2 class="titolo-scheda" style="margin-top:24px">Account e ritmo</h2>
    <div class="griglia g-auto">
      ${(SNAP.gruppi || []).map(g => `<div class="scheda carta-account">
        <div class="piatt">${esc(g.nicchia)}</div><div class="handle">gruppo ${esc(g.id)}</div>
        <div style="margin:6px 0">${g.aperto ? '<span class="tag ok">attivo</span>' : '<span class="tag attenzione">account da creare</span>'}</div>
        <div class="fine">${g.piattaforme_mancanti.length ? 'da aprire: ' + esc(g.piattaforme_mancanti.join(', ')) : 'tutti i profili aperti'}
          · ${g.scelte} campagne scelte, ${g.in_corso} in corso</div></div>`).join('')}
      ${(SNAP.ritmo || []).map(r => `<div class="scheda carta-account">
        <div class="piatt">${esc(r.piattaforma)}</div><div class="handle">${esc(r.handle)}</div>
        <div style="margin:6px 0">${r.puo ? '<span class="tag ok">si può pubblicare</span>' : '<span class="tag attenzione">in pausa</span>'}</div>
        <div class="fine">${esc(r.motivo)}</div></div>`).join('')}
    </div>`;
}

// ======================================================================
// NUMERI — views, account, Whop
// ======================================================================

// Un grafico a linee in SVG, senza librerie. Tre serie al massimo, distinte
// per colore **e** per tratto (pieno, tratteggiato, puntinato), con il nome
// scritto in fondo alla linea: il colore da solo non lo legge chi non lo vede.
function graficoLinee(andamento) {
  const serie = Object.entries(andamento || {}).filter(([, v]) => v.length);
  if (!serie.length) return '<div class="vuoto">Nessuna misura ancora: le views si leggono con «Aggiorna social».</div>';
  const L = 1100, H = 300, sx = 56, dx = 110, su = 14, giu = 30;
  const giorni = serie[0][1].map(p => p.giorno);
  const max = Math.max(10, ...serie.flatMap(([, v]) => v.map(p => p.views)));
  const scalaY = v => su + (H - su - giu) * (1 - v / max);
  const scalaX = i => sx + (L - sx - dx) * (giorni.length > 1 ? i / (giorni.length - 1) : 0);
  const tratti = { youtube: ['var(--serie-1)', ''], instagram: ['var(--serie-2)', '6 5'], tiktok: ['var(--serie-3)', '2 4'] };
  const tacche = [0, 0.25, 0.5, 0.75, 1].map(f => {
    const v = max * f, y = scalaY(v);
    return `<line class="asse" x1="${sx}" x2="${L - dx}" y1="${y}" y2="${y}"/>
      <text class="etichetta-asse" x="${sx - 8}" y="${y + 4}" text-anchor="end">${numero(v)}</text>`;
  }).join('');
  const date = giorni.map((g, i) => (i % 7 === (giorni.length - 1) % 7)
    ? `<text class="etichetta-asse" x="${scalaX(i)}" y="${H - 8}" text-anchor="middle">${dataBreve(g)}</text>` : '').join('');
  const linee = serie.map(([nome, v]) => {
    const [colore, trattino] = tratti[nome] || ['var(--serie-3)', ''];
    const d = v.map((p, i) => `${i ? 'L' : 'M'}${scalaX(i).toFixed(1)},${scalaY(p.views).toFixed(1)}`).join(' ');
    const ultimo = v[v.length - 1];
    return `<path class="serie" d="${d}" stroke="${colore}" ${trattino ? `stroke-dasharray="${trattino}"` : ''}/>
      <text class="nome-serie" x="${L - dx + 8}" y="${scalaY(ultimo.views) + 4}" fill="${colore}">${SIGLA[nome] || nome} ${numero(ultimo.views)}</text>`;
  }).join('');
  const riassunto = serie.map(([nome, v]) => `${nome} ${numero(v[v.length - 1].views)}`).join(', ');
  return `<svg class="grafico" viewBox="0 0 ${L} ${H}" role="img" aria-label="Views totali negli ultimi ${giorni.length} giorni; oggi ${attr(riassunto)}">
      ${tacche}${date}${linee}</svg>
    <div class="legenda">${serie.map(([nome]) => {
      const [colore, trattino] = tratti[nome] || ['var(--serie-3)', ''];
      return `<span><i style="border-top-color:${colore};border-top-style:${trattino ? (nome === 'tiktok' ? 'dotted' : 'dashed') : 'solid'}"></i>${nome}</span>`;
    }).join('')}</div>`;
}

// Le analitiche per post: quanto, quanto tiene, da dove. Una quota che la
// piattaforma non da' resta «—», mai zero; e la tenuta porta il suo nome, perche'
// su TikTok e' chi resta al terzo secondo e su YouTube la percentuale guardata.
const quota = v => (v == null ? '—' : `${Math.round(v * 100)}%`);
const voce = v => (v ? `${esc(v[0])} <b>${quota(v[1])}</b>` : '—');

function schedaAnalitiche(righe) {
  if (!righe.length) {
    return '<div class="scheda"><h2>Analitiche per post</h2><div class="vuoto">Nessuna analitica letta: le legge «Aggiorna social».</div></div>';
  }
  const quando = righe.map(r => r.quando).filter(Boolean).sort().pop();
  return `<div class="scheda"><h2>Analitiche per post <span class="a-destra fine">lette ${tempoFa(quando)}</span></h2>
    <div class="scorre"><table><thead><tr><th>Piattaforma</th><th>Account</th><th>Clip</th><th class="num">views</th>
      <th class="num">tempo medio</th><th>tenuta</th><th>fonte principale</th><th>paese principale</th><th></th></tr></thead><tbody>
    ${righe.map(r => `<tr><td>${esc(r.piattaforma)}</td><td>${esc(r.identita || '—')}</td>
      <td>${r.cartella ? `${esc(r.cartella.slice(0, 28))} <b>${esc(r.clip_id || '')}</b>` : '<span class="debole">fuori dalle consegne</span>'}</td>
      ${r.letto ? `<td class="num"><b>${numero(r.views)}</b></td>
        <td class="num">${r.tempo_medio_s == null ? '—' : `${r.tempo_medio_s.toFixed(1).replace('.', ',')} s`}</td>
        <td>${r.tenuta ? `${esc(r.tenuta[0])} <b>${quota(r.tenuta[1])}</b>` : '—'}</td>
        <td>${voce(r.fonte)}</td><td>${voce(r.paese)}</td>`
        : `<td colspan="5" class="debole" title="${attr(r.guasto || '')}">non letto</td>`}
      <td><a href="${attr(r.post_id)}" target="_blank" rel="noopener">apri ↗</a></td></tr>`).join('')}
    </tbody></table></div>
    <p class="nota">Dalle pagine di analisi di Studio, post per post. Instagram sul web dà solo le views; con il token dell'API di Meta anche tempo medio e tenuta. Fonti e paesi per reel li mostra solo l'app.
      Il dettaglio completo — curve, età, views ora per ora — con <code>python dashboard/analitiche.py mostra</code>.</p></div>`;
}

function disegnaNumeri() {
  const n = SNAP.numeri || {};
  const account = n.account || [];
  const whop = n.whop || [];
  $('#numeri-corpo').innerHTML = `
    ${schedaPiattaforme(n)}
    <div class="scheda"><h2>Views totali, ultimi 30 giorni <span class="a-destra fine">l'ultima misura di ogni post, sommata giorno per giorno</span></h2>
      ${graficoLinee(n.andamento)}</div>
    <div class="griglia g-2">
      <div class="scheda"><h2>Per account</h2>
        ${account.length ? `<div class="scorre"><table><thead><tr><th>Account</th><th>Piattaforma</th><th class="num">post</th>
          <th class="num">mediana</th><th class="num">migliore</th><th class="num">totale</th></tr></thead><tbody>
          ${account.map(a => `<tr><td>${esc(a.identita)}</td><td>${esc(a.piattaforma)}</td><td class="num">${a.post}</td>
            <td class="num"><b>${numero(a.mediana)}</b></td><td class="num">${numero(a.massimo)}</td><td class="num">${numero(a.totale)}</td></tr>`).join('')}
          </tbody></table></div>` : '<div class="vuoto">Nessun post misurato.</div>'}
        <p class="nota">Un post mai letto non entra nella mediana: «non misurato» non è zero.</p></div>
      <div class="scheda"><h2>Whop, per gruppo</h2>
        ${whop.length ? `<div class="scorre"><table><thead><tr><th>Gruppo</th><th class="num">invii</th><th class="num">approvati</th>
          <th class="num">in attesa</th><th class="num">respinti</th><th class="num">incassato</th><th>letto</th></tr></thead><tbody>
          ${whop.map(w => `<tr><td>${esc(w.gruppo)}</td><td class="num">${numero(w.clip)}</td><td class="num">${numero(w.approvate)}</td>
            <td class="num">${numero(w.in_attesa)}</td><td class="num">${numero(w.respinte)}</td>
            <td class="num"><b>${w.pagato == null ? '<span title="importo da riconoscere: la forma del pagamento non è nota">?</span>' : denaro(w.pagato)}</b></td>
            <td class="fine">${tempoFa(w.quando)}</td></tr>`).join('')}
          </tbody></table></div>` : '<div class="vuoto">Whop non è ancora stato letto: lo legge «Aggiorna social».</div>'}
        <p class="nota">Letto dal pannello Content Rewards di ogni gruppo: i contatori di «Submissions» e i pagamenti.
          Un pagamento stornato non è un incasso, e un importo in un'unità che non si riconosce resta «?», mai zero.</p></div>
    </div>
    ${schedaAnalitiche(n.analitiche || [])}
    <details class="scheda"><summary>Dettaglio per cartella <span class="fine">· ogni pubblicazione, con identità, stato ed esito Whop</span></summary>
      ${(SNAP.campagne || []).map(camp => `<h3 style="margin:18px 0 6px">${esc(camp.campagna)} <span class="fine">${camp.pubblicate}/${camp.totale_clip} pubblicate</span></h3>
        <div class="scorre"><table><thead><tr><th>ID</th><th>Titolo</th><th>Identità</th><th>Piattaforma</th><th>Stato</th><th>Whop</th><th></th></tr></thead><tbody>
        ${camp.clip.map(c => `<tr><td>${esc(c.id)}</td><td>${esc((c.titolo || '').slice(0, 60))}</td><td>${esc(c.identita || '—')}</td>
          <td>${esc(c.piattaforma || '—')}</td><td>${esc(c.stato_pubblicazione)}</td><td>${esc(c.stato_whop)}</td>
          <td>${c.url ? `<a href="${attr(c.url)}" target="_blank" rel="noopener">apri ↗</a>` : '—'}</td></tr>`).join('')}
        </tbody></table></div>`).join('')}
    </details>`;
}

// ======================================================================
// BARRA — contatori, freschezza, il giro in corso
// ======================================================================

function disegnaBarra() {
  const urgenti = (SNAP.da_fare || []).filter(v => v.livello !== 'presto').length;
  const mano = (SNAP.invii_a_mano || []).filter(r => !r.finita).length;
  const inProduzione = PANNELLO.reduce((t, s) => t + (s.attive || []).length, 0);
  const pronte = prendibili().length;
  const metti = (id, n) => { const el = $(id); el.hidden = !n; el.textContent = n || ''; };
  metti('#conta-oggi', urgenti);
  metti('#conta-pubblicazione', mano);
  metti('#conta-produzione', inProduzione);
  metti('#conta-campagne', pronte);
  // L'età di quello che si guarda: il radar si aggiorna a mano, quindi la
  // distanza dall'ultimo clic è esattamente l'età del catalogo.
  const f = SNAP.freschezza || {};
  const vecchio = iso => !iso || (Date.now() - new Date(iso)) > 2 * 86400000;
  $('#freschezza').innerHTML = [['radar', f.radar], ['views', f.views], ['analitiche', f.analitiche], ['Whop', f.whop]].map(([k, v]) =>
    `<dt>${k}</dt><dd class="${vecchio(v) ? 'vecchio' : ''}" title="${attr(v || 'mai letto')}">${tempoFa(v)}</dd>`).join('');
}

// ======================================================================
// AGGIORNA e LAVORI — un giro alla volta, e la pagina chiede a che punto è
// ======================================================================

let GIRO = null;
let SONDA = null;

// Un giro alla volta: mentre ne lavora uno, tutti e due i tasti sono spenti, e
// quello del giro in corso dice a che passo e'.
const TASTI_BARRA = { social: ['#btn-social', 'Aggiorna social'], radar: ['#btn-radar', 'Aggiorna radar'] };

function mostraGiro(g) {
  GIRO = g;
  const nota = $('#stato-giro');
  const suo = (g.che || '').replace('aggiornamento ', '');
  if (g.in_corso) {
    for (const [chiave, [id, nome]] of Object.entries(TASTI_BARRA)) {
      $(id).disabled = true;
      $(id).textContent = chiave === suo || g.che === 'aggiornamento' ? (g.passo ? `${g.passo}…` : 'avvio…') : nome;
    }
    nota.innerHTML = `<b>${esc(g.che || 'lavoro')}</b> in corso, da ${esc((g.iniziato_il || '').slice(11, 16))}`;
    return true;
  }
  for (const [id, nome] of Object.values(TASTI_BARRA)) { $(id).disabled = false; $(id).textContent = nome; }
  if (g.finito_il) {
    const rotti = (g.passi || []).filter(p => p.codice !== 0);
    nota.textContent = `${g.che || 'aggiornamento'} finito ${tempoFa(g.finito_il)}` + (rotti.length ? ` · ${rotti.map(p => p.passo).join(' e ')} in errore` : '');
    mostraErrore(rotti, g);
  }
  return false;
}

// Il testo vero del guasto, non solo il fatto che c'è stato: il server cattura
// le ultime venti righe di stderr di ogni passo.
function mostraErrore(rotti, g) {
  const box = $('#errore-giro');
  if (!rotti.length) { box.hidden = true; return; }
  const p = rotti[0];
  $('#errore-titolo').textContent = `«${p.passo}» è fallito (codice ${p.codice}) durante ${g.che || 'il giro'}`;
  const righe = (p.coda || []).filter(r => r.trim());
  $('#errore-testo').textContent = righe.length ? righe.join('\n')
    : "Il comando non ha scritto niente su stderr: non c'è altro da dire oltre al codice di uscita.";
  box.hidden = false;
}

async function sondaGiro() {
  try {
    const g = await get('/api/aggiorna');
    if (!mostraGiro(g)) {
      clearInterval(SONDA); SONDA = null;
      await avvio();
      toast(`${g.che || 'Aggiornamento'} finito${g.esito === 'ok' ? '' : ' con errori'}`, { errore: g.esito !== 'ok' });
    } else if (sezioneCorrente() === 'produzione') {
      disegnaProduzione();
    }
  } catch { /* il server può essere occupato: si riprova al giro dopo */ }
}

function avviaSonda() { if (!SONDA) SONDA = setInterval(sondaGiro, 4000); }

function sorgenteDi(bottone) {
  // La radio scelta fra i candidati, altrimenti quella incollata a mano.
  const comandi = bottone.closest('.comandi');
  const scelta = comandi.querySelector('input[type=radio]:checked');
  const campo = comandi.querySelector('.fonte');
  return (scelta && scelta.value) || (campo ? campo.value.trim() : '');
}

async function lanciaLavoro(corpo, bottone) {
  if ((corpo.azione === 'prepara' || corpo.azione === 'tutto') && !corpo.sorgente) {
    toast('Serve il link del video sorgente: la campagna dice cosa clippare, non dove sta.', { errore: true });
    return;
  }
  // L'etichetta di partenza si tiene da parte prima di sovrascriverla: un avvio
  // fallito non deve lasciare scritto «avviato…» su un lavoro mai partito.
  const etichetta = bottone.textContent;
  bottone.disabled = true;
  bottone.textContent = 'avviato…';
  try {
    mostraGiro(await post('/api/lavora', corpo));
    avviaSonda();
    toast(`Avviato: ${corpo.azione}. Lo stato è nella barra a sinistra.`);
  } catch (err) {
    bottone.disabled = false;
    bottone.textContent = etichetta;
    toast(`Non riesco ad avviare: ${err.message}`, { errore: true });
  }
}

// Sposta una campagna fra attive, concluse e disponibili. Scrive solo in
// `scelte.json`: non tocca le clip, non cancella cartelle, non manda niente.
async function cambiaScelta(corpo, bottone) {
  if (bottone) bottone.disabled = true;
  try {
    await post('/api/scelte', corpo);
    MENU_APERTO = null;
    await avvio();
  } catch (err) {
    if (bottone) bottone.disabled = false;
    toast(`Non riesco a registrare la scelta: ${err.message}`, { errore: true });
  }
}

async function segnaWhop(el, annulla = false) {
  const corpo = { cartella: el.dataset.cartella, clip: el.dataset.clip, piattaforma: el.dataset.piattaforma, annulla };
  el.disabled = true;
  try {
    await post('/api/whop-mano', corpo);
    await avvio();
    if (!annulla) {
      toast(`Clip ${corpo.clip} su ${corpo.piattaforma}: segnata come inviata a mano.`, {
        azione: { etichetta: 'Annulla', fn: () => segnaWhop({ dataset: el.dataset, disabled: false }, true) },
        durata: 9000,
      });
    }
  } catch (err) {
    el.disabled = false;
    toast(`Non riesco a registrarlo: ${err.message}`, { errore: true });
  }
}

async function segnaPubblicato(el) {
  const { cartella, clip, piattaforma } = el.dataset;
  const url = prompt(`Clip ${clip} su ${piattaforma}: incolla il link del post pubblicato.`);
  if (!url) return;
  const whop_inviato = confirm("L'hai già inviata anche su Whop?\nOK = sì, inviata · Annulla = non ancora (finisce in «Da inviare a mano»)");
  el.disabled = true;
  try {
    await post('/api/pubblicato-mano', { cartella, clip, piattaforma, url: url.trim(), whop_inviato });
    await avvio();
    toast(`Clip ${clip} su ${piattaforma}: registrata come pubblicata${whop_inviato ? ' e inviata a Whop' : ''}.`);
  } catch (err) {
    el.disabled = false;
    toast(`Non riesco a registrarla: ${err.message}`, { errore: true });
  }
}

// ------------------------------------------------ le azioni, in un posto solo

const AZIONI = {
  aggiorna: async el => {
    mostraGiro(await post('/api/aggiorna', { cosa: el.dataset.cosa || 'tutto' }));
    avviaSonda();
  },
  tema: () => {
    const ora = document.documentElement.dataset.tema;
    const dopo = TEMI[(TEMI.indexOf(ora) + 1) % TEMI.length];
    localStorage.setItem('tema', dopo);
    applicaTema(dopo);
  },
  'chiudi-errore': () => { $('#errore-giro').hidden = true; },
  'chiudi-clip': () => chiudiClip(),
  nicchia: el => {
    APERTA = APERTA === el.dataset.chiave ? null : el.dataset.chiave;
    disegnaCampagne();
  },
  'catalogo-tutto': el => {
    const k = el.dataset.chiave;
    CATALOGO_TUTTO.has(k) ? CATALOGO_TUTTO.delete(k) : CATALOGO_TUTTO.add(k);
    disegnaCampagne();
  },
  'solo-pronte': () => { SOLO_PRONTE = !SOLO_PRONTE; disegnaCampagne(); },
  scegli: async el => {
    el.disabled = true;
    try {
      await post('/api/scelte', { campagna_id: el.dataset.id, gruppo: el.dataset.gruppo || null, nicchia: el.dataset.nicchia });
      await avvio();
      toast('Campagna scelta: la trovi in Produzione.', { azione: { etichetta: 'Apri', fn: () => { location.hash = 'produzione'; } } });
    } catch (err) {
      el.disabled = false;
      toast(`Non riesco a registrare la scelta: ${err.message}`, { errore: true });
    }
  },
  campagna: (el, e) => {
    // Il nome è un collegamento dentro una testata che apre e chiude la scheda:
    // un clic sul nome o su un pulsante non deve fare anche l'altra cosa.
    if (e.target.closest('a, button, input, label')) return;
    CAMPAGNA_APERTA = CAMPAGNA_APERTA === el.dataset.chiave ? null : el.dataset.chiave;
    disegnaProduzione();
  },
  menu: (el, e) => {
    e.stopPropagation();
    MENU_APERTO = MENU_APERTO === el.dataset.chiave ? null : el.dataset.chiave;
    disegnaProduzione();
  },
  scelta: (el, e) => {
    e.stopPropagation();
    // Il gruppo **vero** della nicchia, non la sua chiave: su «intrattenimento»
    // il gruppo si chiama `podcast`, e la chiave riscriveva la scelta su un
    // gruppo che non esiste.
    cambiaScelta({ campagna_id: el.dataset.uuid, stato: el.dataset.stato, gruppo: el.dataset.gruppo || null,
                   nicchia: el.dataset.nicchia || null }, el);
  },
  libera: (el, e) => {
    e.stopPropagation();
    cambiaScelta({ campagna_id: el.dataset.uuid, rimuovi: true }, el);
  },
  lavora: el => {
    const tipo = el.dataset.tipo;
    if (tipo === 'tutto') {
      // «Fai tutto» arriva fino agli mp4 senza che nessuno guardi in mezzo: si
      // chiede conferma una volta, perché un clic distratto costa un'ora di
      // macchina e una consegna decisa senza revisione.
      if (!confirm('Fa tutto da solo: scarica, trascrive, legge il brief, sceglie le clip e le renderizza. '
          + 'Nessuno le guarda prima del render. Procedo?')) return;
    }
    const corpo = { azione: tipo };
    if (tipo === 'tutto' || tipo === 'prepara') {
      corpo.campagna_id = el.dataset.uuid;
      corpo.sorgente = sorgenteDi(el);
    } else {
      corpo.cartella = el.dataset.cartella;
    }
    lanciaLavoro(corpo, el);
  },
  clip: el => apriClip(el.dataset.cartella, el.dataset.id),
  copia: async el => {
    try {
      await navigator.clipboard.writeText(el.dataset.testo);
      toast('Link copiato: incollalo nel pannello Whop.');
    } catch {
      toast('Non riesco a copiare: il link è quello del pulsante «Post».', { errore: true });
    }
  },
  'whop-mano': el => segnaWhop(el),
  'pubblicato-mano': el => segnaPubblicato(el),
  dimentica: async () => {
    if (!confirm("Ricomincio da zero: il prossimo messaggio non ricorderà questa conversazione. Lo scritto resta leggibile.")) return;
    disegnaConversazione(await post('/api/dimentica', {}));
  },
};

document.addEventListener('click', e => {
  const el = e.target.closest('[data-azione]');
  if (!el) {
    if (MENU_APERTO && !e.target.closest('.menu')) { MENU_APERTO = null; disegnaProduzione(); }
    return;
  }
  const fn = AZIONI[el.dataset.azione];
  if (fn) fn(el, e);
});
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') {
    if (!$('#finestra-clip').hidden) chiudiClip();
    else if (MENU_APERTO) { MENU_APERTO = null; disegnaProduzione(); }
  }
  // Le righe cliccabili (una clip, la testata di una campagna) si aprono anche da tastiera.
  if ((e.key === 'Enter' || e.key === ' ') && e.target.matches('tr[data-azione], .testa[data-azione]')) {
    e.preventDefault();
    e.target.click();
  }
});

// ======================================================================
// CHIEDI
// ======================================================================

let SONDA_CHIEDI = null;

function bollaScambio(s) {
  if (s.separatore) return `<div class="separatore-filo">${esc(s.separatore)}</div>`;
  const quando = (s.quando || '').replace('T', ' ').slice(0, 16);
  const durata = s.secondi >= 60 ? `${Math.round(s.secondi / 60)} min` : `${Math.round(s.secondi || 0)} s`;
  // La risposta e l'errore si mostrano insieme: un comando può fallire dopo
  // aver già fatto metà del lavoro, e vedere solo l'errore nasconde cosa è
  // cambiato sul disco nel frattempo.
  return `<div class="scambio">
    <div class="domanda"><div class="fine">tu · ${esc(quando)}</div>${esc(s.domanda)}</div>
    ${s.risposta ? `<div class="risposta"><div class="fine">Claude · ${durata}</div>${esc(s.risposta)}</div>` : ''}
    ${s.errore ? `<div class="risposta errore-risposta"><b>non ha funzionato</b><pre>${esc(s.errore)}</pre></div>` : ''}
  </div>`;
}

function disegnaConversazione(c) {
  const box = $('#conversazione');
  const inFondo = box.scrollHeight - box.scrollTop - box.clientHeight < 60;
  let html = (c.scambi || []).map(bollaScambio).join('');
  if (c.in_corso) {
    html += `<div class="scambio"><div class="domanda">${esc(c.domanda_in_corso)}</div>
      <div class="risposta"><span class="fine">sto lavorando… la risposta arriva qui quando ho finito. Puoi cambiare sezione, non si perde.</span></div></div>`;
  }
  box.innerHTML = html || '<div class="vuoto">Ancora niente. Scrivi qui sotto.</div>';
  if (inFondo) box.scrollTop = box.scrollHeight;
  $('#btn-chiedi').disabled = !!c.in_corso;
  $('#btn-chiedi').textContent = c.in_corso ? 'sto lavorando…' : 'Chiedi';
  $('#nota-chiedi').textContent = c.sessione ? 'il filo continua: ricordo i messaggi precedenti'
    : 'filo nuovo: il prossimo messaggio riparte da zero';
  // Si sonda solo mentre c'è qualcosa in volo: una sonda che gira per sempre è
  // traffico che nessuno guarda.
  if (c.in_corso && !SONDA_CHIEDI) {
    SONDA_CHIEDI = setInterval(async () => disegnaConversazione(await get('/api/conversazione')), 2500);
  } else if (!c.in_corso && SONDA_CHIEDI) {
    clearInterval(SONDA_CHIEDI);
    SONDA_CHIEDI = null;
  }
}

$('#modulo-chiedi').addEventListener('submit', async e => {
  e.preventDefault();
  const campo = $('#domanda');
  const testo = campo.value.trim();
  if (!testo) return;
  campo.value = '';
  try {
    disegnaConversazione(await post('/api/chiedi', { testo }));
  } catch (err) {
    campo.value = testo;          // la domanda non si perde se l'invio fallisce
    toast(`Non riesco a chiedere: ${err.message}`, { errore: true });
  }
});
$('#domanda').addEventListener('keydown', e => {
  if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
    e.preventDefault();
    $('#modulo-chiedi').requestSubmit();
  }
});

// ======================================================================
// AVVIO
// ======================================================================

async function avvio() {
  SNAP = await get('/api/snapshot');
  PANNELLO = SNAP.pannello || [];
  disegnaBarra();
  disegnaOggi();
  disegnaCampagne();
  disegnaProduzione();
  disegnaPubblicazione();
  disegnaNumeri();
}

async function primoAvvio() {
  mostraSezione();
  await avvio();
  disegnaConversazione(await get('/api/conversazione'));
  // Un giro partito da un'altra scheda o prima di un ricaricamento: se c'è, la
  // pagina lo segue invece di mostrare «Aggiorna» come se niente fosse.
  if (mostraGiro(await get('/api/aggiorna'))) avviaSonda();
}

primoAvvio().catch(err => {
  $('#contenuto').innerHTML = `<div class="riquadro-errore"><b>Errore nel caricare il pannello</b><pre>${esc(err.stack || err)}</pre></div>`;
});
