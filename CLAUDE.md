# viral-clips — istruzioni per chi ci lavora (e per Claude)

Skill di Claude Code che trasforma un video lungo (un'ora o piu', da link o file locale)
in clip verticali 9:16 pronte per TikTok, Reels e Shorts, piu' l'operazione intorno:
trovare le campagne, pubblicare, leggere i numeri.

## I file del progetto

Qui stanno **decisioni stabili e trappole gia' pagate**: ogni riga deve restare vera tra
sei mesi e su un altro video. Ogni voce e' la regola col suo perche' in breve. Niente
stato. Una misura resta solo se giustifica una decisione: senza i quattro tempi
dell'`-hwaccel` la trappola e' un'opinione, e qualcuno la riprova.

- **[docs/roadmap.md](docs/roadmap.md)** — il piano a fasi, con date e cancelli (nel repo
  c'e' un modello). Lo legge il pannello (`stato.roadmap`), quindi titoli delle fasi e
  tabelle `## Scadenze` e `## Cancello` hanno una forma fissa.
- **[SKILL.md](.claude/skills/viral-clips/SKILL.md)** — come si esegue. Niente
  giustificazioni, niente stato.
- **[publish/README.md](publish/README.md)**, **[dashboard/README.md](dashboard/README.md)**
  — come funzionano pubblicazione e pannello.

**Lo stesso fatto sta in un file solo**, gli altri lo citano. **E questo file resta uno
solo** (30 settembre 2026): l'agente delle didascalie lavora in `output/` e `work/` e non
vedrebbe un `CLAUDE.md` annidato in `publish/`. Si accorcia, non si divide.

## I tre requisiti

1. **Tutto gratuito e in locale.** Nessuna API a pagamento, nessun credito, nessun upload
   dei video, nessun modello proprietario obbligatorio. Una soluzione che ne richiede una
   si scarta anche se e' tecnicamente migliore.
2. **Un comando solo.** E' una promessa sull'esperienza, non sul numero di invocazioni
   interne: in mezzo c'e' un giudizio, e nessun processo sa aspettarlo. Quindi `run.py
   prepare` si ferma, Claude sceglie, `run.py render` riparte.
3. **Qualita' pari al sorgente.** Il re-encode e' inevitabile (crop + sottotitoli
   impressi), quindi zero perdita percepibile e **una sola generazione di encode**.

## La divisione del lavoro (il cuore del progetto)

- **Gli script fanno il lavoro meccanico**: download, trascrizione, volti, segnali audio,
  render. Deterministico, ripetibile.
- **Claude fa il lavoro editoriale**: quali momenti reggono, dove iniziano e finiscono,
  quale hook, quali parole evidenziare.

E' la ragione d'essere della skill: gli strumenti in commercio delegano la selezione a un
LLM a pagamento (`AI-Youtube-Shorts-Generator` vuole OpenAI o Gemini anche in modalita'
«locale»). Lo stesso schema vale per `assegnazioni.json`, la nicchia di ogni campagna:
scritta leggendo, non calcolata, e la lettura sta dentro il giro del radar
(`radar/classifica.py`, a lotti, ripetuta solo dove il materiale e' cambiato).

## Scelte prese con l'utente

| Scelta | Valore |
|---|---|
| Lingua dei video | Italiano + inglese |
| Output | Solo 9:16 verticale, 1080x1920 |
| Elaborazione | Interamente locale |
| Modalita' | **Sempre automatica**: un link, e arrivano le clip. Nessuna domanda |
| Quante clip | **Tutte quelle che reggono da sole**, senza tetto |
| Criterio di selezione | **Autoconclusivita', non viralita'** |
| Durata | **Minimo 20 secondi**, nessun tetto |
| Punteggi | 6 dimensioni su **tre livelli**: debole / solido / forte |
| Sorgente 1080p | Crop verticale comunque (upscale 1.78x), **non** fascia sfocata |
| Hook in sovrimpressione | In cima, 84px, 5 secondi poi sfuma. **Una** parola tra `*asterischi*` in giallo, e almeno una |

- **L'hook deve incuriosire, e puo' essere leggermente clickbait**: basta che la clip
  tenga il nucleo della promessa. Nessun controllo automatico hook-transcript: un buon
  hook contiene parole che nessuno pronuncia («this», «30 seconds later»). Un brief che
  vieta di esagerare vince: **i paletti di campagna restringono le regole base, mai il
  contrario**.
- **Nessuna domanda e nessun tetto al numero di clip**: si consegna il lavoro finito, non
  una lista da approvare.
- **Autoconclusivita', non viralita'.** La soglia vecchia chiedeva `hook` e `carica`
  solide, cioe' di prevedere un pubblico, e teneva fuori clip utilizzabili. I sei
  punteggi **descrivono** la clip in `clips.md`; solo l'autosufficienza decide se esce.
  Niente taratura della rubrica sui giudizi di chi pubblica.
- **Il minimo di 20 secondi e' la stessa regola**: in quindici secondi non ci stanno
  premessa, sviluppo e chiusura. Un momento corto si allarga ai bordi prima di scartarlo.
- **Sopra non c'e' tetto.** Shorts accetta 3 minuti dal 15 ottobre 2024. Il confine reale
  a 60 secondi e' un **rischio di copyright** sulla musica di sottofondo (sopra il minuto
  una rivendicazione blocca lo Short ovunque): `validate.py` lo dice come avviso
  (`COPYRIGHT_RISK_LIMIT`). Il suo `MAX_DURATION = 180` intercetta solo i bordi sbagliati.
- Ci si ferma solo per le tre eccezioni di SKILL.md. «Fammi vedere prima» non e' un'opzione
  da riga di comando: la parte meccanica e' `run.py render --preview`.

## L'operazione: campagne, account, pubblicazione

La skill produce clip; trovare le campagne, pubblicare e leggere i numeri sta in `radar/`,
`publish/` e `dashboard/`. Gira su un portatile Windows o su un server Linux; in quel caso
il portatile apre il pannello via tunnel ssh (`dashboard/apri.py --server <host>`).

### Scegliere le campagne

- **Una campagna si sceglie su `$/post`, views per post per CPM** (`tassonomia.dollari_post()`).
  Non sul budget (ha proposto il collezionismo: Initech aveva 345 clipper, ultima del
  catalogo) e non su `$/clipper/mese`, che divide per la concorrenza (ha proposto il
  gaming su una campagna sola in scadenza). Il nostro limite sono i post.
- **Il CPM e' quello delle nostre tre piattaforme** (`tassonomia.cpm_nostro()`), non il
  campo `cpm` della scheda (`primaryPayoutCents`), che e' il **massimo** dichiarato: Fabrikam dichiara $2,00 (X)
  e paga $0,12 su YouTube, TikTok e Instagram. Il ripiego sul campo vecchio vale solo dove
  mancano le tariffe per piattaforma.
- **Accanto a `$/post` si legge il break-even**, `minimo / cpm x 1000`
  (`tassonomia.break_even()`): sotto quella soglia una clip vale zero, e per un account
  giovane e' il vincolo che morde. Oltre il tetto per clip una clip non
  paga piu': la viralita' e' capped, il mestiere sta nella mediana.
- **Tetto e minimo per clip sono per piattaforma.** Divergono su una campagna su sette
  (Contoso: $2,30 Instagram, $2,50 TikTok, $3,50 YouTube). `tetto_clip` e `minimo_clip`
  valgono `None` quando le piattaforme non concordano, il dettaglio sta in `tariffe`, e li
  legge solo `tassonomia.paletti()`. Meglio nessun numero che uno falso.
