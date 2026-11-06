# Rubrica di selezione dei momenti

Come decidere se un tratto di video puo reggere da solo su TikTok, Reels o Shorts.

Non e una formula: e un insieme di criteri per rendere il giudizio ripetibile e
discutibile. Se una clip viene scartata, deve essere possibile dire *perche*.

## Le sei dimensioni

Ognuna su tre livelli: **debole / solido / forte**. Tre livelli e non dieci perche
la differenza tra 7 e 8 e inventata, quella tra debole e forte no.

**Sono tutte proprieta osservabili leggendo il testo**, non previsioni su come si
comportera un pubblico che non conosciamo. E una distinzione che conta: "quanto verra
condiviso" non e valutabile, "contiene un'affermazione su cui si puo dissentire" si.
Se ti accorgi di star indovinando il comportamento di qualcuno, torna a chiederti cosa
c'e nel testo.

### 1. Hook — l'attacco della clip

La domanda e una sola: **qualcuno che sta scorrendo resta invece di continuare?** Non e
"i primi 3 secondi fermano lo scroll" — nessuna piattaforma dichiara una regola simile.
La ragione e piu indiretta e piu solida: YouTube conta ufficialmente la *percentuale
media vista*, e un attacco che fa scorrere via abbassa quella percentuale per pura
meccanica, prima ancora che il resto della clip conti qualcosa.

Due strategie, non una giusta e una sbagliata. **Qui si giudica un attacco; le forme
concrete in cui si scrive la frase impressa** — cinque, che sono queste due viste da chi
scrive — **e i suoi limiti di lunghezza e di accento stanno in [SKILL.md](../SKILL.md),
«Come si scrive un hook».** Un elenco solo, in un posto solo: due liste della stessa cosa
divergono al primo che ne aggiorna una.

- **Trattenere.** Domanda diretta, affermazione netta, numero concreto, contraddizione
  del senso comune, o una frase che apre un ciclo che il cervello vuole chiudere
  ("un istante prima di andare a letto, arriva un segno").
- **Dichiarare il risultato.** Un riassunto in terza persona del punto d'arrivo, scritto
  da chi monta — non la prima frase del parlato — che dice *come va a finire* prima che
  la clip lo mostri ("ha chiesto 5 miliardi per scherzo, e gliel'hanno dato per davvero").
  Funziona quando il risultato stesso e sorprendente: la clip non serve a sapere *cosa*
  succede, serve a sentire *come*.

Livelli:

- **forte** — una delle due forme sopra, netta
- **solido** — situazione chiara e incuriosente, ma senza tensione immediata
- **debole** — preamboli, ringraziamenti, "allora, dunque", contesto prima del punto

Un hook debole penalizza la clip: chi scorre non arriva al buono. Se il momento e valido
ma l'attacco no, **si sposta l'inizio piu avanti** invece di tenere il preambolo — e
quasi sempre basta quello. Un hook che resta debole non e pero un motivo per scartare:
peggiora la clip, non la annulla.

### 2. Autosufficienza — **e l'unica che decide**

**Si capisce senza aver visto il resto del video?**

- **forte** — completamente autonoma
- **solido** — un dettaglio resta oscuro ma non impedisce di capire
- **debole** — servono nomi, fatti o situazioni spiegati altrove

Le altre cinque descrivono la clip; questa dice se la clip esiste. Sotto `solido` non si
consegna, per quanto il momento sia bello.

Il difetto piu comune e il pronome orfano: "e quando *lui* me l'ha detto...". Se il
soggetto e stato nominato due minuti prima, la clip e monca.

Il secondo, meno evidente e piu frequente di quanto sembri, e **la clip troppo corta**:
non le manca un nome, le manca la forma. Premessa, sviluppo e chiusura in tredici
secondi non ci stanno, e quello che resta e un frammento che sembra completo solo a chi
ha gia visto il video. Vedi il minimo di durata fra le regole pratiche.

### 3. Arco emotivo

**C'e un movimento, o e una linea piatta?**

- **forte** — tensione e scioglimento, domanda e rivelazione, solennita e battuta
- **solido** — una progressione riconoscibile ma tenue
- **debole** — informazione distribuita in modo uniforme, per quanto interessante

"Interessante" non basta. Serve che qualcosa **cambi** tra l'inizio e la fine.

### 4. Concretezza

**Storie, numeri ed esempi battono i ragionamenti astratti.** Sempre.

- **forte** — una scena che si puo vedere, una cifra, un oggetto, un gesto
- **solido** — un esempio, ma raccontato genericamente
- **debole** — solo concetti

### 5. Attrito

**Contiene un'affermazione su cui si puo dissentire?**

- **forte** — opinione controcorrente, ammissione scomoda, tensione irrisolta
- **solido** — qualcosa di sorprendente ma su cui si e tutti d'accordo
- **debole** — nessuno avrebbe motivo di rispondere

Formulato cosi si vede leggendo. "Quanto verra commentato" non si vede: e una previsione.

Attrito non vuol dire *rissa*: una clip che fa arrabbiare senza dare niente in cambio
raccoglie commenti e perde il pubblico.

### 6. Carica

**Il momento colpisce, o e solo ben costruito?**

- **forte** — c'e paura, sollievo, imbarazzo, meraviglia, indignazione: qualcosa si sente
- **solido** — interessante, ma resta un fatto raccontato
- **debole** — informazione neutra, per quanto ben confezionata

E la dimensione piu facile da perdere di vista, perche tutte le altre si possono
verificare con una checklist e questa no.

