#!/usr/bin/env python
"""
provider.py — chi gestisce le campagne, e quanto costa consegnargli una clip.

Perche' esiste
--------------
Le campagne del sito non sono di una piattaforma sola: ognuna appartiene a
un'**organizzazione** diversa, che e' una comunita' Whop separata con la sua
iscrizione. Sulle 512 campagne lette se ne contano decine. Sapere quali
concentrano il denaro dice a quali conviene iscriversi; sapere **come** vogliono
ricevere le clip dice quanto costa lavorarci.

E li' sta il punto che nessuna classifica per tariffa puo' vedere. Clip Farm, ad
esempio, chiede per **ogni singolo video** una registrazione dello schermo fatta
col telefono, che parta dalla schermata home e apra l'applicazione dallo Store,
pena il rifiuto. Nessuno script lo fa. A parita' di tariffa, un provider senza
quel vincolo vale di piu' — e la differenza non e' nel codice, e' nel costo per
clip consegnata.

Percio' `attrito_invio` e' un campo **scritto a mano**, leggendo le regole di
ogni provider, e questo programma non lo sovrascrive mai: rigenera i numeri e
conserva i giudizi.

Uso
---
    python provider.py                 # aggiorna e stampa la classifica
    python provider.py --min-campagne 2
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import radar  # noqa: E402, F401  (per l'uscita a UTF-8 e i piccoli aiuti)

DETTAGLI = HERE / "radar_dettagli.json"
PROVIDER = HERE / "provider.json"

# I campi decisi da una persona: si conservano tra una rigenerazione e l'altra.
# `attrito_invio`: "ignoto" finche' nessuno ha letto le regole di quel provider;
# poi "automatico" (basta compilare un modulo) oppure la descrizione del
# passaggio manuale, che e' il vero costo.
CAMPI_A_MANO = ("iscritto", "attrito_invio", "note")
VUOTI = {"iscritto": False, "attrito_invio": "ignoto", "note": ""}


def chiave(scheda: dict) -> str:
    """
    L'unita' di iscrizione e' la **comunita'**, non l'esperienza.

    Il `joinHref` porta un `exp_...`, ma una stessa organizzazione ne ha piu'
    d'una — Clipping Culture, ad esempio, ospita le campagne Call of Duty sotto
    identificativi diversi. Raggruppando per esperienza si concluderebbe di
    doversi iscrivere quattro volte allo stesso posto, e il campo "iscritto"
    direbbe no dopo esserci gia' entrati.

    E gli `exp_...` **cambiano nel tempo**: misurato, le campagne Call of Duty
    sono passate da `exp_P8OVXy0vYac3nx` a `exp_zeADOv9rOOKk2x` nel giro di un
    giorno. Un `joinHref` messo da parte ieri porta a una porta che non c'e'
    piu'; va sempre riletto dalla scansione fresca.
    """
    return (scheda.get("organizzazione") or "").strip() or scheda.get("provider", "")


def carica_precedenti() -> dict[str, dict]:
    if not PROVIDER.exists():
        return {}
    try:
        dati = json.loads(PROVIDER.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return {
        p.get("organizzazione") or p.get("provider"): p
        for p in dati.get("provider", [])
        if p.get("provider")
    }


def aggrega(schede: list[dict], precedenti: dict[str, dict]) -> list[dict]:
    gruppi: dict[str, list[dict]] = collections.defaultdict(list)
    for s in schede:
        if s.get("errore") or not s.get("provider"):
            continue
        gruppi[chiave(s)].append(s)

    fuori = []
    for nome, campagne in gruppi.items():
        vecchio = precedenti.get(nome, {})
        misurabili = [c for c in campagne if c.get("campione_ok")]
        esperienze = sorted({c["provider"] for c in campagne if c.get("provider")})
        voce = {
            "provider": esperienze[0] if esperienze else nome,
            "esperienze": esperienze,
            "organizzazione": next(
                (c.get("organizzazione") for c in campagne if c.get("organizzazione")), ""
            ),
            "campagne": len(campagne),
            "residuo": round(sum(c["residuo"] for c in campagne), 2),
            "soldi_mese": round(sum(c["soldi_mese"] for c in campagne), 2),
            # La migliore delle sue campagne su cui la media abbia senso: e' il
            # motivo per cui varrebbe la pena entrare.
            "miglior_per_clipper": max((c["per_clipper"] for c in misurabili), default=0.0),
            "join": next((c["join"] for c in campagne if c.get("join")), ""),
        }
        for campo in CAMPI_A_MANO:
            voce[campo] = vecchio.get(campo, VUOTI[campo])
        fuori.append(voce)

    fuori.sort(key=lambda p: -p["residuo"])
    return fuori


def tabella(righe: list[dict]) -> str:
    testa = (
        f"{'camp':>4}  {'residuo':>10}  {'$/mese':>8}  {'miglior':>8}  "
        f"{'iscr':>4}  {'attrito':<12}  organizzazione"
    )
    fuori = [testa, "-" * len(testa)]
    for r in righe:
        fuori.append(
            f"{r['campagne']:>4}  {r['residuo']:>10,.0f}  {r['soldi_mese']:>8,.0f}  "
            f"{r['miglior_per_clipper']:>8,.0f}  {'si' if r['iscritto'] else 'no':>4}  "
            f"{str(r['attrito_invio'])[:12]:<12}  {(r['organizzazione'] or r['provider'])[:40]}"
        )
    return "\n".join(fuori)


def main() -> None:
    ap = argparse.ArgumentParser(description="I provider delle campagne")
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--min-campagne", type=int, default=1)
    args = ap.parse_args()

    if not DETTAGLI.exists():
        sys.exit(f"{DETTAGLI.name} non esiste: esegui prima catalogo.py e dettagli.py.")
    schede = json.loads(DETTAGLI.read_text(encoding="utf-8"))

    voci = aggrega(schede, carica_precedenti())
    PROVIDER.write_text(
        json.dumps({"provider": voci}, ensure_ascii=False, indent=1), encoding="utf-8"
    )

    mostrate = [v for v in voci if v["campagne"] >= args.min_campagne][: args.top]
    ignoti = sum(1 for v in voci if v["attrito_invio"] == "ignoto")
    print(f"{len(voci)} provider su {len(schede)} campagne\n")
    print(tabella(mostrate))
    print(
        f"\n{ignoti} provider con attrito di invio ancora ignoto: prima di "
        f"lavorare con uno, leggerne le regole e scrivere il campo. "
        f"I giudizi in {PROVIDER.name} non vengono mai sovrascritti."
    )


if __name__ == "__main__":
    main()
