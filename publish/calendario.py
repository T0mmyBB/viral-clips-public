"""Quando esce ogni clip, deciso una volta e scritto nel piano.

    python calendario.py --cartella output/<slug>            # calcola e mostra
    python calendario.py --cartella output/<slug> --scrivi   # lo scrive in publish.json

**L'unita' e' la clip, non la coppia clip-piattaforma.** La stessa clip esce su TikTok,
Instagram e YouTube **nello stesso momento**: e' una consegna sola vista da tre posti, e
scaglionarla darebbe tre date da ricordare per la stessa cosa. Da qui una conseguenza che
va detta: siccome ogni piattaforma ha il **suo** tetto giornaliero, il numero di clip che
un giorno regge e' il **minimo** fra i tetti delle tre — la piattaforma piu' giovane detta
il passo a tutte.

**Il calendario non pubblica niente.** Scrive un campo `quando` accanto a ogni clip, e
quello e' un dato inerte: si rigenera quante volte si vuole, si legge, si corregge a mano.
Chi lo esegue e' `esegui.py`, che e' un altro programma e un'altra decisione.

**Le regole del ritmo non sono qui.** Stanno in `ritmo.py`, che le ha misurate: sette
giorni di sola presenza, poi 1, 2, 3 post al giorno, e una distanza minima fra un post e
l'altro. Riscriverle qui sarebbe la seconda copia che diverge — e' gia' successo, ed e'
il motivo per cui `orchestrator.cadenza()` importa `ritmo` invece di ricalcolare.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

import ritmo

QUI = Path(__file__).resolve().parent
RADICE = QUI.parent

# Cosa rende caricabile un mp4 lo dice il cancello della consegna, in un posto
# solo: `validate` e i moduli che importa sono di sola libreria standard.
sys.path.insert(0, str(RADICE / ".claude" / "skills" / "viral-clips" / "scripts"))
import validate  # noqa: E402

IDENTITA = RADICE / "dashboard" / "identita.json"
SCELTE = RADICE / "dashboard" / "scelte.json"

# Gli orari in cui si pubblica, in ordine. Quando un giorno regge piu' di una
# clip si prendono i primi N, cosi' la distanza fra due post dello stesso
# account resta quella che `min_gap_seconds` pretende senza doverla ricalcolare.
#
# **Sono ore italiane, ma il pubblico e' americano.** Il fuso e' l'unica cosa
# che conta nello sceglierli: la macchina sta in Italia, chi guarda no. Con
# l'ora legale l'Italia e' ET+6 e PT+9, quindi le vecchie 09:00 erano **le
# 03:00 a New York e la mezzanotte in California** — l'ora in cui un post esce
# e non lo vede nessuno, e non lo vedra' nemmeno dopo perche' la prima ora di
# vita e' quella che decide la distribuzione.
#
#   19:00 IT = 13:00 ET = 10:00 PT   pomeriggio est, mattina ovest
#   21:00 IT = 15:00 ET = 12:00 PT   pomeriggio est, pranzo ovest
#   23:00 IT = 17:00 ET = 14:00 PT   uscita dal lavoro est, pomeriggio ovest
#
# **Il primo orario e' quello che conta di piu', non l'ultimo**: durante la
# rampa la quota e' 1 al giorno, quindi `orari[0]` e' l'unico che viene usato
# per settimane. Ordinarli per orologio mettendo davanti l'ora piu' presto
# significa dare a ogni account nuovo il suo peggior orario per un mese.
#
# Il picco vero — 18:00-21:00 ET — cade all'una di notte in Italia, e li' il
# portatile dorme: e' una decisione sul portatile (`WakeToRun`), non sul
# calendario. Questi tre sono il meglio raggiungibile a macchina sveglia.
ORARI_DEFAULT = ("19:00", "21:00", "23:00")


def leggi(p: Path, default=None):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def gruppo_di(campagna_id: str) -> str | None:
    """Il gruppo che lavora questa campagna, letto dal registro delle scelte.

    Non si indovina dalla nicchia della campagna: l'assegnazione e' una
    decisione scritta, non una deduzione. **Ma la nicchia della scelta si'**:
    una campagna scelta dove l'account non c'era ancora porta la `nicchia` in
    cui e' stata cliccata e nessun `gruppo`, e quella nicchia e' scritta quanto
    un gruppo. Il gruppo allora e' quello che il registro mette su quella
    nicchia — se ce n'e' uno solo. Nessuno o due non sono una risposta, e il
    chiamante si ferma.
    """
    for s in (leggi(SCELTE, {}) or {}).get("scelte", []):
        if s.get("campagna_id") != campagna_id:
            continue
        if s.get("gruppo") or not s.get("nicchia"):
            return s.get("gruppo")
        candidati = [
            g["id"]
            for g in (leggi(IDENTITA, {}) or {}).get("gruppi", [])
            if g.get("nicchia") == s["nicchia"]
        ]
        return candidati[0] if len(candidati) == 1 else None
    return None


def identita_del_gruppo(gruppo: str) -> list[dict]:
    return [
        i
        for i in (leggi(IDENTITA, {}) or {}).get("identita", [])
        if i.get("gruppo") == gruppo and i.get("attiva") is not False
    ]


def clip_al_giorno(identita: list[dict], giorno: date) -> tuple[int, str]:
    """Quante clip regge questo giorno, e chi detta il passo.

    Il minimo fra le piattaforme, perche' una clip le impegna tutte insieme:
    se YouTube oggi ne ammette 3 e Instagram 0, la risposta e' 0 — pubblicarla
    su due sole piattaforme non e' la consegna che il brief chiede.
    """
    if not identita:
        return 0, "nessuna identita' nel registro"
    quote = []
    for i in identita:
        q, motivo = ritmo.tetto_giornaliero(
            int(i.get("max_per_day") or 0), i.get("account_started"), giorno
        )
        quote.append((q, f"{i.get('piattaforma')}: {motivo}"))
    return min(quote)[0], min(quote)[1]


def momenti_del_giorno(orari: list[str], giorno: date) -> list[datetime]:
    """Gli istanti di un giorno di pubblicazione, nell'ordine in cui `orari` li elenca.

    **Un orario che precede quello prima e' dopo mezzanotte**: con 19:00, 21:30,
    00:00 il terzo post e' la mezzanotte *successiva* al 21:30, non quella che ha
    aperto la giornata. Letto come la stessa data, il `quando` cadrebbe prima del
    primo post, e il primo passaggio dell'esecutore lo pubblicherebbe subito.
    """
    fuori, dopo_mezzanotte, prima = [], False, None
    for o in orari:
        hh, mm = (int(x) for x in o.split(":"))
        if prima is not None and (hh, mm) < prima:
            dopo_mezzanotte = True
        prima = (hh, mm)
        fuori.append(datetime.combine(giorno + timedelta(days=int(dopo_mezzanotte)), time(hh, mm)))
    return fuori


def gia_uscite(cartella: Path) -> set[str]:
    """Gli id delle clip che hanno gia' un post pubblicato, su qualunque piattaforma."""
    stato = leggi(cartella / "publish_state.json", {}) or {}
    return {
        str(k).split("@")[0]
        for k, r in (stato.get("items") or {}).items()
        if isinstance(r, dict) and r.get("status") in ("pubblicato", "done")
    }