- **Due indirizzi per campagna**: `scheda` (`contentrewards.com/discover/<uuid>`, pubblica,
  per guardare) e `join` (whop, per inviare: a chi non e' iscritto rimbalza sulla home
  senza errore).
- **Una campagna che pretende una pagina intestata a se' e' fuori dal piano.** Campo
  `pagina` di `assegnazioni.json`: `libera`, `tema` (il nostro modello), `marca`,
  `esclusiva`, `non_detta`, `da_verificare`. `marca` ed `esclusiva` non entrano in nessuna
  somma. «Pagina dedicata crypto» e' `tema`; «pagina dedicata a Globex» e' `marca`.
- **Content Rewards copre 345 provider.** Il radar li vede tutti; per inviare serve
  l'iscrizione al whop di quel provider, un passo per campagna scelta.
- **I materiali chiusi si leggono dalla scheda dopo l'iscrizione**
  (`orchestrator.py iscrivi`, poi `whop_agent.py materiali`; archivio
  `radar/materiali_noti.json`, che il radar scrive). Scartati
  l'elenco `discover` da loggati, che li porta per tutti (dato nascosto apposta, letto con
  l'account che incassa), e l'iscrizione in massa (raffica da bot). L'iscrizione si
  propone solo se la campagna paga su TikTok, e la approva l'utente con «scegli».
- **Il catalogo si legge dall'API che la pagina usa**: `catalogo.py` pagina
  `/api/campaign/campaigns/discover`, `dettagli.py` ne prende le schede intere. **Una
  fonte si cambia dopo il confronto con la vecchia su tutto il catalogo**, non su un
  campione: l'API scriveva i materiali senza il prefisso del bucket, e 29 campagne si
  sarebbero ridecise. **Un campo che cambia forma non da' errore, svuota chi ne deriva**:
  `giornaliero.sorveglia()` avvisa in cima al pannello se una quota cala di 25 punti, e il
  riferimento non si lascia trascinare giu' dal guasto.
- **Il sorgente si risolve a secco prima di scegliere** (`radar/sorgenti.py`, specifica in
  [docs/risolutore-sorgenti.md](docs/risolutore-sorgenti.md)). L'export txt di un Google
  Doc butta gli indirizzi, quindi il risolutore rilegge in html (`briefs.py` resta in txt).
  **Un possibile esempio non si preseleziona mai**, e le parole che lo segnalano sono
  larghe apposta: un esempio preso per sorgente fa lavorare un'ora sul video sbagliato.
  **Una quota sotto il bersaglio si legge categoria per categoria prima di scriverla**: al
  primo giro meta' dei «non letto» erano difetti del risolutore.
- **Nessuno paga per clippare video gia' montati** (zero campagne su 606): il valore sta nel
  materiale grezzo e lungo. **E si clippa solo il materiale che una campagna fornisce.**
- **Nessuna nicchia si apre prima di aver misurato quanto costa lavorarla** (il gaming ha
  VOD da ore): un account messo a scaldare per una nicchia troppo cara e' una settimana
  buttata.
- **La nicchia la decide la lettura, e l'`impronta` dice quando rileggere**: titolo,
  organizzazione, descrizione, piattaforme, materiali e brief, **mai i soldi**, che
  cambiano ogni giorno. `pagina` e `pagina_nota` non si toccano da `classifica.py`:
  vengono dal brief intero.
- **«Non ancora letta» e' uno stato a se'**: `si`, `lingua`, `no` e `?`. Le `?` escono in
  fondo al report e in cima al pannello, perche' nessuna somma le comprende, e **non si
  nascondono mai**: una campagna dietro «mostra tutte» non riceve il giudizio.
- **Il report delle nicchie lo riscrive `dettagli.py`** appena le schede cambiano, e
  report e pannello passano dallo stesso `tassonomia.quadro()`: due somme scritte due
  volte divergono in silenzio.

### Account e identita'

- **Tre account, uno per nicchia.** Il numero viene dalle identita' di pagina, non dalle
  campagne: la stessa pagina serve tutte le campagne della sua nicchia.
- **Un gruppo = YouTube, TikTok e Instagram, piu' un Whop** (Whop collega un solo account
  per piattaforma). Lo stato di pubblicazione e' **per coppia (clip, piattaforma)**,
  chiave `01@tiktok`, e il lavoro si conta in pubblicazioni, non in clip.
- **Un account Google nuovo non si crea piu', e non serve**: un account Google gestisce
  fino a 100 canali YouTube come Brand Account. Instagram, TikTok e Whop vogliono solo una
  mail (alias col `+`, Outlook, Proton). Gli account nuovi si creano dal telefono.
- **Da quale account si pubblica lo dice il registro** (`profilo_browser` in
  `dashboard/identita.json`, `publish/registro.py`), **e prima di caricare si confronta
  l'account collegato con quello atteso** (`registro.controlla_handle`,
  `registro.controlla_canale`): se non coincidono, o non si sa, non si
  pubblica; un gruppo non registrato si ferma invece di ripiegare. Il ripiego silenzioso
  era il difetto: avrebbe fatto uscire una clip business dall'account del laboratorio.
  **La guardia vale su ogni porta**: Instagram e' rimasta scoperta per mesi, quindi quando
  se ne aggiunge una si conta che tutte ce l'abbiano. E con `atteso` vuoto la guardia non
  esce: «non so chi devo essere» non autorizza «non so chi sono». Casi in
  `tests/test_registro.py`.
- **Chi si tagga in didascalia lo dice il brief**: `handles` in `publish.json`, scritto da
  `prepara.py`; senza, `orchestrator.tag()` si ferma. Anche le grafie da sostituire vengono
  da `handles`: se ne sostituisce una sola, e se il tag giusto c'e' gia' non si tocca
  niente. Cablate, quelle stringhe avrebbero tolto il tag a 14 pubblicazioni su 21 di
  Contoso. Per la stessa ragione `whop_app` e la cartella di `pubblica` non hanno un
  default.
- **Il warm-up di un account nuovo e' astenersi** (`ritmo.RAMPA`: sette giorni di sola
  presenza, poi 1, 2, 3 post al giorno). Non si simula attivita' umana: l'unico account
  strozzato, `@canale_lab`, lo e' stato per attivita' a raffica. Seguire qualche
  account della nicchia si', perche' dice di cosa parla il profilo. Il confine e' la
  raffica, non il gesto.
- **Il ritmo di regime e' due clip al giorno per account. Non alzarlo.** I canali clipper
  piu' grandi stanno fra 1,6 e 2,2 al giorno (mediana 5,5 al mese, CreatorDB), e lo spam
  comincia a 3-5. Il volume viene dal numero di account; ne discende una sorgente a
  settimana per gruppo.
- **Le impostazioni dentro le app le fa una persona**, lista in
  [publish/README.md](publish/README.md). Criterio: gratis e vere. Quindi il «Paese di
  residenza» di YouTube resta vero (regola monetizzazione e moduli fiscali, non il
  pubblico); la lingua inglese si dichiara, perche' il materiale e' inglese.
- **Il browser dichiara lingua inglese e fuso italiano.** `registro.ambiente()` e' l'unica
  fonte di `locale` e `timezone_id` per tutti i lanci. Un fuso americano con un IP italiano
  e' l'euristica con cui si rilevano i proxy; il fuso che sposta le views sta negli orari
  del calendario. Niente proxy e niente VPN: la regione dell'account l'ha fissata la
  registrazione, e cambiare IP a una sessione aperta e' il segnale del furto d'account.
  **La domanda sulla geolocalizzazione si misura** (`dashboard/pubblico.py`), e «la
  piattaforma non lo dice ancora» e «il selettore non aggancia» danno errori diversi.

