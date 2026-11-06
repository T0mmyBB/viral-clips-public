#!/usr/bin/env python
"""
classifica.py — mette in nicchia le campagne che l'ultimo giudizio non copre piu'.

Il buco che chiude
------------------
Il giro del radar rilegge **tutti** i numeri di **tutte** le campagne: CPM, views,
clipper, budget, stato. Ma la nicchia no: quella sta in `assegnazioni.json`, la scrive
Claude leggendo descrizione e brief, e finche' nessuno legge una campagna nuova quella
campagna **non esiste per nessuna somma** — non e' in una nicchia, non e' nei totali,
compare solo in fondo al report come «da classificare». Il 9 settembre 2026 erano 105 su
627: un sesto del catalogo invisibile in un aggiornamento che si dichiarava completo.

Quindi il giudizio entra nel giro, e resta un giudizio: **lo fa Claude**, come per le
clip. Questo file non decide niente, prepara la lettura e raccoglie la risposta.

Cosa vuol dire «rianalizzare tutto»
-----------------------------------
Ogni campagna viene **considerata** a ogni giro. Non ogni campagna viene **ridecisa**:
si ridecide quella la cui **impronta** e' cambiata, cioe' quella cui e' cambiato
qualcosa che puo' cambiare la risposta.

L'impronta e' fatta di titolo, organizzazione, descrizione, piattaforme, domini dei
materiali e testo del brief. **Non contiene i soldi** — CPM, clipper, residuo, views —
e la distinzione e' il cuore di questo file: quei numeri cambiano tutti i giorni e
ordinano le tabelle, ma la nicchia di una campagna non cambia perche' ieri ha speso
mille dollari. Se entrassero nell'impronta, ogni giro ridarebbe da leggere 600 campagne
per riscrivere le stesse 600 righe.

Da qui una proprieta' che conviene conoscere: una campagna decisa **senza** il brief
(giro con `--salta-brief`) viene ridecisa da sola quando il brief arriva, perche' il
testo del brief entra nell'impronta.

Le assegnazioni vecchie non hanno impronta, quindi il primo giro dopo questa aggiunta le
ridecide tutte: e' la rianalisi completa, una volta, e poi si torna a pagare solo il
cambiamento. `--tutte` la rifa da capo quando si vuole.

Cosa non tocca
--------------
`pagina` e `pagina_nota` (che pagina pretende la campagna) si leggono dal brief per
intero e non da un digest: **si conservano** cosi' come sono. Riscriverle da qui vorrebbe
dire cancellare un giudizio piu' informato con uno meno informato.

Le campagne assegnate che dal catalogo sono sparite restano nel file: non fanno somma
(`tassonomia.quadro()` le ignora perche' non hanno piu' una scheda) e se tornano, il
giudizio c'e' gia'.

Uso
---
    python classifica.py                 # decide il nuovo e cio' che e' cambiato
    python classifica.py --tutte         # ridecide tutto da capo
    python classifica.py --prova         # dice cosa farebbe, non chiama Claude
    python classifica.py --lotto 30      # quante campagne per lettura
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import tassonomia

for _flusso in (sys.stdout, sys.stderr):
    try:
        _flusso.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

HERE = Path(__file__).resolve().parent
DETTAGLI = HERE / "radar_dettagli.json"
BRIEFS = HERE / "briefs"
ASSEGNAZIONI = HERE / "assegnazioni.json"
TASSONOMIA = HERE / "tassonomia.json"
ULTIMA = HERE / "ultima_classifica.json"

# Quante campagne in una lettura sola. Cinquanta stanno larghe nel contesto e
# tengono basso il numero di chiamate; oltre, una risposta troncata costa
# l'intero lotto invece di una fetta.
LOTTO = 50

# Un lotto non dovrebbe superare i due minuti. Il tetto serve a non lasciare il
# giro del radar appeso a una chiamata che non torna.
ATTESA = 20 * 60

# Modello ed effort si scrivono qui e non si ereditano da `settings.json`: quelli
# sono le preferenze della chat, e cambiarle per la chat non deve cambiare il
# giudizio del radar senza che nessuno se ne accorga. Il `--effort` esplicito
# vince anche su `CLAUDE_EFFORT` ereditato da una sessione: misurato.
#
# **Sonnet 5 no, e lo dice una misura** (17 settembre 2026, alla cieca,
# stesse 100 campagne): sulla nicchia si equivalgono (92% e 91% d'accordo con i
# giudizi in uso), su `lavorabile` no — 95% contro 86%, e delle 25 campagne
# lavorabili Sonnet a `high` ne tiene 18, Opus 22. Sonnet scarta quando il brief
# manca e segna `si` campagne in spagnolo. Una lavorabile scartata esce da ogni
# somma senza errore, quindi il risparmio si paga in campagne che non si vedono.
#
# **Opus 5.5 a `medium`** (22 settembre 2026, stesse condizioni, 99 campagne, 23
# lavorabili in uso): `medium` ne tiene 19 in tutti e due i giri, come Opus 5
# `xhigh`, a $0,60 invece di $1,22 e in 130 secondi invece di 340. Opus 5.5 a
# `xhigh` ne tiene 17 e 18: ragionare di piu' lo rende piu' prudente dove il
# creator «non e' identificato», e quella prudenza e' proprio la lavorabile
# scartata. A `low` cala la lingua (90%).
#
# **Sonnet 5.5 a `medium`** (29 settembre 2026, stesse condizioni, 200 campagne,
# 54 lavorabili in uso): ne tiene 46 contro le 42 di Opus 5.5 `medium` rifatto lo
# stesso giorno, nicchia pari (88%), nessuna campagna in lingua sbagliata segnata
# `si` — i due difetti di Sonnet 5 del 17 settembre non ci sono piu' — a $0,63
# invece di $1,31 e in 180 secondi invece di 293. Come per Opus, `high` e `xhigh`
# diventano piu' prudenti e ne tengono meno; `low` non ragiona affatto e segna
# lavorabili campagne di sole clip gia' montate. L'id e' scritto per intero
# perche' l'alias lo sposta la versione del CLI, non una decisione (CLAUDE.md).
MODELLO = "claude-sonnet-5-5"
EFFORT = "medium"

# **La lettura gira fuori dal progetto e senza niente intorno.** Il prompt porta
# tutto quello che serve — istruzioni, vocabolario, schede — e la risposta e'
# testo: strumenti, server MCP, plugin, hook e skill non servono, e CLAUDE.md
# nemmeno. Ma `claude -p` li carica tutti, e CLAUDE.md lo trova risalendo dalla
# cartella di lavoro: dalla radice del progetto una domanda vuota costava 107mila
# token di contesto, cosi' ne costa 10mila. `--bare` li toglierebbe con un flag,
# ma non legge il login dell'abbonamento. Misurato il 17 settembre 2026 sulle
# stesse 100 campagne alla cieca: stessi giudizi (nicchia 95% d'accordo con quelli
# in uso contro 92%, `lavorabile` 94% contro 95%) e un lotto da 50 a $0,52 invece
# di $1,00 a listino. Il conto si rovescia solo sui giri da molti lotti, perche'
# senza il contesto del progetto Opus ragiona (9mila token a lotto) e con il
# contesto no; i giri veri decidono di solito 0-11 campagne.
SNELLO = (
    "--strict-mcp-config",
    "--setting-sources",
    "project,local",
    "--disable-slash-commands",
    "--tools",
    "",
)

# Il vocabolario chiuso: sono i valori che `assegnazioni.json` gia' contiene e
# che `tassonomia.py` sa impaginare. Una parola nuova qui dentro non e' una
# sfumatura in piu', e' una colonna che nessuno sa leggere.
FORME = (
    "parlato_lungo",
    "stream_commentato",
    "evento_reazione",
    "asset_fornito",
    "ugc_da_filmare",
    "musica_sound",
    "slideshow",
    "repost",
    "gameplay_muto",
    "ignoto",
)
LAVORABILE = ("si", "no", "lingua")

# I campi che descrivono **cosa e'** la campagna. Gli altri — cpm, clipper,
# residuo, views, mesi, stato — descrivono quanto rende adesso: cambiano ogni
# giorno e non spostano di un millimetro la nicchia.
CONTENUTO = ("titolo", "organizzazione", "descrizione", "piattaforme")


def carica(percorso: Path, difetto):
    try:
        return json.loads(percorso.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return difetto


def brief_di(campagna: dict) -> str | None:
    percorso = BRIEFS / f"{campagna['id']}.txt"
    if not percorso.exists():
        return None
    return percorso.read_text(encoding="utf-8", errors="replace")


def impronta(campagna: dict, brief: str | None) -> str:
    """Cio' che, cambiando, puo' cambiare la nicchia. Non i soldi."""
    pezzi = [str(campagna.get(c) or "") for c in CONTENUTO]
    pezzi.append(" ".join(sorted(str(m) for m in (campagna.get("materiali") or []))))
    pezzi.append(brief or "")
    return hashlib.sha1(" ".join(pezzi).encode("utf-8")).hexdigest()[:16]


def da_decidere(
    schede: list[dict], assegnate: dict, tutte: bool
) -> list[tuple[dict, str, str | None]]:
    """Le campagne da far leggere, con la loro impronta e il loro brief."""
    fuori = []
    for campagna in schede:
        brief = brief_di(campagna)
        firma = impronta(campagna, brief)
        vecchia = assegnate.get(campagna["id"])
        if not tutte and vecchia and vecchia.get("impronta") == firma:
            continue
        fuori.append((campagna, firma, brief))
    # Prima le campagne che rendono di piu': se un giro viene interrotto a
    # meta', quel che e' stato deciso e' la parte che conta.
    fuori.sort(key=lambda v: -(v[0].get("per_clipper") or 0))
    return fuori


# ------------------------------------------------------------------ la lettura


def vocabolario() -> str:
    """Le nicchie esistenti con le loro sottonicchie: si sceglie qui dentro."""
    tass = carica(TASSONOMIA, {})
    righe = []
    for n in tass.get("nicchie", []):
        sotto = ", ".join(n.get("sottonicchie", [])[:24])
        righe.append(
            f"- {n['nome']}: {n.get('definizione', '')[:220]}\n"
            f"  sottonicchie gia' in uso: {sotto or '(nessuna)'}"
        )
    return "\n".join(righe)


ISTRUZIONI = """\
Sei il classificatore del catalogo di campagne di clipping di questo progetto.

