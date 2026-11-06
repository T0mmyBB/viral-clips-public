# Ricerca esterna — settembre 2026

Mercato di riferimento: **Stati Uniti e anglofono**. Cosa dicono le
piattaforme, cosa dicono i dati sugli outlier, e cosa di tutto questo cambia
davvero qualcosa in `viral-heuristics.md`, `SKILL.md` e `validate.py`.

**Regola di lettura.** La rubrica di selezione si fonda su una scelta precisa:
si valutano **proprieta osservabili nel testo**, non previsioni sul pubblico.
Questa ricerca non la ribalta. Serve a correggere i punti in cui la rubrica
afferma un **fatto sulle piattaforme** — quelli si verificano — e a tarare le
soglie numeriche su una popolazione reale invece che a intuito.

## Metodo e limiti

Tre fonti, di affidabilita molto diversa. Vanno tenute separate.

| Fonte | Cosa da | Affidabilita |
|---|---|---|
| Documentazione ufficiale Google / TikTok / Instagram | Regole di formato, segnali di ranking dichiarati | Alta — prima parte |
| Outlier vidIQ (Instagram + TikTok, US/anglofono) | Durata, hook, formato di 31 clip da podcast e intervista che hanno battuto la mediana del proprio account | Media — dati reali, campione piccolo |
| Blog di settore e agenzie di clipping | Prezzi, CPM, modelli di business | Bassa — quasi tutti hanno un prodotto da vendere |

**Il limite piu grosso:** l'analisi outlier guarda solo chi ha vinto. Non
esistono i dati delle clip con lo stesso hook e la stessa durata che sono
morte. Ogni volta che qui si legge "gli outlier fanno X", va inteso come *X e
presente tra chi ha sfondato*, mai come *X fa sfondare*. E la stessa ragione
per cui la rubrica vieta le previsioni sul pubblico.

Il campione e stato ristretto al formato che produciamo davvero: clip
ritagliate da podcast e interviste lunghe, parlato a camera, sottotitoli
impressi, verticale. Nicchie: business, finanza personale, self-improvement,
carriera.

Non e stata usata nessuna metrica delle nostre clip gia consegnate: non sono
state raccolte. Finche non lo saranno, tutto quello che segue resta esterno.

---

## 1. Shorts arriva a 3 minuti dal 2024. Il codice ne sa 60 secondi

**Il piu concreto dei risultati, ed e un difetto nostro.**

Google, pagina di supporto ufficiale: i video caricati **dal 15 ottobre 2024**
con formato quadrato o verticale e durata **fino a tre minuti** sono
classificati come Shorts, monetizzabili dal feed Shorts. Il limite di 60
secondi e stato superato quasi due anni fa. YouTube dichiara inoltre di **non
avere una durata minima**.

Nel nostro codice:

```
validate.py:42    MAX_DURATION  = 75.0
validate.py:46    SHORTS_LIMIT  = 60.0
```

e in `SKILL.md`: *"oltre i 60 resta buona per TikTok e Reels ma non per Shorts"*.
Stessa affermazione in `viral-heuristics.md`: *"Oltre i 60 si perde Shorts"*.

Tre punti che ripetono un fatto non piu vero. L'effetto non e cosmetico:
`MAX_DURATION = 75.0` **scarta materialmente** i candidati sopra i 75 secondi,
e la sezione 2 mostra che un quarto degli outlier anglofoni sta proprio li
sopra.

**Le eccezioni, che vanno tenute:**

- Musica sotto copyright: la maggior parte dei brani copre fino a 90 secondi
  dentro uno Short da 3 minuti, alcuni si fermano a 60 o 30.
- **Uno Short sopra il minuto con una rivendicazione di copyright attiva viene
  bloccato globalmente e demonetizzato.** Per noi il rischio e la musica di
  sottofondo del video sorgente, non la voce: se il tratto scelto ha musica di
  terzi sotto, sopra i 60 secondi diventa una zona di rischio reale.
- Canali artista ufficiali: la soglia e scattata l'8 dicembre 2025.

**Diff proposto:**

```
- MAX_DURATION = 75.0
+ MAX_DURATION = 180.0

- # Oltre questa durata YouTube Shorts non la accetta piu.
- SHORTS_LIMIT = 60.0
+ # Shorts accetta fino a 3 minuti (video caricati dal 15/10/2024).
+ # I 60 secondi restano una soglia di rischio, non di formato: sopra il
+ # minuto, una rivendicazione di copyright attiva blocca lo Short a livello
+ # globale. Rileva solo se il tratto ha musica di terzi sotto la voce.
+ SHORTS_LIMIT = 180.0
+ COPYRIGHT_RISK_LIMIT = 60.0
```

