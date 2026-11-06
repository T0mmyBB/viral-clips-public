#!/usr/bin/env python
"""
giornaliero.py — il giro completo del radar, una volta al giorno.

Cosa fa, in ordine
------------------
1. `catalogo.py`   l'elenco di tutte le campagne (API JSON del sito, a pagine)
2. `dettagli.py`   una scheda per campagna: numeri, descrizione, materiali
                   (e da qui esce gia' `nicchie_report.md`, riscritto)
3. `briefs.py`     i documenti allegati che ancora mancano
4. `classifica.py` la nicchia delle campagne nuove o cambiate, letta da Claude
5. `tassonomia.py --report`  il quadro per nicchia con le assegnazioni note

Il quarto passo e' l'unico che non e' meccanico, ed e' li' perche' senza di lui
l'aggiornamento era completo solo per meta': i numeri di tutte le campagne
venivano riletti, ma una campagna mai letta da nessuno non entrava in nessuna
nicchia e quindi **in nessuna somma** — 105 su 627 il 9 settembre 2026,
visibili solo come un elenco in fondo al report. Chi decide resta Claude, come
per le clip; `classifica.py` prepara la lettura e raccoglie la risposta, e
ridecide solo cio' che e' cambiato davvero (vedi la sua **impronta**).

Il quinto passo e' una rete, non l'unica occasione: il report lo riscrive gia'
`dettagli.py` appena le schede cambiano, perche' descrive il catalogo e non
deve dipendere da chi lo lancia. Qui serve a `--secco`, che non scarica niente
e ricalcola soltanto, e a raccogliere i brief nuovi arrivati dopo le schede.

Perche' e' un file e non quattro voci in pianificazione
------------------------------------------------------
I quattro passi non sono indipendenti: `dettagli` legge il catalogo di
`catalogo`, `briefs` legge le schede di `dettagli`. Quattro attivita'
pianificate a orari fissi si sovrappongono il giorno in cui una rallenta, e
l'ultima gira sui dati di ieri senza dirlo. Qui girano in sequenza e, se una
fallisce, le successive non partono su dati vecchi. Ognuno ha un tetto di
tempo (`TETTI`): un passo appeso viene ucciso, invece di tenere in ostaggio
la mattina.

Un interprete solo
------------------
Tutti i passi vogliono solo la libreria standard e girano nel venv del
progetto. Fino al 16 settembre 2026 `catalogo.py` scorreva la pagina con
Scrapling, dal venv dello scraping; ora legge l'API JSON che la pagina stessa
usa, e quella dipendenza non c'e' piu'. Se il catalogo fallisce il giro si
ferma e lo dice, invece di scaricare il decimo del sito che l'indice espone.

Cosa lascia dietro
------------------
`ultimo_giro.json` — cos'e' cambiato: campagne nuove, campagne sparite,
quante ancora senza nicchia. E' la cosa che si guarda la mattina; i log
completi stanno in `radar/logs/`.

Uso
---
    python giornaliero.py             # il giro intero
    python giornaliero.py --salta-brief
    python giornaliero.py --salta-nicchie   # senza il passo che fa leggere Claude
    python giornaliero.py --secco     # non scarica niente, ricalcola e basta
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

for _flusso in (sys.stdout, sys.stderr):
    try:
        _flusso.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CATALOGO = HERE / "catalogo.json"
DETTAGLI = HERE / "radar_dettagli.json"
GIRO = HERE / "ultimo_giro.json"
LOGS = HERE / "logs"

sys.path.insert(0, str(ROOT / "publish"))
from interpreti import PYTHON_PROGETTO as PY_PROGETTO  # noqa: E402


def leggi(percorso: Path, difetto):
    try:
        return json.loads(percorso.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return difetto


def campagne(dati) -> dict[str, str]:
    """{id: titolo} da un catalogo, qualunque delle due forme abbia."""
    voci = dati.get("campagne") if isinstance(dati, dict) else dati
    return {c["id"]: c.get("titolo", "") for c in (voci or []) if c.get("id")}


# Quanto puo' durare ciascun passo prima che sia meglio ammazzarlo, **contando
# solo il tempo in cui la macchina e' sveglia**.
#
# La distinzione non e' teorica, ed e' costata una diagnosi sbagliata. Il
# 3 settembre 2026 il passo `catalogo` risultava durato **quindici ore**: aveva
# finito lo scorrimento in un minuto — otto giri, 605 campagne — e poi era
# rimasto fermo fino alle 22:26. Sembrava appeso. Non lo era: il portatile e'
# entrato in ibernazione alle 07:27:13, sei secondi dopo l'avvio del giro, e ne
# e' uscito alle 22:25. Il processo era **congelato**, non bloccato, e le
# quindici ore erano tutte di sonno.
#
# Da qui la regola: un orologio da parete non misura il lavoro di un processo,
# misura quanto tempo e' passato nel mondo. Un tetto sull'orologio avrebbe
# ammazzato un giro perfettamente sano — cioe' avrebbe trasformato una notte
# tranquilla in un guasto. Si misura il **tempo sveglio**
# (`QueryUnbiasedInterruptTime`), che e' l'unico che cresce quando il lavoro
# procede davvero.
#
# I tetti restano larghi — un multiplo abbondante del tempo reale misurato
# (catalogo ~1 min, schede ~4 min, brief ~13 min) — perche' servono a
# distinguere «appeso» da «lento», non a mettere fretta.
TETTI = {
    "catalogo": 20 * 60,
    "dettagli": 30 * 60,
    "brief": 60 * 60,
    # Le nicchie sono l'unico passo che aspetta una lettura, non una rete:
    # a lotti da cinquanta il catalogo intero sono una dozzina di letture.
    # Il tetto e' largo perche' il primo giro ridecide tutto (nessuna
    # assegnazione vecchia ha un'impronta) e i giri dopo costano quasi
    # niente — quel che cambia in un giorno sono poche campagne.
    "nicchie": 2 * 60 * 60,
    "report": 5 * 60,
}

# Ogni quanto si va a vedere se il passo e' finito: non e' una fretta, e' la
# risoluzione con cui si accorge di aver sforato.
CONTROLLO = 20.0


def tempo_sveglio() -> float:
    """Secondi trascorsi **escluso** il tempo in cui la macchina ha dormito.

    Su Windows `time.monotonic()` conta anche la sospensione (usa
    `GetTickCount64`), quindi non distingue un processo bloccato da un
    portatile chiuso. `QueryUnbiasedInterruptTime` invece si ferma con la
    macchina: e' la sola misura che risponde alla domanda vera, cioe' da quanto
    questo processo ha avuto una CPU a disposizione senza finire.

    Fuori da Windows, o se la chiamata non c'e', si ripiega sull'orologio
    monotono: peggio, ma mai piu' aggressivo del tetto.
    """
    try:
        import ctypes

        valore = ctypes.c_ulonglong(0)
        if ctypes.windll.kernel32.QueryUnbiasedInterruptTime(ctypes.byref(valore)):
            return valore.value / 1e7  # unita' da 100 ns
    except (AttributeError, OSError, ValueError):
        pass
    return time.monotonic()


def passo(nome: str, interprete: Path, argomenti: list[str], registro) -> dict:
    """Esegue un passo, ne registra l'uscita e ne misura il tempo."""
    orologio = time.time()
    sveglio = tempo_sveglio()
    intestazione = f"\n===== {nome} — {datetime.now():%H:%M:%S} =====\n"
    registro.write(intestazione)
    print(intestazione.strip(), file=sys.stderr)

    if not interprete.exists():
        messaggio = f"interprete assente: {interprete}"
        registro.write(messaggio + "\n")
        return {
            "passo": nome,
            "esito": "interprete assente",
            "secondi": 0.0,
            "secondi_svegli": 0.0,
            "dettaglio": str(interprete),
        }

    tetto = TETTI.get(nome)
    scaduto = False
    processo = subprocess.Popen(
        [str(interprete), *argomenti],
        cwd=str(HERE),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    while True:
        try:
            # Su Windows `communicate` legge in due thread, quindi richiamarla
            # dopo un timeout e' il modo previsto di aspettare a rate: le pipe
            # continuano a svuotarsi e il processo non si blocca sul buffer.
            stdout, stderr = processo.communicate(timeout=CONTROLLO)
            break
        except subprocess.TimeoutExpired:
            if tetto and tempo_sveglio() - sveglio > tetto:
                processo.kill()
                stdout, stderr = processo.communicate()
                stderr = (stderr or "") + (
                    f"\n[ucciso dopo {tetto}s di macchina sveglia: appeso, non lento]\n"
                )
                scaduto = True
                break

    registro.write(stdout or "")
    registro.write(stderr or "")
    registro.flush()
    durata = round(time.time() - orologio, 1)
    lavoro = round(tempo_sveglio() - sveglio, 1)
    ultima = [r for r in (stderr or stdout or "").splitlines() if r.strip()]
    stato = (
        "scaduto"
        if scaduto
        else ("ok" if processo.returncode == 0 else f"uscita {processo.returncode}")
    )
    dormito = durata - lavoro
    print(
        f"  {nome}: {stato.upper() if stato != 'ok' else 'ok'} in {lavoro}s"
        + (f" (piu' {dormito / 3600:.1f} h di macchina addormentata)" if dormito > 60 else ""),
        file=sys.stderr,
    )
    return {
        "passo": nome,
        "esito": stato,
        # Due tempi e non uno: `secondi` e' quanto e' passato nel mondo — serve
        # a spiegare perche' la plancia si e' aperta tardi — e `secondi_svegli`
        # e' quanto e' costato il lavoro. Con un numero solo, la notte di sonno
        # sembra un guasto o il guasto sembra una notte di sonno.
        "secondi": durata,
        "secondi_svegli": lavoro,
        "ultima_riga": ultima[-1][:200] if ultima else "",
    }


def senza_nicchia() -> list[dict]:
    """Le campagne che nessuno ha ancora messo in una nicchia: il lavoro del mattino."""
    assegnate = {a["id"] for a in leggi(HERE / "assegnazioni.json", {}).get("assegnazioni", [])}
    return [
        {
            "id": c["id"],
            "titolo": c.get("titolo", ""),
            "per_clipper": c.get("per_clipper", 0),
            "residuo": c.get("residuo", 0),
        }
        for c in leggi(DETTAGLI, [])
        if c.get("id") not in assegnate
    ]


# ------------------------------------------------------------ sorveglianza

# I campi che una scheda letta bene porta, e come si riconosce che li porta.
# Il 16 settembre 2026 il sito ha cambiato forma a `joinHref` e `provider` e'
# uscito vuoto su **tutte** le schede mentre ogni numero tornava e ogni passo
# finiva `ok`: un guasto che nessun codice di uscita sa dire. Si vede solo
# contando quante schede portano ciascun campo e confrontando col giro prima.
CAMPI = {
    "provider": lambda s: bool(s.get("provider")),
    "join": lambda s: bool(s.get("join")),
    "cpm": lambda s: (s.get("cpm") or 0) > 0,
    "clipper": lambda s: (s.get("clipper") or 0) > 0,
    "residuo": lambda s: (s.get("residuo") or 0) > 0,
    "tariffe": lambda s: bool(s.get("tariffe")),
    "piattaforme": lambda s: bool(s.get("piattaforme")),
    "views_30g": lambda s: (s.get("views_30g") or 0) > 0,
    "giorni_serie": lambda s: (s.get("giorni_serie") or 0) > 0,
    "descrizione": lambda s: bool(s.get("descrizione")),
    "stato": lambda s: bool(s.get("stato")),
    "organizzazione": lambda s: bool(s.get("organizzazione")),
    "materiali": lambda s: bool(s.get("materiali")),
    "tipo_pagamento": lambda s: bool(s.get("tipo_pagamento")),
}

# Di quanto puo' scendere la quota di schede che portano un campo prima che sia
# un guasto e non il catalogo che cambia. Fra il 9 e il 16 settembre 2026 le
# quote si sono mosse al massimo di 4 punti in discesa (`speso`); un campo
# svuotato da un ridisegno cade a zero.
CALO_MASSIMO = 0.25


def copertura(schede: list[dict]) -> dict[str, float]:
    """Per ogni campo sorvegliato, la quota di schede lette che lo porta."""
    lette = [s for s in schede if not s.get("errore")]
    quote = {"lette": round(len(lette) / len(schede), 3) if schede else 0.0}
    for campo, porta in CAMPI.items():
        quote[campo] = round(sum(1 for s in lette if porta(s)) / len(lette), 3) if lette else 0.0
    return quote


def sorveglia(
    riferimento: dict[str, float], adesso: dict[str, float]
) -> tuple[list[dict], dict[str, float]]:
    """
    I campi crollati rispetto al riferimento, e il riferimento del giro dopo.

    Il riferimento non e' semplicemente l'ultimo giro: se lo fosse, un campo
    svuotato oggi diventerebbe la normalita' domani e l'allarme durerebbe un
    giro solo. Un campo crollato tiene il valore di prima finche' non torna su;
    gli altri si aggiornano, cosi' le derive lente del catalogo non si sommano
    in un falso allarme. Un campo che il codice non sorveglia piu' si ignora.
    """
    allarmi = []
    nuovo = dict(adesso)
    for campo, prima in riferimento.items():
        if campo not in adesso:
            continue
        if prima - adesso[campo] >= CALO_MASSIMO:
            allarmi.append({"campo": campo, "prima": prima, "adesso": adesso[campo]})
            nuovo[campo] = prima
    return allarmi, nuovo


def main() -> None:
    ap = argparse.ArgumentParser(description="Il giro giornaliero del radar")
    ap.add_argument(
        "--salta-brief",
        action="store_true",
        help="non scarica i documenti allegati (il passo piu' lento)",
    )
    ap.add_argument(
        "--salta-nicchie",
        action="store_true",
        help="non fa leggere a Claude le campagne nuove o cambiate",
    )
    ap.add_argument(
        "--secco",
        action="store_true",
        help="nessun download: ricalcola solo il report dalle assegnazioni",
    )
    args = ap.parse_args()

    LOGS.mkdir(exist_ok=True)
    prima = campagne(leggi(CATALOGO, {}))
    giro_prima = leggi(GIRO, {})
    inizio = datetime.now()
    passi: list[dict] = []

    with (LOGS / f"{inizio:%Y-%m-%d}.log").open("a", encoding="utf-8") as registro:
        registro.write(f"\n\n######## giro del {inizio:%Y-%m-%d %H:%M:%S} ########\n")
        if not args.secco:
            passi.append(passo("catalogo", PY_PROGETTO, ["catalogo.py"], registro))
            if passi[-1]["esito"] != "ok":
                # Senza catalogo, `dettagli.py` ripiega sulle 50 campagne
                # dell'indice: un decimo del sito con l'aspetto di tutto il
                # sito. Meglio un giro mancato che una classifica sbagliata.
                registro.write("catalogo fallito: il giro si ferma qui\n")
                print("catalogo fallito: mi fermo, non leggo il decimo del sito", file=sys.stderr)
                # Il catalogo su disco e' ancora quello di prima: confrontarlo
                # con un catalogo vuoto dichiarava sparite tutte le 627
                # campagne, in `ultimo_giro.json` e nel pannello.
                # Le schede su disco sono quelle di prima, e con loro la
                # sorveglianza: un allarme aperto resta aperto.
                scrivi_giro(
                    inizio,
                    passi,
                    prima,
                    prima,
                    fermato=True,
                    sorveglianza={
                        k: giro_prima.get(k, d)
                        for k, d in (("copertura", {}), ("riferimento", {}), ("allarmi", []))
                    },
                )
                sys.exit(1)
            passi.append(passo("dettagli", PY_PROGETTO, ["dettagli.py", "--top", "0"], registro))
            # Gli organizzatori nuovi entrano nell'elenco dei provider, quello da
            # cui il pannello legge `iscritto`: fuori dal giro, un organizzatore
            # arrivato dopo l'ultima rigenerazione a mano non c'era mai.
            passi.append(passo("provider", PY_PROGETTO, ["provider.py"], registro))
            if not args.salta_brief:
                passi.append(passo("brief", PY_PROGETTO, ["briefs.py"], registro))
            # Dopo i brief e non prima: il brief entra nell'impronta, quindi
            # una campagna decisa senza sarebbe da ridecidere subito dopo.
            if not args.salta_nicchie:
                passi.append(passo("nicchie", PY_PROGETTO, ["classifica.py"], registro))
        passi.append(passo("report", PY_PROGETTO, ["tassonomia.py", "--report"], registro))

    if args.secco:
        # Un ricalcolo non e' un giro, quindi non ne prende il posto.
        # `ultimo_giro.json` risponde a una domanda sola — quand'e' l'ultima
        # volta che abbiamo guardato il sito — e un `--secco` che la
        # sovrascrivesse metterebbe l'ora di adesso accanto ai dati di ieri,
        # cioe' proprio la bugia che la prima riga della plancia esiste per non
        # dire. Le campagne nuove dell'ultimo giro vero resterebbero indietro.
        print(
            f"ricalcolo soltanto: {GIRO.name} resta quello dell'ultimo giro vero", file=sys.stderr
        )
        return

    dopo = campagne(leggi(CATALOGO, {}))
    adesso = copertura(leggi(DETTAGLI, []))
    allarmi, riferimento = sorveglia(giro_prima.get("riferimento") or {}, adesso)
    scrivi_giro(
        inizio,
        passi,
        prima,
        dopo,
        sorveglianza={"copertura": adesso, "riferimento": riferimento, "allarmi": allarmi},
    )
    # Un passo fallito e' scritto in `ultimo_giro.json`, ma chi lancia il giro —
    # il pulsante del pannello, `aggiorna.ps1` — guarda il codice di uscita.
    # Uscire 0 lo faceva dire «ok» anche con un passo in errore.
    falliti = [p["passo"] for p in passi if p["esito"] != "ok"]
    if falliti:
        print(f"passi falliti: {', '.join(falliti)}", file=sys.stderr)
        sys.exit(1)


def scrivi_giro(
    inizio, passi, prima, dopo, fermato: bool = False, sorveglianza: dict | None = None
) -> None:
    nuove = [{"id": i, "titolo": t} for i, t in dopo.items() if i not in prima]
    sparite = [{"id": i, "titolo": t} for i, t in prima.items() if i not in dopo]
    da_classificare = senza_nicchia()
    giro = {
        "girato_il": inizio.isoformat(timespec="seconds"),
        "durata_secondi": round(sum(p["secondi"] for p in passi), 1),
        # Quanto e' costato davvero il giro, tolte le ore in cui la macchina
        # dormiva. Quando i due numeri divergono di molto non c'e' niente di
        # rotto: c'e' un portatile che si e' chiuso a meta' lavoro.
        "secondi_svegli": round(sum(p.get("secondi_svegli", p["secondi"]) for p in passi), 1),
        "fermato": fermato,
        "passi": passi,
        "campagne_prima": len(prima),
        "campagne_dopo": len(dopo) or len(prima),
        "nuove": nuove,
        "sparite": sparite,
        "da_classificare": sorted(da_classificare, key=lambda c: -(c.get("per_clipper") or 0)),
        # Quante schede portano ciascun campo, e il riferimento con cui si
        # confronta il giro dopo: vedi `sorveglia`.
        **(sorveglianza or {"copertura": {}, "riferimento": {}, "allarmi": []}),
    }
    GIRO.write_text(json.dumps(giro, ensure_ascii=False, indent=1), encoding="utf-8")
    print(
        f"\n{len(nuove)} campagne nuove, {len(sparite)} sparite, "
        f"{len(da_classificare)} senza nicchia — dettagli in {GIRO.name}",
        file=sys.stderr,
    )
    for a in giro["allarmi"]:
        print(
            f"ATTENZIONE: `{a['campo']}` c'e' nel {a['adesso']:.0%} delle schede, "
            f"era nel {a['prima']:.0%} — il sito e' cambiato? Guarda dettagli.py",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