### Pubblicare

- **La pubblicazione va verso i canali ufficiali; il browser automatizzato e' un ponte**
  (fragile, leggibile come bot, contro i termini).
  - **TikTok: un'app nostra non pubblichera' mai in pubblico.** Il Direct Post non
    auditato e' `SELF_ONLY`; l'Upload manda una bozza senza didascalia, e le Content
    Sharing Guidelines escludono proprio «uno strumento per gli account che gestisci tu».
    Chi ha passato l'audit si': si pubblica tramite Buffer (`publish/buffer_agent.py`).
    **Una strada si dichiara praticabile dopo aver letto le regole su chi puo' usarla**,
    non solo come si chiama l'endpoint.
  - **Instagram: API ufficiale con Instagram Login** (`publish/instagram_api.py`):
    «Standard Access» basta per gli account che gestisci. Il video non si carica, Meta lo
    scarica dal tunnel di `ospita.py`. **Il contenitore e' la ricevuta**: si pubblica una
    volta sola, quindi dopo un giro finito male si chiede a lui invece di ricominciare.
    Due unita' non le scrive la documentazione e le ha dette la prima risposta vera:
    tempi in millisecondi, skip rate fra 0 e 100. Un valore che nella sua unita' non sta
    in piedi resta vuoto, col grezzo conservato.
  - **YouTube Data API: privata fino all'audit**, che va chiesto e richiede settimane.
  - La strada la sceglie `pubblica_via` nel registro, **per piattaforma**
    (`registro.VIE`), e ogni record porta `via`: un confronto fra strade fatto su
    un'etichetta falsa non vale niente.
- **Una piattaforma intera si ferma dal registro**: `pubblicazione_sospesa`
  sull'identita', col motivo (`registro.sospesa`), che vale alla porta
  (`orchestrator.cmd_pubblica`). Nel pannello e' «sospesa», non «in ritardo»: una
  decisione non deve sembrare un guasto. `sospesa` e `a_mano` di `publish.json` restano
  per la singola clip.
- **Scegliere non e' pubblicare, e preparare non e' armare.** Nessun pulsante del pannello
  manda niente fuori: «Prepara la pubblicazione» scrive didascalie e calendario, dati
  inerti che si rifanno. Pubblica `publish/esegui.py`, da un'attivita' pianificata.
- **Il calendario dice quando, il ritmo dice se.** `esegui.py` pubblica cio' che e'
  scaduto («cosa e' scaduto», non «che ora e'») e non passa mai `--forza`; se i due non
  concordano vince il ritmo. L'unita' e' la clip, quindi un giorno regge il **minimo**
  fra le tre piattaforme.
- **Gli orari si scelgono sul fuso di chi guarda** (le nicchie sono americane), e **il
  primo della lista e' il migliore, non il piu' presto**: in rampa la quota e' 1 al giorno
  e si usa solo `orari[0]` di `batch_times`. Una clip non esce prima del passaggio
  dell'esecutore successivo al suo `quando`: sul server passa ogni 15 minuti
  (`viralclips-pubblica.timer`), sul portatile solo alle ore di `$orePubblica` in
  `pianifica.ps1`, che vanno spostate insieme agli orari.
- **Le tre piattaforme di una clip escono insieme, le clip no**: il parallelo e' fra
  account diversi; due clip sulla stessa piattaforma a zero secondi sono la raffica. Whop
  resta in fila: un profilo browser per gruppo, e due Chromium sullo stesso
  `user_data_dir` non convivono.
- **Ogni scrittura di `publish_state.json` rilegge dal disco sotto serratura e cambia il
  solo record proprio** (`serratura.py`, `orchestrator.scrivi_stato`): chi risalva la
  copia in memoria cancella le altre pubblicazioni, e il giro dopo ripubblica.
- **Su quante piattaforme esce una clip lo dice il calendario**: `calendario_piattaforme`,
  poi `platforms`, poi il gruppo, per ultimo il vecchio `platform`, piu' quelle gia'
  pubblicate. Leggendo `platform` lo stato dichiarava 7 pubblicazioni su 21.
  `prepara.togli_campi_estranei` toglie dal `publish.json` nuovo i campi che l'agente
  ricopia dal modello.
- **Una cartella che dichiara il suo gruppo appartiene a quello e a nessun altro.**
  `publish.json.gruppo` dice da quali account e' uscita *questa* consegna,
  `dashboard/scelte.json` quale gruppo lavora *quella campagna*; quindi «lavorata» si
  chiede per gruppo.

### Whop

- **Content Rewards non ha un'API per gli invii** (le Bounties sono un altro prodotto):
  l'invio va via browser, e un invio fallito **non ferma la pubblicazione**. Finisce nella
  coda «da inviare a mano» del pannello, con notifica di Windows (`publish/notifica.py`),
  perche' la finestra d'invio e' di trenta minuti. «Segna inviato» prende nota di un
  gesto fatto su Whop: passa da `orchestrator.scrivi_stato` e rifiuta lo schema senza
  `items`.
- **Al pannello si entra dalla radice** (`/<slug>/<exp>/app/`, `radice_app()`): il collegamento profondo
  rimbalza sulla home su alcuni provider. La campagna si aggancia per
  `aria-label="Preview <titolo>"`, e **l'uuid nell'indirizzo della scheda aperta e' la
  verifica: senza, non si invia**.
- **Prima di pubblicare si chiede se gli invii sono aperti** (`orchestrator.py invii`,
  una volta per cartella; `whop_agent.descrivi_invii()` su
  `/api/submission/submissions/intake`). `invii in pausa` (motivo `hold`) tiene ferme le
  clip, che escono da sole alla riapertura; `invii chiusi` pure. **Solo `intake: open` e'
  aperto**, un valore mai visto no, e un esito illeggibile ferma e tinge di rosso: una
  clip ferma per niente esce al giro dopo, una uscita a campagna chiusa e' persa.
  Controllo e invio usano lo stesso profilo e la stessa serratura
  (`orchestrator.comando_whop`).
- **Un invio si giudica quando e' finito**: dopo la conferma il pulsante resta su
  «Submitting…», e Whop elenca un post solo dopo averlo verificato. Da qui
  `aspetta_conferma()`, quattro letture ad attese crescenti in `risulta_inviato()`, e tre
  esiti: `inviato`, `non registrato` (il post non c'e') e `non verificato` (non ho potuto
  guardare). Senza la riga `esito:` e' `non confermato`, perche' `whop_agent` esce 0 anche
  dalle uscite anticipate. `orchestrator.leggi_esito_whop()` ritaglia l'esito intero.
- **Una campagna finita lo e' per i fatti, non per la nostra chiusura**
  (`tassonomia.campagna_finita`): fuori dall'elenco `discover`, stato non attivo, o
  residuo che non paga il minimo di una clip (Contoso restava `active` con 1,01 $ contro un
  minimo di 2,30). La leggono il pannello, che toglie le clip dalla coda, ed `esegui.py`,
  che salta la cartella prima di chiedere a Whop. Il radar non insegue le campagne
  sparite. Un residuo che non si legge non chiude niente.