def non_caricabili(cartella: Path, items: list[dict], sonda=validate.video_info) -> list[str]:
    """Le clip da programmare il cui file manca o non si puo' caricare.

    La strada del pannello — `automatico.py`, poi `prepara.py`, poi qui — non
    passa da `validate.py --consegna`, che lancia solo chi segue il passo 8 di
    SKILL.md. Senza questo controllo una data si assegna anche a un mp4 vuoto, e
    il guasto arriva dall'agente di pubblicazione, dentro la finestra d'invio.
    Le sospese non si programmano, quindi non si guardano.
    """
    rotti = []
    for it in items:
        if it.get("sospesa"):
            continue
        f = cartella / (it.get("file") or "")
        if not it.get("file") or not f.is_file():
            rotti.append(f"clip {it.get('id')}: file {it.get('file')!r} non trovato")
        elif difetto := validate.difetto_clip(f, sonda):
            rotti.append(f"clip {it.get('id')}: {difetto}")
    return rotti


def calcola(
    cartella: Path,
    da: date | None = None,
    giorni_max: int = 120,
    sonda=validate.video_info,
    solo_nuove: bool = False,
    dopo: datetime | None = None,
) -> dict:
    """Il piano: una data e un'ora per ogni clip, piu' il perche' di ogni scelta.

    `solo_nuove` lascia fuori le clip gia' uscite su una piattaforma qualunque:
    serve a rifare il piano a campagna avviata, senza dare un posto a cio' che
    e' gia' online e senza completare in silenzio le uscite a meta'.

    `dopo` scarta gli orari che non lo superano: con `da` a ieri il piano parte
    oggi, dal primo orario ancora davanti. Lo usa la settimana senza
    approvazioni degli agenti (3/11), dove nessuno guarda il piano prima.
    """
    cfg = leggi(cartella / "publish.json")
    if not cfg:
        raise SystemExit(f"{cartella / 'publish.json'} non trovato o illeggibile")

    items = cfg.get("items") or []
    if not items:
        raise SystemExit("publish.json non contiene nessuna clip in 'items'")

    if rotti := non_caricabili(cartella, items, sonda):
        raise SystemExit(
            "nessuna data assegnata: " + str(len(rotti)) + " clip non si "
            "possono caricare\n  " + "\n  ".join(rotti)
        )

    gruppo = cfg.get("gruppo") or gruppo_di(cfg.get("whop_campaign_uuid") or "")
    identita = identita_del_gruppo(gruppo) if gruppo else []
    if not gruppo:
        raise SystemExit(
            "nessun gruppo: publish.json non lo dice, e fra le scelte la campagna non "
            "ne ha uno — o non e' stata scelta, o e' stata scelta in una nicchia che "
            "non ha ancora un account. Senza account non c'e' nessuna rampa da seguire: "
            "le clip restano pronte, e il calendario si fa quando la nicchia ne avra' "
            "uno in dashboard/identita.json."
        )
    if not identita:
        raise SystemExit(
            f"nessuna identita' per il gruppo '{gruppo}': senza sapere da quale account "
            f"si pubblica non si puo' calcolare nessuna rampa. Registralo in "
            f"dashboard/identita.json."
        )

    orari = [o for o in (cfg.get("batch_times") or ORARI_DEFAULT)]
    # Si parte da **domani**: un piano scritto oggi che pubblica fra dieci minuti
    # toglie la sola cosa che un piano serve a dare, cioe' il tempo di guardarlo.
    giorno = (da or date.today()) + timedelta(days=1)

    piano, note = [], []
    # Una clip sospesa non si programma: le si toglierebbe il `quando` che
    # aveva, e con lui la sola traccia di quando doveva uscire.
    da_piazzare = [it for it in items if not it.get("sospesa")]
    if solo_nuove:
        uscite = gia_uscite(cartella)
        da_piazzare = [it for it in da_piazzare if str(it["id"]) not in uscite]
    for it in items:
        if it.get("sospesa"):
            note.append(f"clip {it['id']} sospesa, non programmata: {it['sospesa']}")
    fine = giorno + timedelta(days=giorni_max)

    while da_piazzare and giorno < fine:
        quota, motivo = clip_al_giorno(identita, giorno)
        if quota <= 0:
            note.append(f"{giorno}: nessuna pubblicazione — {motivo}")
            giorno += timedelta(days=1)
            continue
        momenti = [m for m in momenti_del_giorno(orari, giorno) if not dopo or m > dopo]
        for n in range(min(quota, len(da_piazzare), len(momenti))):
            it = da_piazzare.pop(0)
            piano.append(
                {
                    "id": it["id"],
                    "file": it.get("file"),
                    "quando": momenti[n].isoformat(timespec="minutes"),
                    "piattaforme": [i["piattaforma"] for i in identita],
                    "perche": motivo,
                }
            )
        giorno += timedelta(days=1)

    if da_piazzare:
        note.append(f"ATTENZIONE: {len(da_piazzare)} clip non piazzate entro {giorni_max} giorni")

    return {
        "gruppo": gruppo,
        "clip": len(items),
        "piazzate": len(piano),
        "piattaforme": [i["piattaforma"] for i in identita],
        "piano": piano,
        "note": note,
    }


