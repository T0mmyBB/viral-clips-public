# Dashboard — il pannello di controllo

Legge quello che gia' esiste in `output/`, `radar/`, `publish/` e `docs/roadmap.md`, lo
mette insieme in un'unica pagina, e segnala dove quello che dichiariamo non corrisponde a
quello che una fonte esterna conferma. Non e' una seconda verita': i dati hanno ciascuno
il suo proprietario (tabella «Da dove viene ogni dato»), e il pannello scrive solo le tre
cose elencate in fondo.

Dal 28 settembre 2026 e' **il pannello di controllo di tutto il progetto**: la cabina di
regia della roadmap, il catalogo da cui si sceglie, la produzione, la pubblicazione e i
numeri che decidono se il piano funziona.

## Avvio

```powershell
cd C:\Projects\viral-clips\dashboard
..\.venv\Scripts\python.exe server.py
```

Apre `http://127.0.0.1:8787/` da solo. `--porta N` per un'altra porta, `--no-apri` per non
aprire il browser. Se la porta e' gia' occupata non e' un errore: quasi sempre vuol dire
che il pannello e' gia' aperto, quindi il comando apre il browser su quella ed esce.

**Il codice Python vive nel processo**: dopo una modifica a `stato.py` o `server.py` il
server va riavviato. `vigila.py` lo rialza da solo se muore, quindi basta fermarlo quando
non ha lavori in corso. I file di `static/` invece si rileggono a ogni richiesta.

### Dopo ogni modifica alla pagina: il collaudo

```powershell
..\publish\.venv\Scripts\python.exe collaudo.py
```

Apre la pagina in un Chromium headless (Playwright sta gia' nel venv di `publish/`),
raccoglie errori di console, eccezioni e risposte `/api` sopra 399, apre le sei sezioni
della barra, conta cosa ciascuna ha disegnato e salva uno screenshot per sezione in
`dump-collaudo/`, piu' uno in **tema scuro** e uno a **400 px di larghezza** — dove
controlla anche che la pagina non scorra di lato. Esce con 1 al primo problema. Se il
pannello non e' acceso ne alza uno temporaneo su una porta libera.

**Clicca solo quello che non scrive**: le voci della barra, una carta di nicchia, la
testata di una campagna. Mai «Aggiorna social», «Aggiorna radar», «Chiedi», «Fai tutto», «scegli», «Segna
inviato», che fanno partire lavoro vero. Serve perche' un'eccezione a meta' di `avvio()`
lascia le sezioni vuote senza un messaggio, e rileggere `app.js` non la trova.

## Com'e' fatta la pagina

Una **barra laterale** che segue il flusso del lavoro, dall'alto in basso:

| Sezione | Risponde a |
|---|---|
| **Oggi** | cosa chiede me adesso, e a che punto e' il piano |
| **Campagne** | cosa conviene lavorare |
| **Produzione** | a che punto e' ogni campagna scelta, e il comando per farla avanzare |
| **Pubblicazione** | cosa esce e quando, cosa va inviato a Whop a mano, il ritmo degli account |
| **Numeri** | views per piattaforma e per account, e cosa dice Whop |
| **Chiedi** | quello che nessun pulsante fa |

Ogni sezione e' un indirizzo (`#oggi`, `#campagne`...): avanti, indietro e un link mandato
a se stessi funzionano come in qualunque pagina. Sulle voci della barra c'e' un contatore
solo dove serve un gesto: le cose da fare adesso od oggi, le campagne da prendere, quelle
in produzione, gli invii Whop in sospeso. In fondo alla barra «Aggiorna social» e
«Aggiorna radar», lo stato del giro in corso, e **l'eta' dei dati**: radar, views,
analitiche e Whop, ognuno con quando e' stato
letto — in ambra se ha piu' di due giorni. Con l'aggiornamento a mano quella riga non e'
un dettaglio: la distanza dall'ultimo clic e' l'eta' di tutto il resto.

Il tema segue Windows, oppure chiaro o scuro con il pulsante in fondo alla barra (la
scelta resta nel browser). La palette e' quella scelta dall'utente il 28 settembre 2026
— Jet Black, Silver, White, Coral Glow, Blue Slate — come token in `style.css`; verde,
ambra e rosso sono derivati, e **nessuno stato e' detto dal solo colore**: ogni etichetta
ha anche la sua parola.

In `app.js` i clic passano da **un solo ascoltatore** che legge `data-azione`. I pezzi di
pagina si ridisegnano spesso, e riagganciare gli ascoltatori a ogni disegno era il modo in
cui un pulsante smetteva di rispondere senza un errore: nella versione di prima c'erano
due funzioni `cambiaScelta`, e la seconda sostituiva in silenzio la prima.

### Oggi — la cabina di regia della roadmap

- **Roadmap**: la fase di oggi, le sue voci (fatte o no), i giorni che restano e le
  scadenze vicine.
- **Cancello**: le condizioni del cancello, col valore di oggi. Le views mediane con una
  barra per piattaforma e la tacca del bersaglio — basta una piattaforma — e l'incasso
  con lo stato degli invii su Whop.
- **Da fare**: solo cio' che si risolve con un gesto, in ordine di urgenza (`adesso`,
  `oggi`, `presto`), ognuno col pulsante della sezione dove si fa. Gli invii Whop con la
  finestra aperta, quelli fuori finestra, le scadenze della roadmap, un radar vecchio di
  due giorni, le clip in ritardo, le clip pronte senza calendario, le campagne scelte da
  preparare, gli avvisi di livello errore.
