#!/usr/bin/env python
"""
serratura.py — una cosa alla volta, fra processi diversi.

Serve da quando le tre piattaforme di una clip escono **insieme**: tre
`orchestrator.py` che girano in parallelo condividono due risorse che non
reggono due scrittori, e nessuna delle due protesta quando viene calpestata.

- `publish_state.json` si legge, si modifica e si riscrive intero. Tre
  processi che lo fanno nello stesso momento producono il caso classico:
  ognuno riscrive la versione che aveva letto prima, e i record delle altre
  due piattaforme spariscono. Il video resta online, lo stato dice che non
  e' mai uscito, e il giro dopo **ripubblica**.
- Il profilo del browser Whop e' uno per gruppo, e due Chromium sullo stesso
  `user_data_dir` non convivono — sta scritto nell'intestazione di
  `whop_agent.py`, e' una trappola gia' pagata. Percio' gli invii si mettono
  in fila: la finestra utile e' 30 minuti dalla pubblicazione e un invio ne
  prende circa uno, quindi tre in fila ci stanno larghi.

Come funziona
-------------
Un file creato con `O_CREAT | O_EXCL`, che e' atomico su Windows come su
Unix: o lo crei tu, o esiste gia' ed e' di qualcun altro. Niente `fcntl`
(non c'e' su Windows) e niente `msvcrt` (non c'e' altrove): questo file deve
restare di sola libreria standard e importabile ovunque, come i tre moduli
che i test coprono.

**Una serratura che non si apre piu' e' peggio del difetto che cura**: se un
processo muore ammazzato, il suo file resta li' e blocca tutti per sempre.
Da qui `scaduta`: passato quel tempo il file viene considerato abbandonato e
tolto di mezzo, dicendolo. Il valore va scelto piu' lungo del lavoro
protetto, mai piu' corto — una serratura scavalcata mentre il lavoro e'
ancora in corso rimette esattamente il problema che doveva togliere.
"""

from __future__ import annotations

import os
import re
import sys
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

QUI = Path(__file__).resolve().parent
CARTELLA = QUI / ".serrature"

# Quanto si aspetta prima di rinunciare, e dopo quanto un file di serratura si
# considera abbandonato. I difetti sono pensati per la scrittura di un file
# JSON, cioe' millisecondi: chi protegge un browser passa i suoi valori.
ATTESA_DEFAULT = 60.0
SCADUTA_DEFAULT = 300.0
PAUSA = 0.05


class Occupata(RuntimeError):
    """L'attesa e' finita e la serratura e' ancora di qualcun altro."""


def _log(msg: str) -> None:
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] serratura: {msg}", file=sys.stderr, flush=True)


def _file(nome: str) -> Path:
    """Il file che rappresenta la serratura. Il nome non finisce nel percorso grezzo."""
    pulito = re.sub(r"[^A-Za-z0-9._-]+", "-", nome).strip("-") or "senza-nome"
    return CARTELLA / f"{pulito}.lock"


def _eta(percorso: Path) -> float:
    """Da quanti secondi esiste, o 0 se e' sparito mentre lo guardavamo."""
    try:
        return max(0.0, time.time() - percorso.stat().st_mtime)
    except OSError:
        return 0.0


@contextmanager
def presa(nome: str, attesa: float = ATTESA_DEFAULT, scaduta: float = SCADUTA_DEFAULT):
    """
    Tiene la serratura di quel nome per la durata del blocco `with`.

    Alza `Occupata` se dopo `attesa` secondi e' ancora di un altro: chi chiama
    decide se e' un guasto o un «riprovo il giro dopo». Non si prosegue mai
    senza averla presa — proseguire sarebbe la corsa che questo file esiste
    per togliere.
    """
    CARTELLA.mkdir(exist_ok=True)
    percorso = _file(nome)
    scadenza = time.monotonic() + attesa
    fd = None

    while True:
        try:
            fd = os.open(str(percorso), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            eta = _eta(percorso)
            if eta > scaduta:
                # Abbandonata da un processo morto. Si dice sempre: se compare
                # spesso, il lavoro protetto dura piu' di `scaduta` e il numero
                # e' sbagliato, non il processo.
                _log(
                    f"«{nome}» ferma da {eta:.0f}s (limite {scaduta:.0f}s): "
                    f"la considero abbandonata e la tolgo"
                )
                try:
                    percorso.unlink()
                except OSError:
                    pass
                continue
            if time.monotonic() >= scadenza:
                raise Occupata(
                    f"«{nome}» e' occupata da {eta:.0f}s e ho aspettato "
                    f"{attesa:.0f}s. Non proseguo: due processi sulla stessa "
                    f"risorsa si sovrascrivono in silenzio."
                )
            time.sleep(PAUSA)

    try:
        os.write(fd, f"{os.getpid()} {datetime.now():%Y-%m-%d %H:%M:%S}\n".encode())
        os.close(fd)
        fd = None
        yield percorso
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            percorso.unlink()
        except OSError:
            pass


def nome_profilo(percorso: Path) -> str:
    """
    Il nome della serratura di un profilo browser: quello della sua cartella.

    Lo usano chi pubblica (`orchestrator.py`) e chi legge le metriche
    (`leggi_account.py`), e deve venire da un posto solo. Due Chromium sullo
    stesso `user_data_dir` non convivono, e una serratura che i due chiamassero
    in modo diverso non chiuderebbe niente — senza un errore, il giorno in cui
    il bottone «Aggiorna» cade durante una pubblicazione.
    """
    return "profilo-" + Path(percorso).name


def scrivi_atomico(percorso: Path, testo: str) -> None:
    """
    Scrive sostituendo il file in un colpo solo.

    La serratura mette in fila gli scrittori, ma i lettori non la chiedono —
    `esegui.py` legge `publish_state.json` mentre gli agenti pubblicano. Con
    `write_text` c'e' un istante in cui il file esiste vuoto o a meta', e un
    lettore che ci capita dentro legge «nessuna clip pubblicata». `os.replace`
    e' atomico: si vede la versione vecchia o quella nuova, mai un troncone.
    """
    percorso = Path(percorso)
    provvisorio = percorso.with_name(percorso.name + f".tmp{os.getpid()}")
    provvisorio.write_text(testo, encoding="utf-8")
    os.replace(provvisorio, percorso)