- **Un invio Pending sotto soglia non e' un guasto**: Whop paga quando le views toccano il
  break-even. `stato.pagamento()` dice `sotto soglia`, `sopra soglia`, `soglia ignota`,
  `views non lette` («non si sa» non e' «sotto soglia»), e a campagna finita «non
  paghera'». Per il singolo invio e' dedotto; i contatori veri, sommati, li legge
  `metriche.py --whop`.
- **Gli incassi si leggono dal pannello Whop** (`whop_agent.py guadagni`), **e un importo
  senza unita' non si indovina**: un campo `...Cents` e' in centesimi e si preferisce il
  netto, zero e' zero in ogni unita', uno storno non e' un incasso, e un importo diverso da
  zero senza unita' resta `null`. Il totale e' `null` se un conto non si e' letto, e il
  cancello dice «non si sa».

### Il browser sulle piattaforme

- **Il compositore di Instagram parte su 1:1 e va portato a 9:16 prima di «Avanti»**:
  il ritaglio quadrato butta meta' clip senza errore. Le voci del menu sono `div` con
  icona: si clicca il nodo di testo. Se `scegli_verticale()` non lo trova, si ferma.
- **Un popup su Instagram si chiude con un gesto che non annulla niente**: un pulsante
  coperto fa aspettare Playwright trenta secondi senza errore. `chiudi_popup()` clicca
  sempre le rinunce («Not Now», «Non ora», cookie facoltativi) e la X solo prima del
  compositore, che e' a sua volta un dialogo.
- **Su TikTok il pulsante e' `data-e2e="post_video_button"`**: cercato per testo prendeva
  la voce «Posts» della barra laterale (`has-text` e' sottostringa). E «Content check
  lite» va spento: tiene il video in verifica e non lo fa uscire.
- **Il venv condiviso dello scraping si trova**: `Path.home() / ".venvs" / "scraping"`,
  dove lo mette `setup.ps1`. Un percorso cablato fallisce come WinError 2 dentro un
  subprocess, senza dire quale file manchi.

### Leggere i numeri

- **Le views si leggono dalla pagina del proprietario, un account alla volta**
  (`publish/leggi_account.py`): `__Creator_Center_Context__` di TikTok Studio, il graphql
  della griglia dei reel, `list_creator_videos` di YouTube Studio. Il profilo pubblico di
  TikTok chiede un captcha e non e' una fonte. **Uno zero a un giorno dall'uscita e' un
  segnale**: i quattro post TikTok business sono stati a zero per cinque giorni e nessuna
  pagina lo diceva. **Un valore che non si sa interpretare si conserva grezzo e non
  diventa un avviso**: un avviso inventato insegna a ignorarli.
- **Chi legge apre i profili di chi pubblica**, quindi prende la stessa serratura di
  profilo (`serratura.nome_profilo`) e si ritira davanti a una pubblicazione.
- **Quando un riquadro di account dice «dati non sufficienti», si guarda un livello
  sotto**: le analitiche del singolo post (`dashboard/analitiche.py`,
  `publish/leggi_analitiche.py`) avevano gia' fonti e paesi. Quote fra 0 e 1, tempi in
  secondi, e **un campo che la piattaforma non da' resta vuoto, mai zero**. Instagram sul
  web non ha analitiche per post: servono l'API. Il riquadro «Verifica la tua identita'»
  di YouTube Studio lo tocca una persona.
- **Un vuoto non prova niente finche' non si e' guardato nel posto giusto.** Id o handle
  nell'URL finale e nella pagina, account nell'intestazione; con una cattura vuota, prima
  lo screenshot salvato (`instagram.com/reel/<code>/` apre il feed, `/p/<code>/` il post).
  **La copertura e' una misura**: un lettore con meno elementi dell'inventario noto
  (`publish_state` per account) fallisce in silenzio; si legge per id, mai con valori
  troncati per la stampa. **Serie che cambiano «insieme»** si allineano ora per ora sullo
  stesso orologio e sul totale cumulato prima di dare una causa comune, e «non girava
  niente di nostro» si prova col registro della macchina. **Prima di dare la colpa
  all'account, la base dei pari**: le clip degli altri sulla stessa campagna
  (`yt-dlp "ytsearch40:<episodio>"`). Un account per comando in background, e dopo un
  lettore ucciso si guardano i lock di pid morti in `publish/.serrature`.

### Il pannello

- **L'aggiornamento lo fa il pulsante, non l'orologio**, e i pulsanti sono due perche' le
  domande sono due: «Aggiorna radar» (`server.PASSI_RADAR`: catalogo e sorgenti) per
  scegliere cosa lavorare, «Aggiorna social» (`server.PASSI_SOCIAL`: views, analitiche,
  Whop) per vedere come va. Un giro alla volta. **La prima riga del pannello dice quando
  ha girato il radar**: e' l'unico posto dove si legge se i dati valgono ancora.
- **`aggiorna.ps1` e i due pulsanti fanno la stessa lista** (`server.PASSI`, ordine
  fissato da `test_analitiche`): radar per primo, sorgenti subito dopo, perche' leggono
  le schede appena riscritte, poi metriche e analitiche. Il browser si apre alla fine.
- **Il pannello e' la cabina di regia della roadmap e la legge dal file**: sei sezioni
  (Oggi, Campagne, Produzione, Pubblicazione, Numeri, Chiedi); fasi, scadenze e cancello
  vengono da `docs/roadmap.md` senza copia. «Da fare» contiene solo cio' che si risolve
  con un gesto. Dettagli in [dashboard/README.md](dashboard/README.md).
- **Le campagne si elencano in un posto solo**, dentro la loro nicchia. «Da prendere
  adesso» e' un filtro del catalogo e scrive il filtro in testa. **Fondere una vista non
  e' cancellarla**: prima si sposta cio' che portava solo lei (da «Radar» e' stato
  salvato `iscritto`, senza il quale la pagina elencherebbe lavoro non consegnabile).
- **Ogni nicchia, anche senza account, ha le sue campagne e «scegli»**: decidere cosa
  lavorare viene prima di aprire l'account. La scelta porta la `nicchia` e nessun
  `gruppo`; `calendario.gruppo_di` lo prende dal registro solo se e' uno. **Il gruppo di
  una nicchia e' il suo `id`, non la chiave** (su «intrattenimento» si chiama `podcast`).
- **Dove «scegli» non c'e', la casella dice perche'** (`stato.riserva_scegli`, stessa
  catena di condizioni di `proponibile`, scritta dal server): una regola applicata
  togliendo un comando senza dirlo e' indistinguibile da un guasto. Ogni riserva ha la sua
  etichetta: «aspetta un giudizio» e «giudicata e scartata» non si confondono. Niente
  pulsante dietro una conferma: un divieto cliccabile non e' un divieto.
- **Il pannello conta solo il lavoro in inglese** (`lingue_in_plancia` in
  `dashboard/identita.json`). La lingua si legge da `work/<cartella>/transcript.json`,
  non dallo slug ne' da `meta.json`. Un account ritirato e' `attiva: false` e resta nel
  registro. Si nasconde solo cio' che si e' dimostrato.
- **Il collegamento sul desktop apre, non aggiorna**: `pythonw.exe dashboard\apri.py`
  (con `pythonw`, non `powershell` nascosto, che compare e sparisce). Se il pannello non
  risponde lo dice con una finestrella e indica `vigila-errori.log`.
- **Il pannello si rialza da solo con `dashboard/vigila.py`**: il codice Python vive nel
  processo, e l'errore «azione sconosciuta» non dice «riavvia». Il sorvegliante non
  riavvia mentre un lavoro gira e aspetta un `200` vero prima di dirsi riuscito.
- **Un file si manda a blocchi e col `Range`, mai con `read_bytes()`**: le clip arrivano
  a 70 MB e un server a thread ne terrebbe una copia per scheda. Ogni trascinamento della
  barra abbandona la risposta, quindi `BrokenPipeError` e parenti si zittiscono dove si
  scrive: il gestore generico risponderebbe 500 dentro una risposta gia' partita.
- **Un guasto si legge in pagina**: `_esegui()` cattura venti righe di stderr (la riga
  che spiega sta sopra le ultime tre) e la pagina le mostra sopra le sezioni.
- **«Chiedi» e' la stessa cosa dei pulsanti con la domanda scritta dall'utente**: `claude
  -p`, filo tenuto con `--session-id` e `--resume` scritti su disco.

### Chiamare Claude dagli script

- **Modello ed effort stanno nel codice**: costanti `MODELLO` ed `EFFORT` in ogni
  chiamante, con `--model` ed `--effort` sempre passati. Senza, `claude -p` eredita
  `settings.json`, cioe' le preferenze della chat, e il flag vince su `CLAUDE_EFFORT` che
  una sessione passa ai figli. **Il modello si scrive con l'id pieno**: l'alias lo
  risolve il CLI, e un `claude update` ha cambiato modello a quattro chiamanti senza una
  riga di codice. Lo fissa `tests/test_modelli.py`.
- **L'effort si sceglie per chiamante e si misura**: piu' effort non e' un margine di
  sicurezza ma un'altra variante (su Opus 5.5 `xhigh` scartava piu' campagne lavorabili
  di `medium`, per prudenza).
