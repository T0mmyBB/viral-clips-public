"""Esegue il calendario: pubblica quello che e' scaduto, su tutte le piattaforme insieme.

    python esegui.py                       # cosa farebbe, senza farlo
    python esegui.py --davvero             # pubblica sul serio
    python esegui.py --cartella output/x   # una cartella sola

**Chiede «cosa e' scaduto», non «che ora e'».** Su un portatile un'attivita' pianificata
non e' una promessa: le 09:00 arrivano con la macchina in ibernazione e il giro parte
quando qualcuno la sveglia — il 3 settembre alle 07:27, il 4 mai. Un esecutore che
pubblicasse solo *esattamente* all'ora scritta salterebbe la clip e non lo direbbe. Qui
si guarda `quando <= adesso` e non ancora pubblicata, quindi girare tre volte al giorno
recupera qualunque ritardo senza raddoppiare niente.

**Una clip esce su tutte le piattaforme insieme**, ed e' l'unita' del calendario. Ma se
una delle tre fallisce, le altre restano pubblicate: lo stato e' per coppia (clip,
piattaforma), quindi il giro dopo riprende **solo** quella mancante invece di ripubblicare
le riuscite. Il fallimento parziale e' la norma qui, non l'eccezione.

**«Insieme» vuol dire davvero insieme**: le piattaforme di una clip partono nello stesso
istante, un processo per ognuna. Prima erano in fila e la terza cominciava venti minuti
dopo la prima — tre account che dovevano postare alle 19:00 postavano alle 19:00, 19:08 e
19:19. Il collo di bottiglia non e' la CPU: sono tre browser che aspettano tre
caricamenti, cioe' rete e attese, che si sovrappongono bene. **Non e' il caso di
`--jobs` del render**, dove il parallelo e' risultato piu' lento perche' la CPU era gia'
satura da sola.

Quel che invece resta in fila e' l'**invio su Whop**, e non e' un ripiego: il conto Whop
e' uno per gruppo, quindi un profilo browser solo, e due Chromium sullo stesso
`user_data_dir` non convivono. La serratura sta in `orchestrator.py`; la finestra utile
di Whop e' 30 minuti dalla pubblicazione e tre invii in fila ne prendono tre.

Le **clip** invece restano in sequenza. Non e' una svista: due clip sulla stessa
piattaforma a distanza di zero secondi sono la raffica che ha strozzato @canale_lab, e
il ritmo le fermerebbe comunque (`ritmo.py`). Il parallelo qui e' fra piattaforme, che
sono account diversi, mai fra clip.

**Il cancello del ritmo non viene scavalcato.** `orchestrator.py` controlla `ritmo.py` per
conto suo e rifiuta con codice 2 se l'account e' in rampa o troppo vicino all'ultimo post.
Questo file non passa mai `--forza`: se il calendario e il ritmo non sono d'accordo, ha
ragione il ritmo — il calendario e' stato scritto giorni prima e non sa cos'e' successo
nel frattempo.

**E prima di tutto, la campagna deve accettare invii.** Una volta per cartella, prima
della prima clip, si chiede a Whop (`orchestrator.py invii`). In pausa o chiusi, le clip
scadute restano ferme e non e' un guasto: escono da sole al primo giro dopo la
riapertura, senza che nessuno tocchi `publish.json`. **Se lo stato non si legge ci si
ferma lo stesso**, e questo si' e' un guasto: i due errori non costano uguale. Una clip
ferma per niente esce al giro dopo; una clip uscita a campagna chiusa non si invia piu',
perche' la finestra e' di trenta minuti — e' successo il 16 settembre 2026 con la clip
05 di Contoso.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

QUI = Path(__file__).resolve().parent
RADICE = QUI.parent
OUTPUT = RADICE / "output"
ORCHESTRATOR = QUI / "orchestrator.py"

# L'orchestratore si lancia col venv dello scraping, non con `sys.executable`.
# Non e' un dettaglio di comodita': `orchestrator.pubblica_tiktok()` importa
# Playwright **nel proprio processo** (YouTube e Instagram invece delegano a un
# sottoprocesso che il venv giusto ce l'ha gia'), quindi ereditare l'interprete
# di chi ha lanciato questo file significa un `ModuleNotFoundError: playwright`
# su TikTok e **solo** su TikTok. L'attivita' pianificata gira col `.venv` del
# progetto, dove Playwright non c'e': le clip 01 e 02 di Acme Show sono rimaste
# indietro per giorni cosi', uscite su YouTube e mai su TikTok, mentre ogni
# passaggio falliva a meta' senza che la meta' riuscita lo facesse notare.
# Il percorso si trova, non si scrive — stessa forma di `orchestrator.py`.
from interpreti import PYTHON_SCRAPING  # noqa: E402

# Se la campagna e' finita lo dicono il catalogo del radar e le scelte del
# pannello, con la stessa regola che il pannello usa per toglierla dalla coda.
SCELTE = RADICE / "dashboard" / "scelte.json"
DETTAGLI = RADICE / "radar" / "radar_dettagli.json"
sys.path.insert(0, str(RADICE / "radar"))
import registro  # noqa: E402
import tassonomia  # noqa: E402


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def leggi(p: Path, default=None):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def gia_pubblicata(stato: dict, cid: str, piattaforma: str) -> bool:
    """Se questa clip e' gia' uscita su questa piattaforma, comunque sia scritto lo stato.

    Guarda entrambe le forme di chiave — `<id>` e `<id>@<piattaforma>` — perche'
    `orchestrator.chiave()` non migra le vecchie, e un record non riconosciuto
    qui vorrebbe dire **ripubblicare** qualcosa che e' gia' online.
    """
    items = stato.get("items", {})
    for k in (f"{cid}@{piattaforma}", cid):
        r = items.get(k)
        if isinstance(r, dict) and (r.get("platform") or "").lower() == piattaforma:
            return r.get("status") in ("pubblicato", "done")
    return False


def scadute(cartella: Path, adesso: datetime) -> list[dict]:
    """Le clip il cui momento e' passato e che non sono ancora uscite dappertutto."""
    cfg = leggi(cartella / "publish.json")
    if not cfg or not cfg.get("items"):
        return []
    piattaforme = [p.lower() for p in (cfg.get("calendario_piattaforme") or [])]
    if not piattaforme:
        # Senza calendario non c'e' niente di scaduto — vero, e da dire. Prima
        # si tornava a mani vuote in silenzio: una cartella con clip mai
        # programmate spariva da ogni giro, e da fuori era identica a una
        # cartella finita. **Il modo di fallire che questo progetto insegue e'
        # esattamente questo**, quindi lo si dichiara. Solo se resta lavoro:
        # una cartella tutta pubblicata non ha piu' niente da programmare.
        da_fare = sum(1 for it in cfg["items"] if not it.get("quando"))
        if da_fare:
            log(
                f"  {cartella.name}: nessun calendario ({da_fare} clip non "
                f"programmate). Serve `calendario.py --cartella <...> --scrivi`, "
                f"o la campagna e' finita e la cartella va chiusa."
            )
        return []
    stato = leggi(cartella / "publish_state.json", {"items": {}}) or {"items": {}}

    # Una piattaforma sospesa nel registro non esce da nessuna cartella del
    # gruppo, e lo si dice a ogni giro: tolta in silenzio sembrerebbe finita.
    # Le altre piattaforme delle stesse clip escono come sempre.
    ferme = {p: registro.sospesa(cfg.get("gruppo"), p) for p in piattaforme}
    ferme = {p: motivo for p, motivo in ferme.items() if motivo}
    in_attesa = dict.fromkeys(ferme, 0)

    fuori = []
    for it in cfg["items"]:
        if it.get("sospesa"):
            # Una clip sospesa a mano non esce finche' qualcuno non toglie il
            # campo, e lo si dice a ogni giro: saltarla in silenzio la farebbe
            # sembrare pubblicata. Il caso che l'ha introdotta: il 16 settembre
            # 2026 Contoso ha chiuso gli invii Whop, e una clip che esce mentre la
            # campagna non la accetta non si recupera piu' — la finestra
            # d'invio e' di trenta minuti dalla pubblicazione.
            log(f"  {cartella.name} clip {it['id']}: sospesa, non esce — {it['sospesa']}")
            continue
        quando = it.get("quando")
        if not quando:
            continue
        try:
            momento = datetime.fromisoformat(quando)
        except ValueError:
            log(f"  {cartella.name} clip {it['id']}: 'quando' illeggibile ({quando!r})")
            continue
        if momento > adesso:
            continue
        # Una piattaforma `a_mano` la pubblica una persona, non questo file: il
        # caso che l'ha introdotta e' la prova di TikTok del 29 settembre 2026,
        # due clip caricate dall'app del telefono per sapere se lo zero di views
        # viene dalla pubblicazione dal desktop. Le altre piattaforme della
        # stessa clip escono come sempre.
        a_mano = it.get("a_mano") or {}
        for p in piattaforme:
            if p in a_mano and not gia_pubblicata(stato, it["id"], p):
                log(f"  {cartella.name} clip {it['id']} su {p}: a mano, non esce da qui — {a_mano[p]}")
        for p in ferme:
            if p not in a_mano and not gia_pubblicata(stato, it["id"], p):
                in_attesa[p] += 1
        mancanti = [
            p
            for p in piattaforme
            if p not in a_mano and p not in ferme and not gia_pubblicata(stato, it["id"], p)
        ]
        if mancanti:
            fuori.append(
                {
                    "id": it["id"],
                    "quando": quando,
                    "mancanti": mancanti,
                    "ritardo_ore": round((adesso - momento).total_seconds() / 3600, 1),
                }
            )
    for p, quante in in_attesa.items():
        if quante:
            log(
                f"  {cartella.name}: {p} e' sospesa nel registro, {quante} clip scadute "
                f"non escono li' — {ferme[p]}"
            )
    return fuori


