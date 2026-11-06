#!/usr/bin/env python
"""
orchestrator.py — Orchestratore multi-piattaforma (YouTube, TikTok, Instagram) con invio automatico su Whop.

Comandi:
  python orchestrator.py status
  python orchestrator.py pubblica --platform [youtube|tiktok|instagram] --id <clip_id> [--dry-run] [--forza]
  python orchestrator.py --folder <cartella> invii

Il ritmo (`min_gap_seconds`, `max_per_day` di publish.json) è verificato prima di
pubblicare e vale per account, cioè per piattaforma: `--forza` lo scavalca e va
usato sapendo cosa costa.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import notifica
import registro
import ritmo
import serratura

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

# Il venv condiviso dello scraping sta nella home di chi lavora, dove lo mette
# `setup.ps1` (`$env:USERPROFILE\.venvs\scraping`): non a un indirizzo scritto
# qui dentro. E' la stessa forma che usano gia' `accedi.py` e
# `radar/giornaliero.py`, ed e' l'unico posto che era rimasto indietro. Il
# percorso cablato era vero sulla macchina su cui e' stato scritto e falso sulla
# seconda, e falliva come WinError 2 dentro un subprocess — cioe' una
# pubblicazione che non parte senza dire quale file manchi.
from interpreti import PYTHON_PUBLISH, PYTHON_SCRAPING  # noqa: E402

# `--folder` per `status` puo' mancare: leggere la cartella sbagliata costa una
# tabella inutile. Per `pubblica` no — vedi `cmd_pubblica()`.
DEFAULT_FOLDER = ROOT / "output" / "why-a16z-is-betting-7m-on-clipping-the-cap-table-ep-25-w-clo"

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def log(msg: str) -> None:
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    log_dir = HERE / "logs"
    log_dir.mkdir(exist_ok=True)
    with (log_dir / "orchestrator.log").open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def load_json(path: Path, default=None):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default
    return default


def save_json(path: Path, data) -> None:
    serratura.scrivi_atomico(path, json.dumps(data, ensure_ascii=False, indent=2))


# Quanto si aspetta il proprio turno. Lo stato e' un file JSON: chi lo tiene lo
# tiene per millisecondi, e un minuto e' gia' un'eternita'. Whop invece e' un
# browser che carica una pagina, compila un modulo e aspetta la conferma: le
# tre piattaforme della stessa clip finiscono insieme e chi arriva terzo
# aspetta due invii interi. Quindici minuti stanno dentro la finestra dei
# trenta, e chi la sfora se lo sente dire invece di inviare a vuoto.
ATTESA_STATO, SCADUTA_STATO = 60.0, 120.0
ATTESA_WHOP, SCADUTA_WHOP = 900.0, 1800.0
# Il profilo browser di una piattaforma, mentre si carica. Chi aspetta e' di
# solito `leggi_account.py`, che lo tiene un paio di minuti: cinque bastano. La
# scadenza e' lunga perche' un caricamento con l'invio Whop dietro supera la
# mezz'ora, e una serratura tolta a lavoro in corso rimette due browser sulla
# stessa cartella.
ATTESA_PROFILO, SCADUTA_PROFILO = 300.0, 3600.0


def leggi_esito_whop(uscita: str) -> str:
    """
    L'esito dell'invio come lo scrive `whop_agent`: `esito: <esito> — <testo>`.

    **Intero, non la prima parola.** Con `(\\w+)` «non registrato» diventava
    «non», che nello stato non vuol dire niente e nel pannello si leggeva come
    un invio mai tentato. Senza la riga, l'esito e' «non confermato»: tutte le
    uscite anticipate di `cmd_invia` escono con codice 0, e quello zero non e'
    un invio.
    """
    m = re.search(r"esito:\s*(.+?)\s*(?:—|$)", uscita, re.M)
    return m.group(1).strip() if m and m.group(1).strip() else "non confermato"


def leggi_esito_invii(uscita: str) -> str:
    """
    Lo stato degli invii come lo scrive `whop_agent.py invii`: `esito invii: <esito>`.

    Senza la riga l'esito e' «non verificato», per la stessa ragione di
    `leggi_esito_whop`: le uscite anticipate dell'agente escono con codice 0, e
    uno zero non dice che qualcuno abbia guardato. Si prende l'**ultima** riga:
    e' quella scritta dopo aver letto la risposta del pannello.
    """
    trovati = re.findall(r"esito invii:\s*(.+?)\s*$", uscita, re.M)
    return trovati[-1] if trovati else "non verificato"


def leggi_esito_iscrizione(uscita: str) -> str:
    """
    L'esito di `whop_agent.py join`: la riga `iscrizione: <esito>`.

    Senza la riga e' «non verificato»: l'agente esce 0 anche quando muore prima
    di guardare, e «non so» non e' «fatta».
    """
    trovati = re.findall(r"^iscrizione:\s*(.+?)\s*$", uscita, re.M)
    return trovati[-1] if trovati else "non verificato"


# Gli esiti con cui una campagna scelta si puo' lavorare senza che nessuno
# guardi Whop. Gli altri chiedono una persona, e lo si dice con una notifica.
ISCRIZIONE_A_POSTO = ("fatta", "gia' dentro")
ISCRIZIONI = HERE / "iscrizioni.json"
DETTAGLI_RADAR = HERE.parent / "radar" / "radar_dettagli.json"


def cmd_iscrivi(args) -> None:
    """
    Entra nella campagna scelta, sul conto Whop del gruppo. Lo lancia «scegli».

    Fino al 7 ottobre 2026 l'iscrizione la faceva l'utente a mano, per ogni
    campagna, ed era il passo che separava «scelta» da «lavorabile». Adesso
    parte da sola: indirizzo e titolo vengono dal radar, il profilo e la
    serratura sono quelli dell'invio (`comando_whop`), e l'esito si scrive in
    `iscrizioni.json`. Un esito che chiede una persona manda una notifica.
    """
    schede = {s.get("id"): s for s in json.loads(DETTAGLI_RADAR.read_text(encoding="utf-8"))}
    scheda = schede.get(args.campagna)
    if not scheda or not scheda.get("join"):
        sys.exit(f"la campagna {args.campagna} non ha un indirizzo d'iscrizione nel radar")
    cmd, profilo, nome_serratura = comando_whop(args.gruppo)
    cmd += ["--headless", "join", "--url", scheda["join"], "--titolo", scheda.get("titolo") or ""]
    if profilo is not None and not profilo.is_dir():
        esito = "non loggato"
    else:
        try:
            with serratura.presa(nome_serratura, attesa=ATTESA_WHOP, scaduta=SCADUTA_WHOP):
                res = subprocess.run(
                    cmd, capture_output=True, text=True, encoding="utf-8", errors="replace"
                )
            uscita = res.stdout + "\n" + res.stderr
            print(uscita, file=sys.stderr)
            esito = leggi_esito_iscrizione(uscita)
        except serratura.Occupata as exc:
            log(f"iscrizione non tentata: {exc}")
            esito = "non verificato"

    with serratura.presa("iscrizioni", attesa=30.0, scaduta=60.0):
        try:
            tutte = json.loads(ISCRIZIONI.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            tutte = {}
        tutte[args.campagna] = {
            "titolo": scheda.get("titolo"),
            "gruppo": args.gruppo,
            "esito": esito,
            "quando": datetime.now().isoformat(timespec="seconds"),
        }
        serratura.scrivi_atomico(ISCRIZIONI, json.dumps(tutte, ensure_ascii=False, indent=2))
    if esito not in ISCRIZIONE_A_POSTO:
        notifica.avvisa(
            f"Iscrizione Whop: {esito}",
            f"«{scheda.get('titolo')}» non risulta iscritta in automatico. "
            "Va guardata su Whop prima di pubblicare.",
        )
    print(f"iscrizione: {esito}")
    if esito in ISCRIZIONE_A_POSTO:
        leggi_materiali(scheda, args.gruppo)


# Dal sito nuovo (fra il 5 e il 27 ottobre 2026) i materiali di una campagna si
# vedono solo da iscritti: l'iscrizione e' il momento in cui arrivano, quindi
# si leggono li' e vanno nell'archivio da cui `dettagli.py` li riprende.
MATERIALI_NOTI = ROOT / "radar" / "materiali_noti.json"


def leggi_esito_materiali(uscita: str) -> tuple[str, list[dict]]:
    """L'esito di `whop_agent.py materiali` e le voci lette; senza riga, «non verificato»."""
    esiti = re.findall(r"^materiali:\s*(.+?)\s*$", uscita, re.M)
    voci = re.findall(r"^materiali_json:\s*(\[.*\])\s*$", uscita, re.M)
    esito = esiti[-1] if esiti else "non verificato"
    try:
        lette = json.loads(voci[-1]) if voci and esito == "letti" else []
    except ValueError:
        return "non verificato", []
    return esito, lette