- **Un modello si promuove misurando l'errore che non da' errore**: Sonnet 5 rispettava
  tutte le regole meccaniche e travisava l'ospite in 3 didascalie su 14. Regole del
  banco:
  - **alla cieca**: un agente con gli strumenti va a cercare la risposta, quindi la prova
    gira su una copia del progetto senza la consegna vera, senza i commenti sopra
    `MODELLO` e senza le misure precedenti;
  - **contro il modello vecchio nelle stesse condizioni**, non contro i giudizi in uso,
    che favoriscono il modello che li ha scritti;
  - **i fatti si controllano sulla trascrizione**: i controlli meccanici avevano promosso
    tutte le varianti.

  Il banco sta in `tools/banco_modelli/` (README), legge gli stessi messaggi della
  produzione (`automatico.messaggio_decisioni`, `prepara.messaggio_consegna`) e si ferma
  da solo se la copia contiene ancora la risposta.
- **Il contesto fisso si toglie solo dopo averlo misurato.** `--bare` toglierebbe tutto ma
  non legge il login dell'abbonamento. Le nicchie girano snelle
  (`classifica.SNELLO`: fuori dal progetto e senza strumenti, 10mila token invece di
  107mila, giudizi pari). Gli agenti con strumenti no: il contesto tolto pesava meno della
  varianza fra due corse, e a loro CLAUDE.md serve.

## Ambiente

- **Nessuna GPU CUDA**, su nessuna macchina usata finora: tutto su CPU. Esclude torch,
  mediapipe e i modelli pesanti per fattibilita', non per gusto.
- **Il Python di sistema e' troppo recente** per le wheel di ctranslate2 e opencv: si usa
  un venv con **Python 3.12**, creato da `setup.ps1`.

```
Interprete:  .venv\Scripts\python.exe
Script:      .claude\skills\viral-clips\scripts\
```

- **I percorsi si scrivono relativi alla radice del progetto**, anche quelli che scrive il
  codice: le cartelle di lavoro viaggiano fra macchine. Un file dentro la cartella di
  lavoro si scrive col solo nome (`ingest.in_work()`) e si rilegge dalla cartella che si
  sta leggendo (`common.work_file()`); resta assoluto il solo sorgente locale adottato
  senza copiarlo. `work_dir` dice dove la cartella *era*.
- **Su un'altra macchina si porta con `export_skill.py`, non con `git`**: il giudizio
  editoriale sta in cartelle ignorate (`decisions.json`, `corrections.json`, i
  `clips.md`), ed e' l'unica cosa che nessuno script rigenera. Il `.venv` non si copia
  mai: lo rifa' `setup.ps1`.

## Trappole gia' pagate — non ripercorrerle

### Scaricare: YouTube, TikTok, Drive

- **Su qualunque problema YouTube, prima di ogni diagnosi:**
  `pip install --upgrade --pre yt-dlp`. La stabile vedeva una sola traccia audio su sette doppiaggi («Requested
  format is not available»), e la prerelease ha curato anche «Sign in to confirm you're
  not a bot». Non erano ne' PO Token ne' cookie.
- **yt-dlp vuole ffmpeg sul PATH del processo**, `ffmpeg_location` non basta
  (`ingest.ensure_ffmpeg_on_path()`).
- **Di un video YouTube si scarica tutto e si taglia in locale**: con
  `--download-sections` ffmpeg prende l'URL diretto e si becca lo strozzamento (85 MB
  interi in 23 s, dieci secondi di sezione in oltre 170).
- **Un `HTTP Error 500` sempre allo stesso byte si cura scaricando a blocchi**
  (`http_chunk_size` in `ingest.fetch()`), non con i retry.
- **Sui video doppiati gli id dei formati hanno un suffisso** (`251-4`) e cambiano fra
  estrazioni: si seleziona per lingua (`ba[language^=en]`) e si verifica che esista prima
  di scaricare. Il codice `ia` indica di norma la traccia originale, non l'interlingua.
- **TikTok: la rotta `embed` scarica senza login** (`tiktok.com/embed/v2/<id>`); il file
  esce `.unknown_video` ed e' un mp4.
- **I cookie di Chrome ed Edge su Windows sono inutilizzabili** (App-Bound Encryption):
  non far chiudere il browser all'utente, non serve.
- **Un file di Google Drive lo scarica yt-dlp** (nome con estensione doppia,
  `nome.mp4.mp4`); **una cartella no**, e l'errore (`TypeError ... got 'bool'`) non nomina
  Drive. L'elenco si legge da `drive.google.com/embeddedfolderview?id=<id>#list`, HTML
  piatto: `ingest.rifiuta_cartella_drive()` si ferma prima della libreria ed elenca i file
  con l'indirizzo pronto. Quale episodio lavorare e' una scelta editoriale.
- **Il master di Drive non dichiara ne' risoluzione ne' codec, e il silenzio non e' una
  dichiarazione**: letto come valore ha tolto il proxy (analisi sul 4K che non finiva) e
  scritto `orientation: verticale` su un 16:9. Solo `"none"` dichiara una traccia assente
  (`traccia_assente()`), e `ffprobe` su un indirizzo http legge l'intestazione in pochi
  secondi (`misure_dal_flusso()`).
- **git-lfs su GitHub**: si scarica da `github.com/.../raw/...`, non da
  `raw.githubusercontent.com`, che da' il puntatore (vale per YuNet).
- Il fornitore di PO Token compilato sulla prima macchina non e' mai servito e non si
  esporta: se un video lo chiede davvero, si ricompila.

### Ambiente e installazione

- **Un `.ps1` senza BOM non puo' avere caratteri fuori ASCII dentro una stringa**:
  PowerShell 5.1 legge cp1252, un trattino lungo diventa una virgoletta che chiude la
  stringa, e l'errore parla di una parentesi cinquanta righe sotto. Si controlla senza
  eseguire: `[System.Management.Automation.Language.Parser]::ParseFile(<file>, [ref]$null,
  [ref]$e)`.
- **winget vuole `--source winget`**, se no interroga `msstore` e fallisce per ambiguita'.
- **`allow_reuse_address` su Windows lascia due processi sulla stessa porta viva**, e le
  richieste vanno a uno dei due a caso. `server.Server` lo mette a `False` solo su Windows:
  su Linux la porta resterebbe in TIME_WAIT a ogni riavvio.
- **Uscire con 0 vuol dire «ho finito», e chi sorveglia lo legge «rialzami»**: `server.py`
  sulla porta occupata esce con `PORTA_OCCUPATA`, `vigila.py` si ritira se qualcuno serve
  gia', e la pausa raddoppia quando il figlio muore prima di `VITA_BREVE`.