Per ogni campagna qui sotto decidi sette campi. Rispondi **solo** con righe TSV, una per
campagna, separate da tabulazioni, in quest'ordine:

id	nicchia	sottonicchia	forma	lingua	lavorabile	nota

- id: gli 8 caratteri come stampati qui sotto.
- nicchia: una di quelle elencate. Se davvero nessuna calza, scrivi la piu' vicina: una
  nicchia nuova non la sa impaginare nessuno.
- sottonicchia: una di quelle gia' in uso per quella nicchia, oppure una nuova in
  minuscolo-con-trattini se nessuna descrive la campagna.
- forma, cioe' che materiale da' la campagna: {forme}.
- lingua: codice ISO a due lettere del parlato del materiale (en, it, es, de...), `mul`
  se piu' lingue.
- lavorabile: `si` se la pipeline puo' lavorarla, cioe' materiale grezzo e lungo con
  parlato in italiano o inglese (parlato_lungo, stream_commentato, evento_reazione);
  `lingua` se la forma va bene ma il parlato non e' ne' italiano ne' inglese; `no` se la
  forma e' incompatibile — video gia' montati, musica, contenuti da filmare di persona,
  slideshow, repost, gameplay muto.
- nota: massimo 90 caratteri, e solo se dice qualcosa che i campi non dicono gia'.
  Vuota altrimenti (lascia la colonna vuota, non scrivere "-").