def scrivi(cartella: Path, piano: dict) -> None:
    """Mette `quando` accanto a ogni clip in publish.json, senza toccare altro."""
    p = cartella / "publish.json"
    cfg = leggi(p)
    quando = {v["id"]: v["quando"] for v in piano["piano"]}
    for it in cfg.get("items", []):
        if it["id"] in quando:
            it["quando"] = quando[it["id"]]
    cfg["calendario_scritto_il"] = datetime.now().isoformat(timespec="seconds")
    cfg["calendario_piattaforme"] = piano["piattaforme"]
    p.write_text(json.dumps(cfg, ensure_ascii=False, indent=1), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description="Quando esce ogni clip, secondo il ritmo")
    ap.add_argument("--cartella", required=True)
    ap.add_argument(
        "--scrivi",
        action="store_true",
        help="scrive `quando` in publish.json (senza, mostra e basta)",
    )
    ap.add_argument(
        "--solo-nuove",
        action="store_true",
        help="programma solo le clip non ancora uscite su nessuna piattaforma",
    )
    ap.add_argument(
        "--inizio",
        type=date.fromisoformat,
        help="primo giorno di pubblicazione (AAAA-MM-GG); senza, domani",
    )
    args = ap.parse_args()

    cartella = Path(args.cartella).resolve()
    # `calcola` parte dal giorno dopo `da`: per iniziare in `--inizio` si passa il giorno prima.
    da = args.inizio - timedelta(days=1) if args.inizio else None
    piano = calcola(cartella, da=da, solo_nuove=args.solo_nuove)

    print(
        f"gruppo {piano['gruppo']} · {piano['clip']} clip · "
        f"su {', '.join(piano['piattaforme'])} insieme",
        file=sys.stderr,
    )
    for n in piano["note"][:10]:
        print(f"  {n}", file=sys.stderr)
    if len(piano["note"]) > 10:
        print(f"  ... e altri {len(piano['note']) - 10} giorni fermi", file=sys.stderr)
    print("", file=sys.stderr)
    for v in piano["piano"]:
        print(
            f"  {v['quando'].replace('T', ' ')}  clip {v['id']}  ({v['perche']})", file=sys.stderr
        )

    if args.scrivi:
        scrivi(cartella, piano)
        print(f"\nscritto in {cartella / 'publish.json'}", file=sys.stderr)

    print(json.dumps(piano, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
