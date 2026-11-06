"""Leggere i risultati del banco.

    python tools/banco_modelli/confronta.py nicchie [--prima-di AAAA-MM-GG] [--contro VARIANTE] [-v]
    python tools/banco_modelli/confronta.py decisioni <banco>... [-v]
    python tools/banco_modelli/confronta.py didascalie <banco>...
    python tools/banco_modelli/confronta.py testo <cartella-di-lavoro>

Le tre regole di lettura stanno nel README accanto. In breve: **l'accordo con i
giudizi in uso favorisce il modello che li ha scritti**, quindi si guarda anche il
modello vecchio rifatto nelle stesse condizioni (`--contro`) e le sole campagne
decise prima del suo arrivo (`--prima-di`); e **i fatti si controllano sulla
trascrizione** (`testo`), perche' i controlli meccanici promuovono tutte le varianti.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

RADICE = Path(__file__).resolve().parents[2]
for cartella in (RADICE / "radar", RADICE / "publish"):
    if str(cartella) not in sys.path:
        sys.path.insert(0, str(cartella))

from nicchie import BASE as NICCHIE  # noqa: E402

CAMPI_NICCHIA = ("nicchia", "lavorabile", "forma", "lingua")


def leggi(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- nicchie


def varianti_nicchie() -> dict:
    varianti = {}
    for d in sorted(NICCHIE.iterdir()) if NICCHIE.is_dir() else []:
        lotti = [leggi(f) for f in sorted(d.glob("lotto*.json"))]
        if lotti:
            varianti[d.name] = {"voci": {v["id"]: v for l in lotti for v in l["voci"]}, "lotti": lotti}
    return varianti


def tabella_nicchie(titolo: str, insieme: set, rif: dict, varianti: dict) -> None:
    lav = [i for i in insieme if rif[i].get("lavorabile") == "si"]
    print(f"\n{titolo}: {len(insieme)} campagne, lavorabili nel riferimento {len(lav)}")
    print(f"{'variante':30} nicchia lavor. forma lingua tenute  secondi  costo")
    for nome, v in varianti.items():
        voci = v["voci"]
        acc = {c: sum(voci[i][c] == rif[i].get(c) for i in insieme) / max(1, len(insieme)) for c in CAMPI_NICCHIA}
        tenute = sum(voci[i]["lavorabile"] == "si" for i in lav)
        sec = sum(l["secondi"] for l in v["lotti"])
        costo = sum(l["costo"] or 0 for l in v["lotti"])
        print(
            f"{nome:30} {acc['nicchia']:6.0%} {acc['lavorabile']:6.0%} {acc['forma']:5.0%} "
            f"{acc['lingua']:6.0%} {tenute:3}/{len(lav)}  {sec:7.0f}  ${costo:5.2f}"
        )


def nicchie(args) -> None:
    import classifica

    uso = {a["id"]: a for a in leggi(classifica.ASSEGNAZIONI)["assegnazioni"]}
    varianti = varianti_nicchie()
    if not varianti:
        raise SystemExit(f"nessuna variante in {NICCHIE}: lancia prima nicchie.py")
    ids = set.intersection(*(set(v["voci"]) for v in varianti.values())) & set(uso)
    print(f"campagne comuni a tutte le varianti: {len(ids)}")
    tabella_nicchie("contro i giudizi in uso, tutte", ids, uso, varianti)
    if args.prima_di:
        vecchie = {i for i in ids if (uso[i].get("deciso_il") or "") < args.prima_di}
        tabella_nicchie(f"contro i giudizi in uso, decise prima del {args.prima_di}", vecchie, uso, varianti)
    if args.contro:
        if args.contro not in varianti:
            raise SystemExit(f"variante {args.contro} assente: {', '.join(varianti)}")
        tabella_nicchie(f"contro {args.contro}", ids, varianti[args.contro]["voci"], varianti)

    print("\nlavorabili in uso scartate:")
    lav_uso = [i for i in ids if uso[i].get("lavorabile") == "si"]
    for nome, v in varianti.items():
        perse = [
            (i[:8], v["voci"][i]["lavorabile"], uso[i]["titolo"][:45], v["voci"][i].get("nota", "")[:70])
            for i in lav_uso
            if v["voci"][i]["lavorabile"] != "si"
        ]
        print(f"  {nome}: {len(perse)}")
        if args.v:
            for p in perse:
                print("    ", *p)


# ---------------------------------------------------------------- decisioni


def iou(a: dict, b: dict) -> float:
    inter = max(0, min(a["end"], b["end"]) - max(a["start"], b["start"]))
    return inter / (max(a["end"], b["end"]) - min(a["start"], b["start"]))


def riassumi_clip(nome: str, clips: list[dict], rif: list[dict]) -> str:
    """Quante clip, quanto lunghe, e quante di quelle in uso ritrova (sovrapposizione >= 50%)."""
    durate = [c["end"] - c["start"] for c in clips]
    trovate = sum(any(iou(r, c) >= 0.5 for c in clips) for r in rif)
    nuove = sum(not any(iou(r, c) >= 0.5 for r in rif) for c in clips)
    media = sum(durate) / len(durate) if durate else 0
    return (
        f"{nome:28} {len(clips):3} clip  {sum(durate):5.0f}s  media {media:4.0f}s  "
        f"in uso ritrovate {trovate:2}/{len(rif)}  nuove {nuove}"
    )


def decisioni(args) -> None:
    gia: set[str] = set()
    for banco in map(Path, args.banchi):
        info = leggi(banco / "_banco.json")
        rif = leggi(RADICE / "work" / info["cartella"] / "decisions.json")["clips"]
        if info["cartella"] not in gia:
            print(riassumi_clip(f"in uso ({info['cartella'][:18]})", rif, rif))
            gia.add(info["cartella"])
        esito = leggi(banco / "_esito.json") if (banco / "_esito.json").exists() else {}
        f = banco / "work" / info["cartella"] / "decisions.json"
        if not f.exists():
            print(f"{banco.name}: nessun decisions.json", esito.get("consumi"))
            continue
        clips = leggi(f)["clips"]
        print(riassumi_clip(banco.name, clips, rif))
        print(
            f"{'':28} {esito.get('modello')} {esito.get('effort')} valido={esito.get('valido')} "
            f"tentativi={esito.get('tentativi')} costo=${esito.get('costo_totale')} "
            f"secondi={esito.get('secondi_totali')} turni={[c['turni'] for c in esito.get('consumi', [])]}"
        )
        if args.v:
            for c in clips:
                print(f"    {c['start']:7.2f} {c['end']:7.2f} {c['end'] - c['start']:4.0f}s  {c.get('hook')}")


# ---------------------------------------------------------------- didascalie


def didascalie(args) -> None:
    import orchestrator
    import prepara

    richiesti = {*prepara.CAMPI_CONSEGNA, "items"}
    for banco in map(Path, args.banchi):
        info = leggi(banco / "_banco.json")
        forma = leggi(banco / "output" / "why-a16z-is-betting-7m-on-clipping-the-cap-table-ep-25-w-clo" / "publish.json")
        p = leggi(banco / "output" / info["cartella"] / "publish.json")
        esito = leggi(banco / "_esito.json") if (banco / "_esito.json").exists() else {}
        h = p.get("handles") or {}
        # I valori ricopiati dal modello di forma sono il difetto da cercare: il
        # prompt indica un'altra campagna, e un agente ne ha preso anche i valori.
        copiati = {
            k: p[k] for k in p
            if k not in ("items", "batch_times", "min_gap_seconds", "max_per_day") and forma.get(k) == p[k]
        }
        print(
            f"\n######## {banco.name}  {esito.get('modello')} {esito.get('effort')} "
            f"${esito.get('costo_totale')} {esito.get('secondi_totali')}s\n"
            f"  handles={h}\n  campi fuori dall'elenco: {sorted(set(p) - richiesti)}\n"
            f"  valori identici al modello di forma: {copiati}"
        )
        for it in p.get("items", []):
            print(f"\n--- {it['id']} {it['file']} | {it.get('title')}")
            for pl in ("tiktok", "instagram", "youtube"):
                try:
                    t = orchestrator.didascalia(it["caption"], h, orchestrator.tag(h, pl))
                except SystemExit as e:
                    t = f"ERRORE {e}"
                print(f"  [{pl}] tag: {' '.join(re.findall(r'[@#][\w.]+', t))}")
            print("  " + it["caption"].replace("\n", " / "))


def testo(args) -> None:
    """Il parlato di ogni clip, per controllare i fatti di hook e didascalie."""
    work = Path(args.cartella)
    tr = leggi(work / "transcript.json")
    for n, c in enumerate(leggi(work / "decisions.json")["clips"], 1):
        parlato = " ".join(
            s["text"].strip() for s in tr["segments"] if s["end"] > c["start"] + 0.05 and s["start"] < c["end"] - 0.05
        )
        print(f"\n=== clip {n:02d} {c['start']:.1f}-{c['end']:.1f} | {c.get('hook')}\n{parlato}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cosa", required=True)
    n = sub.add_parser("nicchie")
    n.add_argument("--prima-di", help="conta a parte le campagne decise prima di questa data")
    n.add_argument("--contro", help="variante di riferimento: il modello vecchio rifatto oggi")
    n.add_argument("-v", action="store_true")
    n.set_defaults(func=nicchie)
    d = sub.add_parser("decisioni")
    d.add_argument("banchi", nargs="+")
    d.add_argument("-v", action="store_true")
    d.set_defaults(func=decisioni)
    c = sub.add_parser("didascalie")
    c.add_argument("banchi", nargs="+")
    c.set_defaults(func=didascalie)
    t = sub.add_parser("testo")
    t.add_argument("cartella")
    t.set_defaults(func=testo)
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
