# Agenti di pubblicazione — YouTube, TikTok, Instagram, Whop

Carica le clip di una cartella su tutte le piattaforme attese dal gruppo, ci scrive
le didascalie con i tag che il brief pretende, registra gli URL e manda l'invio a Whop.

Tutto in locale con Playwright su un profilo browser per identità
(`dashboard/identita.json`, campo `profilo_browser`): niente API a pagamento, nessuna
credenziale nello script.

## Da quale account si pubblica

Ogni agente aveva **una** cartella di profilo scritta nel codice, perché c'era un account
per piattaforma. Con un gruppo per nicchia quella coincidenza è finita, e il modo in cui
sarebbe fallita è il peggiore: `orchestrator.pubblica_tiktok()` ripiegava su
`.browser-profile` e assumeva l'handle `canale_lab`, quindi una clip della nicchia
business sarebbe uscita **dall'account del laboratorio, senza un errore**.

Adesso il profilo lo dice `dashboard/identita.json` — che aveva già il campo
`profilo_browser` per ogni identità: il modello dei dati era giusto, era il codice a
ignorarlo. Tutti e quattro gli agenti prendono `--gruppo`:

```
python registro.py                              # chi pubblica cosa, e con quale profilo
python registro.py --gruppo business

python accedi.py --gruppo business --elenco     # cosa serve, senza aprire niente
python accedi.py --gruppo business              # i quattro login in fila
```

**La guardia.** Prima di caricare, l'agente confronta l'handle con cui il browser è
collegato e quello che il registro si aspetta: se non coincidono **non pubblica**. Vale
anche quando il browser non sa dire chi è — succede a sessione scaduta, cioè proprio
quando un caricamento finirebbe dove non deve. Una clip uscita sull'account sbagliato si
cancella, ma l'algoritmo di quel canale l'ha già vista.

**E vale su tutte e tre le piattaforme — per un po' ne copriva una.** Fino al 13 settembre
2026 `instagram_agent` leggeva l'account collegato **solo se** il registro non diceva
niente: con il registro su @X e il profilo browser collegato a @Y, il Reel usciva da @Y e
poi l'URL veniva cercato fra i reel di @X. Il sintomo non era un errore ma «URL non
determinabile con certezza», che sembra un problema di lettura. Adesso `handle_attuale()`
si chiama sempre e il confronto passa da `registro.controlla_handle`, come su TikTok.

**E «non so chi devo essere» non autorizza «non so nemmeno chi sono».** Con l'atteso vuoto
— le cartelle nate prima dei gruppi, che un gruppo non ce l'hanno — la guardia usciva
subito, e `orchestrator` ripiegava sull'handle cablato `canale_lab`. Non sbagliava il
caricamento, che va dove il profilo è collegato: sbagliava la **riga di stato**, cioè il
posto dove si legge cos'è uscito e dove. Adesso senza nessuno dei due nomi ci si ferma. I
cinque casi della guardia stanno in `tests/test_registro.py`.

**Su YouTube si confronta l'id del canale, non il nome.** Studio mostra «MioCanale»
mentre il canale è `@MioCanale67`: un confronto per nome bloccherebbe proprio l'account
giusto. L'id `UC...` sta nell'indirizzo su cui Studio atterra, è unico e non lo traduce
nessuno — sta in `canale_id` accanto all'handle.

**Su Whop si confronta il conto, ma «non si sa» non ferma.** I Whop sono uno per nicchia e
un invio nel pannello sbagliato non paga. Ma l'handle Whop non compare su tutte le pagine —
dentro una comunità la barra in alto mostra il saldo, non il nome — e la sessione l'ha già
dimostrata il controllo sulla rotta `/login`. Quindi qui l'incognita passa e solo la
**discordanza** blocca: fermarsi sull'incognita vorrebbe dire buttare la finestra dei 30
minuti su un video già pubblicato. È la differenza fra le due guardie, e non è
un'incoerenza: su TikTok l'handle assente **è** il sintomo della sessione scaduta.