- **Con `$ErrorActionPreference = "Stop"` PowerShell trasforma ogni riga di stderr di un
  programma esterno in un errore**, e qui la diagnostica va su stderr per contratto: nei
  wrapper si torna a `Continue` e si guarda il codice di uscita. Il testo di un
  `ErrorRecord` sta in `$_.Exception.Message`.
- **ffmpeg installato da winget non e' nel PATH dei processi gia' avviati**:
  `common.tool()` legge il PATH dal registro. Mai `"ffmpeg"` nudo in un subprocess.
- **`scenedetect-core` e' ritirato da PyPI**: si usa `scenedetect`.
- **libass non gestisce i font variabili**: serve un font statico (Archivo Black).

### Trascrizione e testo

- **`large-v3-turbo` per italiano e inglese, ed e' il default.** `small` sull'italiano ha
  perso due frasi intere, fra cui la tesi del video; sull'inglese perdeva quattro volte
  il parlato di turbo senza essere piu' veloce. **Il criterio e' la completezza**: un
  refuso si corregge a valle, una parola assente non ha un istante a cui agganciarla. Una
  regola che dipende dalla lingua e un default che non la guarda sono due verita', e vince
  il default.
- **Il filtro VAD non si regola alla cieca e non si toglie**: nessuna regolazione migliora
  le due lingue insieme, e senza filtro Whisper scrive «Grazie a tutti.» sul silenzio.
- **Il rilevamento automatico della lingua e' pericoloso**: su un video inglese ha
  risposto gallese e ha decodificato tutto in gallese inventato. La difesa che salva e'
  la **restrizione a it/en**, non il voto su piu' punti (il gallese vinceva ovunque).
- **Su un audio bilingue `--language` non trascrive, traduce**, e il risultato sembra
  giusto. Non si corregge con `corrections.json`: si ritrascrive la finestra nella lingua
  giusta e se ne sostituiscono i segmenti. Il sintomo e' una frase che non vuol dire
  niente dentro un testo scorrevole.
- **`transcript.json` tiene le parole in due copie**: `words` (letta da `make_subs.py`,
  scritta da `apply_corrections.py`) e `segments[i].words` (letta da `windows.py`). Dopo un
  intervento a mano si riallineano **per posizione**, non per tempo.
- **Whisper puo' emettere surrogati spaiati**, che fanno morire `windows.py` su console
  cp1252: si tolgono in `transcribe.py`, e l'uscita e' forzata a UTF-8 in `common.py`.

### Render, ffmpeg e colore

- **Mai percorsi assoluti Windows dentro un argomento di filtro ffmpeg** (i due punti
  separano le opzioni). `render_clip.stage_assets()` copia `.ass`, font e `sendcmd` in
  `work/<slug>/` ed esegue ffmpeg da li' con nomi relativi.
- **L'accelerazione hardware peggiora le cose**: su 60 s di 4K AV1 verso 480p, software
  12,8 s, vulkan 19,2, dxva2 33,8, d3d11va 43,7. I frame tornano in memoria di sistema per
  i filtri CPU, e a 4K il trasferimento costa piu' del risparmio. Non riprovare
  `-hwaccel`.
- **I flag di colorspace non sono opzionali**: VP9 e AV1 di YouTube perdono BT.709 nel
  filtergraph e la clip esce slavata. **Ma un sorgente HDR** (`smpte2084`,
  `arib-std-b67`) vuole tone mapping
  (`zscale=t=linear:npl=100,tonemap=hable,zscale=t=bt709:m=bt709:r=tv`) prima del tag.
- **`avg_frame_rate` mente** (WebM con `30000/1`): `common.video_info()` ripiega su
  `r_frame_rate`.

### Inquadratura e materiale di partenza

- **I sottotitoli gia' impressi nel sorgente si cercano su un fotogramma dentro il
  parlato**, scelto dal transcript: a caso si cade negli stacchi. Cosa farne lo dice la
  geometria: su un sorgente **verticale** bastano i suoi, e si spegne il karaoke passando
  a `--subs` un `.ass` da `build_ass([], start, end, hook=...)` (non `--no-subs`, che
  toglie anche l'hook); su un **16:9** il ritaglio li tronca, quindi si tengono i nostri.
- **Su un sorgente verticale il movimento di camera e' solo perdita**: se la finestra e'
  larga quanto il fotogramma niente zoom, se lo e' anche in altezza niente crop dinamico.

### Diagnosi: cosa non fidarsi di guardare

- **I casi limite di questa pipeline non crashano: tacciono**, e una consegna sbagliata
  sembra riuscita. L'anomalia va **in cima alla vista di `windows.py`**: un avviso in un
  log che scorre non arriva in tempo per cambiare una decisione.
- **Il buco fra `start` e la prima parola non e' silenzio**: quasi sempre e' musica,
  folla o lo stacco che prepara la battuta. Quindi niente controllo automatico sul
  silenzio iniziale (sbaglierebbe venticinque volte su ventisei) e niente taglio sul primo
  fonema (i timestamp sbagliano di decine di millisecondi e la prima parola esce monca).
- **I tagli dei silenzi dentro la clip** (`tagli.py`, attivi di default, `"tagli": false`
  per spegnerli) tolgono solo cio' che e' **senza parole e quieto** (95° percentile almeno
  18 dB sotto il parlato), mai i bordi ne' sotto il minimo di durata, lasciando 0,22 s
  dopo la parola e 0,12 s prima della successiva. **Un taglio non cade entro un secondo
  da uno stacco ne' lascia uno spezzone sotto 1,5 s**, e **uno scatto di zoom sta ad
  almeno 0,6 s dall'inizio della sua inquadratura**: tre eventi in sette decimi sono un
  inciampo. Gli stacchi si portano sul fotogramma esatto (`signals.refine_cuts`).
- **L'orologio da parete misura il mondo, non il lavoro**: un giro «durato quindici ore»
  era un portatile in ibernazione. Su Windows anche `time.monotonic()` conta la
  sospensione; serve `QueryUnbiasedInterruptTime` (`giornaliero.tempo_sveglio()`). I tempi
  si registrano in due, e un tetto sull'orologio da parete ammazzerebbe un giro sano.
- **Su un portatile un'attivita' pianificata e' un'intenzione, non una promessa**:
  `StartWhenAvailable` recupera la corsa, non l'orario. Per l'orario serve `WakeToRun`.
- **Un `.pyc` vecchio sopravvive a una modifica della stessa lunghezza nello stesso
  secondo** (Python guarda dimensione e mtime): il codice che si legge non e' quello che
  gira. Chi modifica sorgenti da un programma usa `python -B`, e nel dubbio si cancellano
  le `__pycache__`.
- **Un selettore che non trova si guarda nell'HTML salvato da `dump()`**, non si cura con
  i tentativi: trenta secondi contro sei minuti di pubblicazioni fallite.
- **Un elenco completo non e' una consegna completa, e un mp4 che si apre non e'
  intero.** Sette mp4 da zero byte hanno passato `validate.py --consegna`: ora il cancello
  guarda i file (`validate.difetto_clip`), e `calendario.calcola` lo ripete. Con
  `+faststart` ffprobe da' la durata piena a un file troncato; lo tradisce solo il
  rapporto fra i byte dell'indice e il peso vero (`stream_bytes` di `common.video_info`).
- **Un render si giudica su un fotogramma a risoluzione piena**: un contact sheet dice
  dove guardare, non se qualcosa e' rotto.
- **Il primo verdetto negativo di `check_sync.py` si guarda, non si incassa.** Due cause
  di falso allarme: il testo impresso nel sorgente (stessa firma, bianco con contorno), e
  gli eventi sotto tre fotogrammi, che `check_sync.campionabili` scarta e conta. Se non
  resta niente da campionare il verdetto e' «non verificabile», non «sfasato».
- **`cv2.imread` non apre i percorsi con lettere accentate su Windows e torna `None`**:
  si legge con `cv2.imdecode(np.fromfile(...))` (`check_sync.read_image`), per ogni
  `imread` nuovo.
- **Lo z-score sull'energia esplode con audio piatto**: pavimento a 0,5 dB in
  `signals.energy_peaks()`.

### Impacchettare e portare altrove

- **Un filtro di esclusione per percorso lascia passare il doppione fuori posto**: i pesi
  Whisper avevano una seconda copia in `models--<org>--<nome>`, e uno zip «senza modelli»
  pesava 3,1 GB. Si esclude per forma (`models--*`).
- **In `publish/` le cartelle che cominciano col punto sono sessioni gia' autenticate**
  (profili Chrome di YouTube, TikTok, Whop): sono credenziali. `export_skill.py` salta
  ogni cartella col punto, e le stesse sono in `.gitignore`.

## Regole di render (requisito di qualita')

- **Si scarica la risoluzione massima, non 1080p**: da 1080p il crop 9:16 e' un upscale
  di 1.78x, da 1440p 1.33x, da 2160p un downscale. Selettore `bv*[height>=1440]+ba/bv*+ba/b`,
  merge in mkv, flag `upscale_required` nei metadati.
- **Un solo passaggio ffmpeg per clip**: taglio, crop dinamico, scale, ASS, grafiche e
  loudnorm nello stesso filtergraph. Anche i tagli dei silenzi: un `trim`/`atrim` per
  tratto e un `concat`, bordi sulla griglia dei fotogrammi e 15 ms di dissolvenza audio.
- `-ss` **prima** di `-i`, `-t` dopo. `-crf 17 -preset slow -profile:v high`, scale con
  `flags=lanczos`.
- **`medium` non conviene**: rende il render solo il 21% piu' veloce (sotto c'e' un
  pavimento di decodifica e filtri) e costa il 4,4% di file in piu' su ogni clip caricata
  tre volte; la qualita' e' indistinguibile. Il preset veloce, `veryfast`, sta gia' sulle
  anteprime.
