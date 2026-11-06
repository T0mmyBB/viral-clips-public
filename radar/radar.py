#!/usr/bin/env python
"""
radar.py — le campagne di clipping ordinate per valore atteso, non per CPM.

Perche' esiste
--------------
La prima campagna e' stata scelta a occhio: $0,50/1k, $450 di budget, tema di
nicchia. Nello stesso momento erano attive campagne a $5,00/1k con budget da
decine di migliaia di dollari e meno concorrenti. Quella scelta, fatta in tre
secondi, e' costata piu' di qualsiasi ottimizzazione tecnica.

Come funziona
-------------
contentrewards.com pubblica la lista campagne dentro l'HTML della pagina
(payload React server-side), in forma gia' strutturata. Niente browser, niente
selettori da riparare a ogni rilascio: una richiesta HTTP e un parse.

Il punteggio non e' il CPM. E':

    tariffa x soldi_veri_rimasti x poca_concorrenza x freschezza x bacino_del_tema

L'ultimo termine e' quello che gli altri non calcolano, ed e' quello che ha
ucciso le nostre prime 29 clip: un tema che interessa a poche migliaia di
persone non paga, qualunque sia la tariffa.

Uso
---
    python radar.py                 # classifica delle campagne aperte
    python radar.py --top 30        # piu' righe
    python radar.py --nuove         # solo quelle mai viste prima
    python radar.py --json out.json # dati grezzi per altri usi
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

# I titoli delle campagne contengono di tutto: trattini lunghi, emoji, valute.
# Su una console cp1252 stamparli fa morire il comando con UnicodeEncodeError —
# e cioe' toglie di mezzo l'unica cosa che si legge per scegliere, dopo aver
# gia' scaricato 512 schede. Stessa medicina della pipeline video.
for _flusso in (sys.stdout, sys.stderr):
    try:
        _flusso.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

HERE = Path(__file__).resolve().parent
STATE = HERE / "radar_state.json"
REPORT = HERE / "radar_report.md"
CONFIG = HERE / "radar_config.json"

DISCOVER_URL = "https://contentrewards.com/discover"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# Quanto e' largo il pubblico potenziale di un tema, da 0 a 1.
# Non e' una verita' assoluta: e' la nostra scommessa, e va corretta con i dati
# veri del registro man mano che arrivano.
#
# Il campo `category` del payload e' **vuoto su tutte le campagne** (50 su 50,
# misurato il 30 agosto 2026): pesarlo vorrebbe dire dare a tutte lo stesso
# punteggio proprio sul fattore che conta di piu'. Il tema si riconosce dal
# testo — titolo, marchio e descrizione — quindi le chiavi qui sotto sono
# **parole da cercare**, non nomi di categoria.
BACINI_DEFAULT = {
    "gameplay": 1.0,
    "gaming": 1.0,
    "call of duty": 1.0,
    "warzone": 1.0,
    "fortnite": 1.0,
    "minecraft": 1.0,
    "beta": 0.95,
    "entertainment": 0.95,
    "reaction": 0.95,
    "podcast": 0.9,
    "sports": 0.9,
    "football": 0.9,
    "nba": 0.9,
    "breaks": 0.85,
    "cards": 0.85,
    "music": 0.85,
    "lifestyle": 0.8,
    "fitness": 0.8,
    "health": 0.75,
    "finance": 0.7,
    "trading": 0.7,
    "business": 0.7,
    "startup": 0.55,
    "education": 0.6,
    "tech": 0.6,
    "app": 0.55,
    "software": 0.5,
    "saas": 0.4,
    "b2b": 0.35,
    "local": 0.2,
}
BACINO_IGNOTO = 0.6

# `fundedAgo` e' l'etichetta con cui la pagina dichiara da quanto il budget e'
# stato messo: "3d", "1w", "2mo". E' piu' affidabile di `createdAtMs`, che
# ringiovanisce le campagne vecchie: misurato, una campagna con fundedAgo "9mo"
# portava un createdAtMs di quindici giorni prima. Prendendo quello si
# scambierebbe per appena aperta una campagna con 192 clipper gia' dentro.
UNITA_ETA = {"m": 1 / 1440, "h": 1 / 24, "d": 1, "w": 7, "mo": 30, "y": 365}


# --------------------------------------------------------------------- lettura


def scarica(url: str = DISCOVER_URL) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    with urllib.request.urlopen(req, timeout=45) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _blocco_bilanciato(testo: str, start: int) -> str:
    """Estrae l'array JSON che comincia a `start`, contando le parentesi."""
    livello, in_stringa, escape = 0, False, False
    for i in range(start, len(testo)):
        c = testo[i]
        if escape:
            escape = False
            continue
        if c == "\\":
            escape = True
            continue
        if c == '"':
            in_stringa = not in_stringa
            continue
        if in_stringa:
            continue
        if c == "[":
            livello += 1
        elif c == "]":
            livello -= 1
            if livello == 0:
                return testo[start : i + 1]
    raise ValueError("array delle campagne non chiuso")