**Gli handle si leggono, non si digitano.** Dopo il primo accesso si rileva l'account dal
browser e lo si scrive nel registro. Un handle digitato male blocca ogni pubblicazione di
quel gruppo e sembra un guasto dell'agente; `handle: null` significa «non ancora letto» e
lascia passare finché non lo si riempie.

Chiedere un gruppo che non ha ancora l'identità registrata **si ferma** invece di ripiegare
sul profilo storico: il ripiego silenzioso era il difetto, non la cura.

## Un account Whop per nicchia

Su Whop si può collegare **un solo account per piattaforma** — un TikTok, un Instagram —
quindi tre nicchie vogliono tre Whop, uno per mail di nicchia. Il profilo browser segue il
gruppo (`.whop-business`, `.whop-carte`, `.whop-finanza`): con una cartella sola il secondo
accesso sovrascriveva la sessione del primo, e nessuno se ne sarebbe accorto finché un
invio non fosse finito nella campagna dell'account sbagliato.

Il vecchio `.whop-profile` è la sessione dell'account unico di prima. Se ti serve ancora,
rinominalo in `.whop-laboratorio`; altrimenti rifai il login dove ti serve.

**Su Whop i login sono due**, ed è la trappola del passo: entrare in whop.com non apre il
pannello Content Rewards, che è un'applicazione su un altro dominio con la sua
autenticazione a email più codice. Con il solo `login` la cartella del profilo esiste,
`accedi.py` dice «già fatto», e il difetto salta fuori molto dopo — la campagna mostra un
modulo d'accesso al posto del contenuto, che da fuori somiglia a una pagina vuota. Perciò
`accedi.py` esegue `login` **e** `login-app`, e Whop non viene mai dato per fatto: quella
cartella non può dimostrare che la sessione dell'app sia viva.

**Prima di inviare bisogna essere dentro il whop del fornitore, e avere gli account
collegati.** Un conto nuovo non è iscritto a niente: la campagna si apre come pagina di
vendita, senza pannello e senza nessun posto dove collegare gli account.

```
python whop_agent.py --gruppo business whops        # a quali whop è iscritto
python whop_agent.py --gruppo business collega      # Settings -> Connected accounts, e aspetta
python whop_agent.py --headless --gruppo business collegati   # cosa risulta collegato
```

`collega` non fa l'autorizzazione: la fa una persona, perché è un OAuth con credenziali che
da qui non passano. Quello che aggiunge è aprire **il profilo giusto** — autorizzare nella
finestra sbagliata collega l'account sbagliato, e lo si scopre quando una campagna rifiuta
un invio — e **rileggere la scheda dopo**, perché «l'ho fatto» non è una verifica.

In quel profilo le sessioni social non ci sono: ogni piattaforma richiederà l'accesso. Non
è un guasto, è la separazione dei profili che funziona.

## Un account nuovo: cosa si imposta a mano

Gli agenti pubblicano; **le impostazioni dentro le app non le tocca nessuno script**, e
non c'è modo di accorgersi che mancano — la clip esce lo stesso. Questa è la lista, una
volta per account, prima del primo post.

**Nessuna di queste voci è stata misurata su questi account.** Sono gratis e sono vere:
è tutto quello che si può dire, e basta a giustificarle. La differenza con l'unica voce
scartata qui sotto è proprio quella.

### La lingua — l'unica cosa che descrive davvero il contenuto

Le tre nicchie sono anglofone e il materiale è inglese, quindi dichiararlo non costa una
bugia:

- **YouTube** — nella pagina di caricamento, `Lingua del video` e la lingua dei
  sottotitoli: inglese. `youtube_agent.py` **non le compila**, quindi o si mettono a mano
  o restano vuote.
- **TikTok** — Impostazioni → Preferenze di contenuto → lingue: solo inglese.
- **Instagram** — lingua dell'app: inglese.

Il browser lo dichiara già da sé: `registro.ambiente()` dà `locale = en-US` a tutti e
cinque i lanci di Playwright.

### Il paese su YouTube: è una dichiarazione, non una preferenza — si lascia vero