**Non e una soglia.** Una clip tiepida ma chiara e completa si consegna: dire "questa non
colpisce abbastanza" sarebbe una previsione sul pubblico, cioe proprio quello che questa
rubrica dice di non fare. Il punteggio serve a chi pubblica per decidere l'ordine, non a
decidere se la clip esce.

La concretezza e un **indizio** di carica, non la carica stessa: un numero preciso aiuta
a far sentire qualcosa, ma non basta da solo.

## I bordi non sono una dimensione

**Inizia a inizio frase e finisce su una chiusura?** Va verificato, ma non votato: se i
bordi sono sbagliati **si correggono**, non si assegna loro un punteggio. Metterli sulla
stessa scala delle altre confonde un difetto riparabile con un giudizio sul momento.

Stanno nella checklist prima del render:

- [ ] l'attacco e a inizio frase, non a meta pensiero
- [ ] la fine cade su una chiusura, meglio se una battuta o una ripetizione secca
- [ ] entrambi posati su un **confine di segmento** (`transcript.json` -> `segments`), mai
      su un timestamp arrotondato della vista e nemmeno sulla fine di una parola: una
      parola intera puo comunque amputare la frase. Lo controlla `validate.py`

Mezzo secondo puo trasformare un finale monco in una chiusura secca. E gratis: farlo
sempre.

## La soglia: quando una clip e valida

Nessun tetto al numero di clip e **nessun minimo di viralita**: cinque o trenta lo decide
il materiale. **Il cancello e uno solo, `autosufficienza` almeno solida**; le altre cinque
dimensioni descrivono la clip e finiscono in `clips.md`, non decidono se esiste.

**Perche la `carica` non e un cancello.** Scarterebbe momenti utilizzabili perche
"tiepidi" — e tiepido rispetto a cosa non e verificabile: e la previsione sul pubblico che
questa rubrica vieta dalla prima riga.

**E nemmeno l'hook lo e**: un attacco debole e un difetto riparabile, si sposta l'inizio
piu avanti. Se resta debole peggiora la clip, non la annulla.

Quello che si scarta davvero:

- non si capisce senza il resto del video, nemmeno spostando i bordi
- e un segmento sponsorizzato, o un richiamo interno al canale
- l'audio e coperto per buona parte del tratto
- e lo stesso momento di un'altra clip, tagliato peggio
- non ci sta un pensiero intero: sotto il minimo di durata

**Il motivo si scrive lo stesso**, anche per quello che non passa: "se una clip viene
scartata, deve essere possibile dire *perche*".

## Regole pratiche

**Durata: il minimo e 20 secondi, e il motivo non e il formato.** Le clip da 10-15
secondi **non risultano autoconclusive** — e il difetto piu ricorrente di questa skill,
verificato su materiale gia consegnato: in quindici secondi non ci stanno premessa,
sviluppo e chiusura, e quel che resta sembra completo solo a chi ha in testa il video
attorno. Se un momento buono dura tredici secondi, **la prima cosa da provare e allargare
i bordi**; se allargando si prende solo della coda, non era una clip.

Zona buona **20-45 secondi**, e sopra i 40 va benissimo quando il momento lo chiede: non
e un tetto, Shorts accetta fino a 3 minuti. Sopra il minuto `validate.py` segnala solo di
verificare che non ci sia musica di terzi nel sottofondo del sorgente — sopra quella
soglia una rivendicazione di copyright attiva blocca lo Short a livello globale, non e
un limite di formato. Un tratto valido di un minuto e mezzo si stringe togliendo le
divagazioni interne.

**La chiusura conta quasi quanto l'attacco.** Le clip migliori finiscono su una
frase che chiude il cerchio. Una ripetizione secca ("Vuoto totale?" "Vuoto totale.")
vale piu di dieci secondi di spiegazione aggiuntiva.

**Non tenere piu varianti dello stesso momento.** Se due candidati si sovrappongono,
si sceglie quello con i bordi migliori e si scarta l'altro.

**Le keyword vanno scelte rare.** Sono le parole che i sottotitoli colorano, e la parola
attiva del karaoke ha gia il suo colore: se la keyword ricorre di continuo dentro la
clip, prima o poi finisce accanto a quella accesa e a schermo restano due colori saturi
affiancati, che annullano l'effetto "una parola alla volta". E la stessa ragione per cui
nell'hook si marca **una** parola sola. Caso reale: `macchinari` scelta come keyword in
una clip che parla di macchinari per trenta secondi.

## Cosa escludere sempre

- **Segmenti sponsorizzati.** Sui video lunghi sono un pattern fisso e vanno
  riconosciuti dal testo: cambio di tono, chiamata all'azione, nome di un prodotto,
  "codice sconto", "link in descrizione", "QR code". Da marcare come zona vietata
  prima ancora di valutare i momenti.
- **Richiami interni al canale** ("come vi dicevo nel video di ieri"): rompono
  l'autosufficienza.
- **Passaggi in cui l'audio e coperto** da musica o rumore.

## Errori tipici da evitare

**Scegliere il momento piu importante invece del piu clippabile.** La rivelazione
centrale di un video spesso richiede tutto il contesto che la precede: e il momento
migliore del video e una pessima clip.

**Fidarsi dei picchi audio.** Un picco di energia segnala che *succede qualcosa*, non
che sia interessante. Sono un indizio, non un criterio.

**Prendere il primo taglio pulito disponibile.** I confini vanno scelti per senso e
poi agganciati alla parola piu vicina, non il contrario.

**Confondere "mi piace" con "funziona".** Il criterio non e se il momento piace a
noi, ma se regge davanti a qualcuno che non ha chiesto di vederlo.