---

## 2. La "zona buona 20-45 secondi" e tarata troppo stretta

Durate delle 16 clip da podcast/intervista del campione anglofono per cui
TikTok riporta i secondi:

```
20  22  23  26  28  39  45  55  56  60  72  73  107  141  158  169
```

- **mediana 55,5s** — contro una "zona buona" dichiarata di 20-45
- **6 su 16 (37%) superano i 60 secondi**
- **4 su 16 (25%) superano i 75 secondi**, cioe il nostro `MAX_DURATION`
- la coda arriva a 169s, comodamente dentro la finestra Shorts da 3 minuti

Le due clip piu lunghe del campione non sono casi marginali: 158s con 539x la
mediana dell'account, 169s con 71x. Nessuna clip sotto i 20 secondi compare tra
gli outlier di questo formato — il minimo attuale non viene contraddetto dai
dati, viene semmai confermato.

**Cosa questo conferma della rubrica.** Il minimo di 20 secondi regge. La
giustificazione scritta — sotto i 20 secondi *"non ci stanno premessa, sviluppo
e chiusura"* — e coerente con un campione dove il valore minimo osservato e
esattamente 20.

**Cosa questo smentisce.** Il tetto implicito. "Zona buona 20-45" piu
`MAX_DURATION = 75` chiude fuori il 25% della popolazione che sfonda, e taglia
via la mediana stessa.

**Diff proposto per `viral-heuristics.md`:**

> **Durata: minimo 20 secondi.** Sotto, premessa, sviluppo e chiusura non ci
> stanno, e resta un frammento che sembra completo solo a chi ha visto il video
> attorno. Nessuna clip sotto i 20 secondi compare tra gli outlier del formato
> podcast anglofono.
>
> **Zona buona 30-90 secondi**, mediana osservata 55. Sopra i 90 va benissimo
> quando il momento lo chiede: un quarto degli outlier sta sopra i 75 secondi e
> le code arrivano oltre i 160. **Non e un tetto:** fino a 3 minuti resta
> Shorts a tutti gli effetti. Oltre i 60 secondi, verificare solo che non ci sia
> musica di terzi sotto la voce.

---

## 3. L'hook che domina il mercato USA e un riassunto del finale scritto dal clipper

Il risultato piu utile, e non e quello che mi aspettavo.

Sui 31 hook raccolti, la forma piu frequente **non e** la domanda
dell'intervistatore ne la prima frase del parlato. E una **frase in terza
persona, scritta da chi monta, che racconta come va a finire**:

| Testo hook | Risultato |
|---|---|
| HE ASKED FOR $5 BILLION AS A JOKE. THEN BACARDI CAME BACK WITH EXACTLY THAT. | 1,5M view · 769x |
| FROM $500 EVENT TO $500 MILLION FASHION EMPIRE | 185K · 102x |
| FIRST TIME IN HISTORY 60 YEAR OLDS OUTNUMBER 16 YEAR OLDS | 52K · 70x |
| Graham Stephen explains why he doesn't raise rents. | 836K · 167x |
| THE MARBLE JAR THEORY THAT WILL CHANGE HOW YOU SEE TRUST | 44K · 539x |
| THE LEADERSHIP STYLE THAT BUILDS GREAT COMPANIES | 109K · 84x |
| Why some people never get jet lag. | 184K · 54x |
| How Many Americans Have $2M Cash? The Number Will Shock You | 26K · 26x |

Distribuzione delle 31 forme osservate:

| Forma | Casi |
|---|---|
| Riassunto in terza persona del punto d'arrivo, scritto dal clipper | ~10 |
| Domanda diretta allo spettatore | ~7 |
| Prima frase del parlato ripresa alla lettera | ~7 |
| Payoff trattenuto ("It's About *This*") | ~3 |
| Imperativo o interiezione ("LISTEN!!!") | ~2 |

Sei contengono una cifra.

**Il punto controintuitivo:** il piu forte del campione — 1,5 milioni di view,
769 volte la mediana dell'account — **rivela il finale nell'hook**. "Ha chiesto
5 miliardi per scherzo. Poi Bacardi e tornato con esattamente quella cifra." La
clip non serve a sapere *cosa* succede, serve a sentire *come*. Lo stesso hook
compare identico su Instagram e su TikTok da due account diversi: e testo
deliberato e riusato, non improvvisato.

Per noi cambia la natura del compito. Oggi `SKILL.md` definisce l'hook come una
frase che *"apre un ciclo che il cervello vuole chiudere"* — trattenere. I dati
anglofoni dicono che sul materiale da intervista funziona bene anche
l'opposto: **dichiarare il risultato e lasciare che la clip mostri il
percorso.** Sono due strategie, non una giusta e una sbagliata, e oggi ne
documentiamo una sola.

