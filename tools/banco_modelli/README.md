# Banco dei modelli

Serve quando si vuole cambiare il modello o l'effort di uno dei chiamanti di
`claude -p`: `automatico.py` (scelta delle clip), `prepara.py` (didascalie),
`classifica.py` (nicchie). Il banco dice se il modello nuovo fa lo stesso lavoro
**prima** che lo faccia in produzione. Il perche' delle regole sta in CLAUDE.md
(«Modello ed effort di ogni chiamata a Claude stanno nel codice»), le cifre delle
prove gia' fatte in PROGRESS.md.

Finche' nessuno cambia `MODELLO`, non succede niente: l'id e' scritto per intero e
`tests/test_modelli.py` lo controlla. Il banco serve il giorno in cui lo si vuole
cambiare, o in cui un modello viene ritirato.

## Le tre prove

| Prova | Cosa misura | Materiale |
|---|---|---|
| `decisioni` | la scelta delle clip, passi 4-5 della skill | Northwind Podcast E106 |
| `didascalie` | `clips.md` e `publish.json`, passo 8 | Tracee Ellis Ross (Contoso) |
| `didascalie_candid` | come sopra, su un brief con quattro tag obbligatori | Northwind Podcast E106 |
| nicchie (`nicchie.py`) | la lettura delle campagne del radar | le prime 100-200 schede |

## Come si usa

Dalla radice del progetto, con il Python del `.venv`:

```powershell
# 1. un banco per variante: una copia del progetto senza la risposta
.venv\Scripts\python.exe tools\banco_modelli\banco.py decisioni --nome dec_opus55_high
.venv\Scripts\python.exe tools\banco_modelli\banco.py decisioni --nome dec_nuovo_high

# 2. l'agente, con lo stesso messaggio della produzione
.venv\Scripts\python.exe tools\banco_modelli\agente.py $env:TEMP\banco_modelli\dec_opus55_high claude-opus-5-5 high
.venv\Scripts\python.exe tools\banco_modelli\agente.py $env:TEMP\banco_modelli\dec_nuovo_high <id-nuovo> high

# 3. il confronto
.venv\Scripts\python.exe tools\banco_modelli\confronta.py decisioni $env:TEMP\banco_modelli\dec_opus55_high $env:TEMP\banco_modelli\dec_nuovo_high -v

# nicchie: niente banco, il prompt e' gia' senza strumenti
.venv\Scripts\python.exe tools\banco_modelli\nicchie.py claude-sonnet-5-5 medium 4
.venv\Scripts\python.exe tools\banco_modelli\nicchie.py <id-nuovo> medium 4
.venv\Scripts\python.exe tools\banco_modelli\confronta.py nicchie --contro claude-sonnet-5-5_medium --prima-di 2026-09-29 -v

# alla fine
.venv\Scripts\python.exe tools\banco_modelli\banco.py --elimina
```

### Il motore agy (Gemini e gli altri modelli dell'abbonamento Google)

Con `--motore agy` la stessa prova gira con Antigravity CLI. Il modello e' lo slug
di `agy models` senza l'effort (`gemini-4-argon`, `claude-opus-5-5`), l'effort e'
`low`, `medium` o `high`:

```powershell
.venv\Scripts\python.exe tools\banco_modelli\agente.py $env:TEMP\banco_modelli\dec_argon_high gemini-4-argon high --motore agy
.venv\Scripts\python.exe tools\banco_modelli\nicchie.py gemini-4-argon medium 4 --motore agy
```

- **Per la prova alla cieca servono tre giri:** Opus via `claude` (la produzione),
  Opus via agy (stesso modello, motore diverso) e Argon via agy. Senza il secondo,
  una differenza fra il primo e il terzo puo' venire dal motore e non dal modello.
- **agy legge `AGENTS.md`, non `CLAUDE.md`:** `banco.py` ne scrive la copia
  pulita. Non legge il CLAUDE.md utente, che pero' parla dell'ambiente, non del
  lavoro.