- **Prossime uscite** e **segnali**. Un segnale si guarda e basta, per questo non sta fra
  le cose da fare. Gli «0 views» di una stessa campagna e piattaforma arrivano gia'
  raggruppati (`stato.avvisi_raggruppati`): sei righe gialle in cima alla pagina dicevano
  una cosa sola — TikTok non distribuisce — e spingevano sotto tutto il resto.

**La roadmap si legge dal file, non da una copia.** `stato.roadmap()` prende le fasi dai
titoli `## Fase N — nome (periodo)`, le voci dalle righe numerate in grassetto — fatta
quando porta «*Fatto il» — e scadenze e cancello dalle tabelle `## Scadenze` e
`## Cancello` di `docs/roadmap.md`. La chiave nella tabella del cancello
(`views_mediane`, `incassato`) e' quella con cui `stato.stato_cancello()` calcola la
condizione. Una condizione che non si puo' leggere e' «non si sa», mai «non raggiunta».

### Campagne — scegliere

In cima **«Da prendere adesso»**: le campagne che si possono scegliere, sulle nicchie con
un account **aperto** (un gruppo «da aprire» ha il nome e non i profili), con la
**sorgente gia' risolta** dal risolutore, e con almeno $100 e due settimane di budget —
la roadmap chiede «budget e durata residui», e una campagna a $1 di residuo e' in
catalogo ma non e' lavoro. Sono ordinate per **soglia di pagamento** prima che per
`$/post`: per un account giovane una campagna che paga da mille views vale piu' di una da
$36 che ne pretende centomila.

Sotto, **una carta per nicchia**, tutte e sedici, con le tre che hanno un account in
cima. Chi ha un account mostra pubblicazioni e views; chi non ce l'ha quante campagne ci
sono da prendere — una barra a zero su una nicchia senza account direbbe «lavoro fermo»
dove non e' mai cominciato. Cliccando una nicchia si apre il suo **catalogo**, per
sottonicchia e ordinato per `$/post`.

Le regole del catalogo, che restano quelle decise nel tempo:

- **Si apre sulle lavorabili.** Su 627 campagne 490 sono di forma incompatibile — video
  gia' montati. «mostra tutte» le rivela; il conto in cima le dichiara sempre.
- **Gli stati sono quattro**: `si`, `lingua`, `no` e `?`. `?` e' «nessuno l'ha ancora
  letta», che non e' «scartata», e **le non ancora lette non si nascondono mai**.