Un criterio che viene da quello che questo progetto ha misurato:
- si clippa solo materiale **grezzo e lungo**, dove i quaranta secondi buoni sono
  sepolti. Un video gia' montato al fotogramma non ha niente da estrarre: e' `no`.

Niente intestazione, niente blocchi di codice, niente commenti: solo le righe TSV.

NICCHIE
{nicchie}

CAMPAGNE
{campagne}
"""


def claude() -> str:
    """Su Windows e' `claude.CMD`, e subprocess non applica PATHEXT."""
    trovato = shutil.which("claude")
    if not trovato:
        raise RuntimeError("'claude' non trovato: serve Claude Code installato")
    return trovato


def chiedi(prompt: str, modello: str) -> str:
    """La domanda entra da **stdin**, non dalla riga di comando.

    Un lotto da cinquanta campagne e' un prompt da decine di migliaia di
    caratteri, e su Windows `claude` e' un `.CMD`: la riga di comando passa da
    cmd.exe, che si ferma a 8191 caratteri con «La riga di comando e' troppo
    lunga» — un errore che parla della shell e non dice affatto «il testo va
    passato in un altro modo». Con `-p` senza argomento il prompt si legge dallo
    standard input, e li' non c'e' nessun tetto.
    """
    cmd = [claude(), "-p", "--model", modello, "--effort", EFFORT, *SNELLO]
    esito = subprocess.run(
        cmd,
        cwd=tempfile.gettempdir(),
        input=prompt,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=ATTESA,
    )
    if esito.returncode != 0:
        # Con stderr vuoto la spiegazione, se c'e', sta su stdout, dove `-p`
        # scrive la risposta. Il 17 settembre 2026 sette lotti di fila sono
        # falliti dicendo soltanto «uscito con 1», e la causa non si e' potuta
        # leggere piu' tardi.
        coda = ((esito.stderr or "").strip() or (esito.stdout or "").strip()).splitlines()[-5:]
        raise RuntimeError(f"claude e' uscito con {esito.returncode}: {' / '.join(coda)}")
    return esito.stdout or ""


