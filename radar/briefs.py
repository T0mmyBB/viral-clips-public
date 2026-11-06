#!/usr/bin/env python
"""
briefs.py — il testo vero del brief, scaricato dai Google Doc allegati.

Perche' esiste
--------------
La scheda di `dettagli.py` porta una descrizione di due righe, e il titolo di
una campagna e' quasi sempre un nome proprio ("Whoods's Campaign"): con quei
due appigli la classificazione per argomento resta un regex, e un regex sul
titolo ha gia' sbagliato le campagne piu' grosse del catalogo — quella da
$86.733 di budget classificata "gaming" non ha ne' volto ne' parlato.

Il documento allegato invece dice **cosa si taglia e da dove**: e' li' che si
legge "clips from the podcast", "download the provided :30 asset", "film
yourself using the product". Oltre la meta' delle campagne ne ha uno, ed e'
pubblico: `docs.google.com/document/d/<id>/export?format=txt` risponde senza
autenticazione se il documento e' condiviso col link — come sono, visto che
devono leggerlo i clipper.

Trappole gia' pagate
--------------------
- **Il rate limit di Google non da' errore: da' un file vuoto.** Con HTTP 200.
  Quindi una risposta di zero byte non si scrive mai su disco, altrimenti la
  passata dopo la salta credendola gia' fatta. Chi scarica in blocco senza
  guardare la lunghezza si ritrova 45 brief vuoti e li scopre a valle.
- **Un ID con dentro uno spazio o un a capo non e' un ID.** Vengono da elenchi
  copiati a mano: si ripulisce sempre, e si scarta quello che non ha la forma
  di un identificativo Google.
- **Una cartella di Drive non e' un documento**: `drive.google.com/drive/
  folders/...` non ha un export testuale. Si registra come materiale non
  leggibile, non si prova a scaricarlo.

Uso
---
    python briefs.py                 # scarica quelli che mancano
    python briefs.py --rifai         # riscarica anche quelli gia' presenti
    python briefs.py --pausa 2.0     # piu' lento, se Google inizia a tacere
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

for _flusso in (sys.stdout, sys.stderr):
    try:
        _flusso.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

HERE = Path(__file__).resolve().parent
DETTAGLI = HERE / "radar_dettagli.json"
CARTELLA = HERE / "briefs"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

DOC = re.compile(r"docs\.google\.com/document/d/([A-Za-z0-9_-]{20,})")
EXPORT = "https://docs.google.com/document/d/%s/export?format=txt"

# Sotto questa soglia il documento non e' un brief: e' una pagina di
# intestazione, o la risposta troncata di un rate limit che non si e' dichiarato.
MIN_UTILE = 200


def documenti(campagna: dict) -> list[str]:
    """Gli identificativi dei Google Doc allegati a una campagna, senza doppioni."""
    visti: list[str] = []
    for url in campagna.get("materiali") or []:
        for ident in DOC.findall(str(url).strip()):
            if ident not in visti:
                visti.append(ident)
    return visti


def scarica_doc(ident: str, pausa: float, tentativi: int = 3) -> str | None:
    """Il testo di un documento, o None se Google non lo da'."""
    return scarica(EXPORT % ident, pausa, tentativi)


def scarica(url: str, pausa: float, tentativi: int = 3) -> str | None:
    """Una pagina di Google, con i tentativi contro il rate limit che non si dichiara.

    Separata da `scarica_doc` perche' serve anche a `sorgenti.py`, che rilegge
    i Doc in html: la trappola del 200 vuoto e' la stessa, e scritta due volte
    divergerebbe al primo ritocco.
    """
    for giro in range(tentativi):
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=45) as risposta:
                testo = risposta.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 403, 500, 503):
                time.sleep(pausa * (giro + 2) * 3)
                continue
            return None
        except (urllib.error.URLError, TimeoutError):
            time.sleep(pausa * (giro + 2))
            continue
        # Un 200 vuoto e' il rate limit travestito: si riprova piu' lentamente.
        if len(testo.strip()) < 20:
            time.sleep(pausa * (giro + 2) * 3)
            continue
        return testo
    return None


def main() -> None:
    ap = argparse.ArgumentParser(description="Scarica i brief dai Google Doc allegati")
    ap.add_argument("--dettagli", default=str(DETTAGLI))
    ap.add_argument("--out", default=str(CARTELLA))
    ap.add_argument("--pausa", type=float, default=1.2, help="secondi tra un documento e l'altro")
    ap.add_argument("--rifai", action="store_true", help="riscarica anche i brief gia' presenti")
    ap.add_argument("--max", type=int, default=0, help="fermati dopo N campagne (prova)")
    args = ap.parse_args()

    schede = json.loads(Path(args.dettagli).read_text(encoding="utf-8"))
    fuori = Path(args.out)
    fuori.mkdir(exist_ok=True)

    da_fare = [c for c in schede if documenti(c)]
    print(f"{len(da_fare)} campagne su {len(schede)} hanno almeno un Google Doc", file=sys.stderr)

    fatti = vuoti = saltati = 0
    for n, campagna in enumerate(da_fare, 1):
        if args.max and fatti + saltati >= args.max:
            break
        destinazione = fuori / f"{campagna['id']}.txt"
        if destinazione.exists() and destinazione.stat().st_size > MIN_UTILE and not args.rifai:
            saltati += 1
            continue

        pezzi: list[str] = []
        for ident in documenti(campagna):
            testo = scarica_doc(ident, args.pausa)
            time.sleep(args.pausa)
            if testo:
                pezzi.append(f"===== {ident} =====\n{testo}")

        if not pezzi:
            vuoti += 1
            print(f"  [{n}/{len(da_fare)}] vuoto: {campagna['titolo'][:50]}", file=sys.stderr)
            continue

        intestazione = (
            f"# {campagna['titolo']}\n"
            f"# id: {campagna['id']}\n"
            f"# descrizione: {campagna.get('descrizione', '')}\n\n"
        )
        destinazione.write_text(intestazione + "\n\n".join(pezzi), encoding="utf-8")
        fatti += 1
        if n % 20 == 0:
            print(
                f"  [{n}/{len(da_fare)}] {fatti} scaricati, {vuoti} vuoti, {saltati} gia' presenti",
                file=sys.stderr,
            )

    print(
        f"{fatti} brief scaricati, {vuoti} non ottenuti, {saltati} gia' presenti in {fuori}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