def campagna_finita(cartella: Path) -> str | None:
    """
    Perche' la campagna di questa cartella e' finita, o `None` se paga ancora.

    Una campagna finita non si chiede nemmeno a Whop. Contoso, a budget
    esaurito, rispondeva «in pausa» invece di «chiusi», e le sue clip restavano
    «ferme in attesa della riapertura» per sempre: tre volte al giorno un
    browser aperto per niente e una finestra con «3 clip scadute». La regola
    e' `tassonomia.campagna_finita`, la stessa del pannello.
    """
    uuid = (leggi(cartella / "publish.json") or {}).get("whop_campaign_uuid")
    if not uuid:
        return None
    schede = leggi(DETTAGLI, []) or []
    scheda = next((s for s in schede if s.get("id") == uuid), None)
    scelta = next(
        (s for s in (leggi(SCELTE, {}) or {}).get("scelte", []) if s.get("campagna_id") == uuid),
        None,
    )
    return tassonomia.campagna_finita(uuid, scheda, bool(schede), scelta)


def decidi_invii(esito: str) -> str:
    """
    Cosa fare delle clip scadute di una cartella, dato lo stato degli invii.

    `pubblica` solo su «aperti», o dove non c'e' una campagna da chiedere —
    li' `orchestrator.py pubblica` si ferma da solo e dice perche'. `ferma` su
    pausa e chiusura: e' la campagna, non un difetto. **Tutto il resto e'
    `guasto`**, compreso un esito mai visto: non sapere se la campagna accetta
    la clip non e' un permesso di pubblicarla.
    """
    if esito in ("aperti", "nessuna campagna"):
        return "pubblica"
    if esito in ("in pausa", "chiusi"):
        return "ferma"
    return "guasto"


