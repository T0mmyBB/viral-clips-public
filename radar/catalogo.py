#!/usr/bin/env python
"""
catalogo.py — l'elenco completo delle campagne del sito.

Perche' esiste
--------------
`radar.py` legge le campagne dall'HTML di `/discover` e ne trova **50**. Non
sono tutte: il sito ne ha oltre seicento, e le altre arrivano a scorrimento.
Per anni di radar si sarebbe scelto dentro il 10% del catalogo senza
accorgersene, perche' una classifica di 50 voci ha esattamente l'aspetto di una
classifica giusta.

Da dove si leggono
------------------
Dall'API JSON con cui la pagina stessa si riempie scorrendo:

    /api/campaign/campaigns/discover?limit=50&cursor=<nextCursor>

pubblica, senza login, a pagine da 50 (un `limit` piu' alto risponde errore),
finche' `pagination.nextCursor` non torna `null`.

Fino al 16 settembre 2026 qui si apriva `/discover` con un browser vero e si
scorreva contando i collegamenti `<a href="/discover/<uuid>">`. Il sito e'
stato ridisegnato: le schede sono diventate `<button aria-label="Preview …">`
senza l'uuid, la pagina rispondeva 200 e il selettore trovava **zero**
campagne. L'API era gia' li' sotto, ed e' la fonte che il disegno della pagina
non tocca: 643 campagne in 13 richieste e 9 secondi, contro un minuto di
scorrimento, e senza il venv dello scraping.

Una pagina che fallisce a meta' ferma tutto: un catalogo parziale scritto su
disco ha lo stesso aspetto di quello intero, e `dettagli.py` lo leggerebbe
come tale.

Uso
---
    python catalogo.py                 # scrive radar/catalogo.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

for _flusso in (sys.stdout, sys.stderr):
    try:
        _flusso.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import radar  # noqa: E402  (stessa cartella: riusa lo user agent)

CATALOGO = HERE / "catalogo.json"

API = "https://contentrewards.com/api/campaign/campaigns/discover"
PER_PAGINA = 50

# Tredici richieste non sono una raffica, ma la loro applicazione ha gia'
# risposto `Rate Exceeded` una volta (vedi dettagli.PAUSA_MIN): la pausa costa
# cinque secondi in tutto.
PAUSA = 0.35

# Un tetto alle pagine, non al catalogo: con 50 a pagina sono 5000 campagne,
# otto volte quelle che ci sono. Serve solo a non girare per sempre se il
# cursore smettesse di avanzare senza tornare `null`.
MAX_PAGINE = 100


def pagina(cursore: str | None) -> dict:
    parametri = {"limit": PER_PAGINA}
    if cursore:
        parametri["cursor"] = cursore
    req = urllib.request.Request(
        f"{API}?{urllib.parse.urlencode(parametri)}",
        headers={
            "User-Agent": radar.UA,
            "Accept": "application/json",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def raccogli() -> dict[str, str]:
    """{id: titolo} di tutte le campagne."""
    return {c["id"]: str(c.get("name") or c.get("title") or "") for c in voci()}


def voci() -> list[dict]:
    """
    Tutte le voci dell'API, intere, pagina dopo pagina.

    Intere perche' ognuna porta gia' la scheda completa — tariffe, metriche,
    materiali, descrizione — e `dettagli.py` la legge da qui invece di aprire
    una pagina per campagna.
    """
    trovate: dict[str, dict] = {}
    cursore: str | None = None
    visti: set[str] = set()
    for n in range(1, MAX_PAGINE + 1):
        dati = pagina(cursore)
        if not dati.get("success") or not isinstance(dati.get("data"), list):
            raise ValueError(f"pagina {n}: risposta inattesa (chiavi: {sorted(dati)[:6]})")
        for c in dati["data"]:
            if c.get("id"):
                trovate[c["id"]] = c
        cursore = (dati.get("pagination") or {}).get("nextCursor")
        print(f"  pagina {n}: {len(dati['data'])} (totale {len(trovate)})", file=sys.stderr)
        if not cursore or not dati["data"]:
            return list(trovate.values())
        if cursore in visti:
            raise ValueError(f"pagina {n}: il cursore si ripete, la paginazione e' cambiata")
        visti.add(cursore)
        time.sleep(PAUSA)
    raise ValueError(f"oltre {MAX_PAGINE} pagine senza fine: la paginazione e' cambiata")


def main() -> None:
    ap = argparse.ArgumentParser(description="Elenco completo delle campagne")
    ap.add_argument("--out", default=str(CATALOGO))
    args = ap.parse_args()

    try:
        raccolte = raccogli()
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        sys.exit(
            f"Catalogo non letto: {exc}\n  Controlla l'API in {API}: "
            "catalogo.json resta quello di prima."
        )

    if not raccolte:
        sys.exit(
            f"Nessuna campagna trovata: l'API {API} risponde ma e' vuota. "
            "catalogo.json resta quello di prima."
        )

    dati = {
        "letto_il": datetime.now().isoformat(timespec="seconds"),
        "quante": len(raccolte),
        "campagne": [
            {"id": i, "titolo": t} for i, t in sorted(raccolte.items(), key=lambda kv: kv[1])
        ],
    }
    Path(args.out).write_text(json.dumps(dati, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(raccolte)} campagne in {Path(args.out).name}")


if __name__ == "__main__":
    main()