- **L'audio si prende dalla traccia originale, mai da `audio.wav`** (16k mono, serve solo
  a Whisper).
- **Niente `h264_amf` per il render finale**: perde contro x264 CRF 17. Solo anteprime.
- `loudnorm` a due passate: la prima analizza soltanto, quindi non viola la generazione
  singola.

## Architettura

```
------------------- tutto l'esame del video: sul proxy 480p -------------------

    transcribe -> signals -> windows -> [ CLAUDE sceglie ] -> decisions.json -> validate
                                                                    |
                                            faces (denso) -> track_subject
                                                              |
--------------- solo il render: dal sorgente a piena risoluzione --------------
                                                              |
                                                          render -> output/
```

Il diagramma dice **dove gira** ogni cosa; la sequenza concreta sta in SKILL.md.

- **Il proxy di analisi e' un requisito, non un'ottimizzazione**: sul 4K i cambi scena non
  finivano affatto. Lo prepara `ingest.py` una volta (scaricato da un link, transcodificato
  solo dai file locali) e lo usano `signals.py`, `faces.py` e `track_subject.py` via
  `common.analysis_source()`, che da' anche il fattore di riscalamento: senza, i riquadri
  del 480p sbaglierebbero il 4K di un fattore 4.5. Il sorgente lo tocca solo
  `render_clip.py`.
- **`faces.py` denso gira dopo la decisione**, sulle sole clip scelte. Prima c'e' solo una
  prescansione economica (1 fps, 480px) per la linea «una faccia / due facce / nessuna»,
  che serve durante la selezione.

### Contratto tra script e parte editoriale

- **`transcript.json` non si legge mai intero**: e' quasi due ordini di grandezza piu'
  grande della vista testuale e satura il contesto. In ingresso c'e' **la vista a
  segmenti** che `windows.py` stampa su stdout; i timestamp per parola si consultano solo
  sulle candidate (`windows.py --words --around <t>`).
- **In uscita c'e' `decisions.json`** in `work/<slug>/`: bordi, hook, keyword, punteggi,
  grafiche, motivazione. Lo scrive Claude, lo legge il render con `--decisions`. **Non e'
  opzionale**: senza, rirenderizzare domani vuol dire rigiudicare. **Hook e keyword non
  passano mai da riga di comando**: un hook italiano ha quasi sempre un apostrofo.
- **Niente `clips.json` e nessun riordino automatico**: l'ordine di `decisions.json` e'
  quello editoriale.
- **I paletti della campagna viaggiano con la cartella di lavoro**: `run.py prepare
  --campagna <uuid>` copia il brief **per intero** in `work/<slug>/brief.txt` (un
  estratto e' una decisione presa da una regex) e scrive `campagna.json`; `windows.py` ne
  stampa i vincoli in cima alla vista. Si copia invece di linkare, perche' conta il brief
  di allora. Una campagna fuori catalogo entra con `--brief` (file o testo incollato),
  `origine: fuori catalogo` e numeri vuoti; i link che scadono finiscono in
  `da_scaricare_a_mano`.
- **Le regole di campagna sono un secondo livello**, nel blocco `regole` di
  `campagna.json`. **Una campagna puo' stringere una regola base, mai allentarla**
  (`campagna.limiti()`): un brief che accetta clip da dieci secondi non abbassa il minimo
  di venti. Riagganciare la stessa campagna conserva le regole, agganciarne un'altra le
  azzera. Le scrive Claude leggendo e `validate.py` le fa rispettare; i vincoli in prosa
  si ristampano prima del render. **`letto_il` a `null` vuol dire «nessuno ha aperto il
  brief»**, non «nessuna regola».
- Gli script scrivono la diagnostica su **stderr** e il risultato su **stdout** in JSON;
  l'eccezione e' la vista di `windows.py`, testo perche' il suo lettore e' Claude.

## Struttura

```
.claude/skills/viral-clips/
  SKILL.md            # orchestrazione + criteri editoriali
  scripts/            # la pipeline
  references/         # rubrica virale, stili sottotitoli, ricerca esterna
  grafiche/modelli/   # i modelli HyperFrames delle grafiche animate
  assets/             # font e modelli (non versionati)
work/<slug>/          # intermedi
output/<slug>/        # clip finali + clips.md
radar/                # catalogo e giudizio delle campagne
publish/ dashboard/   # pubblicazione e pannello di controllo, ognuno col suo README
tests/                # i numeri decisi, fissati (libreria standard, mezzo secondo)
tools/banco_modelli/  # prova alla cieca di un modello nuovo, prima di cambiarlo
docs/                 # roadmap (modello), ricerca, specifiche
```

```
.venv\Scripts\python.exe -m unittest discover -s tests -t tests
```

**I test fissano le decisioni, non coprono il codice**: le funzioni pure dove un numero o
una condizione cambiati per sbaglio non rompono niente e si pagano settimane dopo —
l'account strozzato, la clip tagliata a meta' parola, la nicchia scelta sulla misura
sbagliata, la clip uscita dall'account di un'altra nicchia. Solo libreria standard, niente
video ne' rete, cosi' girano anche su una macchina appena preparata; `unittest` e non
`pytest` per non avere dipendenze. Una regola nuova di questo file che si puo' fissare ha
il suo test.

**La prova che un test morde e' la mutazione**: si rompe a mano la regola che difende, e
il conto deve andare in rosso.

- Gira su una **copia** dell'albero, mai sui file che un'attivita' pianificata puo'
  eseguire, con `PYTHONPYCACHEPREFIX` su una cartella vuota. Prima si lancia il comando
  esatto sull'albero sano, e si va avanti solo se e' verde.
- Una mutazione e' presa solo se il runner riporta `FAILED` (un ImportError non e' un test
  rosso); la sostituzione e' esatta e su una sola occorrenza, e una mutazione non applicata
  si riporta come tale.