def controlla_invii(cartella: Path) -> str:
    """Lo stato degli invii della campagna di questa cartella, chiesto all'orchestratore."""
    if not PYTHON_SCRAPING.exists():
        log(f"  manca {PYTHON_SCRAPING}: gli invii Whop non si possono controllare")
        return "non verificato"
    esito = subprocess.run(
        [str(PYTHON_SCRAPING), str(ORCHESTRATOR), "--folder", str(cartella), "invii"],
        cwd=str(QUI),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    righe = [r for r in (esito.stdout or "").splitlines() if r.startswith("invii: ")]
    if not righe:
        coda = (esito.stderr or "").strip().splitlines()
        if coda:
            log(f"  controllo invii: {coda[-1][:200]}")
        return "non verificato"
    return righe[-1].removeprefix("invii: ").strip()


def pubblica(cartella: Path, cid: str, piattaforma: str, davvero: bool) -> tuple[bool, str]:
    """Una pubblicazione, delegata all'orchestratore che sa gia' come si fa.

    Non si reimplementa niente qui: il controllo dell'handle collegato, il
    profilo browser giusto, l'invio su Whop e il record di stato stanno tutti
    di la'. Questo file decide **quando**, non come.
    """
    if not PYTHON_SCRAPING.exists():
        return False, (
            f"manca {PYTHON_SCRAPING} — lo crea setup.ps1. Senza Playwright TikTok non parte"
        )
    cmd = [
        str(PYTHON_SCRAPING),
        str(ORCHESTRATOR),
        "--folder",
        str(cartella),
        "pubblica",
        "--platform",
        piattaforma,
        "--id",
        cid,
    ]
    if not davvero:
        cmd.append("--dry-run")
    esito = subprocess.run(
        cmd, cwd=str(QUI), capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    if esito.returncode == 0:
        return True, "ok"
    # Il codice 2 e' il cancello del ritmo, non un guasto: si riprova domani.
    coda = (esito.stderr or esito.stdout or "").strip().splitlines()
    motivo = coda[-1][:200] if coda else f"codice {esito.returncode}"
    return False, ("ritmo: " if esito.returncode == 2 else "") + motivo


def insieme(
    cartella: Path, cid: str, piattaforme: list[str], davvero: bool, in_fila: bool
) -> list[tuple[str, bool, str]]:
    """
    Le piattaforme di una clip, tutte nello stesso momento.

    I thread qui non calcolano niente: ognuno resta fermo dentro
    `subprocess.run` ad aspettare il suo agente, quindi il GIL non c'entra e il
    lavoro vero lo fanno tre processi separati.

    L'ordine dell'elenco che torna e' quello di `piattaforme` e non quello di
    arrivo: il registro di un giro deve leggersi uguale a se stesso, altrimenti
    due esecuzioni identiche sembrano diverse.
    """
    if in_fila or len(piattaforme) < 2:
        return [(p, *pubblica(cartella, cid, p, davvero)) for p in piattaforme]
    with ThreadPoolExecutor(max_workers=len(piattaforme)) as pool:
        futuri = [(p, pool.submit(pubblica, cartella, cid, p, davvero)) for p in piattaforme]
        return [(p, *f.result()) for p, f in futuri]


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Pubblica le clip il cui momento e' arrivato, su tutte le piattaforme"
    )
    ap.add_argument(
        "--cartella", default=None, help="una cartella sola (default: tutte quelle in output/)"
    )
    ap.add_argument(
        "--davvero",
        action="store_true",
        help="pubblica sul serio. Senza, dice soltanto cosa farebbe",
    )
    ap.add_argument(
        "--in-fila",
        action="store_true",
        dest="in_fila",
        help="una piattaforma alla volta invece che tutte insieme: "
        "serve a leggere un guasto senza tre browser aperti",
    )
    args = ap.parse_args()

    adesso = datetime.now()
    cartelle = (
        [Path(args.cartella).resolve()]
        if args.cartella
        else sorted(p.parent for p in OUTPUT.glob("*/publish.json"))
    )

    log(f"{'PUBBLICO' if args.davvero else 'PROVA (niente esce)'} — {adesso:%Y-%m-%d %H:%M}")
    fatte, fallite, saltate, ferme, finite = 0, 0, 0, 0, 0

    for cartella in cartelle:
        da_fare = scadute(cartella, adesso)
        if not da_fare:
            continue
        finita = campagna_finita(cartella)
        if finita:
            # Non e' un guasto e non e' un'attesa: queste clip non usciranno
            # piu'. Lo si dice in una riga e si passa oltre, senza aprire Whop.
            finite += len(da_fare)
            log(f"\n{cartella.name}: campagna finita ({finita}), {len(da_fare)} clip non escono")
            continue
        log(f"\n{cartella.name}: {len(da_fare)} clip scadute")
        # Anche in prova: il controllo legge e basta, e una prova che dice
        # «pubblicherei» a campagna in pausa racconta un giro che non avverra'.
        invii = controlla_invii(cartella)
        decisione = decidi_invii(invii)
        if decisione != "pubblica":
            quali = ", ".join(v["id"] for v in da_fare)
            if decisione == "ferma":
                ferme += len(da_fare)
                log(
                    f"  invii Whop {invii}: clip {quali} ferme, escono al primo giro "
                    f"dopo la riapertura"
                )
            else:
                fallite += len(da_fare)
                log(
                    f"  invii Whop {invii}: clip {quali} NON pubblicate — senza sapere se "
                    f"la campagna le accetta non escono. Prova a mano: "
                    f"orchestrator.py --folder {cartella} invii"
                )
            continue
        for v in da_fare:
            ritardo = f" (in ritardo di {v['ritardo_ore']}h)" if v["ritardo_ore"] > 1 else ""
            modo = "" if args.in_fila or len(v["mancanti"]) < 2 else " (insieme)"
            log(
                f"  clip {v['id']}, previsto {v['quando'].replace('T', ' ')}{ritardo}"
                f" -> {', '.join(v['mancanti'])}{modo}"
            )
            for p, ok, motivo in insieme(
                cartella, v["id"], v["mancanti"], args.davvero, args.in_fila
            ):
                if ok:
                    fatte += 1
                    log(f"    {p}: fatto")
                elif motivo.startswith("ritmo:"):
                    saltate += 1
                    log(f"    {p}: rimandato — {motivo}")
                else:
                    fallite += 1
                    log(f"    {p}: FALLITO — {motivo}")

    log(
        f"\n{fatte} pubblicate, {saltate} rimandate dal ritmo, "
        f"{ferme} ferme per gli invii, {finite} di campagne finite, {fallite} fallite"
    )
    print(
        json.dumps(
            {
                "fatte": fatte,
                "rimandate": saltate,
                "ferme": ferme,
                "finite": finite,
                "fallite": fallite,
                "davvero": args.davvero,
                "quando": adesso.isoformat(timespec="seconds"),
            },
            ensure_ascii=False,
        )
    )
    # Un fallimento vero deve tingere di rosso l'attivita' pianificata: una
    # riga in un log che nessuno apre non e' un avviso.
    sys.exit(1 if fallite else 0)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