Il campo si chiama **«Paese di residenza»** (Studio → Impostazioni → Canale). YouTube lo
usa per l'idoneità ai programmi di monetizzazione e per i moduli fiscali, non per
scegliere a chi mostrare uno Short. Metterlo su «Stati Uniti» risiedendo in Italia è una
dichiarazione falsa in un campo legato ai pagamenti, in cambio di un effetto sulla
distribuzione che nessuno ha misurato — e gli incassi arrivano da Whop, non da YouTube,
quindi manca anche il guadagno che renderebbe la domanda interessante.

### Il warm-up è astenersi, non fare

Sette giorni di sola presenza, poi 1, 2, 3 post al giorno: sta in `ritmo.RAMPA` ed è
l'unica cosa che conta. **Non si simula attività umana per scaldare un profilo** — la
misura che abbiamo dice il contrario di quello che si crede: l'unico account strozzato,
`@canale_lab`, lo è stato per sei post in nove minuti, non per essere stato fermo.

C'è una cosa diversa che si può fare, e conviene non confonderla con quella sopra:
**seguire cinque o dieci account americani della nicchia** durante i giorni di presenza.
Non è attività simulata per sembrare umani — è dire all'algoritmo di cosa parla questo
profilo, quando non ha ancora un post da cui dedurlo. Si fa piano, una volta, e finisce lì:
mettere like a raffica è esattamente il gesto che ha strozzato l'account di prova.

## Installazione (una volta)

```powershell
cd C:\Projects\viral-clips\publish
.\setup.ps1
.\.venv\Scripts\python.exe publish_agent.py login
```

Il comando `login` apre il browser: accedi a TikTok con l'account che pubblica,
poi apri la pagina della campagna AltroFornitore e accedi anche lì. Da quel momento la
sessione resta salvata nel profilo e l'agente parte da solo.

## Uso

```powershell
# prova a vuoto: carica il video e scrive la didascalia, ma NON pubblica
.\.venv\Scripts\python.exe publish_agent.py run --limit 1 --dry-run

# un lotto da 10
.\.venv\Scripts\python.exe publish_agent.py run --limit 10

# cosa è stato fatto e cosa manca
.\.venv\Scripts\python.exe publish_agent.py status
.\.venv\Scripts\python.exe publish_agent.py plan

# rimette in coda una clip
.\.venv\Scripts\python.exe publish_agent.py reset --id 07
```

Per i tre lotti automatici giornalieri (09:30 / 14:30 / 20:00):

```powershell
.\schedule.ps1
```

Le attività girano con l'utente corrente: il PC deve essere acceso e sbloccato,
perché il browser si apre davvero a schermo.

## File

| File | A cosa serve |
| --- | --- |
| `publish_agent.py` | l'agente TikTok (con commento e invio AltroFornitore) |
| `youtube_agent.py` | l'agente YouTube |
| `instagram_agent.py` | l'agente Instagram — **mai ancora eseguito su un account vero** |
| `buffer_agent.py` | TikTok tramite Buffer, senza browser — vedi sotto |
| `instagram_api.py` | Instagram dall'API ufficiale di Meta, senza browser, e i numeri per reel — vedi sotto. **Mai ancora eseguito con un token vero** |
| `ospita.py` | un indirizzo pubblico temporaneo per una clip (tunnel `cloudflared`) |
| `orchestrator.py` | pubblica una clip su una piattaforma e la invia a Whop |
| `setup.ps1` | venv + Playwright + Chromium |
| `schedule.ps1` | i tre lotti giornalieri in Utilità di pianificazione |
| `<cartella clip>/publish.json` | didascalie, commenti, URL campagna, orari |
| `<cartella clip>/publish_state.json` | stato per clip **e per piattaforma** |
| `logs/` | log, screenshot e HTML di diagnostica |

## TikTok tramite Buffer

Dal 1 ottobre 2026 un'identità può uscire da Buffer invece che dal browser. Lo
decide il registro, identità per identità:

```json
"pubblica_via": "buffer",
"buffer_canale": "<id letto da buffer_agent.py canali>"
```

Senza `pubblica_via` resta il browser. Per ora vale solo per TikTok business:
è una prova (roadmap, fase 1, voce 9), e Instagram e YouTube restano fuori.