def leggi_tsv(risposta: str, lotto: dict[str, dict]) -> tuple[list[dict], list[str]]:
    """Le righe valide e i problemi. Una riga storta non invalida le altre."""
    voci, problemi = [], []
    visti = set()
    for riga in risposta.splitlines():
        riga = riga.strip()
        if not riga or riga.startswith(("```", "#", "id\t")):
            continue
        pezzi = [p.strip() for p in riga.split("\t")]
        if len(pezzi) < 6:
            problemi.append(f"riga con {len(pezzi)} colonne: {riga[:70]}")
            continue
        chiave, nicchia, sotto, forma, lingua, lavorabile = pezzi[:6]
        nota = pezzi[6] if len(pezzi) > 6 else ""
        # Gli identificativi sono esadecimali: uno spazio dentro e' un refuso
        # della risposta, non un'altra campagna. Il 16 settembre 2026 `1c5f a61`
        # ha lasciato una campagna senza nicchia fino al giro dopo.
        chiave = "".join(chiave.split())
        campagna = lotto.get(chiave[:8])
        if not campagna:
            problemi.append(f"id {chiave[:8]} non e' di questo lotto")
            continue
        if campagna["id"] in visti:
            problemi.append(f"id {chiave[:8]} deciso due volte")
            continue
        if lavorabile not in LAVORABILE:
            problemi.append(f"{chiave[:8]}: lavorabile='{lavorabile}'")
            continue
        if forma not in FORME:
            problemi.append(f"{chiave[:8]}: forma='{forma}'")
            continue
        visti.add(campagna["id"])
        voci.append(
            {
                "id": campagna["id"],
                "nicchia": nicchia,
                "sottonicchia": sotto,
                "forma": forma,
                "lingua": (lingua or "").lower()[:3],
                "lavorabile": lavorabile,
                "nota": nota[:90],
                "titolo": campagna["titolo"],
            }
        )
    mancanti = [c for k, c in lotto.items() if c["id"] not in visti]
    for c in mancanti:
        problemi.append(f"senza risposta: {c['id'][:8]} {c['titolo'][:50]}")
    return voci, problemi


# ------------------------------------------------------------------- scrittura


def unisci(assegnate: dict, nuove: list[dict], impronte: dict[str, str]) -> list[dict]:
    """Le nuove decisioni sopra le vecchie, **senza** toccare cio' che viene dal brief."""
    for voce in nuove:
        vecchia = assegnate.get(voce["id"], {})
        unita = {
            **vecchia,
            **voce,
            "impronta": impronte[voce["id"]],
            "deciso_il": datetime.now().isoformat(timespec="seconds"),
        }
        # `pagina` e `pagina_nota` si leggono dal brief intero, non dal digest:
        # una decisione piu' informata non si sovrascrive con una meno informata.
        for campo in ("pagina", "pagina_nota"):
            if vecchia.get(campo):
                unita[campo] = vecchia[campo]
        assegnate[voce["id"]] = unita
    return list(assegnate.values())


