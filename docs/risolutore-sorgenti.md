# Specifica — risolutore delle sorgenti

> **Cosa contiene questo file.** La specifica del risolutore delle sorgenti, scritta il 28
> settembre 2026 insieme alla [roadmap](roadmap.md) (fase 1, voce 4). Dice **cosa** serve e
> **perche**, non impone la forma del codice: nomi e struttura seguono CLAUDE.md. Quando il
> risolutore esiste, le decisioni stabili passano in CLAUDE.md, le misure in PROGRESS.md, e
> questo file resta come storia del progetto.
>
> **Stato, 28 settembre 2026:** passo 1 fatto — `radar/sorgenti.py`, a secco, con i test in
> `tests/test_sorgenti.py`. Risolve da solo il **64,6%** delle schede lavorabili, sotto il 75%
> chiesto qui; il perche', categoria per categoria, sta in PROGRESS.md. Sulla prima domanda
> aperta c'e' una misura: la durata minima da 10 a 2 minuti sposta 5 campagne su 192.

## Il problema

Il pulsante «prepara» del pannello presuppone che una campagna corrisponda a **un link e un video**. Le campagne vere non sono fatte cosi. Conteggio del 2026-09-28 sulle **184 campagne lavorabili che hanno una scheda** (`radar/assegnazioni.json` con `lavorabile: si`, incrociato con `radar/radar_dettagli.json` e `radar/briefs/`). Per ogni campagna si conta la sorgente piu facile che offre, cercata prima nei `materiali` della scheda (esclusi gli `example`) e poi nel brief:

| Sorgente principale | Campagne | Quota |
|---|---|---|
| Cartella Google Drive | 42 | 23% |
| Google Doc (con dentro i link al materiale) | 35 | 19% |
| Video YouTube (in 15 campagne piu video distinti) | 28 | 15% |
| Canale YouTube ("clippa qualsiasi video del canale") | 26 | 14% |
| Nessun link riconoscibile | 18 | 10% |
| WeTransfer | 9 | 5% |
| File Drive singolo | 7 | 4% |
| VOD Twitch o Kick | 7 | 4% |
| File video diretto (.mp4, .mov) | 5 | 3% |
| Altro (cartella su un sito, Dropbox, Frame.io) | 7 | 4% |

Le altre 66 campagne lavorabili non hanno la scheda di dettaglio e sono fuori dal conteggio. Il conteggio usa regex sui link: e un ordine di grandezza, non una misura esatta. Il primo passo dell'implementazione (sotto) lo sostituisce con un numero vero.

**Il caso che il pannello gestisce oggi copre circa il 15% delle campagne. Un risolutore che espande Drive, Doc, YouTube (video e canali), Twitch, Kick, Dropbox e file diretti arriverebbe a circa l'83%.**

## L'obiettivo

Il clic sul pannello passa da "link → clip" a "campagna → elenco di video → clip":

1. **Risolvi:** dalla campagna esce l'elenco dei video candidati, ciascuno con titolo, durata, data, lingua se nota, provenienza, e se e gia stato lavorato.
2. **Scegli:** una preselezione meccanica, piu Claude quando il brief pone vincoli sul sorgente. L'utente conferma con un clic, o lascia passare in automatico.
3. **Accoda:** un lavoro per video, uno dopo l'altro, tutti legati alla stessa campagna.
4. **Non blocca mai:** una sorgente che non si risolve finisce in "da risolvere a mano", con il motivo, e le altre campagne vanno avanti.

Criterio di successo: nel giro a secco sulle 184 schede, **almeno il 75% delle campagne risolte in automatico** in almeno un candidato valido, e **nessun esempio scambiato per sorgente** sui casi di prova noti.

## Cosa c'e gia e va riusato