**Da aggiungere alla scaletta di scrittura dell'hook** come seconda opzione
esplicita: *riassumi in terza persona dove va a parare, numeri compresi.*

### Contro-prova, e va tenuta

Quattro dei trentuno hanno un hook che la nostra rubrica classificherebbe
**debole** senza esitazione, e hanno sfondato lo stesso:

- `THE REASON WHY I ASKED THAT QUESTION IS BECAUSE` — frammento a meta frase.
  102,8x
- `do you` — due parole troncate. 56,1x
- `If someone's watching you for the first time` — subordinata sospesa. 74,1x
- `There are too many men who are able` — frammento. 56,4x

Non e rumore da ignorare: e la conferma di una regola che la rubrica gia ha e
che va lasciata dov'e — *"un hook che resta debole non e un motivo per
scartare: peggiora la clip, non la annulla"*. I dati esterni non chiedono di
irrigidire quella soglia. La difendono.

---

## 4. In questo genere gli effetti sonori non esistono

Su **tutte** le clip TikTok del campione: `sfx_present: false`. Diciotto su
diciotto, zero eccezioni. Il mix audio e "Voice only" nella grande maggioranza,
"Voice + Background Music" in quattro casi. `visual_changes` risulta "Low
(static camera)" o "Moderate"; nessuna clip mostra la cadenza di stacco rapido.

Questo chiude la questione aperta dal tutorial CapCut esaminato in precedenza,
che costruisce tutto su whoosh, suspense rise, shutter click e stacchi ogni 2-5
secondi. **Quella e la grammatica dei faceless commentary shorts, non delle
clip da intervista.** Importarla nel nostro render sarebbe copiare la
convenzione di un genere diverso.

Delle quattro idee che avevo segnalato come riusabili da quel video, ne
sopravvivono due:

- **Sopravvive** il taglio secco in coda: nessun frame morto dopo l'ultima
  parola. Coerente con `avg. % viewed`, che e un segnale ufficiale.
- **Sopravvive** il grading leggero (`eq` + `unsharp` in coda alla catena):
  costo quasi nullo, nessuna controindicazione.
- **Cade** lo stack di effetti sonori: assente nel 100% del campione.
- **Cade** la cadenza di stacco ogni 2-5 secondi: il campione e a camera fissa.

### Una cosa che non facciamo e che il campione fa

Circa sette clip su trentuno usano lo **split screen**: ospite sopra e host
sotto, oppure parlante piu b-roll. Noi facciamo ritaglio dinamico che segue il
soggetto — una scelta diversa, non peggiore. Ma sul materiale a due voci, dove
la domanda dell'host e parte del senso, lo split screen risolve un problema che
il crop singolo non risolve: **si vede chi chiede e chi risponde.** Vale come
possibile aggiunta al render, da valutare.

---

## 5. Segnali ufficiali contro folklore da blog

Vale la pena fissare cosa e dichiarato e cosa e interpretazione, perche il
secondo gruppo circola come se fosse il primo.

**Dichiarato da YouTube** (pagina ufficiale su ricerca e scoperta degli Shorts):
percentuale di spettatori che ha scelto di guardare, **durata media di
visualizzazione**, **percentuale media vista**, like, e risultati dei sondaggi
post-visione.

**Dichiarato da TikTok** (Newsroom e Transparency Center): interazioni utente,
informazioni sul video, impostazioni di dispositivo e account — con
quest'ultimo gruppo esplicitamente **pesato meno**. Un indicatore forte, come
finire un video piu lungo dall'inizio alla fine, pesa piu di uno debole.
Dichiarato anche che **numero di follower e successo dei video precedenti non
sono fattori diretti** di raccomandazione. Utile: significa che un account
nuovo non parte svantaggiato per costruzione, e infatti nel campione ci sono
outlier da account con 50, 219, 536 follower.

**Dichiarato da Instagram** (Mosseri, gennaio 2025): i tre segnali principali
sono **watch time, like e sends**, da leggere come medie per reach, con i sends
piu pesanti sul contenuto mostrato a chi non ti segue.

**Non dichiarato da nessuna piattaforma**, per quanto ripetuto ovunque: la
regola dei "primi 3 secondi", il peso dei loop, le soglie di retention al 65% o
50%, il numero magico di hashtag. Nella pagina ufficiale di YouTube sugli
Shorts non compaiono ne hook, ne loop, ne requisiti di durata.