- Sui file veri: ripristino in un `finally`, lettura e scrittura in bytes, test con
  `sys.executable`, copia di sicurezza cancellata solo dopo il confronto byte per byte.
- **Serve a trovare i test che non mordono.** Le cause viste: dati che danno lo stesso
  esito con la regola e senza; un test che **riderivava la regola** invece di chiamare la
  funzione; un test sulla funzione pura e nessuno sul **chiamante**, dove la regola giusta
  era invocata nel posto sbagliato.

## Decise, da non rimettere in discussione

- **Niente torch, mediapipe, ultralytics.** Volti con **YuNet** (`cv2.FaceDetectorYN`,
  227 KB, gia' in opencv).
- **I bordi di una clip si agganciano ai confini di segmento, mai ai secondi.** I tempi
  della vista sono inizi di segmento arrotondati: usati come `end` hanno chiuso **38 clip
  su 38** in mezzo all'ultima parola. Nemmeno la fine di una parola basta, ne' la
  punteggiatura. `validate.py`: bordo dentro una parola = errore, fuori da un confine di
  segmento = avviso.
- **Su un girato che stacca piu' spesso di un montatore (sotto 3,4 s di inquadratura
  media) la camera non insegue**: la varieta' la fa gia' il montaggio, e in mezzo alla
  folla i volti sono passanti. **Ferma dentro ogni inquadratura, non per tutta la clip**:
  un'inquadratura con soggetto sicuro (almeno 0,8 s, soggetto in meta' dei campioni, volto
  alto almeno l'8% del quadro) ha la sua posizione, che cambia solo sullo stacco del
  girato; la posizione e' il centro del tratto percorso, non la mediana. Volto dentro la
  finestra dal 73,5% al 94,5%.
- **Il soggetto si sceglie una volta per inquadratura**: a ogni fotogramma ogni cambio di
  idea diventava un salto. Dopo un cambio scena la posizione precedente non vale niente:
  senza un volto si va al centro.
- **La camera tiene la posa e stacca, non insegue.** Tre parametri in `camera.py`, tutti
  come frazione del ritaglio e mai in pixel; **oltre il 30% di spostamento si stacca**,
  perche' attraversare mezzo fotogramma e' peggio del tremolio.
- **Come si muove si pianifica a clip intera, e il crop cambia a ogni fotogramma**: da
  79-240 micro-salti al minuto a 0-5. Fermo sulla mediana se il soggetto sta nella zona
  morta, altrimenti **smootherstep**; crop scritto a ogni fotogramma d'uscita con
  `exact=1` e altezza **ricavata dalla larghezza**. **Niente spinta lenta lungo la
  clip** (su pixel interi e' un tic): il movimento voluto sono gli scatti d'enfasi sulle
  `keywords` (uno ogni 15 s al massimo) e il cambio di scala a 1,10 fra spezzoni separati
  da un taglio. Lo zoom non supera l'altezza che tiene la testa nel quadro.
- **«Chi parla» si legge dalla varianza dei pixel nella bocca**, normalizzata sul resto
  del volto: i landmark di YuNet sono gli angoli della bocca, danno la larghezza e non
  l'apertura.
- **Niente split screen**: nelle clip che lo usano le due meta' vengono da due riprese, e
  da un sorgente unico non si genera.
- **Grafiche animate: HyperFrames, solo la CLI, modelli fissi, a rete spenta.** Modelli in
  `grafiche/modelli/`, col font dei sottotitoli e il giallo dell'hook; un modello nuovo
  nasce quando il parlato lo chiede. Claude sceglie modello, istante e testo (`grafiche` in
  `decisions.json`); la grafica esce in ProRes 4444 e si posa nello stesso filtergraph,
  sopra i sottotitoli e sotto il logo. Rete spenta: variabili di `grafiche.AMBIENTE`, il
  Chrome di Playwright, font e GSAP nella cartella del render, e un test ferma un
  `https://` in un modello. **Il plugin per Claude Code no**: manda dati a HeyGen. Durante
  la grafica i sottotitoli tacciono; le misure dei testi le fa Python dal TTF; una grafica
  che non si rende si salta.
- **Niente effetti sonori**: zero su tutte le clip osservate, in tutte le nicchie.
- **Niente YAMNet o SenseVoice per le risate**: si rilevano in DSP, e **dove ci sono parole
  sicure non si ride** (la modulazione 3-8 Hz e' anche la frequenza sillabica: 85 risate
  in un monologo). Prima di aggiungere un segnale, si guarda se uno gia' prodotto risponde.
- **La lunghezza dell'hook e' geometria**: 84px su 1080 fanno 19 caratteri per 3 righe,
  **57 in tutto**. Oltre, `wrap_hook` impacchetta nell'ultima riga, che con `WrapStyle: 2`
  esce dal fotogramma senza errore. L'accento giallo e' una parola sola, e almeno una.
- **Niente pysubs2**: l'ASS si scrive direttamente.
- **Ogni parola dei sottotitoli ha il suo `\pos`**: con la riga in un evento solo la
  parola accesa al 112% faceva scivolare le altre. Le larghezze si leggono dal TTF come fa
  libass (entro un pixel), lo spazio fra due parole contiene la crescita della piu' larga,
  una riga troppo larga si rimpicciolisce, e un taglio dei silenzi chiude la riga.
- **Niente MCP per il video** (kinocut, ffmpeg-mcp): la pipeline e' deterministica e il
  video non esce dalla macchina.
- **`word_timestamps=True` e' obbligatorio.**
- **Nessun file Python che contenga solo istruzioni per Claude**: quel materiale va in
  `references/*.md`.
- **Scartati: espressioni facciali, gesticolazione, picture-in-picture**: costo alto su
  CPU, modelli FER inaffidabili, e il PiP richiede di capire cosa viene mostrato.

### Misurare la macchina, non digitarla

- **Il render in parallelo non conviene**: `--jobs 2` sembrava 1.14x su sei clip ed e'
  stato il 7,7% piu' lento su 38. Default 1. **Una prova dieci volte piu' piccola della
  consegna non misura la consegna**, e accanto al numero va la macchina (`record_metric`
  scrive processore, core fisici e thread).
- **`os.cpu_count()` conta i thread, non i core**: i core fisici si chiedono a CIM
  (`Win32_Processor`, somma di `NumberOfCores`, in cache per processo); se la query fallisce
  si scrive il numero di thread e lo si chiama thread.
- **I parametri che dicono quanta macchina usare si ricavano dalla macchina**: un
  `--threads 8` digitato lasciava ferma meta' CPU su un 16 core. Per la trascrizione si
  parte dai core fisici, coi thread come tetto; la documentazione dichiara il criterio,
  mai la costante. Lo scavalca solo una misura su quella macchina e a scala vera.
- **Un tempo di un portatile dice se stava in carica, per tutta la durata**: un tempo
  doppio era l'alimentatore staccato. `record_metric` scrive `alimentazione` (`rete`,
  `batteria`, `mista`, `null`) campionata ogni 5 s da `run.py`
  (`common.sorveglia_alimentazione`); `null` non e' `rete`. I
  banchi fuori da `run.py` la scrivono accanto a ogni campione.