- `dashboard/stato.py`, `sorgente_campagna()`: trova il link del video nei materiali e poi nel brief, **in quest'ordine**. L'ordine nasce da un errore gia pagato: su Acme Show un esempio (`1P5Y7OcRyz8`) veniva scelto al posto dell'episodio (`WyCRc1TH7ek`). Il risolutore ne e l'estensione naturale: stesso ordine delle fonti, ma restituisce **tutti** i candidati invece del primo.
- `ingest.py`, `elenca_cartella_drive()` e `rifiuta_cartella_drive()`: leggono l'elenco di una cartella Drive pubblica (vista `embeddedfolderview`) e si fermano mostrando i file. Il risolutore usa l'elenco invece di fermarsi.
- `ingest.py`: download a due fasi, proxy, rilevamento di un sorgente gia scaricato. Lo scaricamento dei file Drive funziona gia (consegna Northwind Podcast E106 da cartella Drive).
- `campagna.py`: `stage()` copia il brief nella cartella di lavoro, `link_a_scadenza()` riconosce i link di trasferimento, `scarica_materiali()` prende gli allegati immagine.
- `automatico.py --campagna --sorgente`: la catena intera per **una** sorgente, con cache per fase e verifiche meccaniche dopo ogni risposta di Claude. La coda la chiama una volta per ogni video.
- `dashboard/server.py`, `passi_lavoro()`: le azioni `prepara`, `render` e `tutto`. Va conservato il principio del pannello: **nessuna rotta che pubblica o invia**. Il risolutore legge e prepara soltanto.
- Misura utile da PROGRESS.md: `ffprobe` su un indirizzo http legge larghezza, altezza, codec e fps in circa 5 secondi, senza scaricare il file. Serve per avere la durata dei file Drive e dei file diretti.

## Il modello dei dati

Un file per campagna, per esempio `radar/sorgenti/<campagna_id>.json`, rigenerabile (una cache, non una decisione):

```json
{
  "campagna_id": "…",
  "risolto_il": "2026-09-28T20:00:00",
  "impronta": "hash di materiali + brief",
  "esito": "automatico | con_scelta | a_mano",
  "motivo": "testo, solo se a_mano",
  "candidati": [
    {
      "id_sorgente": "yt:WyCRc1TH7ek | drive:<file_id> | url:<sha1>",
      "url": "…",
      "tipo": "youtube_video | youtube_canale | drive_file | twitch | kick | dropbox | file",
      "da": "materiali | brief | doc:<doc_id> | cartella:<folder_id> | canale:<url>",
      "titolo": "…",
      "durata_s": 3540,
      "data": "2026-09-20",
      "lingua": "en",
      "gia_lavorato": false,
      "forse_esempio": false,
      "preselezionato": true,
      "perche": "il piu recente sopra i 10 minuti"
    }
  ]
}
```

`id_sorgente` e la chiave per sapere se un video e gia stato lavorato: va registrato nella cartella di lavoro (per esempio in `meta.json`), cosi il controllo funziona anche tra campagne diverse che puntano allo stesso video.

## I risolutori, tipo per tipo

| Tipo | Come si riconosce | Come si espande | Note |
|---|---|---|---|
| Video YouTube | `youtube.com/watch`, `youtu.be/`, `/live/` | `yt-dlp --dump-json --no-download` per titolo, durata, data, lingua | Piu video distinti nello stesso brief: tutti candidati |
| Canale YouTube | `youtube.com/@…`, `/channel/`, `/c/` | `yt-dlp --flat-playlist --playlist-end N` sulla scheda `/videos` (e `/streams` se il brief parla di live) | Escludere gli Shorts; N configurabile (per esempio 15) |
| Cartella Drive | `drive.google.com/drive/(u/N/)folders/` | `elenca_cartella_drive()`, ricorsiva con profondita massima 2; tenere solo le estensioni video | Durata con `ffprobe` sull'indirizzo del file |
| File Drive | `drive.google.com/file/d/`, `open?id=`, `uc?id=` | Come fa gia `ingest.py` | — |
| Google Doc | `docs.google.com/document/d/<id>` | Esportazione pubblica `…/export?format=txt`, estrazione dei link, poi risoluzione di quei link (profondita 1). Fogli: `…/export?format=csv` | Il Doc e un indice, non un video; se non e pubblico → a mano |
| Twitch / Kick | `twitch.tv/videos/`, `kick.com/…` | `yt-dlp --dump-json` sui VOD | Verificare il supporto di yt-dlp per Kick; un canale Twitch → ultimi VOD |
| Dropbox | `dropbox.com/` | `dl=1` oppure yt-dlp | — |
| File diretto | `.mp4`, `.mov`, `.m4v`, `.webm` | `ffprobe` sull'URL | — |
| WeTransfer, Frame.io, cartella su un sito, nessun link | — | **Non si espandono**: esito `a_mano` con il motivo | I link WeTransfer scadono: il motivo lo deve dire |