Una volta sola:

1. conto Buffer gratuito, con l'account TikTok collegato;
2. chiave creata in `publish.buffer.com/settings/api` e incollata in
   `publish/.buffer/chiave.txt`, una riga — la cartella è ignorata da git e
   saltata da `export_skill.py`, come i profili browser;
3. `python buffer_agent.py canali` per leggere l'id del canale, e
   `python buffer_agent.py schema` per confrontare lo schema vero con quello
   che il codice si aspetta.

Come esce una clip: l'orchestratore controlla che il canale sia l'account
atteso, `ospita.py` apre un tunnel verso **quel file solo**, Buffer crea il post
con `shareNow` e viene a prendersi il video, e il tunnel chiude quando il post
è uscito. Il file sale dalla linea di casa: 25 MB in circa tre minuti.

L'indirizzo che va a Whop è quello **del video**: se Buffer non lo dà, o dà
altro (il profilo, un link corto), l'orchestratore lo legge da Studio — il
video nato dopo l'inizio del giro e non ancora nello stato, e solo se è uno. Se
non si trova nemmeno lì la clip resta `pubblicato` senza indirizzo, con una
notifica: l'invio a Whop è da fare a mano entro trenta minuti.

Tre esiti, e non due: `pubblicato` (con `via: buffer` nel record),
`buffer_fallito` (non è uscito, si rifà), `buffer_in_corso` (il post vive
ancora dentro Buffer: il giro dopo lo **richiede** invece di crearne un altro,
perché potrebbe essere uscito nel frattempo). Un post fallito fa uscire
l'orchestratore con un codice diverso da zero, così `esegui.py` lo conta fra le
fallite e l'attività pianificata si tinge di rosso; uno in corso no, perché è
un'attesa.

## Instagram dall'API di Meta

Dal 2 ottobre 2026 un'identità Instagram può uscire dall'API ufficiale invece
che dal browser. Lo decide il registro:

```json
"pubblica_via": "api"
```

Senza il campo resta il browser. Le strade sono **per piattaforma** — `api` su
TikTok o `buffer` su Instagram fermano, invece di uscire dal browser con
un'etichetta falsa nel record.

**Provato col token vero il 3 ottobre 2026**: `chi`, `numeri` e una
pubblicazione senza `--davvero` (contenitore pronto in 40 secondi, niente
uscito). Manca solo la prima uscita vera.

Una volta sola, e lo fa una persona:

1. l'account Instagram diventa **professionale** (Creator o Business), dall'app:
   Impostazioni → Tipo di account e strumenti;
2. su `developers.facebook.com` si crea un'app con il caso d'uso «Manage
   messaging & content on Instagram» (tipo Business), che resta in modalità
   sviluppo: per un account proprio non serve App Review;
3. in App roles si aggiunge l'account come Instagram Tester, e l'invito si
   accetta da Instagram: Impostazioni → App e siti web → Inviti tester;
4. in Instagram → API setup with Instagram login → «Generate token», e il token
   si incolla in `publish/.meta/<gruppo>.txt`, una riga — cartella ignorata da
   git e saltata da `export_skill.py`, come la chiave di Buffer;
5. `python instagram_api.py chi --gruppo business`: dice di chi è il token, se
   è l'account che il registro si aspetta, la quota e la scadenza.

Il token dura 60 giorni e si rinnova da solo a ogni uso, dopo una settimana
dall'ultimo rinnovo. Se il rinnovo non riesce e mancano meno di due settimane,
arriva una notifica di Windows: va rigenerato al punto 4.

Come esce una clip: guardia sull'account (il token dice chi è), `ospita.py`
apre il tunnel, Meta crea un **contenitore** e viene a prendersi il video;
quando il contenitore è pronto il tunnel chiude, e una seconda richiesta lo
pubblica. L'indirizzo del reel arriva con la risposta — niente `errore_url`.

```powershell
python instagram_api.py pubblica --gruppo business --file <clip.mp4> --testo-da <didascalia.txt>
```