def archivia_letti(archivio: dict, campagna: str, voci: list[dict], oggi: str) -> dict:
    """La voce dell'archivio per una lettura dalla scheda: la stessa forma di `dettagli.py`."""
    archivio[campagna] = {
        "materiali": [v["url"] for v in voci],
        "materiali_dett": voci,
        "letto_il": oggi,
        "da": "scheda",
    }
    return archivio


def leggi_materiali(scheda: dict, gruppo: str) -> str:
    """
    Legge i materiali dalla scheda, li archivia e risolve subito le sorgenti
    della campagna. Torna l'esito.

    Una lettura che non riesce non tocca l'archivio: la lettura vecchia, se
    c'e', vale piu' di niente — la stessa regola di `riporta_materiali`.
    """
    cmd, _profilo, nome_serratura = comando_whop(gruppo)
    cmd += [
        "--headless", "materiali", "--url", scheda["join"],
        "--titolo", scheda.get("titolo") or "", "--campagna", scheda["id"],
    ]
    try:
        with serratura.presa(nome_serratura, attesa=ATTESA_WHOP, scaduta=SCADUTA_WHOP):
            res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        uscita = res.stdout + "\n" + res.stderr
        print(uscita, file=sys.stderr)
        esito, voci = leggi_esito_materiali(uscita)
    except serratura.Occupata as exc:
        log(f"materiali non letti: {exc}")
        esito, voci = "non verificato", []
    print(f"materiali: {esito}")
    if not voci:
        return esito

    oggi = datetime.now().date().isoformat()
    with serratura.presa("materiali_noti", attesa=30.0, scaduta=60.0):
        archivio = load_json(MATERIALI_NOTI, {}) or {}
        archivia_letti(archivio, scheda["id"], voci, oggi)
        serratura.scrivi_atomico(MATERIALI_NOTI, json.dumps(archivio, ensure_ascii=False))
    # La scheda del radar si completa adesso, invece che al prossimo giro: il
    # risolutore la legge da li', e il girato serve a chi ha appena scelto.
    with serratura.presa("radar_dettagli", attesa=30.0, scaduta=60.0):
        schede = load_json(DETTAGLI_RADAR, []) or []
        for s in schede:
            if s.get("id") == scheda["id"]:
                s["materiali"] = [v["url"] for v in voci]
                s["materiali_dett"] = voci
                s["materiali_letti_il"] = oggi
        serratura.scrivi_atomico(DETTAGLI_RADAR, json.dumps(schede, ensure_ascii=False, indent=2))
    log(f"materiali di «{scheda.get('titolo')}»: {len(voci)} link archiviati, risolvo le sorgenti")
    subprocess.run(
        [sys.executable, "sorgenti.py", "--id", scheda["id"]],
        cwd=ROOT / "radar", capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return esito


def cmd_materiali(args) -> None:
    """Rilegge i materiali di una campagna gia' iscritta. Non iscrive niente."""
    schede = {s.get("id"): s for s in json.loads(DETTAGLI_RADAR.read_text(encoding="utf-8"))}
    scheda = schede.get(args.campagna)
    if not scheda or not scheda.get("join"):
        sys.exit(f"la campagna {args.campagna} non ha un indirizzo nel radar")
    leggi_materiali(scheda, args.gruppo)


def whop_di(gruppo: str | None) -> tuple[Path | None, str]:
    """
    Il profilo Whop di un gruppo e il nome della sua serratura.

    La serratura porta il nome della **cartella del profilo**, non del gruppo:
    il laboratorio e una cartella senza gruppo usano lo stesso `.whop-profile`,
    e due nomi diversi per un profilo solo sono una serratura che non chiude.
    """
    profilo = registro.whop_profilo(gruppo) if gruppo else None
    return profilo, "whop-" + (profilo.name if profilo else ".whop-profile")


def profilo_pubblicazione(cfg: dict, gruppo: str | None, platform: str) -> Path:
    """
    La cartella del profilo browser da cui esce una pubblicazione.

    Era calcolata dentro ogni ramo, e adesso serve anche alla serratura che la
    chiude mentre il browser e' aperto: `leggi_account.py` apre gli stessi
    profili per leggere le views, e due Chromium sulla stessa cartella non
    convivono. La serratura deve portare il nome della cartella che il browser
    apre **davvero**, quindi la cartella si calcola in un posto solo.
    """
    if platform == "youtube":
        # La stessa regola di `youtube_agent.py`: col gruppo il registro, senza
        # il profilo storico.
        return (
            registro.profilo_obbligatorio(gruppo, "youtube") if gruppo else HERE / ".youtube-chrome"
        )
    if platform == "tiktok":
        profilo = registro.profilo(gruppo, "tiktok", HERE / ".tiktok-canale_lab")
        if not profilo.exists() and not gruppo:
            profilo = HERE / ".browser-profile"
        return profilo
    return registro.profilo(
        gruppo, "instagram", HERE / cfg.get("instagram_profile", ".instagram-profile")
    )


def comando_whop(gruppo: str | None) -> tuple[list[str], Path | None, str]:
    """
    L'inizio del comando `whop_agent.py` per un gruppo, con profilo e serratura.

    Lo usano in due — l'invio dopo la pubblicazione e il controllo degli invii
    prima (`cmd_invii`) — e devono coincidere: **la sessione che dice «aperti» e'
    quella che poi invia**, e i due Chromium non convivono sullo stesso profilo.
    Due copie di queste righe divergerebbero al primo ritocco, in silenzio.
    """
    profilo, nome_serratura = whop_di(gruppo)
    cmd = [interprete(PYTHON_SCRAPING, "l'agente Whop (Scrapling)"), str(HERE / "whop_agent.py")]
    if gruppo:
        cmd += ["--gruppo", gruppo]
        # `--gruppo` da solo fa dedurre `.whop-<gruppo>`, che e' giusto per i
        # gruppi nuovi e falso per il laboratorio. Il registro lo sa: si passa
        # anche la cartella, e `whop_agent` fa vincere quella.
        if profilo:
            cmd += ["--profilo", str(profilo)]
    return cmd, profilo, nome_serratura


def scrivi_stato(
    state_file: Path, cid: str, platform: str, campi: dict, da_capo: bool = False
) -> None:
    """
    Aggiorna il record di (clip, piattaforma) rileggendo lo stato dal disco.

    **Non si riscrive la copia che si aveva in memoria.** Da quando le tre
    piattaforme escono insieme (`esegui.py`), tre processi lavorano sullo
    stesso `publish_state.json`: ognuno lo aveva letto all'avvio, e chi salva
    per ultimo rimetterebbe sul disco una versione in cui le altre due
    pubblicazioni non sono mai avvenute. Il video resta online, lo stato dice
    di no, e il giro dopo ripubblica.

    Quindi: serratura, rilettura, modifica del solo record proprio,
    riscrittura. E' lo stesso schema del `--jobs` del render — la parte
    parallela non e' il lavoro, e' la scrittura.
    """
    with serratura.presa(
        f"stato-{state_file.parent.name}", attesa=ATTESA_STATO, scaduta=SCADUTA_STATO
    ):
        state = load_json(state_file, {"items": {}}) or {"items": {}}
        items = state.setdefault("items", {})
        k = chiave(state, cid, platform)
        voce = {} if da_capo else dict(items.get(k) or {})
        voce.update(campi)
        items[k] = voce
        save_json(state_file, state)


def interprete(percorso: Path, a_cosa: str) -> str:
    """
    L'interprete da usare, o un errore che dice quale file manca.

    Senza questo controllo un venv assente arriva fino a `subprocess.run` e
    torna come WinError 2 — «file non trovato», senza dire *quale* — proprio
    mentre si sta pubblicando, cioe' nel punto in cui una diagnosi vaga costa
    di piu'.
    """
    if not percorso.exists():
        sys.exit(f"manca l'interprete per {a_cosa}: {percorso}\n  lo ricrea publish\\setup.ps1")
    return str(percorso)


def tag(handles: dict, platform: str) -> str:
    """
    Lo @handle da mettere in didascalia su questa piattaforma.

    **Niente ripiego cablato.** Il valore di prima era `@AcmeShowTV`,
    l'handle della prima campagna: una cartella senza `handles` avrebbe taggato
    in didascalia **il canale di un'altra campagna**, con l'aria di aver fatto
    la cosa giusta. E' lo stesso difetto del profilo browser (vedi
    `registro.py`) e si cura allo stesso modo: ci si ferma.

    Chi va taggato non si ricava dal registro: quello dice da quale account
    *nostro* si pubblica, mentre questo e' il canale di **chi ha fatto il
    video**, e lo dice il brief della campagna. Percio' l'unica fonte e'
    `handles` in publish.json, che `prepara.py` fa scrivere leggendo il brief.

    Instagram puo' ricadere su TikTok, ed e' un ripiego **dichiarato e dentro
    lo stesso file**: le due piattaforme condividono l'handle quasi sempre.
    """
    chiavi = {"youtube": ("YouTube",), "tiktok": ("TikTok",), "instagram": ("Instagram", "TikTok")}[
        platform
    ]
    for chiave_h in chiavi:
        valore = (handles.get(chiave_h) or "").strip()
        if valore:
            if chiave_h != chiavi[0]:
                log(f"handles non dice «{chiavi[0]}»: uso quello di {chiave_h} ({valore})")
            return valore
    sys.exit(
        f"publish.json non dice l'handle {chiavi[0]} dentro `handles`.\n"
        f"  Non pubblico: la didascalia uscirebbe con il tag di un'altra campagna.\n"
        f'  Aggiungi "handles": {{"{chiavi[0]}": "@..."}} leggendo il brief.'
    )


def didascalia(caption: str, handles: dict, tag_target: str) -> str:
    """
    La didascalia con l'handle di *questa* piattaforma al posto di quello di
    un'altra.

    Lo stesso canale ha tre grafie — `@AcmeShowTV`, `@acmeshowtv`,
    `@acmeshow.tv` — e la didascalia ne contiene una sola, quella che Claude
    ha scritto leggendo il brief. Qui si porta a quella giusta per la
    piattaforma su cui si sta uscendo.

    Le grafie da cercare vengono da `handles`. Prima erano i tre nomi della
    prima campagna scritti dentro il codice, e su ogni **altra** campagna non
    corrispondevano a niente: la sostituzione non avveniva e la clip usciva su
    TikTok con l'handle YouTube — senza un errore, che e' il modo peggiore.

    Si sostituisce **una grafia sola e poi si esce**: incatenare le
    sostituzioni su handle che si contengono a vicenda (`@janedoe` dentro
    `@contosowithjanedoe @janedoe`) duplica il tag. Per la stessa ragione, se
    il tag giusto c'e' gia' non si tocca niente.
    """
    if tag_target in caption:
        return caption
    # Ordine deciso: piu' lunga prima, e a parita' alfabetico. Un ordine che
    # dipende dall'iterazione di un set cambierebbe risultato tra due esecuzioni
    # identiche.
    altre = sorted(
        {(v or "").strip() for v in handles.values()} - {"", tag_target}, key=lambda s: (-len(s), s)
    )
    for vecchia in altre:
        if vecchia in caption:
            log(f"didascalia: «{vecchia}» -> «{tag_target}»")
            return caption.replace(vecchia, tag_target)
    return caption


def get_paths(folder_path: Path):
    cfg_file = folder_path / "publish.json"
    state_file = folder_path / "publish_state.json"
    if not cfg_file.exists():
        sys.exit(f"File di configurazione non trovato: {cfg_file}")
    cfg = load_json(cfg_file, {})
    state = load_json(state_file, {"items": {}})
    return cfg, state, state_file


def chiave(state: dict, cid: str, platform: str) -> str:
    """
    La chiave di stato di una clip **su una piattaforma**.

    Finche' una cartella usciva su una piattaforma sola, `items[cid]` bastava.
    Con i tre account per nicchia la stessa clip esce su YouTube, TikTok e
    Instagram, e la chiave nuda diventa un sovrascrittore: la pubblicazione su
    TikTok cancellava il record di YouTube — URL, orario, esito Whop — senza
    che niente si lamentasse. Da qui in poi la chiave e' `<id>@<piattaforma>`.

    Le chiavi vecchie restano valide e non si migrano: se `items[cid]` esiste
    gia' ed e' di questa piattaforma, si continua a scrivere li'. Riscrivere il
    passato per uniformarlo e' il modo piu' rapido di perderlo.
    """
    vecchia = state.get("items", {}).get(cid)
    if isinstance(vecchia, dict) and (vecchia.get("platform") or "").lower() == platform:
        return cid
    return f"{cid}@{platform}"


def record(state: dict, cid: str, platform: str) -> dict:
    """Il record di quella clip su quella piattaforma, comunque sia stato scritto."""
    items = state.get("items", {})
    diretto = items.get(f"{cid}@{platform}")
    if isinstance(diretto, dict):
        return diretto
    vecchio = items.get(cid)
    if isinstance(vecchio, dict) and (vecchio.get("platform") or "").lower() == platform:
        return vecchio
    return {}


def piattaforme_attese(cfg: dict, state: dict) -> list[str]:
    """
    Su quante piattaforme deve uscire ogni clip di questa cartella.

    In ordine di quanto e' informata la fonte: il calendario gia' scritto
    (`calendario_piattaforme`, che `calendario.py` ha ricavato dalle identita'
    vere del gruppo), poi `platforms`, poi il gruppo, e per ultima la vecchia
    `platform` singola.

    **L'ordine non e' estetica: il campo vecchio mentiva sul piano.** Entrambe
    le cartelle dicono `platform: "YouTube"` perche' sono nate quando una
    consegna usciva su una piattaforma sola, ma la consegna Contoso e' gia'
    programmata su tre — e' scritto nel suo `calendario_piattaforme`, ed e'
    quello che `esegui.py` pubblica. Leggendo il campo vecchio, `status`
    mostrava «7 pubblicazioni» dove il piano ne ha 21: una vista che dichiara
    finito un terzo del lavoro.

    Una piattaforma su cui **abbiamo gia' pubblicato** e' attesa per
    definizione, comunque sia scritta la cartella: la Acme Show dichiara solo
    YouTube e ha tre clip su TikTok, e senza questa riga sarebbero lavoro fatto
    e non visto.
    """
    attese = [
        p.lower()
        for p in (
            cfg.get("calendario_piattaforme")
            or cfg.get("platforms")
            or registro.piattaforme(cfg.get("gruppo"))
            or ([cfg["platform"]] if cfg.get("platform") else [])
        )
    ]
    fatte = {
        (r.get("platform") or "").lower()
        for r in state.get("items", {}).values()
        if isinstance(r, dict)
    }
    return attese + sorted(fatte - set(attese) - {""})


def cmd_status(args) -> None:
    folder = Path(args.folder).resolve() if args.folder else DEFAULT_FOLDER
    cfg, state, _ = get_paths(folder)

    items = cfg.get("items", [])
    campaign_name = cfg.get("campaign", "Sconosciuta")
    provider = cfg.get("provider", "Clip Farm")

    print("\n=======================================================")
    print(f" CAMPAGNA: {campaign_name} ({provider})")
    print(f" CARTELLA: {folder.name}")
    print("=======================================================\n")

    attese = piattaforme_attese(cfg, state)
    pubblicati = 0
    invii_whop = 0

    intestazione = f"{'#':<3} | {'ID':<4} | " + " | ".join(f"{p:<11}" for p in attese) + " | Titolo"
    print(intestazione)
    print("-" * max(90, len(intestazione)))

    for idx, it in enumerate(items, 1):
        cid = it["id"]
        celle = []
        for plat in attese:
            st = record(state, cid, plat)
            stato_pub = st.get("status", "in coda")
            if stato_pub == "pubblicato":
                pubblicati += 1
            if st.get("whop_status") == "inviato":
                invii_whop += 1
            celle.append(f"{stato_pub:<11}")
        titolo = it.get("title", "")[:35]
        print(f"{idx:<3} | {cid:<4} | " + " | ".join(celle) + f" | {titolo}")

    print("-" * max(90, len(intestazione)))
    totale = len(items) * max(1, len(attese))
    print(
        f"Totale: {pubblicati}/{totale} pubblicazioni "
        f"({len(items)} clip x {len(attese)} piattaforme) | "
        f"{invii_whop} inviate a Whop\n"
    )

    for plat in attese:
        motivo = cadenza(cfg, state, plat)
        print(f"Ritmo {plat}: {motivo if motivo else 'si può pubblicare adesso'}")
    print()


def pubblica_tiktok(
    video_path: Path, caption: str, dry_run: bool = False, gruppo: str | None = None
) -> str | None:
    """
    Carica la clip su TikTok tramite Playwright e recupera l'URL.

    Il profilo e l'handle atteso vengono dal registro, non da qui. Prima erano
    scritti dentro questa funzione — `.tiktok-canale_lab`, con ripiego su
    `.browser-profile` e handle `canale_lab` se il rilevamento falliva — e
    finche' l'account TikTok era uno solo la cosa funzionava per coincidenza.
    Con un gruppo per nicchia quel ripiego pubblicherebbe la clip di una
    campagna business **dall'account del laboratorio, in silenzio**.
    """
    from playwright.sync_api import sync_playwright

    import publish_agent

    profile = profilo_pubblicazione({}, gruppo, "tiktok")
    atteso = registro.handle(gruppo, "tiktok")

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            str(profile),
            headless=False,
            user_agent=publish_agent.UA,
            viewport={"width": 1280, "height": 850},
            # Prima qui non c'era: questo lancio ereditava lingua e fuso di
            # Windows, quindi TikTok si presentava in italiano mentre
            # Instagram, dallo stesso gruppo, diceva quel che gli passava
            # `instagram_agent`. Ora e' un valore solo (`registro.ambiente()`).
            **registro.ambiente(),
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            trovato = publish_agent.detect_handle(page)
            # La guardia sta **prima** del caricamento e non dopo: una clip
            # uscita sull'account sbagliato si cancella, ma l'algoritmo di quel
            # canale l'ha gia' vista.
            registro.controlla_handle(trovato, atteso, "tiktok")
            # Nessun ripiego cablato qui dietro: la guardia sopra garantisce
            # che almeno uno dei due ci sia. C'era `or "canale_lab"`, ed era
            # un'arma carica — inerte finche' la guardia copriva tutti i casi,
            # viva il giorno in cui ne avesse scoperto uno. L'handle serve a
            # `all_video_ids` e `fetch_new_url`, cioe' a **leggere l'URL della
            # clip appena uscita**: sbagliarlo non sbaglia il caricamento,
            # sbaglia la riga di stato — che e' il verso peggiore.
            handle = trovato or atteso
            log(f"TikTok Studio ({profile.name}): aperto come @{handle}")
            known = publish_agent.all_video_ids(page, handle)

            ok = publish_agent.upload_tiktok(page, video_path, caption, dry_run=dry_run)
            if not ok or dry_run:
                return None

            url = publish_agent.fetch_new_url(page, handle, known)
            return url
        finally:
            ctx.close()


def indirizzo_da_studio(gruppo: str | None, state: dict, nato_dopo: datetime) -> str | None:
    """
    L'indirizzo del video TikTok appena uscito da Buffer, letto da Studio.

    Buffer pubblica ma non e' detto che dica dove: senza l'indirizzo l'invio a
    Whop resta da fare a mano, dentro trenta minuti e di sera. Studio l'elenco
    lo ha — e' la stessa lettura di `leggi_account.py`, stesso profilo, gia'
    sotto la serratura che chi chiama tiene.

    Tre letture ad attese crescenti: un video appena uscito puo' non essere
    ancora in elenco. Una lettura che non riesce non ferma niente: si torna
    senza indirizzo, e chi chiama lo dice.
    """
    import buffer_agent
    import leggi_account

    profilo = profilo_pubblicazione({}, gruppo, "tiktok")
    atteso = registro.handle(gruppo, "tiktok")
    noti = {
        leggi_account.id_da_url("tiktok", r.get("url"))
        for r in state.get("items", {}).values()
        if (r.get("platform") or "").lower() == "tiktok"
    } - {None}
    for attesa in (0, 60, 120):
        time.sleep(attesa)
        try:
            post = leggi_account.leggi_tiktok(profilo, atteso, headless=True)
        except (SystemExit, Exception) as exc:  # noqa: BLE001 - qualunque guasto vale «non letto»
            log(f"  Studio non letto: {str(exc)[:200]}")
            continue
        url = buffer_agent.nuovo_tiktok(post, nato_dopo, noti)
        if url:
            log(f"  indirizzo letto da Studio: {url}")
            return url
        log(f"  Studio elenca {len(post)} post, nessuno nuovo e solo (riprovo)")
    return None


def cadenza(cfg, state, platform: str) -> str | None:
    """
    Dice perché non si può pubblicare adesso su questa piattaforma, o None se si può.

    Il ritmo si conta **per account**, e un account è una piattaforma: YouTube e
    TikTok non si sommano fra loro. La regola vera sta in `ritmo.py`, importata
    e non riscritta — è nata proprio perché una seconda copia qui, invece che
    un rimando, era il modo in cui questo stesso controllo era già stato
    scavalcato una volta: le clip 01 e 02 sono uscite a sette minuti l'una
    dall'altra su un canale nuovo, la raffica per cui @canale_lab è stato
    strozzato.
    """
    fatti = [
        r["published_at"]
        for r in state.get("items", {}).values()
        if r.get("platform") == platform
        and r.get("status") == "pubblicato"
        and r.get("published_at")
    ]

    # `account_started` puo' essere una data sola (un'unica identita' per
    # tutta la campagna) o un dizionario per piattaforma: YouTube e TikTok qui
    # non sono per forza lo stesso account, e un valore unico applicato a
    # entrambi ha bloccato TikTok per il motivo sbagliato la prima volta che
    # e' stato scritto — @canale_lab non e' nuovo, e' solo penalizzato.
    inizio = cfg.get("account_started")
    if isinstance(inizio, dict):
        inizio = inizio.get(platform)

    esito = ritmo.quando_si_puo(
        fatti,
        distanza_minima=int(cfg.get("min_gap_seconds", 0)),
        massimo=int(cfg.get("max_per_day", 0)),
        aperto_il=inizio,
    )
    return None if esito["puo"] else f"{platform}: {esito['motivo']}"


def cmd_pubblica(args) -> None:
    # Nessuna cartella di ripiego qui. Con un default, `pubblica --id 01` senza
    # `--folder` pubblica la clip 01 **di un'altra campagna**: l'errore non si
    # vede finche' il video non e' online. `esegui.py` passa sempre `--folder`,
    # quindi il giro automatico non cambia.
    if not args.folder:
        sys.exit("serve --folder: senza, non si sa di quale campagna sia la clip.")
    folder = Path(args.folder).resolve()
    cfg, state, state_file = get_paths(folder)

    items = cfg.get("items", [])
    campaign_uuid = cfg.get("whop_campaign_uuid")
    whop_app = cfg.get("whop_app")
    handles = cfg.get("handles", {})

    # Whop si controlla **prima** di pubblicare, non dopo. Il modulo respinge in
    # automatico gli invii oltre 30 minuti dalla pubblicazione, quindi accorgersi
    # a video online che manca l'indirizzo della campagna vuol dire una clip
    # pubblicata che non incassa. E il vecchio ripiego era peggio del vuoto:
    # l'app cablata era quella della prima campagna, cioe' l'invio sarebbe
    # finito **nella campagna sbagliata**.
    if not campaign_uuid or not whop_app:
        sys.exit(
            "publish.json non dice `whop_campaign_uuid` o `whop_app`.\n"
            "  Non pubblico: la clip uscirebbe senza poter essere inviata "
            "entro i 30 minuti, o finirebbe nella campagna di qualcun altro."
        )

    target_id = args.id
    target_item = next((it for it in items if it["id"] == target_id), None)
    if not target_item:
        sys.exit(f"Clip ID '{target_id}' non trovata.")

    cid = target_item["id"]
    file_rel = target_item["file"]
    video_path = folder / file_rel
    titolo = target_item.get("title", "")
    caption_raw = target_item.get("caption", "")

    platform = args.platform.lower()
    if platform not in ("youtube", "tiktok", "instagram"):
        sys.exit(f"Piattaforma non supportata: {platform}")
    # Una piattaforma sospesa nel registro non pubblica, nemmeno a mano da qui:
    # `esegui.py` la salta gia', ma questa e' la porta da cui si esce, e una
    # regola che vale solo per chi passa dall'esecutore non e' una regola. La
    # prova (`--dry-run`) resta: non mette fuori niente.
    fermo = registro.sospesa(cfg.get("gruppo"), platform)
    if fermo and not args.dry_run:
        sys.exit(f"NON PUBBLICO: {platform} e' sospesa nel registro — {fermo}")

    tag_target = tag(handles, platform)
    caption = didascalia(caption_raw, handles, tag_target)

    if not args.dry_run:
        motivo = cadenza(cfg, state, platform)
        if motivo and not args.forza:
            log(f"NON PUBBLICO: {motivo}")
            log("  (--forza scavalca il controllo: la raffica strozza l'account, non il codice)")
            sys.exit(2)
        if motivo:
            log(f"ATTENZIONE: {motivo} — pubblico lo stesso perché è stato chiesto --forza")

    log("=======================================================")
    log(f" PUBBLICAZIONE [{cid}] su {platform.upper()}: {file_rel}")
    log(f" Titolo: {titolo}")
    log(f" Tag: {tag_target}")
    log(f" Modalita': {'DRY-RUN' if args.dry_run else 'EFFETTIVA (--davvero)'}")
    log("=======================================================")

    published_url = None

    # Da quale account esce questa cartella. Lo dice `publish.json` con
    # `gruppo`, e il registro lo traduce in profilo browser e handle atteso.
    # Senza gruppo ogni agente resta sul suo profilo storico: e' il caso delle
    # cartelle nate prima dei gruppi, e va detto invece che subito.
    gruppo = cfg.get("gruppo")
    if not gruppo:
        log("publish.json non dice il gruppo: uso i profili storici degli agenti")
    # Da quale strada: il browser automatico, Buffer, o l'API ufficiale. Lo dice
    # il registro, e finisce nel record di stato: le strade non rendono uguale,
    # e senza saperlo clip per clip il confronto non si puo' fare.
    via = registro.via(gruppo, platform)

    # Il profilo resta chiuso per tutto il caricamento. Non lo apre solo chi
    # pubblica: `leggi_account.py` ci legge le views, e un secondo Chromium
    # sulla stessa cartella o non parte o la rovina. Chi arriva secondo
    # aspetta; se aspetta troppo si ferma prima di caricare, e la clip riprova
    # al giro dopo.
    profilo_in_uso = profilo_pubblicazione(cfg, gruppo, platform)
    with serratura.presa(
        serratura.nome_profilo(profilo_in_uso), attesa=ATTESA_PROFILO, scaduta=SCADUTA_PROFILO
    ):
        if platform == "youtube":
            yt_script = HERE / "youtube_agent.py"
            yt_cmd = [
                interprete(PYTHON_SCRAPING, "YouTube Studio (Scrapling)"),
                str(yt_script),
            ]
            if gruppo:
                yt_cmd += ["--gruppo", gruppo]
            yt_cmd += [
                "pubblica",
                "--file",
                str(video_path),
                "--titolo",
                titolo,
                "--descrizione",
                caption,
            ]
            if not args.dry_run:
                yt_cmd.append("--davvero")

            log("Avvio YouTube Studio...")
            res = subprocess.run(
                yt_cmd, capture_output=True, text=True, encoding="utf-8", errors="replace"
            )
            out = res.stdout + "\n" + res.stderr
            print(out)

            if not args.dry_run:
                m = re.search(r"pubblicato:\s*(https?://[^\s]+)", out)
                if not m:
                    m = re.search(
                        r"(https://youtu\.be/[\w\-]+|https://www\.youtube\.com/shorts/[\w\-]+)", out
                    )
                published_url = m.group(1) if m else None

        elif platform == "tiktok" and via == "buffer":
            import buffer_agent

            log("Avvio Buffer (TikTok)...")
            precedente = record(state, cid, platform).get("buffer_post")
            # Da quando puo' essere nato il video: da adesso se il post si crea
            # in questo giro, dalle ultime due ore se e' rimasto in corso da
            # quello prima — sotto la distanza minima fra due clip, cosi' in
            # quella finestra ce n'e' una sola.
            nato_dopo = datetime.now() - (timedelta(hours=2) if precedente else timedelta(minutes=2))
            try:
                esito_buffer = buffer_agent.pubblica(
                    video_path,
                    caption,
                    gruppo,
                    "tiktok",
                    davvero=not args.dry_run,
                    post_precedente=precedente,
                )
            except (buffer_agent.ErroreBuffer, registro.AccountSbagliato) as exc:
                sys.exit(f"NON PUBBLICO: {exc}")
            if args.dry_run:
                log("[DRY-RUN] Completato.")
                return
            published_url = esito_buffer["url"]
            if esito_buffer["esito"] != "uscito":
                # Ne' online ne' fallito non sono la stessa cosa. Un post
                # `in corso` vive ancora dentro Buffer e puo' uscire: se ne
                # tiene l'id, e il giro dopo lo richiede invece di crearne un
                # altro. Un post fallito non e' uscito, e si puo' rifare.
                scrivi_stato(
                    state_file,
                    cid,
                    platform,
                    {
                        "id": cid,
                        "platform": platform,
                        "status": "buffer_" + esito_buffer["esito"].replace(" ", "_"),
                        "via": via,
                        "buffer_post": esito_buffer["post"],
                        "errore": esito_buffer["errore"],
                        "timestamp": datetime.now().isoformat(),
                    },
                    da_capo=True,
                )
                if esito_buffer["esito"] == "fallito":
                    # Con un codice diverso da zero: `esegui.py` legge lo zero
                    # come «fatto», e contava un post fallito fra le clip
                    # pubblicate del giro. Lo stato era giusto e il giro dopo
                    # lo rifaceva, ma il riepilogo diceva il contrario e
                    # l'attivita' pianificata restava verde.
                    sys.exit(f"NON USCITO: Buffer dice «fallito» — {esito_buffer['errore'] or ''}")
                log(f"NON ANCORA USCITO: Buffer dice «{esito_buffer['esito']}», lo richiedo al prossimo giro.")
                return
            published_url = buffer_agent.link_tiktok(published_url)
            if not published_url:
                log(f"Buffer non da' l'indirizzo del video ({esito_buffer['url']!r}): lo leggo da Studio")
                published_url = indirizzo_da_studio(gruppo, state, nato_dopo)
            if not published_url:
                # Online, ma senza indirizzo ne' da Buffer ne' da Studio. Lo stato deve dire
                # «pubblicato»: con `errore_url` il giro dopo la rimetterebbe
                # online una seconda volta. Resta da inviare a Whop a mano.
                scrivi_stato(
                    state_file,
                    cid,
                    platform,
                    {
                        "id": cid,
                        "platform": platform,
                        "status": "pubblicato",
                        "url": None,
                        "via": via,
                        "buffer_post": esito_buffer["post"],
                        "published_at": datetime.now().isoformat(),
                        "whop_status": "da inviare",
                    },
                    da_capo=True,
                )
                notifica.avvisa(
                    f"Invio Whop da fare a mano: clip {cid} su {platform}",
                    "La clip e' uscita da Buffer ma senza indirizzo. Hai 30 minuti "
                    "dalla pubblicazione: il link si prende dal profilo.",
                )
                log("USCITO SENZA INDIRIZZO: l'invio a Whop resta da fare a mano.")
                return

        elif platform == "tiktok":
            log("Avvio TikTok Studio...")
            if args.dry_run:
                log("[DRY-RUN] Caricamento bozza TikTok...")
                pubblica_tiktok(video_path, caption, dry_run=True, gruppo=gruppo)
                log("[DRY-RUN] Completato.")
                return
            else:
                published_url = pubblica_tiktok(video_path, caption, dry_run=False, gruppo=gruppo)

        elif platform == "instagram" and via == "api":
            import instagram_api
            import leggi_account

            log("Avvio l'API di Instagram...")
            vecchio = record(state, cid, platform)
            precedente = vecchio.get("api_contenitore")
            # Da quando puo' essere nato il reel, se il contenitore risultasse
            # pubblicato senza che ne sappiamo l'indirizzo: da adesso se si
            # crea in questo giro, da mezz'ora prima dell'ultimo record se e'
            # rimasto da quello — il record si scrive a giro finito, e un giro
            # dura al piu' ventitre minuti.
            nato_dopo = datetime.now() - timedelta(minutes=2)
            if precedente and vecchio.get("timestamp"):
                try:
                    nato_dopo = datetime.fromisoformat(vecchio["timestamp"]) - timedelta(minutes=30)
                except ValueError:
                    pass
            noti = {
                leggi_account.id_da_url("instagram", r.get("url"))
                for r in state.get("items", {}).values()
                if isinstance(r, dict) and (r.get("platform") or "").lower() == "instagram"
            } - {None}
            try:
                esito_api = instagram_api.pubblica(
                    video_path,
                    caption,
                    gruppo,
                    davvero=not args.dry_run,
                    contenitore_precedente=precedente,
                    media_precedente=vecchio.get("api_media"),
                    nato_dopo=nato_dopo,
                    noti=noti,
                )
            except (instagram_api.ErroreMeta, registro.AccountSbagliato) as exc:
                sys.exit(f"NON PUBBLICO: {exc}")
            if args.dry_run:
                log(f"[DRY-RUN] Completato: {esito_api['esito']}.")
                return
            published_url = esito_api["url"]
            if esito_api["esito"] != "uscito":
                # Tre esiti e non due, come per Buffer. Un contenitore
                # `in corso` vive ancora dentro Meta: se ne tiene l'id, e il
                # giro dopo lo richiede invece di crearne un altro. Si tiene
                # anche quando e' fallito: se a rifiutare e' stata la
                # pubblicazione e non il video, il contenitore e' ancora pronto.
                scrivi_stato(
                    state_file,
                    cid,
                    platform,
                    {
                        "id": cid,
                        "platform": platform,
                        "status": "api_" + esito_api["esito"].replace(" ", "_"),
                        "via": via,
                        "api_contenitore": esito_api["contenitore"],
                        "errore": esito_api["errore"],
                        "timestamp": datetime.now().isoformat(),
                    },
                    da_capo=True,
                )
                if esito_api["esito"] == "fallito":
                    # Con un codice diverso da zero: `esegui.py` legge lo zero
                    # come «fatto», e un reel non uscito non lo e'.
                    sys.exit(f"NON USCITO: Meta dice «fallito» — {esito_api['errore'] or ''}")
                log("NON ANCORA USCITO: il contenitore e' in corso, lo richiedo al prossimo giro.")
                return
            if not published_url:
                # Online, ma senza indirizzo. Lo stato deve dire «pubblicato»:
                # con `errore_url` il giro dopo la rimetterebbe online una
                # seconda volta. Resta da inviare a Whop a mano.
                scrivi_stato(
                    state_file,
                    cid,
                    platform,
                    {
                        "id": cid,
                        "platform": platform,
                        "status": "pubblicato",
                        "url": None,
                        "via": via,
                        "api_contenitore": esito_api["contenitore"],
                        "api_media": esito_api["media"],
                        "published_at": datetime.now().isoformat(),
                        "whop_status": "da inviare",
                    },
                    da_capo=True,
                )
                notifica.avvisa(
                    f"Invio Whop da fare a mano: clip {cid} su {platform}",
                    "Il reel e' uscito ma senza indirizzo. Hai 30 minuti dalla "
                    "pubblicazione: il link si prende dal profilo.",
                )
                log("USCITO SENZA INDIRIZZO: l'invio a Whop resta da fare a mano.")
                return

        elif platform == "instagram":
            # Instagram non ha bozze per i Reel dal web: la prova (`--prova`)
            # percorre tutto e si ferma prima dell'ultimo clic. E' l'unico
            # collaudo possibile, e va rifatto quando l'interfaccia cambia.
            # Il registro vince su `publish.json`: il primo dice da quale account
            # pubblica quel gruppo, il secondo e' scritto a mano cartella per
            # cartella ed e' la copia che invecchia.
            ig_profilo = profilo_pubblicazione(cfg, gruppo, "instagram")
            ig_handle = registro.handle(gruppo, "instagram") or cfg.get("instagram_handle")
            ig_cmd = [
                interprete(PYTHON_PUBLISH, "l'agente Instagram (Playwright)"),
                str(HERE / "instagram_agent.py"),
                "--profilo",
                str(ig_profilo),
                "pubblica",
                "--file",
                str(video_path),
                "--didascalia",
                caption,
            ]
            if ig_handle:
                ig_cmd += ["--handle", ig_handle]
            ig_cmd.append("--davvero" if not args.dry_run else "--prova")
            log("Avvio Instagram...")
            res = subprocess.run(
                ig_cmd, capture_output=True, text=True, encoding="utf-8", errors="replace"
            )
            out = res.stdout + "\n" + res.stderr
            print(out)
            if args.dry_run:
                return
            m = re.search(r"pubblicato:\s*(https?://\S+)", out)
            published_url = m.group(1) if m else None

    if args.dry_run:
        return

    if not published_url:
        log("ERRORE: Pubblicazione terminata ma URL pubblico non intercettato.")
        scrivi_stato(
            state_file,
            cid,
            platform,
            {
                "id": cid,
                "platform": platform,
                "status": "errore_url",
                "timestamp": datetime.now().isoformat(),
            },
            da_capo=True,
        )
        return

    log(f"[OK] Post pubblicato: {published_url}")
    scrivi_stato(
        state_file,
        cid,
        platform,
        {
            "id": cid,
            "platform": platform,
            "status": "pubblicato",
            "url": published_url,
            "via": via,
            "published_at": datetime.now().isoformat(),
            "whop_status": "inviando...",
        },
        da_capo=True,
    )

    # Invio immediato su Whop.
    #
    # **Uno alla volta, e non e' una scelta di prudenza.** Il conto Whop e' uno
    # per gruppo, quindi un profilo browser solo, e due Chromium sullo stesso
    # `user_data_dir` non convivono: con le tre piattaforme che escono insieme,
    # il secondo invio troverebbe il profilo occupato. La finestra utile e' 30
    # minuti dalla pubblicazione e un invio ne prende circa uno, quindi tre in
    # fila ci stanno larghi.
    #
    # E il gruppo si passa. Prima no, e il ripiego era `.whop-profile`, cioe'
    # il conto del laboratorio: la consegna business veniva inviata dal
    # pannello di un'altra nicchia, senza un errore. Stessa specie del profilo
    # di pubblicazione (vedi `registro.py`), stessa cura.
    log(f"Invio a Whop ({campaign_uuid})...")
    whop_cmd, profilo_whop, nome_serratura = comando_whop(gruppo)
    whop_cmd += [
        "invia",
        "--campagna",
        campaign_uuid,
        "--post",
        published_url,
        "--app",
        whop_app,
        "--davvero",
    ]
    # Il titolo non e' un di piu': nell'elenco del pannello le schede non
    # portano l'uuid, quindi e' l'unica cosa con cui si aggancia la campagna
    # giusta. Sta gia' in publish.json, scritto quando la consegna e' nata.
    if cfg.get("campaign"):
        whop_cmd += ["--titolo", cfg["campaign"]]

    if profilo_whop is not None and not profilo_whop.is_dir():
        # Una cartella che non c'e' non e' un profilo vuoto: e' un browser non
        # collegato, che apre la pagina di accesso e torna indietro. L'invio
        # non avviene e il video e' gia' online, quindi si dice adesso.
        log(
            f"NON invio a Whop: manca il profilo {profilo_whop}.\n"
            f"  Lo crea `whop_agent.py --gruppo {gruppo} login`, poi `login-app`."
        )
        whop_esito = "profilo assente"
    else:
        try:
            with serratura.presa(nome_serratura, attesa=ATTESA_WHOP, scaduta=SCADUTA_WHOP):
                res_whop = subprocess.run(
                    whop_cmd, capture_output=True, text=True, encoding="utf-8", errors="replace"
                )
            out_whop = res_whop.stdout + "\n" + res_whop.stderr
            print(out_whop)
            # **Senza la riga «esito:» non e' andata bene.** `cmd_invia` la
            # scrive solo quando arriva in fondo; tutte le sue vie d'uscita
            # anticipate — pannello non aperto, conto sbagliato, campagna non
            # trovata, conferma spenta — escono con codice 0. Leggere quello
            # zero come «inviato» era il difetto peggiore possibile: un invio
            # mai avvenuto registrato come riuscito, cioe' una clip che non
            # incassa e non risulta da recuperare.
            whop_esito = leggi_esito_whop(out_whop)
        except serratura.Occupata as exc:
            # Il video e' online e lo stato lo dice: qui si perde solo l'invio,
            # e va detto com'e' andata invece di lasciare «inviando...».
            log(f"NON inviato a Whop: {exc}")
            whop_esito = "profilo occupato"

    scrivi_stato(
        state_file,
        cid,
        platform,
        {
            "whop_status": whop_esito,
            "whop_submitted_at": datetime.now().isoformat(),
        },
    )

    if whop_esito != "inviato":
        # Il post e' online e la finestra per registrarlo e' di trenta minuti,
        # mentre questo gira da un'attivita' pianificata, quando il pannello non
        # lo guarda nessuno. La notifica non ferma niente: se non parte, resta la
        # coda «da inviare a mano» del pannello.
        notifica.avvisa(
            f"Invio Whop da fare a mano: clip {cid} su {platform}",
            f"Esito «{whop_esito}». Hai 30 minuti dalla pubblicazione: "
            "pannello di controllo, sezione Pubblicazione.",
        )

    log("=======================================================")
    log(f" ESITO FINALE CLIP [{cid}] ({platform.upper()})")
    log(f" URL Post: {published_url}")
    log(f" Esito Whop: {whop_esito}")
    log("=======================================================\n")


def cmd_invii(args) -> None:
    """
    Se la campagna di questa cartella accetta invii adesso. Non pubblica niente.

    Lo chiede `esegui.py` prima di far uscire una clip. Il 16 settembre 2026 la
    clip 05 di Contoso e' uscita su YouTube e TikTok mentre la campagna aveva
    messo in pausa gli invii: non si poteva inviarla, la finestra e' di trenta
    minuti, e i due post sono stati tolti a mano. Il controllo sta **prima**
    per la stessa ragione di quello su `whop_campaign_uuid` in `cmd_pubblica`.

    Stampa `invii: <esito>` su stdout e lo scrive in `invii.json` nella
    cartella, dove lo legge il pannello: una clip ferma per questo motivo,
    senza quel file, si vedrebbe soltanto «in ritardo».
    """
    if not args.folder:
        sys.exit("serve --folder: il controllo e' per la campagna di una cartella.")
    folder = Path(args.folder).resolve()
    cfg, _, _ = get_paths(folder)
    campaign_uuid = cfg.get("whop_campaign_uuid")
    whop_app = cfg.get("whop_app")
    if not campaign_uuid or not whop_app:
        # Niente da chiedere. Non e' un permesso: `cmd_pubblica` su questa
        # cartella si ferma da solo, e dice perche'.
        print("invii: nessuna campagna")
        return

    gruppo = cfg.get("gruppo")
    cmd, profilo, nome_serratura = comando_whop(gruppo)
    cmd += ["--headless", "invii", "--campagna", campaign_uuid, "--app", whop_app]
    if cfg.get("campaign"):
        cmd += ["--titolo", cfg["campaign"]]

    if profilo is not None and not profilo.is_dir():
        log(
            f"manca il profilo Whop {profilo}: lo crea "
            f"`whop_agent.py --gruppo {gruppo} login`, poi `login-app`"
        )
        esito = "non verificato"
    else:
        try:
            with serratura.presa(nome_serratura, attesa=ATTESA_WHOP, scaduta=SCADUTA_WHOP):
                res = subprocess.run(
                    cmd, capture_output=True, text=True, encoding="utf-8", errors="replace"
                )
            uscita = res.stdout + "\n" + res.stderr
            print(uscita, file=sys.stderr)
            esito = leggi_esito_invii(uscita)
            if esito == "non verificato":
                # L'attivita' pianificata butta lo stderr: senza queste righe un
                # controllo morto a meta' — il 29 settembre 2026 alle 19:15, dopo
                # «profilo Whop» e basta — non lascia niente da leggere.
                coda = "\n  ".join(uscita.strip().splitlines()[-20:])
                log(f"controllo degli invii senza esito (codice {res.returncode}):\n  {coda}")
        except serratura.Occupata as exc:
            log(f"controllo degli invii non fatto: {exc}")
            esito = "non verificato"

    serratura.scrivi_atomico(
        folder / "invii.json",
        json.dumps(
            {
                "esito": esito,
                "letto_il": datetime.now().isoformat(timespec="seconds"),
                "campagna": campaign_uuid,
            },
            ensure_ascii=False,
            indent=2,
        ),
    )
    print(f"invii: {esito}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Orchestratore multi-piattaforma")
    ap.add_argument(
        "--folder", default="", help="Cartella con publish.json (obbligatoria per `pubblica`)"
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status").set_defaults(func=cmd_status)

    p = sub.add_parser("pubblica")
    p.add_argument("--platform", choices=["youtube", "tiktok", "instagram"], required=True)
    p.add_argument("--id", required=True, help="ID della clip (es. 01, 02)")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument(
        "--forza",
        action="store_true",
        help="pubblica anche se il ritmo dell'account non lo consente",
    )
    p.set_defaults(func=cmd_pubblica)

    sub.add_parser(
        "invii", help="se la campagna della cartella accetta invii adesso (sola lettura)"
    ).set_defaults(func=cmd_invii)
    i = sub.add_parser("iscrivi", help="entra nella campagna scelta sul conto Whop del gruppo")
    i.add_argument("--campagna", required=True, help="uuid della campagna")
    i.add_argument("--gruppo", required=True)
    i.set_defaults(func=cmd_iscrivi)

    mt = sub.add_parser("materiali", help="rilegge i materiali di una campagna gia' iscritta")
    mt.add_argument("--campagna", required=True, help="uuid della campagna")
    mt.add_argument("--gruppo", required=True)
    mt.set_defaults(func=cmd_materiali)

    args = ap.parse_args()
    try:
        args.func(args)
    except serratura.Occupata as exc:
        sys.exit(f"NON PUBBLICO: {exc}")


if __name__ == "__main__":
    main()