- **I comandi li permettono le regole di agy**, in
  `%USERPROFILE%\.gemini\antigravity-cli\settings.json`: le decisioni lanciano
  `windows.py` e `validate.py`. Un comando negato non ferma il giro: finisce in
  `negati` dentro `_esito.json`, e prima di leggere un giro andato male si guarda
  li'. Mai `--dangerously-skip-permissions`.
- **agy non da' il costo**, solo token e secondi: la spesa si legge sulla quota,
  con `agy -p /usage` prima e dopo. Gemini 4 Argon ha una quota settimanale sua.

I banchi e i risultati stanno in `%TEMP%\banco_modelli\`, **fuori dal progetto
apposta**: Claude legge i CLAUDE.md risalendo le cartelle, e da dentro il progetto
leggerebbe quello vero. Le cifre che restano si scrivono in PROGRESS.md e nel
commento sopra `MODELLO`, come le prove di settembre.

**I banchi si tolgono con `--elimina`, non con `rm -rf`.** Dentro c'e' una junction
al `.venv` vero; `shutil.rmtree` toglie il collegamento, un `rm -rf` di Git Bash
potrebbe seguirlo.

## Come si leggono i risultati

1. **Il confronto si fa col modello vecchio rifatto nelle stesse condizioni**, lo
   stesso giorno. Non con i giudizi in uso: su quattro campagne i due modelli
   concordavano fra loro contro la produzione. Per questo ogni prova si lancia
   almeno due volte, modello vecchio e nuovo.
2. **L'accordo con i giudizi in uso favorisce il modello che li ha scritti.** Sulle
   nicchie `--prima-di` conta a parte le campagne decise prima che arrivasse.
3. **I fatti si controllano sulla trascrizione**, a mano: `confronta.py testo
   <cartella>` stampa il parlato di ogni clip. I controlli meccanici hanno promosso
   tutte le varianti di settembre, anche quella che travisava l'ospite in tre
   didascalie su quattordici. L'errore che conta e' quello che non da' errore.
4. **Piu' effort non e' un margine di sicurezza.** Va misurato come un modello in
   piu': sulle nicchie `xhigh` scarta piu' lavorabili di `medium`.
5. Due giri per variante quando la differenza e' piccola: fra un giro e l'altro lo
   stesso modello cambia di qualche campagna.

## Cosa toglie il banco, e cosa no

Toglie quello che e' **specifico del materiale della prova**: le frasi in `SPIE`
(come Whisper sente il nome del programma, i fatti che le didascalie hanno
travisato, quante clip ha la consegna vera), con l'intera sezione `###` o voce
d'elenco che le contiene, e i commenti sopra `MODELLO`, che raccontano le prove.
Lascia le regole generali di CLAUDE.md e della skill, anche quando nascono da una
prova («non copiare `platform` dal modello di forma»): la produzione le vede, quindi
fanno parte delle condizioni da misurare.

Dopo la pulizia `banco.py` cerca ogni spia in tutto il codice e i documenti della
copia, e **si ferma se ne trova una**, prima di spendere un token. Il 1 ottobre 2026
una regola nuova di SKILL.md citava come esempio proprio la correzione che la prova
delle decisioni misura. Se il banco si ferma: si toglie la frase dal file vero, se
non serve li', o si insegna a `pulisci()` a toglierla.

**Quando si aggiunge una prova**, si aggiungono anche le sue spie: le frasi che solo
chi conosce la risposta potrebbe aver scritto.

## Effetti collaterali

- Ogni giro di agente costa quota dell'abbonamento: da uno a quattro dollari a
  listino per le decisioni, uno e mezzo per le didascalie, mezzo dollaro per lotto di
  nicchie. La quota e' la stessa della pubblicazione e della chat: a fine settembre
  una coppia di prove si e' fermata a meta' sul limite.
- Gli agenti del banco leggono il CLAUDE.md utente, quindi attivano task-observer e
  possono scrivere nel log delle osservazioni. Dopo un giro si guarda il log, e una
  voce nata da un banco si legge sapendo che il banco e' una copia (osservazione 139).