Senza `--davvero` si ferma a contenitore pronto: Meta ha scaricato e accettato
il video, niente è uscito, e il contenitore scade da solo in 24 ore. È una
prova vera, a differenza di quella del browser.

Tre esiti, come per Buffer: `pubblicato` (con `via: api`), `api_fallito`,
`api_in_corso`. **Il contenitore è la ricevuta**: si pubblica una volta sola e
dice sempre a che punto è, quindi il giro dopo lo richiede invece di crearne un
altro — se è diventato pronto lo pubblica senza ricaricare, se risulta già
uscito ne cerca l'indirizzo, se è fallito o scaduto lo rifà. Vale anche quando
la richiesta di pubblicazione resta senza risposta: non si sa se il reel è
online, e lo dice il contenitore.

**Dal 2 ottobre 2026 Instagram è sospeso**, in attesa del token: non esce più
dal browser e riparte da qui. Una piattaforma intera si ferma dal registro,
sull'identità:

```json
"pubblicazione_sospesa": "il motivo, in una riga"
```

`esegui.py` la salta dicendolo a ogni giro, `orchestrator.py pubblica` la
rifiuta (la prova `--dry-run` resta), e il pannello scrive «sospesa» invece di
«in ritardo». Le altre piattaforme delle stesse clip escono come sempre, e
l'account resta attivo: le sue views si leggono ancora. Tolto il campo, le clip
rimaste indietro escono dentro il ritmo. Non è `sospesa` né `a_mano` di
`publish.json`: quelli fermano una clip di una cartella, questo una piattaforma
in tutte le cartelle del gruppo, comprese quelle che nascono domani.

I numeri per reel (`leggi_analitiche.py`, cioè «Aggiorna social») passano
dall'API appena il token c'è, qualunque sia `pubblica_via`: views, portata,
tempo medio, chi scorre via nei primi tre secondi, salvataggi, condivisioni.
Fonti e paesi per reel non li dà nemmeno l'API, e i reel usciti prima del
passaggio ad account professionale nemmeno gli insight: quelli si completano
dal web. **Due unità non stanno scritte nella documentazione**, e le ha dette la
prima lettura vera: i tempi sono in millisecondi, lo skip rate fra 0 e 100
(`leggi_analitiche.SCALA_SCORRIMENTO`). Il grezzo si vede con
`python instagram_api.py numeri --gruppo business`.

## Instagram

Terza piattaforma dei tre account per nicchia: la stessa clip esce su YouTube,
TikTok e qui. Stessa forma degli altri due — nessuna API, profilo Chromium
persistente, login a mano una volta sola.

```powershell
.\.venv\Scripts\python.exe instagram_agent.py login --profilo .instagram-business
.\.venv\Scripts\python.exe instagram_agent.py --profilo .instagram-business `
    pubblica --file ..\output\<cartella>\01-clip.mp4 --didascalia "..." --prova