def salva(assegnazioni: list[dict]) -> None:
    ASSEGNAZIONI.write_text(
        json.dumps(
            {
                "_leggimi": (
                    "Nicchia e sottonicchia di ogni campagna, decise leggendo "
                    "descrizione e brief. Le scrive Claude, non uno script: "
                    "`classifica.py` prepara la lettura e raccoglie la risposta, "
                    "`tassonomia.py --importa` fa lo stesso da un TSV a mano. "
                    "lavorabile: si = la pipeline puo' lavorarla; lingua = la "
                    "forma va bene ma il parlato non e' ne' italiano ne' "
                    "inglese; no = forma incompatibile. `impronta` e' cio' che, "
                    "cambiando, fa ridecidere la campagna al giro dopo: contenuto "
                    "e brief, mai i soldi."
                ),
                "quante": len(assegnazioni),
                "assegnazioni": assegnazioni,
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )


# ----------------------------------------------------------------------- giro


def main() -> int:
    ap = argparse.ArgumentParser(description="Mette in nicchia le campagne nuove o cambiate")
    ap.add_argument(
        "--tutte", action="store_true", help="ridecide ogni campagna, anche quelle non cambiate"
    )
    ap.add_argument(
        "--prova",
        action="store_true",
        help="dice quante ne deciderebbe e si ferma, senza chiamare Claude",
    )
    ap.add_argument("--lotto", type=int, default=LOTTO)
    ap.add_argument(
        "--max-lotti", type=int, default=0, help="si ferma dopo N letture (per provare); 0 = tutte"
    )
    ap.add_argument(
        "--modello", default=MODELLO, help=f"passa --model a claude (predefinito: {MODELLO})"
    )
    args = ap.parse_args()

    schede = [s for s in carica(DETTAGLI, []) if not s.get("errore")]
    if not schede:
        print(
            f"{DETTAGLI.name} vuoto o illeggibile: non c'e' niente da classificare", file=sys.stderr
        )
        return 1
    assegnate = {a["id"]: a for a in carica(ASSEGNAZIONI, {}).get("assegnazioni", [])}
    lavoro = da_decidere(schede, assegnate, args.tutte)

    print(
        f"{len(schede)} campagne nel catalogo, {len(assegnate)} con un giudizio, "
        f"{len(lavoro)} da decidere adesso",
        file=sys.stderr,
    )
    if not lavoro:
        # Niente da fare non e' niente da dire: il report va riscritto lo stesso,
        # perche' i numeri sotto le stesse nicchie sono cambiati.
        tassonomia.rigenera_report(DETTAGLI)
        ULTIMA.write_text(
            json.dumps(
                {
                    "quando": datetime.now().isoformat(timespec="seconds"),
                    "considerate": len(schede),
                    "decise": 0,
                    "problemi": [],
                },
                ensure_ascii=False,
                indent=1,
            ),
            encoding="utf-8",
        )
        return 0
    if args.prova:
        for campagna, _, brief in lavoro[:20]:
            print(
                f"  {campagna['id'][:8]}  {campagna['titolo'][:60]}"
                f"{'' if brief else '   (senza brief)'}",
                file=sys.stderr,
            )
        if len(lavoro) > 20:
            print(f"  ... e altre {len(lavoro) - 20}", file=sys.stderr)
        return 0

    nicchie = vocabolario()
    impronte = {c["id"]: f for c, f, _ in lavoro}
    decise: list[dict] = []
    problemi: list[str] = []
    lotti = [lavoro[i : i + args.lotto] for i in range(0, len(lavoro), args.lotto)]
    if args.max_lotti:
        # Un tetto lo mette solo chi sta provando, e lo dice: il giro normale
        # non lascia campagne indietro in silenzio.
        rimaste = sum(len(l) for l in lotti[args.max_lotti :])
        lotti = lotti[: args.max_lotti]
        if rimaste:
            print(
                f"  (--max-lotti {args.max_lotti}: {rimaste} campagne restano "
                f"per il prossimo giro)",
                file=sys.stderr,
            )

    def leggi_lotto(nome: str, lotto: list) -> list[dict] | None:
        """Chiede un lotto a Claude e salva cio' che torna. None se non ha risposto."""
        nonlocal decise
        testo = "\n\n".join(
            tassonomia.scheda_digest(campagna, brief, assegnate.get(campagna["id"]))
            for campagna, _, brief in lotto
        )
        prompt = ISTRUZIONI.format(forme=", ".join(FORME), nicchie=nicchie, campagne=testo)
        print(f"  {nome}: {len(lotto)} campagne...", file=sys.stderr)
        try:
            risposta = chiedi(prompt, args.modello)
        except (RuntimeError, subprocess.SubprocessError, OSError) as exc:
            # Un lotto perso non e' il giro perso: si scrive cio' che si e'
            # deciso finora e le campagne rimaste tornano al giro dopo, perche'
            # la loro impronta e' ancora diversa da quella salvata.
            problemi.append(f"{nome}: {type(exc).__name__}: {exc}")
            print(f"    fallito: {exc}", file=sys.stderr)
            return None
        voci, guai = leggi_tsv(risposta, {c["id"][:8]: c for c, _, _ in lotto})
        decise += voci
        problemi.extend(f"{nome}: {g}" for g in guai)
        # Si salva **a ogni lotto**, non alla fine. Il catalogo intero e' una
        # dozzina di letture e una decina di minuti: se si scrivesse in fondo,
        # una macchina che si chiude a meta' butterebbe via tutto il lavoro
        # fatto, e al giro dopo lo si ricomprerebbe uguale.
        if voci:
            salva(unisci(assegnate, voci, impronte))
        print(
            f"    {len(voci)} decise" + (f", {len(guai)} da rivedere" if guai else ""),
            file=sys.stderr,
        )
        return voci

    ripresa: list = []
    muti = 0
    for n, lotto in enumerate(lotti, 1):
        voci = leggi_lotto(f"lotto {n}/{len(lotti)}", lotto)
        if voci is None:
            muti += 1
        else:
            decise_qui = {v["id"] for v in voci}
            ripresa += [voce for voce in lotto if voce[0]["id"] not in decise_qui]

    # Le campagne di un lotto che ha risposto ma le ha saltate — riga storta,
    # identificativo sbagliato, valore fuori vocabolario — si richiedono subito,
    # una volta sola, in un lotto piccolo. Aspettare il giro dopo vuol dire
    # lasciarle fuori da ogni somma per un giorno o piu'. Un lotto che non ha
    # risposto affatto non si riprova: li' il problema e' Claude, non la riga,
    # e riprovare costerebbe un'altra attesa fino al tetto.
    ripresi = 0
    if ripresa:
        voci = leggi_lotto("ripresa", ripresa)
        ripresi = len(voci or [])

    quadro = tassonomia.rigenera_report(DETTAGLI)

    ULTIMA.write_text(
        json.dumps(
            {
                "quando": datetime.now().isoformat(timespec="seconds"),
                "considerate": len(schede),
                "da_decidere": len(lavoro),
                "decise": len(decise),
                # Quante delle decise sono arrivate dal secondo tentativo: i problemi
                # qui sotto restano quelli del primo, anche per le campagne poi riprese.
                "ripresi": ripresi,
                "senza_nicchia_dopo": len(quadro["senza_nicchia"]),
                "problemi": problemi[:100],
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )

    print(
        f"{len(decise)} campagne classificate"
        + (f" ({ripresi} alla ripresa)" if ripresi else "")
        + ", "
        f"{len(quadro['senza_nicchia'])} ancora senza nicchia",
        file=sys.stderr,
    )
    for p in problemi[:20]:
        print(f"  ! {p}", file=sys.stderr)
    if len(problemi) > 20:
        print(f"  ... e altri {len(problemi) - 20} problemi in {ULTIMA.name}", file=sys.stderr)
    # Nessun lotto ha avuto risposta: non e' un giro con dei problemi, e' un giro
    # che non ha letto niente. Il 7 ottobre 2026, sul server, `claude` lanciato dal
    # pannello non trovava il login: ogni lotto falliva, e il passo usciva 0 —
    # il giro si dichiarava «ok» con 31 campagne fuori da ogni somma.
    return 1 if lotti and muti == len(lotti) else 0


if __name__ == "__main__":
    sys.exit(main())