**Esempi contro sorgenti.** E la trappola gia pagata con Acme Show. Regole:
- i `materiali` di tipo `example` non sono mai candidati;
- i link del brief vicini a parole come *example, reference, inspiration, like this, viral clip* si marcano `forse_esempio: true` e **non si preselezionano**;
- se dopo i filtri restano candidati ambigui, l'esito e `con_scelta` e decide Claude leggendo il brief, oppure l'utente.

## La scelta

**La parte meccanica la fanno gli script** (constatano, non decidono):
- durata minima configurabile (per esempio 10 minuti);
- scarto dei video con `gia_lavorato: true`;
- lingua italiana o inglese, quando il dato c'e;
- ordine: i piu recenti prima (le campagne premiano il materiale fresco), poi i piu lunghi;
- **tetto per clic**, per esempio 2 video, perche la CPU e una sola e il render e sequenziale per scelta misurata (`--jobs 2` e piu lento del 7,7%).

**La parte editoriale la fa Claude**, come nel resto della pipeline. Quando il brief pone vincoli sul sorgente ("solo gli episodi dopo il 1° settembre", "solo l'episodio 12", "non le live"), la fase `regole` di `automatico.py` li scrive gia leggendo il brief. L'estensione e un campo in piu delle regole, per esempio `sorgenti_ammesse`, che il risolutore applica prima della preselezione. Una regex che prova a capire il brief sarebbe una decisione presa da uno script, e il repository lo vieta.

## La coda

- Un lavoro per ogni candidato confermato: `automatico.py --campagna <id> --sorgente <url>`, oppure `prepare` da solo se l'utente vuole guardare prima del render.
- **In sequenza**, uno alla volta.
- Uno stato per lavoro (in attesa, in corso, fatto, fallito con il motivo) in un file leggibile dal pannello.
- Un lavoro fallito non ferma gli altri.
- La ripresa dopo un'interruzione usa le cache per fase che `automatico.py` ha gia.

## Il pannello

- Sulla riga della campagna, il pulsante **«Risolvi»** mostra l'elenco dei candidati con le caselle gia spuntate secondo la preselezione, il motivo accanto a ciascuna casella non spuntata, e il pulsante **«Avvia»**.
- Una sezione **«Da risolvere a mano»** con il motivo, il link al brief e un campo dove incollare il link diretto, che rientra nel flusso normale.
- Lo stato della coda, lavoro per lavoro.

## Il radar

Un campo in piu per campagna, `facilita_sorgente` (`automatico`, `con_scelta`, `a_mano`), calcolato dal risolutore e mostrato come filtro. **Nei primi mesi conviene lavorare solo le campagne `automatico`.**

## Fuori perimetro

Download da WeTransfer e Frame.io, sorgenti dietro login, cartelle Drive private, siti con pagine generate in JavaScript. Tutti finiscono in "da risolvere a mano".

## Collaudo

Casi di prova reali, gia nel catalogo o gia lavorati:
- **Northwind Podcast:** cartella Drive con gli episodi E101-E106. Deve elencarli tutti e segnare E106 come gia lavorato.
- **Acme Show:** trappola esempio contro episodio. Deve preselezionare `WyCRc1TH7ek` e non `1P5Y7OcRyz8`.
- **Una campagna "canale YouTube",** per esempio Michael Sartain. Deve dare gli ultimi video lunghi, senza gli Shorts.
- **Una campagna con Google Doc,** per esempio Matt Hazen. Deve seguire i link del Doc.
- **Una campagna Twitch** e **una WeTransfer**: la seconda deve finire in `a_mano` con il motivo.

Numero da produrre e scrivere in PROGRESS.md: la quota di campagne `automatico`, `con_scelta` e `a_mano` sulle schede lavorabili, dal giro a secco.

## Ordine di implementazione

1. **Risolutore da riga di comando, a secco**, su tutte le schede lavorabili: produce i file `sorgenti/` e un riepilogo con le quote. Nessun download. E il passo che conferma o smentisce il conteggio qui sopra.
2. **La coda**, con `automatico.py` per ogni lavoro.
3. **Il pannello:** «Risolvi», «Avvia», «Da risolvere a mano».
4. **Il campo `facilita_sorgente` nel radar.**

Ogni passo si puo usare da solo: dopo il primo si sa gia quali campagne scegliere.

## Domande aperte per l'utente

- La durata minima di un sorgente: 10 minuti va bene?
- Quanti video per clic: 2?
- Dopo il clic su «Avvia», il render parte da solo (`tutto`), o ti fermi a guardare le scelte (`prepara`)?
