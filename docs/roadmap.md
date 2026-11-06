# Roadmap — modello

> **Cosa contiene questo file.** Un piano a fasi con date e cancelli, nella forma che il
> pannello legge (`dashboard/stato.py`, `stato.roadmap()`): titoli delle fasi
> `## Fase N — nome (periodo)`, voci numerate in grassetto, e le tabelle `## Scadenze` e
> `## Cancello`. E' un esempio: le date e le soglie si cambiano qui, e il pannello le
> riprende senza copia.

## Fase 1 — avvio (1 - 14 gennaio)

1. **Scegliere dal radar una campagna.** Sorgente semplice, invii aperti, budget residuo,
   e paga sulle piattaforme dove gli account ci sono.
2. **Produrre la prima consegna.** `run.py prepare`, la scelta dei momenti, `run.py render`.
3. **Aprire gli account del gruppo e lasciarli scaldare.** Sette giorni di sola presenza
   (`publish/ritmo.py`), poi 1, 2 e 3 post al giorno.

## Fase 2 — distribuzione (15 gennaio - 15 febbraio)

1. **Pubblicare col calendario.** Due clip al giorno per account, gli orari sul fuso di chi
   guarda.
2. **Inviare ogni post alla campagna su Whop** e leggere i numeri dal pannello.

## Fase 3 — crescita (da marzo)

1. **Un gruppo per nicchia.** Il volume viene dal numero di account, non dal ritmo.

## Scadenze

Le date che il pannello mette in «Oggi». Una riga si chiude scrivendo l'esito
nell'ultima colonna.

| Data | Cosa | Esito |
|---|---|---|
| 2027-01-14 | Bilancio della fase 1 | |
| 2027-02-15 | Cancello della fase 2 | |

## Cancello

La chiave nella seconda colonna e' quella con cui il pannello calcola la condizione.

| Condizione | Chiave | Soglia |
|---|---|---|
| Views mediane per post, su almeno una piattaforma | `views_mediane` | 1000 |
| Primo pagamento incassato, in dollari netti | `incassato` | 0.01 |