```

**`--prova` prima di `--davvero`, sempre.** Instagram non ha bozze per i Reel
dal web: la prova percorre tutto il caricamento e si ferma prima dell'ultimo
clic, ed e' l'unico collaudo possibile. Va rifatta ogni volta che
l'interfaccia cambia — e cambia.

L'agente **non e' ancora stato eseguito su un account vero**: e' scritto sui
passaggi noti dell'uploader, con piu' selettori per passo e in piu' lingue.
Quando un passo non trova il suo elemento salva uno screenshot in `logs/` e si
ferma li' invece di tirare a indovinare col clic successivo.

Se dopo la pubblicazione non riesce a riconoscere **un solo** reel nuovo sul
profilo, non registra nessun URL: un URL inventato che risulta buono e'
esattamente il difetto del 31 agosto, l'azione dichiarata riuscita che nessuno
aveva verificato da fuori.

## Lo stato è per clip **e per piattaforma**

Finché una cartella usciva su una piattaforma sola, `items["01"]` bastava. Con
tre piattaforme la chiave nuda è un sovrascrittore: pubblicare la clip 01 su
TikTok cancellava il record YouTube — URL, orario, esito Whop — senza che
niente si lamentasse. Da qui in poi la chiave è `01@tiktok`.

Le chiavi vecchie restano valide e non vengono migrate: se `items["01"]` esiste
già ed è di quella piattaforma, si continua a scrivere lì. Riscrivere il
passato per uniformarlo è il modo più rapido di perderlo.

`publish.json` vive **accanto alle clip**, non qui: ogni cartella di output ha le
sue didascalie e il suo stato, e lo stesso agente serve tutte le cartelle con
`--folder "C:\...\altra-cartella"`.

## Le tre piattaforme partono insieme

`esegui.py` lancia le piattaforme mancanti di una clip **nello stesso istante**, un
processo per ognuna: alle 19:15 si aprono tre browser e i tre account postano insieme.
Prima erano in fila e la terza cominciava una ventina di minuti dopo la prima.

Tre cose restano **in fila**, e sono decisioni, non limiti:

- **Le clip.** Due clip sulla stessa piattaforma a zero secondi di distanza sono la
  raffica che ha strozzato @canale_lab. Il parallelo è fra account diversi, mai fra
  clip: `ritmo.py` le fermerebbe comunque.
- **L'invio su Whop.** Il conto è uno per gruppo, quindi un profilo browser solo, e due
  Chromium sullo stesso `user_data_dir` non convivono. La serratura sta in
  `orchestrator.py`; la finestra utile è 30 minuti dalla pubblicazione e tre invii in fila
  ne prendono tre.
- **La scrittura di `publish_state.json`.** Si legge e si riscrive intero, quindi ogni
  scrittura prende la serratura, **rilegge dal disco** e cambia il solo record proprio.
  Chi risalvasse la copia letta all'avvio cancellerebbe le altre due pubblicazioni.

Per leggere un guasto senza tre finestre addosso: `python esegui.py --in-fila`.

**Prima delle piattaforme, Whop.** Una volta per cartella, prima della prima clip scaduta,
`esegui.py` chiede se la campagna accetta invii (`orchestrator.py --folder <cartella>
invii`, un minuto di browser senza finestra, stesso profilo e stessa serratura
dell'invio). Tre casi:

| esito | cosa succede |
|---|---|
| `aperti` | si pubblica |
| `in pausa`, `chiusi` | le clip restano ferme, non e' un guasto; escono da sole al primo giro dopo la riapertura |
| qualunque altra cosa | le clip restano ferme **e** il giro esce con 1: senza sapere se la campagna le accetta non escono |

L'esito sta anche in `output/<cartella>/invii.json`, che il pannello mostra accanto alla
clip. Il campo `sospesa` in `publish.json` resta per fermare una clip per ragioni nostre.

**Anche il profilo browser di ogni piattaforma e' sotto serratura mentre si carica**
(`serratura.nome_profilo`). Non per le piattaforme fra loro, che usano profili diversi, ma
per `leggi_account.py`, che apre gli stessi profili per leggere le views: chi pubblica
aspetta fino a cinque minuti, chi legge venti secondi e poi salta quell'account.

Le serrature sono file in `.serrature/`, uno per risorsa. Se un processo viene ucciso il
suo file resta, e dopo il tempo di scadenza il primo che passa lo toglie dicendolo: se
quel messaggio compare spesso, il lavoro protetto dura più del limite e va allungato il
limite, non tolta la serratura.

## Come riprende dopo un errore

Ogni passo (pubblicato → URL → commento → AltroFornitore) è annotato in
`publish_state.json`. Un nuovo `run` riparte da dove si era fermato: non ricarica
un video già online, e se manca solo il commento fa solo quello.

Quando un selettore salta (TikTok e AltroFornitore cambiano spesso l'interfaccia)
l'agente salva screenshot e HTML in `logs/` con un nome che dice quale passo è
fallito — è quello che serve per rimetterlo a posto.

## Regole della campagna già applicate

Ogni didascalia in `publish.json` contiene la menzione `@brandly.ai` e la frase
obbligatoria «Intervista completa sul canale YouTube di Brandly», non contiene URL
e non tocca le cifre dette nel video. Se cambi le didascalie a mano, tieni questi
tre vincoli.