- **La colonna «sorgente»** viene dal risolutore (`radar/sorgenti.py`): `pronta`, `da
  scegliere`, `a mano`, con il motivo nel titolo. Assente vuol dire «non ancora vista».
  Il filtro «solo sorgente pronta» segue la specifica: nei primi mesi conviene lavorare
  solo quelle.
- **«whop»** dice se si puo' *inviare*: il radar vede ogni campagna dalla pagina
  pubblica, ma l'invio passa dal whop del provider, e l'iscrizione e' un passo per
  campagna scelta.
- **Il pulsante «scegli» manca dove la campagna non si prende, e la casella dice quale
  delle sei ragioni e'**: `pagina marca`, `pagina esclusiva`, `lingua`, `forma`, `da
  leggere`, `già scelta`, con la frase intera nel titolo. Le scrive il server
  (`stato.riserva_scegli`), perche' riderivarle nel browser terrebbe due volte la regola
  che decide chi entra nelle somme. Da fuori, una regola applicata togliendo un comando e'
  indistinguibile da un guasto.
- **I paletti** sotto il nome: il nome porta alla **scheda pubblica**, che si apre senza
  login; `whop ↗` al pannello del provider, da cui si invia (e che senza iscrizione
  rimbalza sulla home senza dirlo); `max per clip` e `min per essere pagati`, che **cambiano
  da piattaforma a piattaforma** su una campagna su sette — quando divergono si elencano,
  perche' un numero solo sarebbe vero per una e falso per le altre; le tre sigle `YT TT
  IG` sempre tutte e tre, accese o spente.
- **«paga da»** e' `minimo ÷ CPM`: le views sotto cui un post vale zero, non poco. Verde
  fino a 2.000, ambra fino a 10.000, rosso oltre.

In fondo, **le campagne sparite dal catalogo**: giudicate e non piu' esposte dal sito. Non
si cancellano — il giudizio costa una lettura del brief, e una campagna puo' rientrare.

### Produzione — dalla scelta alle clip pronte

Le campagne scelte, per nicchia, ognuna con **lo stadio** in cui si trova: scelta,
sorgente, preparata, decisa, renderizzata, calendario, pubblicata. Aprendo una campagna
compare il comando giusto per lo stadio, e sotto le sue clip — una riga per clip e una
colonna per piattaforma, perche' guardarle separate e' il modo di non accorgersi che su
una manca. Cliccando una clip si apre il video con le metriche per piattaforma.

**Lo stadio si legge anche prima della consegna.** Il pannello contava solo le cartelle
di `output/` con un `publish.json`, e in mezzo c'e' tutta la produzione: Northwind Podcast aveva
18 clip renderizzate e risultava «da preparare». `stato.lavorazione_di()` trova le
cartelle di lavoro dal `campagna.json` che `run.py prepare --campagna` scrive.

I comandi girano in locale e non pubblicano niente:

- **Fai tutto / Solo prepara**, con il video da clippare **scelto fra i candidati del
  risolutore** — titolo, durata, data, il preselezionato gia' spuntato — e in mancanza fra
  i link dei materiali e del brief. Il campo da riempire resta solo dove nessuno trova
  niente. «Fai tutto» arriva fino agli mp4 senza che nessuno guardi in mezzo, quindi chiede
  conferma una volta; «Solo prepara» si ferma dove serve il giudizio editoriale.
- **Renderizza le clip decise**, quando `decisions.json` esiste.
- **Prepara la pubblicazione**, quando ci sono clip renderizzate e la nicchia ha un
  account: scrive didascalie e calendario, cioe' dati inerti. Non pubblica: a pubblicare e'
  `publish/esegui.py`, da un'attivita' pianificata.

Il menu `⋯` sposta la campagna senza cancellare niente: **segna come completata**,
**rimetti fra le attive**, **rimetti fra le disponibili** (non per una campagna con una
cartella: il lavoro fatto resta). Scrive solo in `scelte.json`. Le concluse stanno in
fondo, chiuse, con clip e metriche.

Mentre un lavoro gira, una banda in cima alla sezione dice quale e a che passo; il resto
del pannello resta usabile.

### Pubblicazione — cosa esce, e se e' registrato

**«Da inviare a mano»** e' la voce 5 della fase 1 della roadmap. Un invio Whop che fallisce
**non ferma la pubblicazione** — il post esce, l'esito resta scritto in
`publish_state.json` — ma finora l'unico segno era un avviso fra gli altri. Qui ogni
pubblicazione online che su Whop non risulta ha il link al post, «Copia link», il pannello
Whop del provider e **quanto manca alla fine della finestra di 30 minuti**. Fatto l'invio
sul pannello di Whop, **«Segna inviato»** scrive `inviato a mano` su quel record, con un
«Annulla» nel messaggio che segue. A campagna finita la riga resta, marcata e in fondo:
l'invio non paga piu', ma sparire non e' lo stesso che essere a posto. Quando l'invio
automatico fallisce, `orchestrator.py` manda anche **una notifica di Windows**
(`publish/notifica.py`): la pubblicazione gira da un'attivita' pianificata, cioe' quando il
pannello non lo guarda nessuno.

Poi il **calendario**: le prossime, le gia' pubblicate, e le clip senza calendario. Ogni
riga ha le tre piattaforme — tutte e tre sempre, anche quelle fatte, perche' «manca
instagram» si legge solo se si vede che le altre due ci sono — e sotto ognuna la sua spia
**whop**: l'invio e' per coppia (clip, piattaforma), e una spia unica per la clip direbbe
«a posto» con un terzo del lavoro da rifare. Verde vuol dire `inviato` o `inviato a mano`
(`stato.WHOP_FATTO`, una definizione sola). **«ferma»** vuol dire che l'ultimo controllo di
`esegui.py` ha trovato gli invii Whop in pausa o chiusi: esce da sola alla riapertura.
**«in ritardo»** con un esito che non e' `aperti` vuol dire che lo stato non si e' potuto
leggere, di solito la sessione Whop.

In fondo **account e ritmo**: i gruppi con i profili aperti e da aprire, e per ogni
identita' se oggi si puo' pubblicare e perche' no.

### Numeri — quello che misura il cancello

- Per piattaforma: **views mediane per post** (la mediana, non la media: un post virale
  su nove ne fa sembrare buoni otto che non lo sono), post pubblicati e misurati, il
  migliore, il totale. **Un post mai letto non entra nella mediana**: «non misurato» non e'
  zero.
- **Il grafico delle views totali degli ultimi 30 giorni**: l'ultima misura di ogni post
  entro ogni giorno, sommata (`metriche.andamento`). SVG senza librerie; le serie si
  distinguono per colore **e** per tratto, con il nome scritto in fondo alla linea.
- **Per account**, e **Whop per gruppo**: invii, approvati, in attesa, respinti,
  incassato, quando e' stato letto.
- **Il dettaglio per cartella**, chiuso: ogni pubblicazione con identita', stato ed esito
  Whop.

### Chiedi

La stessa cosa dei pulsanti, con la domanda scritta dall'utente: parte `claude -p` sul
progetto, puo' modificare i file e lanciare comandi, non pubblica. Il filo si tiene con
`--session-id` e `--resume`, scritti su disco perche' il sorvegliante puo' riavviare in
mezzo a una conversazione.

## I tasti «Aggiorna social» e «Aggiorna radar»

In fondo alla barra, due dal 1 ottobre 2026, perche' rispondono a due domande diverse.

**«Aggiorna social»** — come vanno i nostri post (`server.PASSI_SOCIAL`), circa 20 minuti:

1. `metriche.py --youtube --account --whop` — le views, i segnali dei post e il pannello
   Whop dei nostri invii e pagamenti;
2. `analitiche.py aggiorna` — le analitiche di ogni post: fonti, paesi, tenuta, tempo medio.
   E' il passo lungo, perche' apre una pagina per post. Le mostra la tabella «Analitiche per
   post» in Numeri; il dettaglio completo con `python dashboard/analitiche.py mostra`.

**«Aggiorna radar»** — cosa c'e' da lavorare (`server.PASSI_RADAR`):

1. `radar/giornaliero.py` — catalogo, schede, brief, nicchie, report;
2. `radar/sorgenti.py` — da ogni campagna lavorabile ai video da clippare, a secco. Dopo il
   radar perche' legge le schede e i brief appena riscritti; con la cache delle impronte
   riapre la rete solo per le campagne cambiate.

Insieme, radar per primo, sono il giro di `aggiorna.ps1` (`server.PASSI`).

**Non pubblica e non invia niente**: legge. Il giro dura minuti, quindi la richiesta torna
subito e la pagina chiede a che punto e' (`GET /api/aggiorna`). Due giri insieme non
partono. Lo stato vive in memoria del server: se il server si riavvia, il giro non risulta
piu' in corso, il che e' giusto. Un passo fallito mostra le sue ultime venti righe di
stderr in un riquadro in cima alla pagina, sopra tutte le sezioni.

## Il giro giornaliero

Due attivita' pianificate, registrate da `pianifica.ps1` nella radice:

| Ora | Attivita' | Cosa fa |
|---|---|---|
| 09:00 | `ViralClips-Dashboard` | alza questo pannello e apre il browser |
| 15:15, 19:15, 21:15, 23:15 | `ViralClips-Pubblica` | `publish/esegui.py`: pubblica le clip il cui momento e' arrivato |

Se il PC alle 09:00 e' spento, l'attivita' parte appena si accende
(`-StartWhenAvailable`). **Il radar non e' fra queste, dal 9 settembre 2026**: lo lancia
il pulsante «Aggiorna radar». Dentro `giornaliero.py` il passo `nicchie` fa leggere a Claude le
campagne nuove o cambiate, quindi dopo un aggiornamento i conti per nicchia possono essere
**diversi**, non solo piu' recenti.

## Da dove viene ogni dato — la mappa contro la seconda verita'

| Dato | Fonte | Chi lo scrive | Chi lo legge qui |
|---|---|---|---|
| Clip di una campagna, titoli, didascalie | `output/<slug>/publish.json` | scritto a mano / da Claude | `stato.py` |
| Stato di pubblicazione, URL, esito Whop | `output/<slug>/publish_state.json` | `orchestrator.py`; **«Segna inviato»** per `inviato a mano` | `stato.py` |
| Invii alla campagna | `publish/whop_invii.json` | `whop_agent.py` | `stato.py` |
| Campagne, metriche misurate, paletti | `radar/radar_dettagli.json` | `radar/dettagli.py` | `stato.py`, `tassonomia.paletti()` |
| Nicchia e forma di ogni campagna | `radar/assegnazioni.json` | Claude, leggendo i brief | `stato.py` |
| **Da dove viene il girato** | `radar/sorgenti/<id>.json` | `radar/sorgenti.py` | `stato.facilita_sorgente`, `stato.risolutore` |
| Cartelle di lavoro di una campagna | `work/<slug>/campagna.json` | `run.py prepare --campagna` | `stato.lavorazione_di` |
| Provider, iscrizioni | `radar/provider.json` | `radar/provider.py` | `stato.py` |
| Hook, punteggi, motivazione | `work/<slug>/decisions.json` | Claude in fase editoriale | `stato.py` |
| Chi siamo, su quale account | `dashboard/identita.json` | scritto a mano | `stato.py` |
| **Fasi, scadenze, cancello** | `docs/roadmap.md` | scritto a mano | `stato.roadmap` |
| **Le views nel tempo** | `dashboard/storico.sqlite`, tabella `misura` | **solo** `metriche.py` | `metriche.py`, `stato.numeri` |
| **I segnali dei post** | `dashboard/storico.sqlite`, tabella `salute` | **solo** `metriche.py --account` | `stato.avvisi_post()` |
| **Stato degli invii e pagamenti** | `dashboard/storico.sqlite`, tabella `whop` | **solo** `metriche.py --whop` | `stato.numeri` |
| Cos'e' cambiato nel radar | `radar/ultimo_giro.json` | `radar/giornaliero.py` | `stato.py` |
| **Quale campagna lavorare** | `dashboard/scelte.json` | **solo** questo pannello | `stato.py` (`lavoro()`) |

Se un dato sembra mancare, la domanda giusta e' "quale file lo possiede", non "come lo
aggiungo alla dashboard": va aggiunto un lettore in `stato.py`, mai un nuovo posto dove
scriverlo.

## Perche' gli avvisi esistono

La notte del 31 agosto 2026 tre azioni sono state dichiarate riuscite nei log e non lo era
nessuna: due clip su YouTube erano rimaste **in bozza** e i loro invii non risultavano nel
pannello Whop. Nessun errore da nessuna parte — ogni script aveva dedotto il proprio
successo da un indizio interno alla pagina che aveva appena compiuto l'azione.

`stato.avvisi()` incrocia i file che gia' abbiamo — offline, veloce — e trova un invio
senza pubblicazione, un ritardo oltre i 30 minuti, un esito diverso da "inviato". Per la
prova **vera**, quella che viene da fuori, c'e' `verifica.py`:

| Piattaforma | Prova | Costo |
|---|---|---|
| YouTube | `oembed` pubblico: 200 = pubblico, 403 = privato/bozza | una richiesta HTTP |
| TikTok | l'id del post compare nell'elenco (Studio + profilo) | un browser, profilo `.tiktok-*` |
| Whop | l'URL compare nell'elenco **Submissions** della campagna | Scrapling, venv condiviso |

```powershell
..\publish\.venv\Scripts\python.exe verifica.py tiktok --handle canale_lab
%USERPROFILE%\.venvs\scraping\Scripts\python.exe verifica.py whop --post <url> --campagna <uuid>
```

## Le views nel tempo, e Whop

```powershell
..\.venv\Scripts\python.exe metriche.py --youtube            # YouTube, in HTTP
..\.venv\Scripts\python.exe metriche.py --account            # ogni account del registro, col browser
..\.venv\Scripts\python.exe metriche.py --whop               # il pannello Whop di ogni gruppo
```

`--youtube` legge le clip YouTube in HTTP puro. `--account` apre, per ogni identita'
attiva, **una pagina per account** che porta i numeri di tutti i post
(`publish/leggi_account.py`): TikTok Studio, la griglia dei reel di Instagram, YouTube
Studio. Da TikTok e Instagram prende views e like (`misura`); da tutte e tre i **segnali**
(`salute`). Il segnale che conta di piu' lo ricava `stato.avvisi_post`: **0 views a un
giorno dall'uscita**.

`--whop` apre, per ogni gruppo con una consegna passata da Whop, il pannello Content
Rewards (`whop_agent.py guadagni`, l'indirizzo dalla consegna piu' recente del gruppo) e
legge due cose:

- **i contatori di «Submissions»**: clip inviate, approvate, in attesa (Pending),
  respinte. Sono lo stato **vero** degli invii, quello che `stato.pagamento()` puo' solo
  dedurre da views e soglia. Prima lettura, 28 settembre 2026: 20 invii, 0 approvati, 19
  in attesa, 1 respinto;
- **i pagamenti**, dalla risposta `/api/submission/payouts` che la pagina chiede da sola.
  Tre regole (`whop_agent.somma_pagamenti`): un campo `...Cents` e' in centesimi e si
  preferisce il netto; **zero e' zero in qualunque unita'**; un importo diverso da zero
  **senza unita' dichiarata non si indovina** — resta `null`, con le chiavi viste. Un
  pagamento stornato (`reversed`) non e' un incasso. La prima voce vista era proprio
  questa: `amount: "0"`, `status: "reversed"`, sul conto del laboratorio.

I lettori aprono gli stessi profili browser degli agenti, quindi prendono la stessa
serratura e aspettano solo venti secondi: durante una pubblicazione quel conto si salta, e
si rilegge al giro dopo.

## Aprirla: un clic

`crea-collegamento.ps1` mette sul desktop **«Pannello di controllo viral-clips»**. Un clic
apre il browser sulla pagina e **nient'altro**: sotto c'e' `pythonw.exe dashboard\apri.py`,
che alza il pannello se non e' su e poi apre. **0,3 s** a pannello acceso, **5,5 s** da
spento. `pythonw.exe` e non `powershell.exe`: «finestra nascosta» per PowerShell vuol dire
che compare e sparisce. E siccome cosi' non resta nessuna console dove leggere un guasto,
se il pannello non risponde `apri.py` lo dice con una finestrella e indica
`vigila-errori.log`.

```text
powershell -ExecutionPolicy Bypass -File crea-collegamento.ps1            # crea l'icona
powershell -ExecutionPolicy Bypass -File crea-collegamento.ps1 -Aggiorna  # icona che aggiorna
powershell -File aggiorna.ps1                                            # giro completo a mano
```

## Cosa entra nei conti

Solo il lavoro nelle lingue dichiarate in `identita.json` (`lingue_in_plancia`, oggi
`["en"]`), e solo le identita' con `attiva: true`. La lingua non si indovina dallo slug: si
legge da `work/<cartella>/transcript.json`. **Quella lettura e' memorizzata per cartella e
per data del file** (`stato.lingua_cartella`): la chiamano in tanti, una volta per misura,
e rileggere un file da megabyte ogni volta portava lo snapshot da 1,4 a 4,7 s. Si nasconde
**solo cio' che si e' dimostrato**: una cartella senza trascrizione resta nei conti.

## Gruppi e identita'

Un **gruppo** e' un account Google con i suoi profili sulle piattaforme, ed e' l'unita' che
ha una nicchia: `business`, `podcast` (nicchia intrattenimento), `finanza`, piu' il
`laboratorio` degli account nati prima della divisione. Un'**identita'** e' un singolo
profilo. Il ritmo si conta per identita', il lavoro per gruppo. I gruppi stanno in
`identita.json` **prima** che gli account esistano: un gruppo senza identita' si vede come
«account da creare», che e' il suo stato.

## File

```text
dashboard/
  identita.json     chi siamo: gruppi (account per nicchia) e identita'
  scelte.json       quali campagne lavorare
  stato.py          lo snapshot, letto dalle fonti esistenti; roadmap, numeri, cose da fare
  verifica.py       la prova esterna: oembed YouTube, id TikTok, Submissions Whop
  metriche.py       il raccoglitore di views, segnali e Whop; scrive storico.sqlite
  pubblico.py       da quale paese arrivano le views; scrive storico.sqlite
  storico.sqlite    solo serie temporali
  server.py         http.server: pagina, /api/*, e i .mp4 per l'anteprima
  vigila.py         rialza il server quando muore, e si ritira se qualcuno serve gia'
  conversa.py       la sezione «Chiedi»: lancia Claude e tiene il filo
  apri.py           il collegamento sul desktop: alza il pannello e apre la pagina
  collaudo.py       il pannello in un browser vero, sezione per sezione
  static/           index.html + style.css + app.js — vanilla, nessun CDN
```

## Le scritture, e cosa manca apposta

Il pannello scrive tre cose, e nessuna manda niente fuori:

- `scelte.json`, da `POST /api/scelte`: quale campagna lavorare, e dove sta;
- `publish_state.json`, da `POST /api/whop-mano`: `inviato a mano` su **un** record
  (clip, piattaforma), attraverso `orchestrator.scrivi_stato` — serratura, rilettura dal
  disco, il solo record proprio, perche' tre pubblicazioni possono scrivere lo stesso file
  nello stesso momento. Rifiuta lo schema vecchio senza `items`, dove scriverci aprirebbe
  un elenco nuovo e i record di prima sparirebbero dalla vista;
- `storico.sqlite`, dai lettori lanciati da «Aggiorna social».

**Niente pulsante pubblica, niente pulsante invia.** «Segna inviato» prende nota di un
gesto fatto sul pannello di Whop; non lo fa. Un clic distratto qui non deve mai diventare
una pubblicazione pubblica: la differenza fra scegliere, preparare e pubblicare e' la
ragione per cui queste rotte esistono e le altre no.
