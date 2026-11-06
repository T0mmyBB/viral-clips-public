---
name: viral-clips
description: Trasforma un video lungo (un'ora o piu, da link YouTube o file locale) in clip verticali 9:16 pronte da postare su TikTok, Reels e Shorts. Gli script fanno il lavoro meccanico - download, trascrizione, rilevamento volti, crop dinamico, render - e Claude fa quello editoriale: legge la trascrizione, sceglie i momenti che possono funzionare, decide dove iniziano e finiscono, scrive gli hook. Tutto in locale e gratuito. Usala quando l'utente da un video e vuole clip, spezzoni, short, reel, o chiede quali momenti di un video potrebbero funzionare sui social.
---

# viral-clips

> **Cosa contiene questo file:** come si esegue. Niente giustificazioni, niente stato.
> Il *perche* delle decisioni sta in [CLAUDE.md](../../../CLAUDE.md), lo stato del lavoro
> e i numeri misurati in [PROGRESS.md](../../../PROGRESS.md).

## Il principio

**Gli script fanno il lavoro meccanico. Tu fai quello editoriale.**

Gli strumenti in commercio delegano la scelta dei momenti a un LLM a pagamento. Qui la
fai tu, direttamente, leggendo il contenuto. E la ragione d'essere della skill: tutto il
resto — tagliare, inquadrare, sottotitolare — e esecuzione.

Non scrivere script che "valutano" i momenti al posto tuo. Se ti viene la tentazione di
automatizzare la selezione con delle euristiche, stai smontando la cosa che rende utile
questa skill.

## Prima di cominciare

Se `.venv/` non esiste, lancia il setup una volta sola:

```
powershell -ExecutionPolicy Bypass -File .claude/skills/viral-clips/scripts/setup.ps1
```

Interprete e script:

```
.venv\Scripts\python.exe
.claude\skills\viral-clips\scripts\
```

Gli script scrivono la diagnostica su **stderr** e il risultato JSON su **stdout**.

## Il flusso

**Due comandi, e in mezzo ci sei tu.**

```
python run.py prepare "<url o percorso>" [--audio-lang en] [--campagna <uuid>]
python run.py prepare "<url>" --audio-lang it --language it
   ... leggi, correggi, scegli, scrivi decisions.json ...
python run.py render <work_dir>
```

**`--campagna` quando il video viene da una campagna**, cioe sempre tranne le prove: copia
il brief del committente nella cartella di lavoro e ne fa uscire i vincoli in cima alla
vista del passo 4. Il pannello di controllo lo passa da solo. Senza, si sceglie senza le
regole della campagna — e non si vede, perche la consegna riesce lo stesso.

**Una campagna fuori dal catalogo del radar** (un altro fornitore, come AltroFornitore) entra
dalla stessa porta: `--campagna <id del fornitore> --brief <file>` (o `--brief -` per
incollarlo), piu `--fornitore` e `--titolo`. Non scrivere `campagna.json` a mano, e non
ricopiarci CPM o piattaforme: fuori catalogo restano vuoti, e i numeri veri stanno nel
brief copiato. I link di trasferimento (WeTransfer, SwissTransfer) escono in cima alla
vista come materiale da scaricare subito, perche scadono.

`prepare` fa i passi 1-3 e si ferma quando c'e tutto per decidere; `render` fa i passi
5-6 su quello che hai deciso. Per l'utente resta **un comando solo**: da il link e riceve
le clip.

I passi qui sotto sono gli stessi, uno per uno: servono quando qualcosa va storto, o
quando ne serve solo uno. Sono tutti con cache, quindi rilanciarli non ricomincia da capo.

### 1. Ingest

```
python ingest.py "<url o percorso>" [--audio-lang en]
```

Da un link scarica **audio e proxy 480p**, non il video pesante: e tutto quello che serve
per trascrivere, analizzare e scegliere, e ci mette meno di un minuto. Scrive `meta.json`.

Il sorgente a **risoluzione massima** (non 1080p: da un 2160p il ritaglio 9:16 e una
riduzione, quindi la clip esce piu nitida del sorgente) arriva dopo — se lo prende il
render da solo. Quando sai gia che renderizzerai, avvialo mentre leggi e scegli, cosi
l'attesa non si somma:

```
python ingest.py <work_dir> --fetch-source
```

**Sui video doppiati la traccia audio si sceglie con `--audio-lang`, non si lascia al
default**: la prima che YouTube offre e spesso un doppiaggio. Prendi quella del parlato
originale, se e in italiano o in inglese; `ingest.py` elenca le tracce disponibili e si
ferma se quella richiesta non esiste. Se nessuna e in italiano o in inglese, il video e
fuori dalle lingue della skill: ti fermi e lo dici. Scaricare 27 minuti nella lingua
sbagliata significa buttare download e trascrizione.

### 2. Trascrizione

```
python transcribe.py <work_dir> --language en
python transcribe.py <work_dir> --language it
```

**Non passare `--threads`.** Il default usa i core fisici della macchina; un numero copiato
da un esempio lascia meta macchina ferma sull'host successivo, senza errori. Il flag serve
solo a scavalcare il default con una misura fatta su quella macchina.

**Il modello e `large-v3-turbo` per tutte e due le lingue, ed e il default**: non serve
`--model`. `small` e `medium` perdono frasi intere, e su un testo bucato si sceglie male,
non solo si sottotitola male. Non passare `--model small` per fare prima: in inglese non
e piu veloce di turbo, e perde di piu.

**Passa sempre `--language` esplicito** quando la lingua e nota: il rilevamento automatico
guarda i primi 30 secondi e quando sbaglia non degrada, allucina il decode intero. Se la
lingua non e nota viene votata su piu campioni e ristretta a quelle attese
(`--languages`).

### 3. Segnali

```
python signals.py <work_dir>
python faces.py <work_dir>
```

Energia, pause, cambi scena, e la linea temporale "una faccia / due facce / nessuna".
Girano sul proxy, sono veloci.

Da qui in poi il materiale puo avere qualcosa di anomalo. Non serve cercarlo a mano:

```
python check_material.py <work_dir>
```

Sorgente gia verticale, niente volti, parlato rado. Lo chiama gia `run.py prepare`, e le
note finiscono **in cima alla vista del passo 4**: e li che le leggi, quando servono.

Nello stesso posto, e per la stessa ragione, arrivano i **paletti della campagna**:

```
python campagna.py <work_dir> --id <uuid>    # se prepare non l'ha gia fatto
```

Le anomalie dicono se c'e qualcosa da consegnare, i paletti a quali condizioni: durata,
watermark che il ritaglio 9:16 non deve tagliare, da quale video si puo tagliare, cosa
deve dire la didascalia. Il brief esce **per intero** sotto le righe evidenziate, che sono
un marcatore e non un riassunto: se una regola conta e il marcatore non l'ha presa, sta
comunque li sotto.

**Poi le regole vanno scritte, ed e un lavoro tuo.** In `work/<slug>/campagna.json` c'e un
blocco `regole` vuoto: lo compili leggendo il brief, come `decisions.json`. Nessuno script
traduce «watermark must be visible throughout» in un parametro — e un giudizio, e uno
sbagliato qui non fallisce: consegna.

```json
"regole": {
  "letto_il": "2026-09-07",
  "durata_min": null, "durata_max": 30,
  "logo_richiesto": true, "logo": "canale.png",
  "cta_richiesta": false,
  "sottotitoli": "karaoke",
  "vincoli_render": ["il sorgente ha gia un watermark: il 9:16 non deve tagliarlo"],
  "vincoli_pubblicazione": ["la didascalia deve nominare X e taggare @y"]
}
```

`letto_il` a `null` non vuol dire «nessuna regola», vuol dire **«nessuno ha ancora
guardato»** — e finche resta li, la vista e `validate.py` lo dicono a ogni giro.

**Cosa ne fa la pipeline.** `validate.py` interseca `durata_min` e `durata_max` con
i limiti base e pretende `logo` e `cta` in `decisions.json` quando la campagna li chiede;
i `vincoli_*` in prosa li ristampa prima del render, perche sono esattamente quelli che
nessun controllo automatico sa verificare; se `vincoli_render` nomina un watermark, un logo
o degli handle e il sorgente e orizzontale, aggiunge un avviso con la quota di larghezza che il
9:16 tiene: guarda un fotogramma del sorgente e uno della clip prima di renderizzare tutto.
**Una regola di campagna puo solo stringere:**
un brief che accetta clip da dieci secondi non abbassa il minimo di venti.

**E valgono solo per questa consegna.** Stanno nella cartella di lavoro, non in un file di
configurazione: la campagna dopo ha la sua cartella e le sue regole, e le vecchie non la
raggiungono. Riagganciare la *stessa* campagna non le cancella; agganciarne un'*altra* sì.

### 4. Leggi e scegli — **questa parte e tua**

**Non leggere mai `transcript.json` intero.** E quasi due ordini di grandezza piu grande
della vista a segmenti — un oggetto per parola, che serve al render e non a te — e
leggerlo satura il contesto, cioe rende impossibile la parte per cui sei qui.

Due letture distinte, in momenti diversi. **Per scegliere**, la vista a segmenti, che sta
in una lettura sola anche per un'ora di parlato:

```
python windows.py <work_dir>
```

Testo con timestamp e i segnali affiancati (picchi, pause, risate, composizione dei
volti). Questo si legge tutto in un colpo.

**In cima, se ci sono, le anomalie del materiale.** Non sono decorazione: dicono che il
9:16 su una condivisione schermo tiene solo una striscia centrale, o che il sorgente e
gia verticale e quindi probabilmente e gia una clip. Leggile *prima* di scegliere i
momenti — dopo il render sarebbe tardi. Se una e marcata `FERMA`, manca la materia prima
e non c'e niente da consegnare.

**Subito sotto, i paletti della campagna**, quando la cartella e agganciata a una. Vengono
prima dei criteri editoriali qui sotto, e li restringono: un vincolo del committente non si
discute e non si compensa con una scelta migliore. Se invece non c'e nessun blocco di
paletti, il video non appartiene a nessuna campagna **oppure nessuno gliel'ha detto** — e
prima di scegliere vale la pena sapere quale delle due.

**Per i bordi** — i timestamp per parola, consultati **solo sulle candidate scelte**:

```
python windows.py <work_dir> --words --around 1120 --span 4
```

Mostra le parole attorno a un istante, per agganciare l'inizio e la fine alla parola
giusta. Sono decine di righe, non decine di migliaia.

**`start` e l'inizio di un segmento, `end` e la *fine* di un segmento: sono due numeri
diversi.** Fra due segmenti di solito c'e un buco, e l'inizio del segmento dopo — il
numero che la vista a segmenti stampa in testa alla riga — non e la fine di quello prima:
usarlo come `end` fa protestare `validate.py`. La vista `--words` scrive ogni segmento con
la sua intestazione `start … end …`: i due numeri si copiano da li.

**Mentre leggi, correggi quello che Whisper ha sentito male.** I sottotitoli vengono
*impressi*: un errore li resta nella clip pubblicata. Scrivi `corrections.json` in
`work/<slug>/` e applicalo — le parole cambiano, i tempi mai. **Si corregge quello che
si nota, non si cerca il refuso**: un errore piccolo non ferma niente. Fa eccezione il
**nome del programma, della campagna e degli ospiti** (dal titolo e da `brief.txt`):
ricorre in ogni clip e Whisper lo sente spesso in due o tre modi diversi, quindi cercane
tutte le varianti nel transcript e mettile fra le `globali`.

```json
{"globali": {"Dalese": "Alesia", "pesta": "cartapesta"},
 "puntuali": [{"t": 169.1, "parola": "si", "in": "sia"}]}
```

```
python apply_corrections.py <work_dir>
```

Le `globali` valgono ovunque, per parole sbagliate in se. Le `puntuali` sono ancorate a un
istante e servono per le parole comuni: `si`, `che`, `alla` sono sbagliate in *quel* punto
e giuste in tutti gli altri, quindi cambiarle ovunque romperebbe il resto.

Il file resta li: rilanciarlo non fa danni, e le correzioni sopravvivono a una nuova
trascrizione. Tenerle a mente invece che scriverle le fa sparire al primo `--force`.

**Cosa non puoi correggere.** L'audio lo sente solo Whisper, e i tempi delle parole li
allinea solo lui: se ha prodotto una frase plausibile ma sbagliata, da fuori non si vede.
Se invece manca del testo — una parola che dura sette secondi, un tratto saltato — non e
un errore di ortografia ma di allineamento: quello si risolve solo ritrascrivendo con un
modello piu grande.

**Il criterio e uno: la clip si capisce da sola.** Non "e virale" — quella e una
previsione su un pubblico che non hai. Esce anche il momento solido e non elettrizzante:
i punteggi diranno com'e fatto, e chi pubblica decidera cosa mandare per primo.

Criteri completi in **[references/viral-heuristics.md](references/viral-heuristics.md)**.
Sei dimensioni su tre livelli — debole / solido / forte — e solo la seconda decide:

**hook** (l'attacco fa restare invece di scorrere via?) · **autosufficienza** (si capisce senza il
resto del video?) · **arco** (qualcosa cambia tra inizio e fine?) · **carica** (il momento
colpisce, o e solo ben costruito?) · **concretezza** (storie e numeri battono i concetti)
· **attrito** (contiene un'affermazione su cui si puo dissentire?)

Sono tutte **proprieta osservabili leggendo**, non previsioni sul comportamento di un
pubblico che non hai. Se ti accorgi di star votando "quanto verra condiviso", stai
indovinando: torna a chiederti cosa c'e nel testo.

I **bordi** non sono una dimensione: se sono sbagliati si correggono, non si votano.
Stanno nella checklist prima del render.

Regole che sbagliano piu spesso:

- **Una clip corta non e autoconclusiva.** E il difetto piu ricorrente, visto su
  materiale gia consegnato: in 10-15 secondi non ci stanno premessa, sviluppo e chiusura,
  e quello che resta sembra completo solo a chi ha in testa il video attorno. Se un
  momento buono dura tredici secondi, **allarga i bordi** fino a prendere la frase che lo
  prepara e quella che lo chiude.
- **Un hook debole si sposta, non si scarta.** Se l'attacco non funziona, sposta l'inizio
  piu avanti invece di tenere il preambolo.
- **Il momento piu importante del video e spesso una pessima clip**, perche richiede tutto
  il contesto che lo precede.
- **Escludi i segmenti sponsorizzati.** Sui video lunghi sono un pattern fisso: cambio di
  tono, nome di prodotto, "codice sconto", "link in descrizione", "QR code". Marcali come
  zona vietata prima di valutare i momenti.

**Durata: minimo 20 secondi**, zona buona 20-45. Sopra i 40 va bene quando il momento lo
chiede — non e un tetto: Shorts accetta fino a 3 minuti. Sopra il minuto `validate.py`
segnala solo di verificare che non ci sia musica di terzi nel sottofondo del sorgente,
per via delle rivendicazioni di copyright.

### 5. Scrivi `decisions.json`

**Le decisioni si persistono, non si tengono in testa.** Per ogni clip scelta: bordi,
hook, parole da evidenziare, punteggi, motivazione.

```json
{"clips": [
  {"slug": "01-il-rospo", "start": 1109.30, "end": 1139.90,
   "hook": "I said I was *scared*. 30 seconds later, this.",
   "keywords": ["sign", "scared", "believe"],
   "scores": {"hook": "forte", "autosufficienza": "forte", "arco": "forte",
              "carica": "forte", "concretezza": "forte", "attrito": "solido"},
   "why": "Storia completa in 30 secondi, coincidenza che sembra scritta."}
]}
```

Serve a **rirenderizzare domani senza rigiudicare**, e a **passare hook e keyword al
render senza attraversare la riga di comando**: fra PowerShell, subprocess e parser dei
filtri un hook con apostrofi si rompe. Passa sempre il file, mai il testo.

Tre campi facoltativi, se una clip li vuole diversi dal default: `style`, `hook_seconds` e
`tagli`. **Le `keywords` non colorano soltanto**: sulle loro parole la camera stringe con uno
scatto d'enfasi, al massimo uno ogni 15 secondi. Scegli quelle che portano il senso, non le
piu frequenti.

**Le grafiche animate (`grafiche`) sono la firma visiva delle clip, e le metti tu.** Un
elenco facoltativo per clip: quando il parlato ha una battuta che merita di restare a
schermo — la frase ad effetto, una cifra detta, un prima e dopo, un elenco detto a voce —
ci metti sopra un modello. Stesso font e stesso giallo dell'hook, sempre: e questo che
rende le clip riconoscibili.

| modello | campi (caratteri massimi) | per cosa |
|---|---|---|
| `parola` | `testo` (18) | una o due parole timbrate su una fascia gialla: la parola che fa la battuta |
| `frase` | `testo` (60), al massimo una `*parola*` | la frase ad effetto, parola per parola, l'accento in giallo sottolineato |
| `numero` | `numero` (10), `etichetta` (28, facoltativa) | una cifra che sale fino al valore: `"$7M"`, `"87%"`, `"20"` |
| `prima_dopo` | `prima` (14), `dopo` (14) | un valore barrato che diventa un altro: `"$0"` → `"$40K / MONTH"` |
| `elenco` | `voci` (da 2 a 3, 22 l'una) | le voci di un elenco detto a voce, una al secondo |
| `bocciature` | `titolo` (22), `testo` (12), `quante` (una cifra da 2 a 8, **testo**: `"6"`), `finale` (22, facoltativa) | tentativi respinti: un riquadro numerato per volta col timbro rosso, e in giallo il risultato (`"0 INTERVIEWS"`) |
| `giorni` | `numero` (5, solo un intero), `etichetta` (28, facoltativa) | un conteggio di giorni: la cifra sale e una striscia di tacche si accende con lei |

```json
"grafiche": [
  {"modello": "numero", "at": 709.4, "parola": "20", "numero": "20",
   "etichetta": "minutes for 50 people"},
  {"modello": "frase", "at": 694.3, "parola": "we're", "testo": "we're *adaptable* chefs"}
]
```

- **`at` e l'istante del sorgente**, in secondi assoluti come `start` ed `end`; **`parola`**
  e la parola detta su cui agganciarsi, e la grafica parte esattamente li. Mettila sempre:
  i tempi della vista sono arrotondati. Le prendi da `windows.py --words --around <t>`.
- **Durante la grafica i sottotitoli tacciono**, perche stanno nella stessa fascia. Quindi
  il testo della grafica dice **quello che si sente**, le stesse parole o la loro sintesi
  fedele, nella lingua dell'audio. Non e un secondo hook.
- **Quante: quando il momento lo chiede, e non per riempire.** Una clip senza grafiche e
  normale; due o tre in una clip lunga vanno bene; due a meno di 2 secondi l'una
  dall'altra no, e il render tiene la prima. La durata la decide il modello; `durata`
  (1,2-6 s) solo se serve diversa.
- Una grafica che non si riesce a rendere si salta e la clip esce lo stesso: il render lo
  scrive in `grafiche_saltate`. `--no-grafiche` le ignora per una prova veloce.

**I silenzi dentro la clip si tolgono da soli**: le pause senza parole e con l'audio davvero
quieto, mai le risate, mai i bordi, mai sotto il minimo di durata. `"tagli": false` sulla
clip — o in cima al file, per tutta la consegna — li lascia dove sono: serve quando la pausa
**e** il momento (la battuta che aspetta, il silenzio dopo una domanda). I bordi che scrivi
restano quelli del sorgente; la durata della clip montata la dice il render.

Controllalo prima di renderizzare: campi mancanti, bordi invertiti, punteggi fuori
rubrica, sovrapposizioni.

```
python validate.py <work_dir>
```

### 6. Render

```
python render_clip.py <work_dir> --decisions decisions.json --slug 01-il-rospo --auto-track
```

Bordi, hook e keyword li prende dal file — non passarli a mano, nemmeno "solo per
provare": e il modo in cui si rompono. Se non indichi `--out`, la clip esce in
`output/<video>/<slug>.mp4`. Prima di partire ricontrolla la clip richiesta, con la
stessa verifica di `validate.py`: un campo sbagliato scoperto a meta di un render costa
tutto il render.

Due campi di consegna, in cima a `decisions.json`, entrambi fuori dal flusso normale —
senza il campo non si stampa niente:

- `"logo": "<file.png>"` — impresso per tutta la clip, quando una campagna lo pretende
  come condizione di attribuzione. **Solo se il file esiste davvero** in
  `work/<slug>/` o in `assets/`: se `regole.logo_richiesto` e vero ma il file del
  committente non e mai arrivato (link a una cartella invece che a un'immagine
  diretta, download fallito), il campo **resta assente**. Non si inventa un nome
  plausibile — successo una volta, `contoso-logo.png` per un file che non esisteva
  da nessuna parte — perche un nome inventato fa fallire `validate.py` con
  l'errore sbagliato ("dichiarato ma non trovato" invece di "nessun asset
  disponibile"), che manda a cercare un file mai esistito invece del vero
  problema: procurarsi il logo del committente.
- `"cta": "Guarda il *VIDEO COMPLETO*"` — compare quando l'hook e sfumato e ne prende il
  posto, cosi i primi secondi restano alla promessa della clip. Gli `*asterischi*` qui
  escono in **rosso**, non in giallo.

`run.py render --jobs N` renderizza piu clip insieme, **ma il default 1 e quello giusto**
(i numeri in [PROGRESS.md](../../../PROGRESS.md)). Con piu di un job il tempo per clip non
viene registrato: sotto concorrenza e una latenza, non un costo.

Un solo passaggio ffmpeg per clip. Se il sorgente a piena risoluzione non e ancora sul
disco, il render se lo scarica prima di partire: sono minuti, ed e il momento in cui si
pagano. La forma con `--start`, `--end` e `--out` espliciti resta per le prove veloci; per
una clip che consegni si passa dal file.

### 7. Verifica la sincronia — non basta guardare un fotogramma

Un frame singolo non dice niente sul difetto piu probabile della pipeline: sottotitoli
sfasati di una parola. Da un solo istante e invisibile.

```
python check_sync.py <work_dir> --slug 01-il-rospo
```

Prende N campioni al centro esatto di altrettante parole e verifica che l'ASS accenda la
parola giusta, che sia davvero quello che si dice li secondo il transcript, e che nel
fotogramma **renderizzato** il giallo cada dove sta quella parola nella riga. Esce con 1
se qualcosa non torna, e lascia i fotogrammi in `work/<slug>/sync_*.png` con il nome
della parola attesa: se fallisce, si guarda quello. Se il render ha tolto dei silenzi,
ritrova da solo l'istante nel sorgente dal piano accanto ai sottotitoli
(`work/<slug>/tagli_<start>_<end>.json`).

Con uno stile che non colora la parola accesa (`minimal`) i pixel non possono misurare
niente: i campioni escono «?» e la sincronia la conferma solo il transcript.

L'altro difetto probabile, il **crop che salta**, si misura dal piano del crop:

```
python check_camera.py <work_dir> --slug 01-il-rospo
```

Rigioca il `sendcmd` fotogramma per fotogramma come lo esegue ffmpeg e conta i
micro-salti (un fotogramma che si sposta fra due fermi, fuori dagli stacchi), gli
inciampi di una panoramica e i fotogrammi in cui il quadro si deforma. Esce con 1 sopra
dieci micro-salti al minuto o con un solo fotogramma deformato: le clip rese col render
attuale stanno fra 0 e 6. Scorrere la clip resta necessario per il **gusto** — se una
posizione e giusta, se un taglio dei silenzi inciampa — non per trovare il tremolio.

### 8. Consegna

`output/<slug>/` con gli mp4 e **`clips.md`**. Per ogni clip: nome del file, timestamp nel
sorgente, durata, i **sei punteggi in chiaro**, il testo in sovrimpressione, una
didascalia pronta da incollare, gli hashtag e la motivazione della scelta.

I punteggi in chiaro servono perche, tranne l'autosufficienza, descrivono e non scartano:
cosa mandare per primo lo decide chi pubblica — senza quella riga dovrebbe aprire `decisions.json`.

Chiudi con **cosa non hai scelto e perche**: due o tre tratti, una riga ciascuno. Se non
lo scrivi li, quella parte del giudizio sparisce. Segnala anche i casi di prova — la clip
piu corta e la piu tiepida — perche sono quelle su cui l'utente puo dirti se la soglia e
tarata bene.

Se il materiale lo richiede, un avviso in testa: persone o aziende nominate, accuse
circostanziate, turpiloquio. Non e censura, e dire all'utente cosa sta per mettere sotto
al proprio nome.

**`publish.json`** lo scrive `python publish/prepara.py --cartella output/<slug>`, non a mano.
Campi: `campaign`, `provider`, `gruppo`, `whop_campaign_uuid`, `whop_app`, `source_episode`,
`note_regole`, `handles`, `batch_times`, `min_gap_seconds`, `max_per_day`, e `items` (`id`, `file`,
`title`, `caption`, piu `a_mano` e `sospesa` dove servono). Da sapere:

- `handles` sono i canali **di chi ha fatto il video**, da taggare in didascalia: una voce per
  piattaforma, e piu tag separati da uno spazio. Non sono i nostri account, che stanno in
  `dashboard/identita.json`.
- `whop_app` lo scrive il codice, dal provider della campagna: non copiarlo da un'altra consegna.
- `platform`, `youtube_handle`, `submit_within_minutes`, `batch_size` sono campi vecchi:
  non si scrivono, e `prepara.py` toglie quelli che non erano gia nel file.
- `quando` e `calendario_*` li scrive `calendario.py`.

**La consegna non e finita finche questo non passa:**

```
python validate.py <work_dir> --consegna
```

Fallisce se `clips.md` manca o non cita per nome ogni mp4 della cartella, e se un mp4 non
si puo caricare: vuoto, illeggibile, troncato, non 1080x1920, senza audio o sotto il minimo
di durata. Esiste perche `clips.md` e l'unica parte della consegna che scrivi tu e non il
codice, e quindi l'unica che nessun altro controllo pretendeva. La stessa verifica sui file
la ripete `calendario.py` prima di assegnare una data.

Le clip si consegnano tutte insieme. **Non chiedere un giudizio clip per clip**: il lavoro
dell'utente e pubblicare, non fare da giuria. Se ti dice spontaneamente che una non va,
usala subito su quel video invece di archiviarla per una taratura futura.

## Come si scrive un hook

L'hook e la frase impressa in cima alla clip per i primi 5 secondi. Non e un titolo: e
una **promessa** che dice allo spettatore perche dovrebbe restare.

Cinque forme. Sono le **due strategie** di
[references/viral-heuristics.md](references/viral-heuristics.md) viste da chi scrive
invece che da chi giudica — quattro modi di *trattenere*, uno di *dichiarare il
risultato*. Gli esempi sono in inglese perche il materiale lo e.

Trattenere — la promessa e che qualcosa sta per arrivare:

- **Apri un ciclo che il cervello vuole chiudere** — "I said I was *scared*. 30 seconds
  later, this."
- **Contraddici un'aspettativa** — "They don't call it a *drug*. They call it medicine."
- **Prometti una spiegazione** — "Your brain deletes those five minutes. Here's *why*."
- **Sposta il punto di vista** — "What my friend was *thinking* while I was down there."

Dichiarare il risultato — la posta in gioco detta subito, quando a sorprendere e il
risultato stesso e non il modo in cui ci si arriva:

- **Rivela dove va a finire** — "He asked for five *billion* as a joke. They said yes."

**L'hook descrittivo e vietato.** Se si riassume come «Tizio parla di X» e un titolo, non
una promessa: non apre niente, e non c'e niente da aspettare. Il test si fa in una
domanda — *quale delle cinque promesse sto facendo?* Se non sai rispondere, l'hook e
descrittivo e va **riscritto**, non accorciato. Non funziona nemmeno superare le tre righe.

**L'hook deve incuriosire, e puo essere leggermente clickbait.** Spingilo: esagera
l'angolo, accentua il contrasto, lascia la domanda aperta. Il minimo e che la clip tenga
il **nucleo** della promessa — se dici «30 seconds later, this», dopo trenta secondi deve
succedere qualcosa che c'entra. Un hook che la clip non mantiene per niente non
funziona: chi resta per quello e trova altro se ne va, e il tempo di visione che
contava e perso. **Se il brief della campagna vieta di esagerare o di dire piu del
girato, vince il brief**: e un paletto, e i paletti restringono le regole base.

**Il limite di lunghezza e geometria, non gusto.** L'hook e 84px su 1080 di larghezza, e
`make_subs.HOOK_STYLE` da 19 caratteri per riga su 3 righe: **57 in tutto**. Oltre quelli
`wrap_hook` non protesta e non tronca — impacchetta l'eccesso nell'ultima riga, che con
`WrapStyle: 2` (nessuna andata a capo automatica) esce dal fotogramma senza un errore.
Tieni sotto i **~55 caratteri, cioe 7-9 parole inglesi**.

**Una parola fra `*asterischi*`, e almeno una.** L'accento giallo non e facoltativo: un
hook senza non e una scelta, e una dimenticanza, e `validate.py` la ferma. Due sono di
troppo per la ragione scritta in
[references/viral-heuristics.md](references/viral-heuristics.md) — la parola attiva del
karaoke ha gia il suo colore, e due gialli saturi affiancati annullano l'effetto «una
parola alla volta».

**Se la clip chiude gia su qualcosa che riecheggia il suo inizio, l'hook lo dice.** Chi
riguarda dall'inizio e tempo di visione che si somma, e ottenerlo non costa niente — ma il
verso conta: **l'hook e la variabile libera, i bordi no.** Scrivere l'hook perche punti a
una chiusura che c'e gia e gratis; spostare `end` perche rimi con l'hook paga il giro
d'anello con l'autosufficienza, che e l'unica dimensione che decide se la clip esce. La
fine sta dove finisce il senso.

**L'hook va nella lingua dell'audio e dei sottotitoli.** Un hook italiano su una clip
inglese stona.

## Non chiedere: consegna

**Un link deve bastare.** L'utente manda un video e riceve le clip: non chiedi quante ne
vuole, non chiedi se va bene, non ti fermi a farti approvare la lista.

**Nessun tetto al numero, e nessun minimo di viralita**: esce tutto quello che regge da
solo — cinque o trenta lo decide il materiale. La soglia e **`autosufficienza` almeno
solida, e basta**; le altre cinque dimensioni descrivono e finiscono in `clips.md`. Si
scarta quello che non si capisce da solo, quello sponsorizzato o coperto dal rumore, il
doppione tagliato peggio, e quello che non arriva ai 20 secondi nemmeno allargando i
bordi. Il resto sta in
**[references/viral-heuristics.md](references/viral-heuristics.md)**.

La motivazione non si salta perche non te l'ha chiesta: va in `clips.md`.

**Ti fermi a chiedere solo se:**

- l'utente **ti chiede** di vedere le clip prima (non e un flag: te lo dice parlando, e le
  anteprime le fai con `run.py render --preview`)
- il video **non ha nemmeno una clip** che passa la soglia — allora non consegni niente e
  spieghi cosa hai trovato, invece di consegnare roba debole per non tornare a mani vuote
- c'e qualcosa di anomalo che cambia il senso della consegna: sorgente gia verticale,
  audio coperto per larghi tratti, doppiaggio che rompe il labiale. **Le prime non devi
  accorgertene da solo**: `check_material.py` le mette in cima alla vista del passo 4,
  ed e li che le trovi. Il doppiaggio no, quello resta da riconoscere guardando

Tutto il resto — quante clip, quali, dove tagliare — lo decidi tu. E il lavoro.

## Regole di qualita, non negoziabili

- **Un solo passaggio di encode.** Nessun file intermedio: ogni passaggio in piu e
  qualita persa. Anche i tagli dei silenzi stanno dentro quel passaggio.
- **L'audio si prende dalla traccia originale, mai da `audio.wav`** — quello e 16k mono e
  serve solo a Whisper. E un errore facile perche il file e li a portata di mano.
- **`-ss` prima di `-i`**, `-t` dopo.

## Prima di dichiarare qualcosa funzionante

Tre strumenti, tre domande diverse. Confonderli ha gia prodotto due bug inesistenti di
fila.

**Un fotogramma a risoluzione piena** stabilisce: soggetto inquadrato con spazio sopra la
testa · hook dentro i bordi e su tre righe al massimo · sottotitoli leggibili sullo
sfondo · nessun artefatto o banda nera. **Non** stabilisce sincronia ne salti del crop: da
un singolo istante sono invisibili, e sono i due difetti piu probabili.

**`check_sync.py`** (passo 7) stabilisce la sincronia da solo, senza che tu guardi.

**`check_camera.py`** (passo 7) stabilisce se il crop salta, dal piano e non dai pixel.
Resta da guardare scorrendo la clip cio che e gusto: una posizione sbagliata ma ferma,
un taglio dei silenzi che inciampa. Un contact sheet a miniature non stabilisce
**niente** — dice solo *dove* guardare da vicino.

Le trappole tecniche gia pagate stanno in [CLAUDE.md](../../../CLAUDE.md) — leggile prima
di diagnosticare un problema, ce n'e gia una che lo spiega.