def _payload_rsc(html: str) -> str:
    """
    Ricompone il payload React.

    La pagina lo consegna a pezzi, come argomento di `self.__next_f.push([1,"…"])`:
    ogni pezzo e' una **stringa JS**, e la sequenza va concatenata nell'ordine in
    cui compare. Non si puo' srotolare la fuga a mano con delle sostituzioni: le
    descrizioni contengono `\\n`, che diventerebbe un a capo vero dentro una
    stringa JSON e la renderebbe non analizzabile. Si decodifica ogni pezzo con
    json.loads, che e' l'unica cosa che conosce davvero le regole della fuga.
    """
    pezzi = []
    for m in re.finditer(r'self\.__next_f\.push\(\s*\[\s*\d+\s*,\s*"', html):
        inizio = m.end() - 1
        i, escape = inizio + 1, False
        while i < len(html):
            c = html[i]
            if escape:
                escape = False
            elif c == "\\":
                escape = True
            elif c == '"':
                break
            i += 1
        try:
            pezzi.append(json.loads(html[inizio : i + 1]))
        except json.JSONDecodeError:
            continue
    return "".join(pezzi) if pezzi else html


def estrai_campagne(html: str) -> list[dict]:
    """Cerca la chiave "campaigns" nel payload ricomposto."""
    testo = _payload_rsc(html)
    fuori = []
    for m in re.finditer(r'"campaigns"\s*:\s*\[', testo):
        try:
            blocco = _blocco_bilanciato(testo, testo.index("[", m.end() - 1))
            dati = json.loads(blocco)
        except (ValueError, json.JSONDecodeError):
            continue
        if isinstance(dati, list) and dati and isinstance(dati[0], dict):
            fuori.extend(dati)
    # deduplica per titolo + brand, tenendo la voce piu' ricca
    unico: dict[tuple, dict] = {}
    for c in fuori:
        chiave = (str(c.get("title", "")), str(c.get("brand", "")))
        if chiave not in unico or len(str(c)) > len(str(unico[chiave])):
            unico[chiave] = c
    return list(unico.values())


# -------------------------------------------------------------------- punteggio