**Cosa ne segue per noi.** La dimensione `hook` non va tolta dalla rubrica, ma
la sua giustificazione va cambiata. Non e "la piattaforma premia i primi 3
secondi" — non lo dice nessuno. E che *"percentuale media vista"* e un segnale
ufficiale, e un attacco che fa scorrere via la gente abbassa quella percentuale
per pura meccanica. La regola resta, l'argomento diventa onesto.

Stesso discorso per la chiusura secca: non serve invocare un segnale
"engagement" che nessuno ha dichiarato. Bastano i secondi morti in coda, che
abbassano la percentuale vista e basta.

---

## 6. Mercato e concorrenti

Fuori dalla viralita, riguarda il posizionamento sul mercato USA.

**Strumenti automatici.** OpusClip sta tra 15 e 29 dollari al mese e conta i
**minuti sorgente**, non le clip prodotte: un podcast da un'ora consuma
sessanta crediti a prescindere da quante clip ne escono. Vizard offre circa
quattro volte i minuti per dollaro e apre le API gia al piano Creator, dove
OpusClip le tiene dietro un contratto Business. Klap sta sui 14-29 dollari ed e
il piu citato per il clipping da podcast.

Segnale ricorrente dalle recensioni: i creator singoli riportano buon ritorno,
**le agenzie riportano credit shock e prezzi di ricarica opachi.** OpusClip non
pubblica il prezzo dei pacchetti di ricarica ne dei posti aggiuntivi. Per chi
lavora a volume e un rischio di budget concreto.

**Prezzi delle agenzie di clipping.** Tre modelli: retainer mensile a volume
fisso, prezzo per clip, oppure campagna a budget con obiettivo di view. Le
comparative indicano un pavimento intorno ai **3.000 dollari** per un servizio
gestito di clipping da podcast.

**CPM per verticale**, per il modello pay-per-view: finanza e crypto 4-6
dollari ogni mille view, SaaS e B2B tech 3-5, salute e fitness 2-4, lifestyle
1,5-3. Le campagne premium arrivano a 5-10.

**Attenzione alle fonti.** Praticamente ogni numero qui sopra e pubblicato da
un'agenzia o da una piattaforma che vende il servizio che sta quantificando. Le
stime di guadagno dei clipper variano di un ordine di grandezza tra fonti — da
"0-80 dollari il primo mese" a "10-30mila al mese". L'unica affermazione
corroborata in modo consistente e la meno lusinghiera: **la maggior parte delle
clip non supera la soglia minima di view per essere pagata.**

**Dove stiamo noi.** Il differenziatore resta la selezione editoriale umana con
le sei dimensioni, contro il "ci pensa l'AI" di tutti gli altri. Il confronto
sul prezzo pero e brutale: se OpusClip costa 29 dollari al mese, il valore va
argomentato sulla qualita della selezione e sull'attribuzione al cliente, non
sul volume. Il fatto che le agenzie stiano su un pavimento da 3.000 dollari
dice che il mercato quel valore lo riconosce — ma a chi vende un servizio
gestito, non uno strumento.

---

## Riepilogo dei cambiamenti proposti

| # | Dove | Cambiamento | Fondatezza |
|---|---|---|---|
| 1 | `validate.py:42,46` | `MAX_DURATION` 75 -> 180; `SHORTS_LIMIT` 60 -> 180; aggiungere `COPYRIGHT_RISK_LIMIT = 60` | Alta — documentazione ufficiale |
| 2 | `SKILL.md`, `viral-heuristics.md` | Togliere "oltre i 60 si perde Shorts" da entrambi | Alta — stessa fonte |
| 3 | `viral-heuristics.md` | Zona buona 20-45 -> 30-90, mediana osservata 55 | Media — 16 durate, nessun controfattuale |
| 4 | `SKILL.md`, scrittura hook | Aggiungere la seconda strategia: riassunto in terza persona del punto d'arrivo, numeri compresi | Media — forma piu frequente su 31 |
| 5 | `viral-heuristics.md` | Rimotivare la dimensione hook su "percentuale media vista", non sui "primi 3 secondi" | Alta — segnale dichiarato |
| 6 | render | Nessun effetto sonoro; taglio secco in coda; grading leggero | Media |
| 7 | render | Valutare lo split screen host/ospite sul materiale a due voci | Bassa — 7 casi su 31, nessun confronto |

I punti 1, 2 e 5 correggono affermazioni fattuali sbagliate o mal motivate: si
possono applicare subito. I punti 3, 4, 6 e 7 cambiano il giudizio editoriale o
il render, e andrebbero decisi da chi seleziona — non dedotti da 31 clip
altrui.

**Il minimo di 20 secondi non si tocca:** e l'unica soglia della rubrica che i
dati esterni confermano invece di contraddire.