def numero(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def bacino(c: dict, tabella: dict) -> tuple[float, str]:
    """Peso del bacino e parola che l'ha deciso, cercata in tutto il testo."""
    testo = " ".join(
        str(c.get(k, "") or "") for k in ("category", "title", "brand", "description")
    ).lower()
    migliore, parola = BACINO_IGNOTO, "?"
    for chiave, peso in tabella.items():
        if chiave in testo and peso > migliore:
            migliore, parola = peso, chiave
    return migliore, parola


def giorni_da(ms) -> float:
    try:
        quando = datetime.fromtimestamp(float(ms) / 1000, tz=UTC)
    except (TypeError, ValueError, OSError):
        return 999.0
    return (datetime.now(UTC) - quando).total_seconds() / 86400


def eta_giorni(c: dict) -> float:
    """Da quanti giorni la campagna e' finanziata: `fundedAgo` se c'e'."""
    m = re.fullmatch(r"\s*(\d+)\s*(mo|[mhdwy])\s*", str(c.get("fundedAgo", "")))
    if m:
        return int(m.group(1)) * UNITA_ETA[m.group(2)]
    return giorni_da(c.get("createdAtMs"))


def valuta(c: dict, tabella: dict) -> dict:
    cpm = numero(c.get("payoutSortRaw"))
    residuo = numero(c.get("availableBudgetRaw"), numero(c.get("budgetTotalRaw")))
    creator = numero(c.get("creatorCountRaw"))
    eta = eta_giorni(c)
    tema, parola = bacino(c, tabella)

    # Soldi veri rimasti: sotto i $2.000 il pool si esaurisce prima che tu
    # prenda ritmo; sopra i $20.000 la differenza non cambia le tue scelte.
    soldi = max(0.0, min(residuo, 20000.0)) / 20000.0
    # Concorrenza: 50 clipper dimezzano il valore, 200 lo dividono per cinque.
    concorrenza = 1.0 / (1.0 + creator / 50.0)
    # Freschezza: i budget sono a chi arriva prima.
    if eta <= 3:
        fresca = 1.0
    elif eta <= 14:
        fresca = 0.75
    elif eta <= 45:
        fresca = 0.5
    else:
        fresca = 0.3

    punteggio = cpm * soldi * concorrenza * fresca * tema * 100
    return {
        "titolo": str(c.get("title", ""))[:70],
        "brand": str(c.get("brand", ""))[:28],
        "categoria": str(c.get("category", "")) or parola,
        "cpm": cpm,
        "cpm_label": c.get("ratePer1kLabel") or f"${cpm:.2f}/1k",
        "budget_totale": numero(c.get("budgetTotalRaw")),
        "budget_residuo": residuo,
        "creator": int(creator),
        "giorni": round(eta, 1),
        "bacino": tema,
        "piattaforme": ", ".join(c.get("platforms") or [])
        if isinstance(c.get("platforms"), list)
        else "",
        "candidatura": bool(c.get("requiresApplication")),
        "id": str(c.get("organizationExperienceId", "")),
        "punteggio": round(punteggio, 1),
    }


def chiave_identita(v: dict) -> str:
    return f"{v['brand']}|{v['titolo']}"


# ---------------------------------------------------------------------- output


def tabella(righe: list[dict]) -> str:
    testa = f"{'#':>2}  {'punt':>6}  {'cpm':>7}  {'residuo':>10}  {'clip':>5}  {'gg':>5}  campagna"
    linee = [testa, "-" * len(testa)]
    for i, r in enumerate(righe, 1):
        nuova = " NUOVA" if r.get("nuova") else ""
        linee.append(
            f"{i:>2}  {r['punteggio']:>6.1f}  {r['cpm']:>6.2f}$  "
            f"{r['budget_residuo']:>9,.0f}$  {r['creator']:>5}  {r['giorni']:>5.0f}  "
            f"{r['titolo'][:52]}{nuova}"
        )
    return "\n".join(linee)


def relazione(righe: list[dict]) -> str:
    ora = datetime.now().strftime("%Y-%m-%d %H:%M")
    out = [f"# Radar campagne — {ora}", ""]
    nuove = [r for r in righe if r.get("nuova")]
    if nuove:
        out += ["## Nuove dall'ultima lettura", ""]
        for r in nuove:
            out.append(
                f"- **{r['titolo']}** — {r['cpm_label']}, "
                f"${r['budget_residuo']:,.0f} rimasti, {r['creator']} clipper "
                f"(punteggio {r['punteggio']})"
            )
        out.append("")
    out += [
        "## Classifica",
        "",
        "| # | Punteggio | CPM | Budget residuo | Clipper | Giorni | Bacino | Campagna |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for i, r in enumerate(righe, 1):
        out.append(
            f"| {i} | {r['punteggio']} | {r['cpm_label']} | ${r['budget_residuo']:,.0f} "
            f"| {r['creator']} | {r['giorni']:.0f} | {r['bacino']} | {r['titolo']} |"
        )
    out += [
        "",
        "Il punteggio combina tariffa, budget residuo, concorrenza, freschezza e",
        "ampiezza del bacino tematico. I pesi dei bacini stanno in `radar_config.json`",
        "e vanno corretti con i dati veri del registro, non lasciati a intuito.",
    ]
    return "\n".join(out)


# ------------------------------------------------------------------------ main


def main() -> None:
    ap = argparse.ArgumentParser(description="Radar delle campagne di clipping")
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--nuove", action="store_true", help="mostra solo le campagne mai viste")
    ap.add_argument("--json", metavar="FILE", help="scrive i dati grezzi in un file")
    ap.add_argument("--url", default=DISCOVER_URL)
    args = ap.parse_args()

    tabella_bacini = dict(BACINI_DEFAULT)
    if CONFIG.exists():
        try:
            tabella_bacini.update(json.loads(CONFIG.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            print("radar_config.json non e' JSON valido: uso i pesi di default", file=sys.stderr)

    try:
        html = scarica(args.url)
    except (urllib.error.URLError, TimeoutError) as exc:
        sys.exit(f"Non riesco a leggere {args.url}: {exc}")

    grezze = estrai_campagne(html)
    if not grezze:
        sys.exit(
            "Nessuna campagna trovata: il formato della pagina e' cambiato. "
            'Cerca la chiave "campaigns" nell\'HTML e adegua estrai_campagne().'
        )

    viste = set(json.loads(STATE.read_text(encoding="utf-8"))) if STATE.exists() else set()
    righe = [valuta(c, tabella_bacini) for c in grezze]
    for r in righe:
        r["nuova"] = chiave_identita(r) not in viste
    righe.sort(key=lambda r: r["punteggio"], reverse=True)

    mostrate = [r for r in righe if r["nuova"]] if args.nuove else righe[: args.top]
    print(f"{len(grezze)} campagne lette, {sum(1 for r in righe if r['nuova'])} nuove\n")
    print(tabella(mostrate) if mostrate else "Nessuna campagna nuova.")

    REPORT.write_text(relazione(righe[: max(args.top, 20)]), encoding="utf-8")
    STATE.write_text(
        json.dumps(sorted({chiave_identita(r) for r in righe}), ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    if args.json:
        Path(args.json).write_text(
            json.dumps(grezze, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    print(f"\nRelazione in {REPORT.name}")


if __name__ == "__main__":
    main()
