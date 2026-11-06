#!/usr/bin/env python
"""
stato.py — uno snapshot solo, letto da tutte le fonti che gia' esistono.

    python stato.py                    # lo snapshot su stdout, in JSON
    python stato.py --sezione oggi     # oggi | campagne | clip | lancio | lavoro

Perche' esiste
--------------
Lo stato dell'operazione vive gia' in tre posti con due schemi diversi:
`output/<slug>/publish_state.json` (piatto nell'agente TikTok, `{"items":{...}}`
nell'orchestratore), `publish/whop_invii.json`, `radar/radar_dettagli.json`.
Questo file **non aggiunge un quarto posto**: legge quei tre, li normalizza in
memoria, e l'unica cosa che scrive e' `scelte.json` — quale campagna abbiamo
deciso di lavorare, che e' un dato che non appartiene a nessun altro file
(vedi `scrivi_scelta`). Tutto il resto e' sola lettura: la plancia non e' una
seconda verita'.

La funzione che conta e' `avvisi()`. La notte del 31 agosto tre azioni erano
state dichiarate riuscite nei log e non lo era nessuna: due clip rimaste in
stato di bozza su YouTube, e i loro invii assenti dal pannello Whop. Nessun
errore da nessuna parte — ogni passo aveva dedotto il successo da un indizio
interno alla propria pagina. Qui si guardano invece le **incoerenze fra le
fonti che gia' abbiamo**: un invio a Whop senza pubblicazione corrispondente,
un invio fuori dalla finestra dei 30 minuti, una clip pubblicata senza URL.
Le prove che vengono da fuori (oembed, elenco Submissions) stanno in
`verifica.py`, perche' quelle costano una richiesta di rete: questo file resta
veloce e offline.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUTPUT_DIR = ROOT / "output"
WORK_DIR = ROOT / "work"
RADAR_DIR = ROOT / "radar"
PUBLISH_DIR = ROOT / "publish"

# ritmo.py vive in publish/, non qui: e' la stessa regola che usano
# publish_agent.py e orchestrator.py, importata e non riscritta apposta.
sys.path.insert(0, str(PUBLISH_DIR))
import ritmo  # noqa: E402

# Stessa ragione: il quadro per nicchia lo somma `tassonomia.quadro()`, che e'
# anche quello che scrive `nicchie_report.md`. La plancia lo **disegna**, non
# lo ricalcola — cosi' la pagina e il file non possono raccontare due cose
# diverse, e la pagina non puo' essere vecchia: e' calcolata sul momento dai
# file che il radar ha appena riscritto.
sys.path.insert(0, str(RADAR_DIR))
import tassonomia  # noqa: E402

FINESTRA_WHOP_MINUTI = 30

# Cosa vuol dire «su Whop c'e'». Una definizione sola, due lettori: il controllo
# delle anomalie qui sotto e la spia verde del calendario in plancia — due somme
# scritte due volte divergono al primo stato nuovo, e divergono in silenzio.
# `inviato a mano` esiste perche' la finestra e' di trenta minuti: quando
# l'automatismo non arriva in tempo si invia dal pannello di Whop, e quella
# clip **e' a posto**, non in attesa.
WHOP_FATTO = ("inviato", "inviato a mano")


def leggi(path: Path, default=None):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, OSError):
        return default


# ------------------------------------------------- cosa entra nella plancia


def registro() -> dict:
    return leggi(HERE / "identita.json", {}) or {}


def lingue_in_plancia() -> set[str]:
    """
    Le lingue del lavoro che la plancia conta. Le altre restano su disco e
    fuori da ogni somma.

    Non e' un filtro estetico: l'operazione ha tre account inglesi, e il
    materiale italiano e' il laboratorio di prima — la prima campagna, su
    `@canale_italiano`, chiusa. Tenerlo nei conti significa misurare il piano
    con dei numeri che non appartengono al piano, ed e' lo stesso motivo per
    cui il gruppo `laboratorio` esiste separato dagli altri tre.
    """
    return {s.lower() for s in registro().get("lingue_in_plancia") or ["en"]}


def lingua_cartella(nome: str) -> str | None:
    """La lingua del parlato registrata da `transcribe.py` per questa cartella.

    Si legge dalla cartella di lavoro perche' e' li' che e' stata **misurata**:
    `meta.json` ha un `audio_lang` che su meta' dei video e' `null` (il campo
    esiste solo quando il download dichiara la traccia), mentre
    `transcript.json` porta la lingua che Whisper ha davvero usato per
    decodificare. Una lingua dedotta dal titolo o dallo slug sarebbe un
    indovinello su un dato che possediamo.
    """
    percorso = WORK_DIR / nome / "transcript.json"
    try:
        mtime = percorso.stat().st_mtime
    except OSError:
        return None
    # `transcript.json` pesa megabyte e questa funzione la chiamano in tanti, una
    # volta per misura: riletto ogni volta costava secondi a ogni caricamento
    # della pagina. La lingua cambia solo se cambia il file.
    visto = _LINGUE.get(nome)
    if visto and visto[0] == mtime:
        return visto[1]
    dati = leggi(percorso, {}) or {}
    lingua = dati.get("language")
    lingua = lingua.lower() if isinstance(lingua, str) else None
    _LINGUE[nome] = (mtime, lingua)
    return lingua


_LINGUE: dict[str, tuple[float, str | None]] = {}


def cartella_esclusa(nome: str) -> bool:
    """
    Vero solo per una cartella di cui **sappiamo** che la lingua sta fuori.

    L'asimmetria e' voluta: si nasconde cio' che si e' dimostrato, non cio'
    che non si e' riconosciuto. Una cartella senza trascrizione resta in
    plancia — sparire in silenzio e' il difetto che questo progetto insegue
    ovunque, e sarebbe assurdo introdurlo qui per un filtro di comodo.
    """
    lingua = lingua_cartella(nome)
    return lingua is not None and lingua not in lingue_in_plancia()


def identita_attive() -> list[dict]:
    """
    Le identita' che contano. `attiva: false` non cancella un account: lo
    toglie dalle somme lasciandone il record, perche' il perche' di un account
    ritirato e' un'informazione che serve piu' tardi, non un rifiuto.
    """
    return [i for i in registro().get("identita", []) if i.get("attiva", True)]


def _iso(s) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s))
    except ValueError:
        return None


def normalizza_stato(state: dict, piattaforma_cartella: str = "") -> dict[tuple[str, str], dict]:
    """
    Le forme di `publish_state.json` ricondotte a una sola: `{(id, piattaforma): record}`.

    Tre schemi, non due. L'agente TikTok scrive `{id: {...}}` diretto;
    l'orchestratore `{"items": {id: {...}}}`; e da quando la stessa clip esce
    su piu' piattaforme la chiave dentro `items` e' `<id>@<piattaforma>`,
    perche' quella nuda si sovrascriveva da sola al secondo passaggio.

    La chiave qui e' la coppia, non l'id: e' l'unica che regge tutte e tre le
    forme senza perdere niente. Le vecchie voci senza piattaforma **nel
    record** prendono `piattaforma_cartella`, che e' l'unica che potevano
    avere: quando sono state scritte la cartella usciva su una piattaforma
    sola. Senza questo ripiego le 29 clip TikTok gia' pubblicate risultavano
    zero — nessun errore, solo un conteggio che ricominciava da capo.
    """
    if not isinstance(state, dict):
        return {}
    grezzi = (
        state["items"]
        if isinstance(state.get("items"), dict)
        else {k: v for k, v in state.items() if isinstance(v, dict)}
    )
    fuori: dict[tuple[str, str], dict] = {}
    for chiave, rec in grezzi.items():
        if not isinstance(rec, dict):
            continue
        cid, _, piattaforma = str(chiave).partition("@")
        piattaforma = (piattaforma or rec.get("platform") or piattaforma_cartella).lower()
        fuori[(rec.get("id") or cid, piattaforma)] = rec
    return fuori


def identifica(
    rec: dict, cfg: dict, identita_per_handle: dict, ids_gruppo: set | None = None
) -> str | None:
    """
    A quale identita' del registro appartiene questa clip pubblicata.

    Attenzione alla trappola: `cfg["handles"]` sono gli account **da taggare**
    nella didascalia — il brand della campagna — non l'account **con cui
    pubblichiamo**. Per YouTube coincidono per come e' scritto `publish.json`
    (`youtube_handle` e' la nostra identita'), per TikTok no: la clip 03 tagga
    `@acmeshowtv` ma e' uscita da `@canale_lab`, e cercare quell'handle
    fra le identita' non trova niente. Per TikTok l'unica fonte affidabile e'
    l'URL pubblicato, che porta l'account vero.
    """
    piattaforma = (rec.get("platform") or cfg.get("platform") or "").lower()
    if piattaforma == "youtube":
        handle = cfg.get("youtube_handle") or (cfg.get("handles") or {}).get("YouTube") or ""
    elif piattaforma == "tiktok":
        m = re.search(r"tiktok\.com/@([\w.\-]+)/", rec.get("url") or "")
        handle = m.group(1) if m else (cfg.get("tiktok_handle") or "")
    else:
        handle = cfg.get("tiktok_handle") or cfg.get("youtube_handle") or ""
    handle = handle.lstrip("@").lower()
    chiave = (piattaforma, handle)
    if chiave in identita_per_handle:
        return identita_per_handle[chiave]
    # Ripiego: un'unica identita' su quella piattaforma nel registro — **del gruppo
    # della consegna**, se lo dice. Con due gruppi su YouTube il ripiego su tutto il
    # registro tornava `None` senza errore quando `handles["YouTube"]` era il brand
    # da taggare e non la nostra identita' (osservazione 138).
    candidate = [
        v
        for (p, _), v in identita_per_handle.items()
        if p == piattaforma and (not ids_gruppo or v in ids_gruppo)
    ]
    return candidate[0] if len(set(candidate)) == 1 else None


def piattaforme_attese(cfg: dict, gruppo: dict | None) -> list[str]:
    """
    Su quante piattaforme deve uscire ogni clip di questa cartella.

    In ordine: quello che dice la cartella (`platforms`), poi quello che dice
    il gruppo che la lavora, poi la vecchia `platform` singola. L'ordine non e'
    una preferenza estetica: la cartella e' piu' specifica del gruppo, e il
    gruppo e' piu' informato del campo vecchio, che sapeva solo di piattaforme
    singole perche' allora erano l'unica cosa che esisteva.
    """
    if cfg.get("platforms"):
        return [p.lower() for p in cfg["platforms"]]
    if gruppo and gruppo.get("piattaforme_attese"):
        return [p.lower() for p in gruppo["piattaforme_attese"]]
    return [cfg["platform"].lower()] if cfg.get("platform") else []


def stato_coppia(rec: dict, piattaforma: str, a_mano: dict, ferme) -> str:
    """
    Lo stato di una clip su una piattaforma, come lo mostra il calendario.

    Una piattaforma che il registro tiene ferma (`pubblicazione_sospesa`, vedi
    `publish/registro.sospesa`) non e' in coda e non e' in ritardo: aspetta che
    la si riapra. Vale anche per un tentativo rimasto a meta' (`errore_url`),
    che l'esecutore non riprovera' finche' la piattaforma e' ferma. **Cio' che
    e' gia' uscito resta uscito**: la sospensione non riscrive il passato.
    """
    stato = rec.get("status")
    if stato in ("pubblicato", "done"):
        return stato
    if piattaforma in ferme:
        return "sospesa"
    return stato or ("a mano" if piattaforma in (a_mano or {}) else "in coda")


def campagne_output() -> list[dict]:
    """Ogni cartella di `output/` che ha un `publish.json`: le clip note alla pubblicazione."""
    reg = registro()
    gruppi_per_id = {g["id"]: g for g in reg.get("gruppi", [])}
    identita_per_handle = {
        # Un handle `null` e' «non ancora letto dal browser»: quell'identita' non ha
        # ancora pubblicato niente, quindi non c'e' niente da attribuirle.
        (i["piattaforma"], i["handle"].lstrip("@").lower()): i["id"]
        for i in identita_attive()
        if i.get("handle")
    }
    ferme_per_gruppo: dict[str, set[str]] = {}
    for i in identita_attive():
        if i.get("gruppo") and i.get("pubblicazione_sospesa"):
            ferme_per_gruppo.setdefault(i["gruppo"], set()).add(i["piattaforma"].lower())

    risultato = []
    for cartella in sorted(OUTPUT_DIR.glob("*/")):
        cfg = leggi(cartella / "publish.json")
        if not cfg or cartella_esclusa(cartella.name):
            continue
        gruppo = gruppi_per_id.get(cfg.get("gruppo"))
        ids_gruppo = set((gruppo or {}).get("identita") or [])
        stato = normalizza_stato(
            leggi(cartella / "publish_state.json", {}),
            piattaforma_cartella=(cfg.get("platform") or ""),
        )
        # Una piattaforma su cui abbiamo gia' pubblicato e' attesa per
        # definizione, anche se `publish.json` non la nomina. La cartella Cap
        # Table dichiara solo YouTube e ha tre clip su TikTok: contando le sole
        # piattaforme dichiarate, quelle tre sparivano dalla plancia — lavoro
        # fatto e non visto, che e' il difetto opposto a quello che la plancia
        # esiste per trovare.
        attese = piattaforme_attese(cfg, gruppo)
        attese += sorted({p for (_, p) in stato if p and p not in attese})
        clip = []
        for it in cfg.get("items", []):
            cid = it["id"]
            for piattaforma in attese:
                rec = stato.get((cid, piattaforma), {})
                clip.append(
                    {
                        "id": cid,
                        "file": it.get("file"),
                        "titolo": it.get("title", ""),
                        # Quando il calendario dice che esce. Sta sulla clip e non
                        # sulla piattaforma perche' le tre escono insieme: una data
                        # per piattaforma sarebbe tre date da tenere allineate per
                        # la stessa cosa.
                        "quando": it.get("quando"),
                        # Il motivo per cui qualcuno l'ha fermata a mano: vedi
                        # `esegui.scadute`, che la salta finche' il campo c'e'.
                        "sospesa": it.get("sospesa"),
                        "identita": identifica(rec, cfg, identita_per_handle, ids_gruppo) if rec else None,
                        "piattaforma": piattaforma,
                        # «a mano» e' una piattaforma che l'esecutore non tocca
                        # (vedi `esegui.scadute`): non e' in coda e non e' in
                        # ritardo, finche' non si registra il post fatto dal telefono.
                        # «sospesa» e' una piattaforma ferma nel registro.
                        "stato_pubblicazione": stato_coppia(
                            rec,
                            piattaforma,
                            it.get("a_mano"),
                            ferme_per_gruppo.get(cfg.get("gruppo"), ()),
                        ),
                        "url": rec.get("url"),
                        "pubblicato_il": rec.get("published_at"),
                        "stato_whop": rec.get("whop_status", "in attesa"),
                        "whop_inviato_il": rec.get("whop_submitted_at"),
                        "nota": rec.get("nota"),
                    }
                )
        fatte = sum(1 for c in clip if c["stato_pubblicazione"] in ("pubblicato", "done"))
        per_piattaforma = {
            p: {
                "pubblicate": sum(
                    1
                    for c in clip
                    if c["piattaforma"] == p and c["stato_pubblicazione"] in ("pubblicato", "done")
                ),
                "totale": len(cfg.get("items", [])),
            }
            for p in attese
        }
        risultato.append(
            {
                "cartella": cartella.name,
                "campagna": cfg.get("campaign", cartella.name),
                "provider": cfg.get("provider"),
                "gruppo": cfg.get("gruppo"),
                "whop_campaign_uuid": cfg.get("whop_campaign_uuid"),
                "whop_app": cfg.get("whop_app"),
                # Il sorgente che questa cartella ha davvero lavorato: e' il piu'
                # affidabile di tutti, perche' non e' stato dedotto da nessuno.
                "source_episode": cfg.get("source_episode"),
                "piattaforme": attese,
                "clip": clip,
                "clip_distinte": len(cfg.get("items", [])),
                # `totale_clip` conta le **pubblicazioni** attese, non le clip:
                # 17 clip su tre piattaforme sono 51 cose da fare. Il nome resta
                # quello di prima perche' lo legge gia' la plancia, ma da qui in
                # poi vale questo — con `clip_distinte` accanto per non confondersi.
                "totale_clip": len(clip),
                "pubblicate": fatte,
                "da_pubblicare": len(clip) - fatte,
                "per_piattaforma": per_piattaforma,
            }
        )
    return risultato


def invii_whop() -> list[dict]:
    return leggi(PUBLISH_DIR / "whop_invii.json", [])


def avvisi(campagne: list[dict], invii: list[dict]) -> list[dict]:
    """
    Le incoerenze fra le fonti che possediamo gia'. Non prova niente da fuori
    — quello e' il mestiere di `verifica.py` — ma quello che due file nostri
    si contraddicono a vicenda si vede subito, senza rete.
    """
    trovati = []
    # Un URL puo' comparire piu' volte in whop_invii.json: un tentativo fallito
    # e poi, dopo la correzione, uno riuscito sullo stesso post — e' successo
    # davvero, due volte, la notte del 31 agosto. Conta solo l'ultimo esito per
    # ogni URL: un avviso su una voce gia' superata insegna a ignorare gli
    # avvisi, che e' peggio di non averli.
    ultimo_per_url: dict[str, dict] = {}
    for i in invii:
        if i.get("post"):
            ultimo_per_url[i["post"]] = i  # l'ordine del file e' cronologico
    per_url = ultimo_per_url
    misure, salute = misure_per_url(), salute_per_url()

    for camp in campagne:
        # Non ogni campagna passa da Whop: quella su AltroFornitore non ha
        # `whop_campaign_uuid` e il suo `whop_status` di default ("in attesa")
        # non e' un'incoerenza, e' un campo che qui non si applica.
        passa_da_whop = bool(camp.get("whop_campaign_uuid"))
        for c in camp["clip"]:
            if c["url"] and c["stato_pubblicazione"] in ("pubblicato", "done"):
                misura = misure.get(c["url"]) or {}
                segnali = avvisi_post(
                    misura.get("views"),
                    c["pubblicato_il"],
                    misura.get("quando"),
                    (salute.get(c["url"]) or {}).get("avvisi"),
                )
                if segnali:
                    trovati.append(
                        {
                            "livello": "avviso",
                            "campagna": camp["cartella"],
                            "clip": c["id"],
                            "messaggio": f"{c['piattaforma']}: {', '.join(segnali)}",
                        }
                    )
            if c["stato_pubblicazione"] in ("pubblicato", "done") and not c["url"]:
                trovati.append(
                    {
                        "livello": "errore",
                        "campagna": camp["cartella"],
                        "clip": c["id"],
                        "messaggio": "pubblicata ma senza URL registrato: l'invio non e' possibile",
                    }
                )
            if not passa_da_whop:
                continue
            if (
                c["url"]
                and c["stato_whop"] not in WHOP_FATTO
                and c["stato_pubblicazione"] in ("pubblicato", "done")
            ):
                trovati.append(
                    {
                        "livello": "avviso",
                        "campagna": camp["cartella"],
                        "clip": c["id"],
                        "messaggio": f"pubblicata ma stato Whop e' «{c['stato_whop']}»",
                    }
                )
            pub, inv = _iso(c["pubblicato_il"]), _iso(c["whop_inviato_il"])
            if pub and inv:
                minuti = (inv - pub).total_seconds() / 60
                if minuti > FINESTRA_WHOP_MINUTI:
                    trovati.append(
                        {
                            "livello": "errore",
                            "campagna": camp["cartella"],
                            "clip": c["id"],
                            "messaggio": f"invio a Whop {minuti:.0f} minuti dopo la pubblicazione: "
                            f"fuori dalla finestra di {FINESTRA_WHOP_MINUTI}",
                        }
                    )
            if (
                c["url"]
                and c["url"] not in per_url
                and c["stato_pubblicazione"] in ("pubblicato", "done")
            ):
                trovati.append(
                    {
                        "livello": "avviso",
                        "campagna": camp["cartella"],
                        "clip": c["id"],
                        "messaggio": "pubblicata, nessun invio corrispondente in whop_invii.json",
                    }
                )

    for i in per_url.values():
        if i.get("esito") not in ("inviato",):
            trovati.append(
                {
                    "livello": "avviso",
                    "campagna": i.get("campagna", ""),
                    "clip": None,
                    "messaggio": f"invio Whop con esito «{i.get('esito')}»: {i.get('post')}",
                }
            )
    return trovati


def ritmo_identita() -> list[dict]:
    """Per ogni identita' attiva del registro: si puo' pubblicare adesso, e perche' no."""
    identita = identita_attive()
    campagne = campagne_output()
    per_identita: dict[str, list[str]] = {i["id"]: [] for i in identita}
    for camp in campagne:
        for c in camp["clip"]:
            if (
                c["identita"]
                and c["stato_pubblicazione"] in ("pubblicato", "done")
                and c["pubblicato_il"]
            ):
                per_identita.setdefault(c["identita"], []).append(c["pubblicato_il"])

    esiti = []
    for i in identita:
        esito = ritmo.quando_si_puo(
            per_identita.get(i["id"], []),
            distanza_minima=int(i.get("min_gap_seconds", 0)),
            massimo=int(i.get("max_per_day", 0)),
            aperto_il=i.get("account_started"),
        )
        esiti.append(
            {
                **esito,
                "identita": i["id"],
                "handle": i["handle"],
                "piattaforma": i["piattaforma"],
                "nicchia": i.get("nicchia"),
            }
        )
    return esiti


def campagne_radar(min_mesi: float = 0.0, solo_iscritti: bool = False) -> list[dict]:
    dettagli = leggi(RADAR_DIR / "radar_dettagli.json", [])
    provider = {
        p["provider"]: p for p in leggi(RADAR_DIR / "provider.json", {}).get("provider", [])
    }
    righe = []
    for c in dettagli:
        p = provider.get(c.get("provider"), {})
        riga = dict(c)
        riga["iscritto"] = bool(p.get("iscritto"))
        riga["organizzazione"] = c.get("organizzazione") or p.get("organizzazione")
        riga["attrito_invio"] = p.get("attrito_invio", "ignoto")
        riga.update(tassonomia.paletti(c))
        righe.append(riga)
    if min_mesi:
        righe = [r for r in righe if (r.get("mesi") or 0) >= min_mesi]
    if solo_iscritti:
        righe = [r for r in righe if r["iscritto"]]
    righe.sort(key=lambda r: r.get("per_clipper", 0), reverse=True)
    return righe


def clip_renderizzate() -> list[dict]:
    """
    Le clip .mp4 gia' pronte, con hook e punteggi da decisions.json quando ci sono.

    L'aggancio e' il nome file: `decisions.json` chiama ogni clip con lo
    `slug` che e' esattamente lo stem del file renderizzato
    (`01-you-were-never-a-pro.mp4` <-> slug `01-you-were-never-a-pro`) — non
    l'id numerico, che in `decisions.json` non esiste.
    """
    risultato = []
    for cartella in sorted(OUTPUT_DIR.glob("*/")):
        mp4 = sorted(cartella.glob("*.mp4"))
        if not mp4 or cartella_esclusa(cartella.name):
            continue
        decisioni = leggi(WORK_DIR / cartella.name / "decisions.json", {}) or {}
        per_slug = {d.get("slug"): d for d in decisioni.get("clips", [])}
        for f in mp4:
            d = per_slug.get(f.stem, {})
            risultato.append(
                {
                    "cartella": cartella.name,
                    "file": f.name,
                    "id": f.stem.split("-", 1)[0],
                    "hook": d.get("hook"),
                    "durata": round(d["end"] - d["start"], 1)
                    if "start" in d and "end" in d
                    else None,
                    "punteggi": d.get("scores"),
                    "motivazione": d.get("why"),
                }
            )
    return risultato


# --------------------------------------------------------------- le scelte

SCELTE = HERE / "scelte.json"
STATI_SCELTA = ("da-lanciare", "in-corso", "sospesa", "chiusa")


def scelte() -> list[dict]:
    """Le campagne che una persona ha deciso di lavorare. Vedi `scrivi_scelta`."""
    return leggi(SCELTE, {}).get("scelte", [])


def scrivi_scelta(
    campagna_id: str,
    gruppo: str | None = None,
    stato: str = "da-lanciare",
    nota: str = "",
    rimuovi: bool = False,
    nicchia: str | None = None,
) -> list[dict]:
    """
    Aggiunge, aggiorna o toglie una scelta. **E' l'unica scrittura di stato
    che questo file fa**, e ha la stessa giustificazione dello storico delle
    views: e' un dato che non appartiene a nessun altro file. Il radar sa
    quali campagne esistono, `assegnazioni.json` sa in che nicchia stanno,
    `publish.json` sa cosa e' gia' stato lavorato — ma quale campagna
    *iniziare adesso* non lo sapeva nessuno, e finiva in un messaggio.

    Non pubblica niente e non tocca `output/`: dice solo che una campagna e'
    stata scelta. Il lavoro vero resta un comando lanciato a mano.

    **Si puo' scegliere anche dove un account non c'e' ancora.** Allora la
    scelta porta la `nicchia` e nessun `gruppo`: dice «questa campagna, per
    questa nicchia», che e' tutto quello che il clic sa. Il gruppo lo aggiunge
    il registro quando la nicchia ne avra' uno (`calendario.gruppo_di`), e fino
    ad allora si prepara e si renderizza ma non si programma niente.
    """
    if stato not in STATI_SCELTA:
        raise ValueError(f"stato «{stato}» sconosciuto: {STATI_SCELTA}")
    dati = leggi(SCELTE, {}) or {}
    elenco = [s for s in dati.get("scelte", []) if s.get("campagna_id") != campagna_id]

    if not rimuovi:
        precedente = next(
            (s for s in dati.get("scelte", []) if s.get("campagna_id") == campagna_id), {}
        )
        radar = {c["id"]: c for c in leggi(RADAR_DIR / "radar_dettagli.json", [])}
        elenco.append(
            {
                "campagna_id": campagna_id,
                "titolo": radar.get(campagna_id, {}).get("titolo", precedente.get("titolo", "")),
                "gruppo": gruppo or precedente.get("gruppo"),
                "nicchia": nicchia or precedente.get("nicchia"),
                "stato": stato,
                "nota": nota or precedente.get("nota", ""),
                "scelta_il": precedente.get("scelta_il")
                or datetime.now().isoformat(timespec="seconds"),
                "aggiornata_il": datetime.now().isoformat(timespec="seconds"),
                "cartelle": precedente.get("cartelle", []),
            }
        )

    dati["_leggimi"] = (
        "Le campagne scelte da lavorare, con il gruppo (account) che le lavora. "
        "Lo scrive la plancia quando ci clicchi sopra; non pubblica niente. "
        "Stati: da-lanciare | in-corso | sospesa | chiusa."
    )
    dati["scelte"] = sorted(elenco, key=lambda s: s.get("aggiornata_il", ""), reverse=True)
    SCELTE.write_text(json.dumps(dati, ensure_ascii=False, indent=1), encoding="utf-8")
    return dati["scelte"]


def gruppi() -> list[dict]:
    """I gruppi (un account Google con i suoi profili) con quello che si sa di loro."""
    per_id = {i["id"]: i for i in identita_attive()}
    scelte_per_gruppo: dict[str, list[dict]] = {}
    for s in scelte():
        scelte_per_gruppo.setdefault(s.get("gruppo") or "(nessuno)", []).append(s)

    fuori = []
    for g in registro().get("gruppi", []):
        presenti = [per_id[i]["piattaforma"] for i in g.get("identita", []) if i in per_id]
        attese = [p.lower() for p in g.get("piattaforme_attese", [])]
        sue = scelte_per_gruppo.get(g["id"], [])
        fuori.append(
            {
                **g,
                "piattaforme_presenti": presenti,
                "piattaforme_mancanti": [p for p in attese if p not in presenti],
                "aperto": bool(presenti),
                "scelte": len(sue),
                "in_corso": sum(1 for s in sue if s.get("stato") == "in-corso"),
            }
        )
    return fuori


def nicchia_per_uuid() -> dict[str, str]:
    """
    Da `whop_campaign_uuid` alla nicchia decisa leggendo il brief.

    E' l'aggancio che mancava fra il lavoro fatto e la nicchia che lo ospita:
    `publish.json` non dice sempre il gruppo — le cartelle nate prima dei
    gruppi non lo dicono affatto — ma dice **sempre** l'uuid della campagna,
    che e' lo stesso identificativo del radar. Il nome della campagna no: nelle
    cartelle e' scritto a mano e non coincide mai due volte.
    """
    return {
        a["id"]: a["nicchia"]
        for a in leggi(RADAR_DIR / "assegnazioni.json", {}).get("assegnazioni", [])
    }


PIATTAFORME = ("youtube", "tiktok", "instagram")


def misure_per_url() -> dict[str, dict]:
    """L'ultima misura di ogni post, indicizzata per URL."""
    try:
        import metriche

        return {r["post_id"]: r for r in metriche.ultime() if r.get("post_id")}
    except Exception:
        return {}


def salute_per_url() -> dict[str, dict]:
    """L'ultima lettura dei segnali di ogni post (`metriche.py --account`), per URL."""
    try:
        import metriche

        return metriche.ultima_salute()
    except Exception:
        return {}


# Dopo quanto uno zero smette di essere «appena uscito» e diventa un segnale.
ORE_ZERO_VIEWS = 24


def avvisi_post(
    views: int | None,
    pubblicato_il: str | None,
    letto_il: str | None,
    segnalati: list[str] | None = None,
) -> list[str]:
    """
    Cosa dire di un post, oltre al numero.

    I segnali che la piattaforma scrive (in revisione, restrizioni) e uno che si
    ricava: **zero views a un giorno dall'uscita**. Non e' un segnale che le
    piattaforme danno, ed e' quello che conta di piu': la prima lettura di TikTok,
    il 17 settembre 2026, ha trovato quattro post su quattro a zero dopo cinque
    giorni, senza un avviso da nessuna parte. Si conta dal momento della
    **lettura**, non da adesso: una lettura vecchia dice cos'era vero allora.
    `None` non e' zero — vuol dire non misurato — e non dice niente.
    """
    avvisi = list(segnalati or [])
    uscita, lettura = _iso(pubblicato_il), _iso(letto_il)
    if views == 0 and uscita and lettura:
        ore = (lettura - uscita).total_seconds() / 3600
        if ore >= ORE_ZERO_VIEWS:
            quanto = f"{ore / 24:.0f} giorni" if ore >= 48 else f"{ore:.0f} ore"
            avvisi.append(f"0 views a {quanto} dall'uscita")
    return avvisi


def pagamenti_chiusi(camp: dict, scheda: dict | None, schede_lette: bool) -> str | None:
    """
    Perche' una campagna Whop non paghera' piu' niente, o `None` se puo' ancora.

    Non e' `conclusa()`: li' «chiusa a mano» viene prima di tutto, ed e' una
    decisione nostra — si smette di lavorare una campagna che magari paga
    ancora. Qui contano solo i fatti della campagna. Il primo e' l'uscita
    dall'elenco: Contoso, finita a settembre 2026, restava `active` nella sua
    pagina pubblica con 4,41 $ di residuo su 10.500, e `discover` non la
    elencava piu'. Se il radar non ha mai girato (`schede_lette` falso) l'assenza
    non dice niente.
    """
    if not camp.get("whop_campaign_uuid"):
        return None
    # La regola sta in `tassonomia.fine_campagna`, perche' la legge anche
    # `publish/esegui.py`: il budget e' esaurito quando il residuo non paga piu'
    # nemmeno il minimo di una clip, non quando arriva a zero.
    return tassonomia.fine_campagna(scheda, schede_lette)


def pagamento(
    whop: str | None, views: int | None, soglia: float | None, finita: str | None = None
) -> dict | None:
    """
    A che punto e' il pagamento di un invio, dedotto da views e soglia.

    Whop tiene un invio in **Pending** finche' il guadagno non arriva al minimo
    della campagna per quella piattaforma, cioe' finche' le views non toccano
    `minimo / cpm x 1000` (`tassonomia.break_even`); solo dopo lo passa alla
    revisione del brand. Il 15 settembre 2026 i tre invii YouTube di Contoso erano
    Pending con 13, 2 e 1.076 views contro una soglia di 1.750, e la pagina non
    lo poteva dire: sembrava un guasto ed era la regola. Da qui l'etichetta
    accanto alle views.

    E' **dedotta**, non letta: lo stato vero sta nella pagina «Submissions» di
    Whop, che nessuno script legge. Per questo «sopra soglia» non si chiama «in
    revisione» — e un respinto non si puo' dedurre affatto. `None` quando l'invio
    non c'e'; soglia e views mancanti hanno la loro etichetta, perche' «non si
    sa» non e' «sotto soglia».

    **A campagna finita (`finita`, da `pagamenti_chiusi`) «sotto soglia» vuol
    dire «non paghera'»**: il Pending non si sblocca piu', e l'etichetta di
    prima avrebbe fatto aspettare soldi che non arrivano. Sopra soglia resta
    com'e': quell'invio puo' essere gia' stato pagato.
    """
    if whop not in WHOP_FATTO:
        return None
    if finita and soglia is None:
        return {
            "etichetta": "campagna finita",
            "frase": f"{finita[0].upper()}{finita[1:]}: se l'invio non aveva raggiunto "
            f"la soglia, non pagherà.",
        }
    if finita and soglia > 0 and (views is None or views < soglia):
        s = f"{round(soglia):,}".replace(",", ".")
        letto = "views non lette" if views is None else f"{views:,}".replace(",", ".") + " views"
        return {
            "etichetta": "non pagherà",
            "frase": f"{letto} su {s} e {finita}: il Pending non si sblocca più.",
        }
    if soglia is None:
        return {
            "etichetta": "soglia ignota",
            "frase": "La campagna non dichiara minimo e CPM per questa piattaforma: "
            "non si sa da quante views paga.",
        }
    s = f"{round(soglia):,}".replace(",", ".")
    if soglia <= 0:
        return {"etichetta": "nessun minimo", "frase": "Paga dalla prima view."}
    if views is None:
        return {
            "etichetta": "views non lette",
            "frase": f"Paga da {s} views; le views di questo post non sono ancora state lette.",
        }
    v = f"{views:,}".replace(",", ".")
    if views < soglia:
        mancano = f"{round(soglia - views):,}".replace(",", ".")
        return {
            "etichetta": "sotto soglia",
            "frase": f"{v} views su {s}: Whop tiene l'invio in Pending finché il "
            f"guadagno non arriva al minimo. Ne mancano {mancano}.",
        }
    return {
        "etichetta": "sopra soglia",
        "frase": f"{v} views, soglia {s}: il minimo è raggiunto, e per le regole di "
        f"Whop l'invio passa alla revisione del brand. Lo stato vero su Whop "
        f"non lo leggiamo.",
    }


def clip_di_campagna(
    camp: dict,
    renderizzate: list[dict],
    misure: dict,
    attese: list[str] | None = None,
    salute: dict | None = None,
) -> list[dict]:
    """
    Una riga per clip, con le tre piattaforme accanto.

    E' la vista che serve davanti a una campagna: non «34 pubblicazioni su 51»
    ma **questa clip dov'e' uscita e quanto ha fatto**. Le tre piattaforme
    stanno sulla stessa riga perche' la stessa clip esce su tutte e tre, e
    guardarle separate e' il modo di non accorgersi che su una manca.
    """
    file_per_id = {c["id"]: c for c in renderizzate if c["cartella"] == camp["cartella"]}
    # Le soglie per piattaforma le ha gia' messe `tassonomia.paletti` sulla riga
    # della campagna: una campagna fuori dal catalogo non le ha, e resta «ignota».
    soglie = camp.get("break_even_per_piattaforma") or {}
    per_id: dict[str, dict] = {}
    for c in camp["clip"]:
        voce = per_id.setdefault(
            c["id"],
            {
                "id": c["id"],
                "titolo": c["titolo"],
                "file": c.get("file"),
                "quando": c.get("quando"),
                "piattaforme": {},
                "views": 0,
            },
        )
        misura = misure.get(c["url"] or "") or {}
        segnali = (salute or {}).get(c["url"] or "") or {}
        voce["piattaforme"][c["piattaforma"]] = {
            "stato": c["stato_pubblicazione"],
            "url": c["url"],
            "views": misura.get("views"),
            "letto_il": misura.get("quando"),
            "whop": c["stato_whop"],
            "avvisi": avvisi_post(
                misura.get("views"), c["pubblicato_il"], misura.get("quando"), segnali.get("avvisi")
            ),
            # Senza `whop_campaign_uuid` la campagna non passa da Whop, e il suo
            # «in attesa» di default non e' un invio: niente da dire sul pagamento.
            "pagamento": pagamento(
                c["stato_whop"],
                misura.get("views"),
                soglie.get(c["piattaforma"]),
                camp.get("pagamenti_chiusi"),
            )
            if camp.get("whop_campaign_uuid")
            else None,
        }
        voce["views"] += misura.get("views") or 0
    # Le piattaforme che la campagna accetta, non quelle che la cartella
    # dichiara. `publish.json` della Acme Show nomina solo YouTube perche' e'
    # da li' che si era partiti, ma la campagna prende tutte e tre: contando due
    # piattaforme la percentuale diceva 8 su 34 invece di 8 su 51, cioe' faceva
    # sembrare piu' vicino il traguardo di quanto sia.
    for voce in per_id.values():
        for p in attese or []:
            voce["piattaforme"].setdefault(
                p,
                {
                    "stato": "in coda",
                    "url": None,
                    "views": None,
                    "letto_il": None,
                    "whop": "in attesa",
                    "avvisi": [],
                    "pagamento": None,
                },
            )
    for cid, voce in per_id.items():
        reso = file_per_id.get(cid, {})
        voce["hook"] = reso.get("hook")
        voce["durata"] = reso.get("durata")
        voce["punteggi"] = reso.get("punteggi")
        voce["renderizzata"] = bool(reso)
        if reso.get("file"):
            voce["file"] = reso["file"]
        voce["pubblicata_su"] = sum(
            1 for p in voce["piattaforme"].values() if p["stato"] in ("pubblicato", "done")
        )
    return sorted(per_id.values(), key=lambda v: v["id"])


# I posti da cui puo' venire un video sorgente, in ordine di preferibilita'.
# YouTube per primo perche' e' l'unico che `ingest.py` sa scaricare senza
# sorprese; Drive funziona; Twitch e Kick vanno ma sono VOD lunghi.
FONTI = (
    re.compile(r"https?://(?:www\.)?youtube\.com/(?:watch\?v=|live/|shorts/)[\w\-?=&.%]+"),
    re.compile(r"https?://youtu\.be/[\w\-?=&.%]+"),
    re.compile(r"https?://(?:www\.)?youtube\.com/playlist\?list=[\w\-]+"),
    re.compile(r"https?://drive\.google\.com/(?:file/d/[\w\-]+|drive/folders/[\w\-]+)[^\s\"'<>)]*"),
    re.compile(r"https?://(?:www\.)?twitch\.tv/videos/\d+"),
    re.compile(r"https?://(?:www\.)?vimeo\.com/\d+"),
)
DOCUMENTI = re.compile(r"https?://(?:docs|app)\.(?:google|notion)\.com/[^\s\"'<>)]+")


def sorgente_campagna(scheda: dict | None, noto: str | None = None) -> dict:
    """
    Il link del video da clippare, **trovato invece che chiesto**.

    Sta in due posti e nessuno dei due e' un campo: fra i `materiali` della
    scheda, oppure dentro il brief, che e' un documento scritto da un'altra
    persona. Cercarlo qui vuol dire che il pulsante «prepara» funziona senza
    che qualcuno vada a rileggere il brief e copi un indirizzo — che e'
    esattamente il lavoro che questa pagina esiste per non far fare.

    Torna anche i documenti, perche' quando un sorgente non si trova la
    risposta giusta non e' un campo vuoto: e' il link al brief da aprire.
    """
    scheda = scheda or {}
    brief = RADAR_DIR / "briefs" / f"{scheda.get('id', '')}.txt"
    testo_brief = ""
    if brief.exists():
        try:
            testo_brief = brief.read_text(encoding="utf-8", errors="replace")
        except OSError:
            pass

    # **L'ordine dei posti conta piu' dell'ordine dei formati.** Il brief e' pieno
    # di link: il video da clippare, ma anche gli esempi di clip riuscite e i
    # canali da guardare. Cercando tutto insieme, la Acme Show proponeva un
    # esempio (`1P5Y7OcRyz8`) al posto dell'episodio (`WyCRc1TH7ek`) — e sarebbe
    # partita una preparazione da un'ora sul video sbagliato, senza un errore.
    # Quindi: prima quello che sappiamo gia', poi gli allegati ufficiali della
    # campagna, e il brief solo se i primi due non dicono niente.
    trovati: list[str] = []

    def raccogli(testo: str) -> None:
        for schema in FONTI:
            for m in schema.finditer(testo or ""):
                if m.group(0) not in trovati:
                    trovati.append(m.group(0))

    if noto:
        trovati.append(noto)
    raccogli(" ".join(scheda.get("materiali") or []))
    quanti_sicuri = len(trovati)
    raccogli(testo_brief)

    documenti = list(
        dict.fromkeys(
            DOCUMENTI.findall(" ".join(scheda.get("materiali") or []) + " " + testo_brief)
        )
    )[:3]
    return {
        "sorgente": trovati[0] if trovati else None,
        "sorgente_certo": bool(quanti_sicuri),
        "altre_fonti": trovati[1:8],
        "documenti": documenti,
    }


def conclusa(
    camp: dict, scheda: dict | None, scelta: dict | None = None, schede_lette: bool = False
) -> tuple[bool, str]:
    """
    Se una campagna e' finita, e perche'.

    Due modi di finire, e vanno distinti: **abbiamo caricato tutto** oppure
    **il budget e' esaurito** — nel secondo caso le clip che restano non
    varranno mai niente, e tenerle in coda come lavoro da fare e' una bugia
    che si racconta ogni mattina.
    """
    # La chiusura a mano viene per prima: e' un giudizio, e un giudizio batte
    # una deduzione. Si smette di lavorare una campagna anche per ragioni che
    # nessun numero conosce — il brief e' cambiato, il cliente non risponde.
    finita = tassonomia.campagna_finita(
        camp.get("whop_campaign_uuid"), scheda, schede_lette, scelta
    )
    if finita:
        return True, finita
    if camp["totale_clip"] and camp["da_pubblicare"] == 0:
        return True, "tutte le clip caricate su tutte le piattaforme"
    return False, ""


def riserva_scegli(c: dict, scelta: dict | None) -> tuple[str, str]:
    """
    Perche' questa campagna non si prende: un'etichetta e una frase.

    **Il pulsante «scegli» resta solo dove la campagna si puo' prendere** —
    una campagna che pretende una pagina intestata a se' e fuori dal piano, e
    un pulsante dietro una conferma sarebbe un divieto che si puo' cliccare.
    Ma la casella **non resta vuota**: su undici righe visibili su
    centosettantaquattro non c'era il pulsante e l'unica spiegazione stava in
    una legenda in fondo alla pagina, che ne elencava due ragioni su cinque.
    Da fuori una regola applicata cosi' e indistinguibile da un guasto.

    Torna `("", "")` per le campagne proponibili. **Le ragioni le scrive il
    server** e non chi disegna: l'etichetta e la frase escono dalla stessa
    catena di condizioni, e riderivarle nel browser vorrebbe dire tenere due
    volte la regola che decide chi entra nelle somme.
    """
    if scelta:
        quando = (scelta.get("scelta_il") or "")[:10]
        stato_scelta = scelta.get("stato") or "da-lanciare"
        return (
            "già scelta",
            f"già scelta{' il ' + quando if quando else ''} ({stato_scelta}): "
            "sta fra le campagne attive o concluse di questa nicchia",
        )
    if c.get("pagina") == "marca":
        return (
            "pagina marca",
            "il brief pretende una pagina intestata a quel creator: fuori dal piano, "
            "perché i tre account devono servire più campagne possibile",
        )
    if c.get("pagina") == "esclusiva":
        return (
            "pagina esclusiva",
            "il brief vieta altre campagne sulla stessa pagina: fuori dal piano, "
            "perché brucerebbe un account per una campagna sola",
        )
    if c.get("lavorabile") == "lingua":
        return (
            "lingua",
            "forma buona, ma il parlato non è italiano né inglese: la pipeline non lo trascrive",
        )
    if c.get("lavorabile") == "no":
        return (
            "forma",
            "forma incompatibile: quasi sempre un video già montato, "
            "dove non c'è niente da estrarre",
        )
    if c.get("lavorabile") != "si":
        return (
            "da leggere",
            "nessuno ha ancora letto questa campagna: la nicchia è un'ipotesi "
            "finché il brief non è stato letto",
        )
    return ("", "")


def catalogo_di_nicchia(nicchia: dict, radar: dict, scelte_per_id: dict) -> dict:
    """
    Tutte le campagne di una nicchia, raggruppate per sottonicchia.

    E' quello che stava nella scheda «Catalogo», portato **dentro** la nicchia:
    la nicchia e' l'unita' del piano — degli account, del ritmo, dei conti — e
    tenerne il catalogo in una pagina a parte voleva dire rispondere a «cosa
    posso lavorare nel gaming» in due posti diversi.

    Le righe restano **tutte**, contate per stato, ma chi disegna apre sulle
    lavorabili: su 627 campagne 490 sono di forma incompatibile — video gia'
    montati, dove non c'e' niente da estrarre — e musica da sola ne ha 147 su
    151. Mostrarle per prime non e' «avere il catalogo», e' seppellire le
    quattro che contano sotto quelle gia' scartate.

    Ogni riga porta anche `iscritto` e `attrito_invio`, che stavano solo nella
    scheda «Radar»: il radar **vede** tutte le campagne dalla pagina pubblica,
    ma per **inviare** bisogna essere iscritti al whop di quel provider, e oggi
    lo siamo a 1 provider su 289. Senza quella colonna la pagina elenca lavoro
    che non si puo' consegnare, e non lo dice.
    """
    # Quattro stati, non tre. «Non ancora letta» (`?`) non e' «forma
    # incompatibile»: la prima aspetta un giudizio, la seconda ne ha gia'
    # ricevuto uno. Contarle insieme direbbe che una campagna arrivata stanotte
    # e' stata esaminata e scartata, ed e' il genere di bugia che questa pagina
    # esiste per non dire.
    conti = {
        "lavorabili": 0,
        "lingua": 0,
        "incompatibili": 0,
        "da_leggere": 0,
        "proponibili": 0,
        "totale": 0,
    }
    quale = {"si": "lavorabili", "lingua": "lingua", "no": "incompatibili"}
    sottonicchie = []
    for sotto in nicchia.get("sottonicchie", []):
        righe = []
        for c in sotto["campagne"]:
            m = radar.get(c["id"], {})
            scelta = scelte_per_id.get(c["id"])
            etichetta, perche = riserva_scegli(c, scelta)
            righe.append(
                {
                    **c,
                    "organizzazione": m.get("organizzazione") or "",
                    "iscritto": bool(m.get("iscritto")),
                    "attrito_invio": m.get("attrito_invio", "ignoto"),
                    "stato": m.get("stato", ""),
                    "scelta": scelta,
                    # «Regge» e' la stessa soglia del report delle nicchie: campione
                    # valido e budget per almeno un mese e mezzo. Separa una
                    # campagna da lavorare da una che finisce prima che l'account
                    # abbia finito di scaldarsi.
                    "regge": bool(c.get("campione_ok") and (c.get("mesi") or 0) >= 1.5),
                    # Si puo' **prendere senza riserve**, che non e' «si puo'
                    # lavorare»: una campagna che pretende una pagina intestata a
                    # se' (`marca`) o vieta di condividerla (`esclusiva`) e'
                    # lavorabile per forma e fuori dal piano per politica — 15 in
                    # catalogo. **Decide anche il pulsante «scegli»**: dove qui c'e'
                    # `False` la campagna non si prende, e l'ordinamento e il conto
                    # «da prendere» leggono lo stesso campo.
                    "proponibile": (
                        c["lavorabile"] == "si"
                        and not scelta
                        and c.get("pagina") not in tassonomia.PAGINA_ESCLUSA
                    ),
                    # Dove il pulsante non c'e', la casella dice perche': un'etichetta
                    # breve al suo posto e la frase intera nel titolo. Vuote entrambe
                    # dove la campagna si prende.
                    "etichetta_non_proponibile": etichetta,
                    "perche_non_proponibile": perche,
                    "sorgente": facilita_sorgente(c["id"]),
                }
            )
            conti["totale"] += 1
            conti[quale.get(c["lavorabile"], "da_leggere")] += 1
            conti["proponibili"] += bool(righe[-1]["proponibile"])
        # **Dentro una sottonicchia si ordina per `$/post`**, non per
        # `per_clipper` come faceva `quadro()`: e' la misura con cui si sceglie
        # una campagna, ed e' quella che ha gia' proposto due nicchie sbagliate
        # di fila quando era l'altra. Prima quelle che si possono prendere.
        righe.sort(key=lambda r: (not r["proponibile"], -(r.get("dollari_post") or 0)))
        sottonicchie.append(
            {
                "nome": sotto["nome"],
                "campagne": righe,
                "lavorabili": sum(1 for r in righe if r["lavorabile"] in ("si", "?")),
            }
        )
    # Prima le sottonicchie che hanno qualcosa da lavorare: dentro una nicchia
    # da 151 campagne, quella con zero lavorabili non e' la prima cosa da
    # leggere. A parita', la piu' grossa.
    sottonicchie.sort(key=lambda s: (-s["lavorabili"], -len(s["campagne"])))
    return {**conti, "sottonicchie": sottonicchie}


def sparite() -> list[dict]:
    """
    Le campagne giudicate che dal catalogo non ci sono piu'.

    Sono 83 su 710 assegnazioni, e 13 erano lavorabili. Finora non comparivano
    da nessuna parte: `quadro()` incrocia le assegnazioni con le schede vive e
    quelle senza scheda cadono fuori in silenzio — che e' il difetto che questo
    progetto insegue ovunque. Non si cancellano: il giudizio su una campagna
    costa una lettura del brief, e una campagna puo' rientrare nel catalogo.
    """
    vive = {s["id"] for s in leggi(RADAR_DIR / "radar_dettagli.json", [])}
    orfane = [
        a
        for a in leggi(RADAR_DIR / "assegnazioni.json", {}).get("assegnazioni", [])
        if a["id"] not in vive
    ]
    orfane.sort(key=lambda a: (a.get("lavorabile") != "si", a.get("nicchia") or ""))
    return [
        {
            "id": a["id"],
            "titolo": a.get("titolo", ""),
            "nicchia": a.get("nicchia") or "",
            "sottonicchia": a.get("sottonicchia") or "",
            "lavorabile": a.get("lavorabile"),
            "nota": a.get("nota") or "",
        }
        for a in orfane
    ]


def pannello() -> list[dict]:
    """
    Una scheda per **nicchia**, tutte e sedici, non piu' una per account.

    E' la pagina del pannello di controllo tradotta in dati, ed e' l'unica: le
    schede «Catalogo» e «Radar» erano altre due viste delle stesse 627
    campagne, e tenerle separate significava rispondere a «cosa lavoro adesso»
    in tre posti che non si parlavano. Qui la nicchia porta tutto quello che la
    riguarda — chi ci pubblica, cosa e' uscito, quanto ha reso, cosa c'e' da
    prendere e **l'intero catalogo** — perche' la nicchia e' l'unita' del piano.

    Le nicchie con un account aperto restano in cima: sono il lavoro. Le altre
    tredici hanno campagne e non hanno pubblicazioni: le loro schede hanno le
    stesse sezioni, perche' una campagna si sceglie e si prepara anche prima
    dell'account, e la differenza la dice l'etichetta «nessun account». **Il laboratorio non e' una nicchia** e sta
    in fondo, dichiarato: tiene consegne vere, ma i suoi numeri non misurano il
    piano — e le sue campagne appartengono alla nicchia loro, non a lui.
    """
    q = tassonomia.quadro(RADAR_DIR / "radar_dettagli.json")
    numeri = {n["nome"]: n for n in q.get("nicchie", [])}
    per_uuid = nicchia_per_uuid()
    campagne = campagne_output()
    scelte_per_id = {s["campagna_id"]: s for s in scelte()}
    misure = metriche_per_cartella()
    ritmi = {r["identita"]: r for r in ritmo_identita()}
    renderizzate = clip_renderizzate()
    per_url = misure_per_url()
    salute = salute_per_url()
    schede = {s["id"]: s for s in leggi(RADAR_DIR / "radar_dettagli.json", [])}
    radar_per_id = {c["id"]: c for c in campagne_radar()}
    gruppo_per_nicchia = {g.get("nicchia"): g for g in gruppi() if g.get("nicchia")}

    def consegne(g: dict | None, nicchia: str) -> dict:
        """
        Cosa si lavora in questa nicchia, e cosa e' uscito dal gruppo che la lavora.

        **Una nicchia senza account ha comunque le sue campagne scelte.** Prima
        qui si tornava vuoti, e senza campagne attive non c'era nemmeno il
        pulsante «scegli»: per prendere una campagna fuori dalle tre nicchie
        aperte bisognava prima aprire un account, cioe' decidere l'account
        prima della campagna. Vuoti si torna solo dove una nicchia non c'e'.
        """
        gid = (g or {}).get("id")
        if not g and not nicchia:
            return {
                "campagne": [],
                "attive": [],
                "concluse": [],
                "pubblicate": 0,
                "da_pubblicare": 0,
                "totale": 0,
                "percentuale": 0,
                "metriche": {"post": 0, "views": 0, "guadagno": 0.0},
            }
        # Una cartella che **dichiara** il suo gruppo appartiene a quello e a
        # nessun altro; la nicchia della campagna aggancia solo le cartelle che
        # il gruppo non ce l'hanno (quelle nate prima dei gruppi). Con un `or`
        # secco una cartella finiva in due schede e i suoi numeri venivano
        # contati due volte: la consegna Acme Show e' uscita dagli account del
        # laboratorio, e la sua campagna e' di nicchia business — sommarla anche
        # al business vuol dire misurare il piano con i risultati degli account
        # che il laboratorio esiste apposta per tenerne fuori.
        # `gid and`: senza gruppo, `None == None` prenderebbe ogni cartella che
        # il gruppo non lo dichiara, di qualunque nicchia sia.
        sue = [
            c
            for c in campagne
            if (
                (gid and c.get("gruppo") == gid)
                or (
                    not c.get("gruppo")
                    and per_uuid.get(c.get("whop_campaign_uuid") or "") == nicchia
                )
            )
        ]
        for c in sue:
            c["metriche"] = misure.get(c["cartella"], {"post": 0, "views": 0, "guadagno": 0.0})
            scheda = schede.get(c.get("whop_campaign_uuid") or "")
            # Le piattaforme su cui la clip **deve** uscire: quelle che la
            # campagna accetta, incrociate con quelle che il gruppo ha. Il
            # conto del lavoro si fa su queste, non su quelle gia' usate.
            accettate = [
                p
                for p in PIATTAFORME
                if p in {x.lower() for x in ((scheda or {}).get("piattaforme") or [])}
            ]
            attese = [
                p
                for p in (accettate or c["piattaforme"])
                if not (g or {}).get("piattaforme_attese")
                or p in [x.lower() for x in g["piattaforme_attese"]]
            ]
            c["piattaforme"] = sorted(set(c["piattaforme"]) | set(attese))
            scelta = scelte_per_id.get(c.get("whop_campaign_uuid") or "")
            c["conclusa"], c["perche_conclusa"] = conclusa(c, scheda, scelta, bool(schede))
            c["scelta_stato"] = (scelta or {}).get("stato")
            c["residuo"] = (scheda or {}).get("residuo")
            c["mesi"] = (scheda or {}).get("mesi")
            # Tetto, minimo, piattaforme accettate e i due indirizzi: sono i
            # paletti che restano veri anche a lavoro iniziato — il tetto dice
            # quando smettere di sperare in una clip che va forte, il minimo
            # quando una clip non pagherebbe comunque.
            c.update(tassonomia.paletti(scheda or {}))
            c.update(sorgente_campagna(scheda, c.get("source_episode")))
            c["risolutore"] = risolutore(c.get("whop_campaign_uuid"))
            c["pagamenti_chiusi"] = pagamenti_chiusi(c, scheda, bool(schede))
            c["clip_dettaglio"] = clip_di_campagna(c, renderizzate, per_url, attese, salute)
            # Ricontato sulle piattaforme vere: 17 clip su tre piattaforme sono
            # 51 pubblicazioni, e la percentuale deve dirlo.
            c["totale_clip"] = len(c["clip_dettaglio"]) * len(c["piattaforme"])
            c["pubblicate"] = sum(v["pubblicata_su"] for v in c["clip_dettaglio"])
            c["da_pubblicare"] = c["totale_clip"] - c["pubblicate"]
            # La percentuale si calcola **dopo** il ricalcolo, non prima: quella
            # di `campagne_output()` conta le piattaforme dichiarate dalla
            # cartella, che qui abbiamo appena corretto.
            c["percentuale"] = (
                round(100 * c["pubblicate"] / c["totale_clip"]) if c["totale_clip"] else 0
            )
            c["materiali"] = (scheda or {}).get("materiali") or []
        # Una campagna scelta ma non ancora lavorata e' gia' attiva: e' la
        # promessa che il pulsante «scegli» ha fatto. Senza questa riga
        # sparirebbe fra il clic e la prima cartella, che e' il momento in cui
        # serve di piu' — e' li' che si prepara il sorgente.
        # «Lavorata» va chiesto **per gruppo**, non in assoluto. Acme Show ha una
        # cartella, ma e' del laboratorio: per il business quella campagna e'
        # ancora da lanciare, ed e' l'unico posto in cui si legge che si potra'
        # rifare sugli account nuovi. Con un insieme globale spariva da li'
        # perche' qualcun altro l'aveva gia' toccata.
        lavorate_qui = {
            c.get("whop_campaign_uuid")
            for c in sue
            if c.get("cartella") and c.get("whop_campaign_uuid")
        }
        # Una scelta appartiene alla nicchia se ne dichiara il gruppo, oppure se
        # un gruppo non ce l'ha — scelta dove l'account non c'era — e la nicchia
        # in cui e' stata cliccata e' questa. La seconda strada e' anche quella
        # che la tiene al suo posto il giorno in cui l'account arriva.
        for s in scelte():
            mia = (gid and s.get("gruppo") == gid) or (
                not s.get("gruppo")
                and (s.get("nicchia") or per_uuid.get(s["campagna_id"])) == nicchia
            )
            if not mia or s["campagna_id"] in lavorate_qui:
                continue
            scheda = schede.get(s["campagna_id"]) or {}
            sue.append(
                {
                    "cartella": None,
                    "campagna": scheda.get("titolo") or titolo_di_lavoro(s["campagna_id"]),
                    "provider": scheda.get("organizzazione"),
                    "gruppo": gid,
                    "whop_campaign_uuid": s["campagna_id"],
                    "piattaforme": [],
                    "clip": [],
                    "clip_distinte": 0,
                    "totale_clip": 0,
                    "pubblicate": 0,
                    "da_pubblicare": 0,
                    "per_piattaforma": {},
                    "percentuale": 0,
                    "metriche": {"post": 0, "views": 0, "guadagno": 0.0},
                    "conclusa": s.get("stato") == "chiusa",
                    "perche_conclusa": "chiusa a mano" if s.get("stato") == "chiusa" else "",
                    "scelta_stato": s.get("stato"),
                    "residuo": scheda.get("residuo"),
                    "mesi": scheda.get("mesi"),
                    "clip_dettaglio": [],
                    "materiali": scheda.get("materiali") or [],
                    "da_preparare": True,
                    "scelta_il": s.get("scelta_il"),
                    "risolutore": risolutore(s["campagna_id"]),
                    "lavorazione": lavorazione_di(s["campagna_id"]),
                    **sorgente_campagna(scheda),
                    **tassonomia.paletti(scheda),
                }
            )
        attive = [c for c in sue if not c["conclusa"]]
        concluse = [c for c in sue if c["conclusa"]]
        pubblicate = sum(c["pubblicate"] for c in sue)
        totali = sum(c["totale_clip"] for c in sue)
        return {
            "campagne": sorted(sue, key=lambda c: -c["pubblicate"]),
            "attive": sorted(attive, key=lambda c: (not c.get("da_preparare"), -c["pubblicate"])),
            "concluse": sorted(concluse, key=lambda c: -c["pubblicate"]),
            "pubblicate": pubblicate,
            "da_pubblicare": totali - pubblicate,
            "totale": totali,
            "percentuale": round(100 * pubblicate / totali) if totali else 0,
            "metriche": {
                "post": sum(c["metriche"]["post"] for c in sue),
                "views": sum(c["metriche"]["views"] for c in sue),
                "guadagno": round(sum(c["metriche"]["guadagno"] for c in sue), 2),
            },
        }

    def da_gruppo(g: dict | None) -> dict:
        """Chi pubblica in questa nicchia. Tredici nicchie su sedici non hanno nessuno."""
        if not g:
            return {
                "gruppo": None,
                "descrizione": "",
                "aperto": False,
                "aperto_il": None,
                "piattaforme_presenti": [],
                "piattaforme_mancanti": [],
                "whop_handle": None,
                "ritmo": [],
            }
        return {
            "gruppo": g["id"],
            "descrizione": g.get("descrizione", ""),
            "aperto": g["aperto"],
            "aperto_il": g.get("aperto_il"),
            "piattaforme_presenti": g["piattaforme_presenti"],
            "piattaforme_mancanti": g["piattaforme_mancanti"],
            "whop_handle": g.get("whop_handle"),
            "ritmo": [ritmi[i] for i in g.get("identita", []) if i in ritmi],
        }

    # **Una lista sola di campagne, non due.** C'era anche «Nuove campagne»,
    # ed era un sottoinsieme stretto del catalogo: `lavorabili` meno quelle
    # gia' scelte meno quelle a pagina esclusa. Misurato sulle sedici nicchie,
    # **zero campagne comparivano solo li'** — business 30 = 38 - 1 - 7 — cioe'
    # erano due elenchi che mostravano le stesse righe con due filtri diversi,
    # e chi guardava doveva ricordarsi quale dei due stava leggendo.
    #
    # Fondere una vista in un'altra pero' non e' cancellarla: quello che
    # portava solo lei si sposta prima, o sparisce senza un errore. Erano
    # quattro cose, e adesso stanno sulla riga del catalogo: il pulsante
    # **scegli**, il filtro sulle **pagine** (`marca` ed `esclusiva`: lavorabili
    # per forma, fuori dal piano per politica — 15 in catalogo), la spia
    # **arriva viva / finisce prima**, e l'ordinamento per **`$/post`**.

    fuori = []
    for n in q.get("nicchie", []):
        nicchia = n["nome"]
        g = gruppo_per_nicchia.get(nicchia)
        fuori.append(
            {
                "chiave": nicchia,
                "nicchia": nicchia,
                "definizione": n.get("definizione", ""),
                "e_nicchia": True,
                **da_gruppo(g),
                # I numeri della nicchia vengono da tassonomia: un conto solo.
                "dollari_post": n.get("dollari_post", 0.0),
                "dollari_post_medio": n.get("dollari_post_medio", 0.0),
                "misurate": n.get("misurate", 0),
                "riusabili": n.get("riusabili", 0),
                "buone_durevoli": n.get("buone_durevoli", 0),
                "budget": n.get("budget", 0.0),
                **consegne(g, nicchia),
                "catalogo": catalogo_di_nicchia(n, radar_per_id, scelte_per_id),
            }
        )

    # I gruppi che non stanno su nessuna nicchia — il laboratorio, che ha
    # `nicchia: nessuna`. **Non e' una nicchia e non va confuso con una**: tiene
    # consegne vere, quindi non puo' sparire, ma i suoi numeri non misurano il
    # piano e le sue campagne appartengono alla nicchia loro. Sommarlo al
    # business vorrebbe dire misurare gli account nuovi con i risultati di
    # quelli che il laboratorio esiste apposta per tenere fuori.
    for g in gruppi():
        if g.get("nicchia") in numeri:
            continue
        fuori.append(
            {
                "chiave": g["id"],
                "nicchia": g.get("nicchia") or "",
                "definizione": "",
                "e_nicchia": False,
                **da_gruppo(g),
                "dollari_post": 0.0,
                "dollari_post_medio": 0.0,
                "misurate": 0,
                "riusabili": 0,
                "buone_durevoli": 0,
                "budget": 0.0,
                **consegne(g, g.get("nicchia") or ""),
                "catalogo": None,
            }
        )

    # Prima le nicchie con un account aperto, poi le altre per quanto vale un
    # post; in fondo cio' che nicchia non e'.
    fuori.sort(key=lambda s: (not s["e_nicchia"], not s["aperto"], -s["dollari_post"]))

    # **Le non classificate stanno in cima, e non in fondo.** Non sono una
    # nicchia povera: sono campagne che nessuno ha ancora letto, e nessuna somma
    # di questa pagina le comprende. Oggi sono zero perche' il giro del radar fa
    # leggere anche la nicchia, ma si riempiono da sole ogni volta che il
    # catalogo cresce — ed e' li' che devono farsi vedere, perche' quello che
    # chiedono e' una lettura, non una decisione.
    senza = q.get("senza_nicchia") or []
    if senza:
        fuori.insert(
            0,
            {
                "chiave": "non-classificate",
                "nicchia": "non classificate",
                "definizione": "Campagne che nessuno ha ancora letto: non entrano in "
                "nessuna somma finche' non hanno una nicchia.",
                "e_nicchia": False,
                **da_gruppo(None),
                "dollari_post": 0.0,
                "dollari_post_medio": 0.0,
                "misurate": 0,
                "riusabili": 0,
                "buone_durevoli": 0,
                "budget": 0.0,
                **consegne(None, ""),
                "catalogo": catalogo_di_nicchia(
                    {
                        "sottonicchie": [
                            {
                                "nome": "(da leggere)",
                                "campagne": [
                                    {
                                        **c,
                                        "lavorabile": "?",
                                        "segno": "?",
                                        "forma": "",
                                        "lingua": "",
                                        "nota": "",
                                        "pagina": "",
                                        "pagina_nota": "",
                                    }
                                    for c in senza
                                ],
                            }
                        ]
                    },
                    radar_per_id,
                    scelte_per_id,
                ),
            },
        )
    return fuori


def metriche_per_cartella() -> dict[str, dict]:
    """Views, guadagno e quanti post misurati, raggruppati per cartella di output."""
    try:
        import metriche

        righe = metriche.ultime()
    except Exception:  # senza database non si spegne la pagina
        return {}
    fuori: dict[str, dict] = {}
    for r in righe:
        voce = fuori.setdefault(r.get("cartella") or "?", {"post": 0, "views": 0, "guadagno": 0.0})
        voce["post"] += 1
        voce["views"] += r.get("views") or 0
        voce["guadagno"] += r.get("guadagno") or 0.0
    return fuori


def candidate(includi_lingua: bool = True) -> list[dict]:
    """
    Le campagne che la pipeline puo' lavorare, pronte da scegliere.

    E' `campagne_radar()` incrociata con il giudizio di
    `radar/assegnazioni.json`: nicchia, sottonicchia, forma del sorgente e
    lingua. Senza quell'incrocio la plancia mostrerebbe 528 righe ordinate per
    resa, cioe' chiederebbe di riscegliere ogni mattina quello che e' gia'
    stato deciso leggendo i brief.
    """
    assegnate = {
        a["id"]: a for a in leggi(RADAR_DIR / "assegnazioni.json", {}).get("assegnazioni", [])
    }
    per_nicchia = {g["nicchia"]: g["id"] for g in registro().get("gruppi", [])}
    scelte_per_id = {s["campagna_id"]: s for s in scelte()}

    fuori = []
    for c in campagne_radar():
        a = assegnate.get(c["id"])
        if not a:
            continue
        if a["lavorabile"] == "no":
            continue
        if a["lavorabile"] == "lingua" and not includi_lingua:
            continue
        fuori.append(
            {
                **c,
                "nicchia": a["nicchia"],
                "sottonicchia": a["sottonicchia"],
                "forma": a["forma"],
                "lingua": a["lingua"],
                "lavorabile": a["lavorabile"],
                "nota_nicchia": a.get("nota", ""),
                # La regola di pagina, letta dal brief, viaggia con la campagna: e'
                # la differenza fra una campagna che un account puo' prendere e una
                # che vorrebbe un account tutto suo.
                "pagina": a.get("pagina", ""),
                "pagina_nota": a.get("pagina_nota", ""),
                "dollari_post": tassonomia.dollari_post(c),
                "gruppo_suggerito": per_nicchia.get(a["nicchia"]),
                "scelta": scelte_per_id.get(c["id"]),
                # «Regge» e' la stessa soglia del report delle nicchie: campione
                # valido e budget per almeno un mese e mezzo. E' la riga che
                # separa una campagna da lavorare da una che finisce prima che
                # l'account abbia finito di scaldarsi.
                "regge": bool(c.get("campione_ok") and (c.get("mesi") or 0) >= 1.5),
            }
        )
    fuori.sort(
        key=lambda r: (not r["regge"], r["lavorabile"] != "si", -(r.get("per_clipper") or 0))
    )
    return fuori


def lavoro() -> list[dict]:
    """
    Lo stato di quello che si sta lavorando: una riga per campagna scelta.

    L'aggancio fra una scelta e le cartelle di `output/` e'
    `whop_campaign_uuid`, che e' lo stesso identificativo del radar — non il
    nome della campagna, che nelle cartelle e' scritto a mano e non coincide
    mai due volte.
    """
    campagne = campagne_output()
    radar = {c["id"]: c for c in leggi(RADAR_DIR / "radar_dettagli.json", [])}
    per_uuid: dict[str, list[dict]] = {}
    for camp in campagne:
        if camp.get("whop_campaign_uuid"):
            per_uuid.setdefault(camp["whop_campaign_uuid"], []).append(camp)

    # L'esito dell'iscrizione che «scegli» lancia da solo (`orchestrator.py
    # iscrivi`): senza, una campagna scelta e mai entrata su Whop sembra pronta.
    iscrizioni = leggi(PUBLISH_DIR / "iscrizioni.json", {}) or {}
    fuori = []
    for s in scelte():
        cartelle = per_uuid.get(s["campagna_id"], [])
        cartelle += [
            c for c in campagne if c["cartella"] in (s.get("cartelle") or []) and c not in cartelle
        ]
        m = radar.get(s["campagna_id"], {})
        piattaforme = sorted({p for c in cartelle for p in c["piattaforme"]})
        fuori.append(
            {
                **s,
                "titolo": s.get("titolo") or m.get("titolo", ""),
                "cpm": m.get("cpm"),
                "per_clipper": m.get("per_clipper"),
                "mesi": m.get("mesi"),
                "residuo": m.get("residuo"),
                **tassonomia.paletti(m),
                "iscrizione": iscrizioni.get(s["campagna_id"]),
                "video": len(cartelle),
                "cartelle_trovate": [c["cartella"] for c in cartelle],
                "piattaforme": piattaforme,
                "clip_distinte": sum(c["clip_distinte"] for c in cartelle),
                "pubblicazioni_attese": sum(c["totale_clip"] for c in cartelle),
                "pubblicate": sum(c["pubblicate"] for c in cartelle),
                "da_pubblicare": sum(c["da_pubblicare"] for c in cartelle),
                "per_piattaforma": {
                    p: {
                        "pubblicate": sum(
                            c["per_piattaforma"].get(p, {}).get("pubblicate", 0) for c in cartelle
                        ),
                        "totale": sum(
                            c["per_piattaforma"].get(p, {}).get("totale", 0) for c in cartelle
                        ),
                    }
                    for p in piattaforme
                },
            }
        )
    ordine = {s: n for n, s in enumerate(STATI_SCELTA)}
    fuori.sort(key=lambda r: (ordine.get(r.get("stato"), 9), -(r.get("per_clipper") or 0)))
    return fuori


def calendario() -> list[dict]:
    """Tutte le pubblicazioni programmate, in ordine di quando escono.

    E' la vista che manca a chi vuole sapere **cosa succedera'**: le tabelle per
    campagna dicono cosa e' stato fatto, questa dice cosa uscira' e quando. Sono
    due domande diverse e finora ne era leggibile una sola — il calendario stava
    dentro `publish.json`, che nessuno apre.

    Una riga per **clip**, non per pubblicazione: le tre piattaforme escono
    insieme, e spezzarle in tre righe con la stessa data direbbe tre volte la
    stessa cosa. Lo stato per piattaforma sta dentro la riga.
    """
    adesso = datetime.now()
    righe: list[dict] = []
    schede = {s["id"]: s for s in leggi(RADAR_DIR / "radar_dettagli.json", []) or []}
    scelte_per_id = {s["campagna_id"]: s for s in scelte()}
    for camp in campagne_output():
        cfg = leggi(OUTPUT_DIR / camp["cartella"] / "publish.json", {})
        uuid = camp.get("whop_campaign_uuid")
        # A campagna finita le clip non uscite non usciranno piu': non sono in
        # ritardo ne' ferme, e tenerle nella coda la faceva sembrare lavoro da
        # fare. La regola e' quella di `esegui.py`, che per la stessa ragione
        # non le pubblica.
        finita = tassonomia.campagna_finita(
            uuid, schede.get(uuid or ""), bool(schede), scelte_per_id.get(uuid or "")
        )
        attese = [
            p.lower() for p in (cfg.get("calendario_piattaforme") or camp.get("piattaforme") or [])
        ]
        # L'ultimo controllo degli invii Whop fatto da `esegui.py` prima di
        # pubblicare. Senza, una clip tenuta ferma perche' la campagna e' in
        # pausa si legge «in ritardo», cioe' come un guasto da cercare.
        invii = leggi(OUTPUT_DIR / camp["cartella"] / "invii.json", None)
        per_clip: dict[str, dict] = {}
        for c in camp["clip"]:
            v = per_clip.setdefault(
                c["id"],
                {
                    "campagna": camp["campagna"],
                    "cartella": camp["cartella"],
                    "id": c["id"],
                    "titolo": c["titolo"],
                    "quando": c.get("quando"),
                    "sospesa": c.get("sospesa"),
                    "piattaforme": {},
                    "whop": {},
                },
            )
            v["piattaforme"][c["piattaforma"]] = c["stato_pubblicazione"]
            # Lo stato Whop sta **accanto** alla piattaforma e non sulla clip:
            # l'invio e' per coppia (clip, piattaforma), quindi un post puo'
            # essere online e non ancora registrato mentre gli altri due lo sono.
            v["whop"][c["piattaforma"]] = c["stato_whop"]
        for v in per_clip.values():
            fatte = [p for p, s in v["piattaforme"].items() if s in ("pubblicato", "done")]
            mancanti = [
                p
                for p in (attese or v["piattaforme"])
                if v["piattaforme"].get(p) not in ("pubblicato", "done", "a mano", "sospesa")
            ]
            v["fatte"] = fatte
            v["mancanti"] = mancanti
            v["whop_fatte"] = [p for p, s in v["whop"].items() if s in WHOP_FATTO]
            v["whop_mancanti"] = [
                p for p in (attese or v["piattaforme"]) if v["whop"].get(p) not in WHOP_FATTO
            ]
            # Lo stato e' un giudizio su **due** cose insieme: la data e cosa e'
            # gia' uscito. «In ritardo» non vuol dire che qualcosa e' rotto —
            # il ritmo puo' averla legittimamente rimandata — ma e' l'unica riga
            # che merita di essere guardata, quindi va distinta da «in attesa».
            if mancanti and finita:
                v["stato"] = "campagna finita"
                v["finita"] = finita
            elif v["sospesa"]:
                v["stato"] = "sospesa"
            elif not v["quando"]:
                v["stato"] = "non programmata"
            elif not mancanti:
                v["stato"] = "pubblicata"
            elif datetime.fromisoformat(v["quando"]) <= adesso:
                v["stato"] = "in ritardo"
                v["invii"] = invii
                if (invii or {}).get("esito") in ("in pausa", "chiusi"):
                    v["stato"] = "ferma"
            else:
                v["stato"] = "in attesa"
            righe.append(v)
    # Le programmate in ordine di uscita, le altre in fondo: una clip senza data
    # non e' imminente, e mescolarla fra le date la farebbe sembrare tale.
    return sorted(
        righe, key=lambda r: (r["quando"] is None, r["quando"] or "", r["cartella"], r["id"])
    )


# ------------------------------------------------------- le sorgenti risolte

SORGENTI_DIR = RADAR_DIR / "sorgenti"
_CACHE_SORGENTI: dict[str, tuple[float, dict]] = {}


def _sorgenti_di(campagna_id: str | None) -> dict | None:
    """Il file di `radar/sorgenti.py` per una campagna, riletto solo se e' cambiato."""
    if not campagna_id:
        return None
    percorso = SORGENTI_DIR / f"{campagna_id}.json"
    try:
        mtime = percorso.stat().st_mtime
    except OSError:
        return None
    visto = _CACHE_SORGENTI.get(campagna_id)
    if visto and visto[0] == mtime:
        return visto[1]
    dati = leggi(percorso)
    if dati is not None:
        _CACHE_SORGENTI[campagna_id] = (mtime, dati)
    return dati


# Le parole con cui una riga del catalogo dice da dove viene il girato.
ETICHETTA_SORGENTE = {"automatico": "pronta", "con_scelta": "da scegliere", "a_mano": "a mano"}


def titolo_di_lavoro(campagna_id: str) -> str:
    """Il titolo scritto in `campagna.json`, per una campagna uscita dal catalogo; se no l'id."""
    for percorso in WORK_DIR.glob("*/campagna.json"):
        dati = leggi(percorso, {}) or {}
        if dati.get("id") == campagna_id and dati.get("titolo"):
            return dati["titolo"]
    return campagna_id


def lavorazione_di(campagna_id: str) -> list[dict]:
    """
    Le cartelle di lavoro di una campagna scelta che non ha ancora una consegna.

    `lavoro()` e il pannello contano le cartelle di `output/` con un
    `publish.json`, cioe' quelle gia' arrivate alla pubblicazione. In mezzo c'e'
    tutta la produzione: Northwind Podcast aveva 18 clip renderizzate e risultava «da
    preparare», perche' il `publish.json` lo scrive «Prepara la pubblicazione».
    L'aggancio e' `campagna.json`, che `run.py prepare --campagna` scrive.
    """
    trovate = []
    for percorso in sorted(WORK_DIR.glob("*/campagna.json")):
        if (leggi(percorso, {}) or {}).get("id") != campagna_id:
            continue
        slug = percorso.parent.name
        uscita = OUTPUT_DIR / slug
        trovate.append(
            {
                "slug": slug,
                "decisa": (percorso.parent / "decisions.json").exists(),
                "renderizzate": len(list(uscita.glob("*.mp4"))) if uscita.is_dir() else 0,
                "consegna": (uscita / "publish.json").exists(),
            }
        )
    return trovate


def facilita_sorgente(campagna_id: str) -> dict | None:
    """
    Quanto e' facile arrivare al girato di una campagna, secondo il risolutore.

    E' il campo `facilita_sorgente` della specifica (docs/risolutore-sorgenti.md,
    passo 4): «nei primi mesi conviene lavorare solo le campagne `automatico`».
    `None` quando il risolutore non l'ha ancora vista, che non e' «a mano».
    """
    dati = _sorgenti_di(campagna_id)
    if not dati:
        return None
    scelti = [c for c in dati.get("candidati") or [] if c.get("preselezionato")]
    primo = scelti[0] if scelti else None
    return {
        "esito": dati.get("esito"),
        "etichetta": ETICHETTA_SORGENTE.get(dati.get("esito"), dati.get("esito")),
        "motivo": dati.get("motivo") or "",
        "titolo": (primo or {}).get("titolo"),
        "durata_min": round(primo["durata_s"] / 60) if primo and primo.get("durata_s") else None,
        "tipo": (primo or {}).get("tipo"),
    }


def risolutore(campagna_id: str | None, quanti: int = 6) -> dict | None:
    """
    I candidati del risolutore per la campagna: prima i preselezionati, poi gli altri video
    misurati che non sono esempi. Servono a «Prepara» al posto del primo link trovato.
    """
    dati = _sorgenti_di(campagna_id)
    if not dati:
        return None
    candidati = [
        c
        for c in dati.get("candidati") or []
        if not c.get("forse_esempio") and (c.get("preselezionato") or c.get("durata_s"))
    ]
    candidati.sort(key=lambda c: (not c.get("preselezionato"), c.get("gia_lavorato", False)))
    return {
        "esito": dati.get("esito"),
        "motivo": dati.get("motivo") or "",
        "risolto_il": dati.get("risolto_il"),
        "candidati": [
            {
                k: c.get(k)
                for k in (
                    "url",
                    "titolo",
                    "tipo",
                    "durata_s",
                    "data",
                    "preselezionato",
                    "perche",
                    "gia_lavorato",
                )
            }
            for c in candidati[:quanti]
        ],
    }


# ------------------------------------------------------ gli invii a mano


def invii_a_mano(campagne: list[dict], ora: datetime | None = None) -> list[dict]:
    """
    Le pubblicazioni online che su Whop non risultano: la coda «da inviare a mano».

    La voce 5 della fase 1 (docs/roadmap.md): un invio che fallisce **non ferma
    la pubblicazione** — il post esce, l'esito resta scritto — ma finora l'unico
    segno era un avviso fra gli altri. Qui c'e' tutto quello che serve per farlo
    in trenta secondi: il post, il pannello del provider, e quanto manca alla
    fine della finestra. A campagna finita l'invio non paga piu': la riga resta,
    marcata, perche' sparire non e' lo stesso che essere a posto.
    """
    ora = ora or datetime.now()
    schede = {s["id"]: s for s in leggi(RADAR_DIR / "radar_dettagli.json", []) or []}
    righe = []
    for camp in campagne:
        uuid = camp.get("whop_campaign_uuid")
        if not uuid:
            continue
        finita = pagamenti_chiusi(camp, schede.get(uuid), bool(schede))
        for c in camp["clip"]:
            if (
                not c["url"]
                or c["stato_pubblicazione"] not in ("pubblicato", "done")
                or c["stato_whop"] in WHOP_FATTO
            ):
                continue
            uscita = _iso(c["pubblicato_il"])
            minuti = (ora - uscita).total_seconds() / 60 if uscita else None
            righe.append(
                {
                    "cartella": camp["cartella"],
                    "campagna": camp["campagna"],
                    "clip": c["id"],
                    "titolo": c["titolo"],
                    "piattaforma": c["piattaforma"],
                    "url": c["url"],
                    "pubblicato_il": c["pubblicato_il"],
                    "minuti": round(minuti) if minuti is not None else None,
                    "in_finestra": minuti is not None and minuti <= FINESTRA_WHOP_MINUTI,
                    "esito": c["stato_whop"],
                    "whop_app": camp.get("whop_app"),
                    "finita": finita,
                }
            )
    return sorted(righe, key=lambda r: (bool(r["finita"]), not r["in_finestra"], r["minuti"] or 0))


def segna_inviato_a_mano(cartella: str, clip: str, piattaforma: str, annulla: bool = False) -> dict:
    """
    Scrive `inviato a mano` sul record (clip, piattaforma) di `publish_state.json`.

    **E' la sola scrittura del pannello su uno stato di pubblicazione**, e passa
    da `orchestrator.scrivi_stato`: serratura, rilettura dal disco, il solo
    record proprio — tre processi di pubblicazione possono scrivere lo stesso
    file nello stesso momento. Rifiuta lo schema vecchio senza `items`, dove
    `scrivi_stato` aprirebbe un elenco nuovo e i record di prima sparirebbero
    dalla vista. `annulla` rimette `non confermato`: l'esito vecchio non si
    conosce piu', e quello e' il nome onesto di «non so se c'e'».
    """
    sys.path.insert(0, str(PUBLISH_DIR))
    import orchestrator

    uscita = (OUTPUT_DIR / cartella).resolve()
    if not cartella or not uscita.is_relative_to(OUTPUT_DIR.resolve()):
        raise ValueError("cartella non valida")
    file_stato = uscita / "publish_state.json"
    stato_ora = leggi(file_stato)
    if not isinstance(stato_ora, dict) or not isinstance(stato_ora.get("items"), dict):
        raise ValueError("publish_state.json assente o nello schema vecchio: si corregge a mano")
    rec = normalizza_stato(stato_ora).get((clip, piattaforma))
    if not rec or not rec.get("url"):
        raise ValueError(
            f"nessuna pubblicazione di {clip} su {piattaforma}: non c'e' niente da inviare"
        )
    adesso = datetime.now().isoformat(timespec="seconds")
    campi = (
        {"whop_status": "non confermato", "whop_mano_annullato_il": adesso}
        if annulla
        else {"whop_status": "inviato a mano", "whop_submitted_at": adesso, "whop_mano_il": adesso}
    )
    orchestrator.scrivi_stato(file_stato, clip, piattaforma, campi)
    return {"cartella": cartella, "clip": clip, "piattaforma": piattaforma, **campi}


# Il dominio che un link deve avere per essere un post di quella piattaforma.
# Non e' pignoleria: incollare il link di YouTube nella casella di TikTok
# scriverebbe «pubblicato» su una coppia che non lo e', e l'esecutore non la
# pubblicherebbe piu'.
DOMINI_POST = {
    "tiktok": ("tiktok.com",),
    "instagram": ("instagram.com",),
    "youtube": ("youtube.com", "youtu.be"),
}


def segna_pubblicato_a_mano(
    cartella: str, clip: str, piattaforma: str, url: str, whop_inviato: bool
) -> dict:
    """
    Registra una pubblicazione fatta a mano — dal telefono, fuori dall'esecutore.

    Prima non c'era modo di dirlo al pannello: la prova TikTok del 29 settembre
    2026 e' uscita dall'app e su Whop, e il calendario la dava ancora «a mano»,
    cioe' da fare. Da qui l'esecutore la considera gia' uscita (`gia_pubblicata`)
    e non la tocca piu', quindi si rifiuta una coppia gia' pubblicata e un link
    che non e' della piattaforma. Stessa strada di `segna_inviato_a_mano`:
    `orchestrator.scrivi_stato`, il solo record proprio, sotto serratura.
    """
    sys.path.insert(0, str(PUBLISH_DIR))
    import orchestrator
    from urllib.parse import urlparse

    uscita = (OUTPUT_DIR / cartella).resolve()
    if not cartella or not uscita.is_relative_to(OUTPUT_DIR.resolve()):
        raise ValueError("cartella non valida")
    if piattaforma not in DOMINI_POST:
        raise ValueError(f"piattaforma sconosciuta: {piattaforma}")
    url = (url or "").strip()
    host = (urlparse(url).hostname or "").lower()
    if not url.startswith("https://") or not any(
        host == d or host.endswith("." + d) for d in DOMINI_POST[piattaforma]
    ):
        raise ValueError(f"il link non e' un post di {piattaforma}: {url or '(vuoto)'}")
    cfg = leggi(uscita / "publish.json")
    if not isinstance(cfg, dict) or not any(
        str(it.get("id")) == clip for it in cfg.get("items") or []
    ):
        raise ValueError(f"la clip {clip} non e' in publish.json di {cartella}")
    file_stato = uscita / "publish_state.json"
    stato_ora = leggi(file_stato)
    if stato_ora is not None and not (
        isinstance(stato_ora, dict) and isinstance(stato_ora.get("items"), dict)
    ):
        raise ValueError("publish_state.json nello schema vecchio: si corregge a mano")
    gia = normalizza_stato(stato_ora or {}).get((clip, piattaforma)) or {}
    if gia.get("status") in ("pubblicato", "done"):
        raise ValueError(f"{clip} su {piattaforma} risulta gia' pubblicata: {gia.get('url')}")
    adesso = datetime.now().isoformat(timespec="seconds")
    campi = {
        "id": clip,
        "platform": piattaforma,
        "status": "pubblicato",
        "url": url,
        "published_at": adesso,
        "a_mano": True,
        "whop_status": "inviato a mano" if whop_inviato else "da inviare",
    }
    if whop_inviato:
        campi |= {"whop_submitted_at": adesso, "whop_mano_il": adesso}
    orchestrator.scrivi_stato(file_stato, clip, piattaforma, campi, da_capo=True)
    return {"cartella": cartella, "clip": clip, "piattaforma": piattaforma, **campi}


# ------------------------------------------------------------- i numeri


def _mediana(valori: list[float]) -> float | None:
    return statistics.median(valori) if valori else None


def numeri(campagne: list[dict]) -> dict:
    """
    Le views per piattaforma e per account, e quello che Whop dice degli invii.

    La voce 7 della fase 1: **views mediane per post, per piattaforma** — la
    misura del cancello del 26 ottobre — e i soldi incassati. La mediana e non
    la media, per la stessa ragione di `$/post` per nicchia: un post da 1.278
    views su nove ne fa sembrare buoni otto che non lo sono. Un post mai letto
    non entra nella mediana: «non misurato» non e' zero.
    """
    misure = misure_per_url()
    per_piatt: dict[str, list[int]] = {p: [] for p in PIATTAFORME}
    non_lette: dict[str, int] = dict.fromkeys(PIATTAFORME, 0)
    per_ident: dict[tuple[str, str], list[int]] = {}
    for camp in campagne:
        for c in camp["clip"]:
            if not c["url"] or c["stato_pubblicazione"] not in ("pubblicato", "done"):
                continue
            views = (misure.get(c["url"]) or {}).get("views")
            p = c["piattaforma"]
            if views is None:
                non_lette[p] = non_lette.get(p, 0) + 1
                continue
            per_piatt.setdefault(p, []).append(views)
            chi = c.get("identita") or f"{p} (laboratorio)"
            per_ident.setdefault((p, chi), []).append(views)
    piattaforme = [
        {
            "piattaforma": p,
            "post": len(v) + non_lette.get(p, 0),
            "misurati": len(v),
            "mediana": _mediana(v),
            "massimo": max(v) if v else None,
            "totale": sum(v),
        }
        for p, v in per_piatt.items()
    ]
    account = [
        {
            "piattaforma": p,
            "identita": chi,
            "post": len(v),
            "mediana": _mediana(v),
            "totale": sum(v),
            "massimo": max(v),
        }
        for (p, chi), v in sorted(per_ident.items())
    ]
    try:
        import metriche

        whop = metriche.ultimi_whop()
        andamento = metriche.andamento(30)
    except Exception:
        whop, andamento = [], {}
    try:
        import analitiche

        per_post = [analitiche.sintesi(r) for r in analitiche.ultime()]
    except Exception:
        per_post = []
    pagati = [w.get("pagato") for w in whop]
    return {
        "piattaforme": piattaforme,
        "account": account,
        "whop": whop,
        # `None` se anche un solo conto non si e' potuto leggere: un totale che
        # ne salta uno non e' un totale.
        "incassato": None if not whop or any(p is None for p in pagati) else sum(pagati),
        "andamento": andamento,
        # L'ultima lettura di ogni post (`dashboard/analitiche.py`), in cinque
        # numeri: il resto — curve, eta', views ora per ora — sta in `mostra`.
        "analitiche": per_post,
    }


# ---------------------------------------------------------- la roadmap

ROADMAP = ROOT / "docs" / "roadmap.md"
MESI_IT = {
    m: i
    for i, m in enumerate(
        "gennaio febbraio marzo aprile maggio giugno luglio agosto settembre ottobre "
        "novembre dicembre".split(),
        1,
    )
}


def _fine_mese(anno: int, mese: int) -> date:
    return date(anno + mese // 12, mese % 12 + 1, 1) - timedelta(days=1)


def periodo_fase(testo: str, anno: int) -> tuple[date | None, date | None]:
    """«28 settembre - 5 ottobre», «6 - 26 ottobre», «novembre», «da dicembre» -> date."""
    t = testo.strip().lower()
    m = re.fullmatch(r"(\d{1,2}) (\w+) - (\d{1,2}) (\w+)", t)
    if m and m.group(2) in MESI_IT and m.group(4) in MESI_IT:
        return (
            date(anno, MESI_IT[m.group(2)], int(m.group(1))),
            date(anno, MESI_IT[m.group(4)], int(m.group(3))),
        )
    m = re.fullmatch(r"(\d{1,2}) - (\d{1,2}) (\w+)", t)
    if m and m.group(3) in MESI_IT:
        mese = MESI_IT[m.group(3)]
        return date(anno, mese, int(m.group(1))), date(anno, mese, int(m.group(2)))
    m = re.fullmatch(r"(da )?(\w+)", t)
    if m and m.group(2) in MESI_IT:
        mese = MESI_IT[m.group(2)]
        return date(anno, mese, 1), None if m.group(1) else _fine_mese(anno, mese)
    return None, None


def _tabella(testo: str, titolo: str) -> list[list[str]]:
    """Le righe della prima tabella sotto `## <titolo>`, senza intestazione."""
    m = re.search(rf"^## {re.escape(titolo)}\s*$(.*?)(?=^## |\Z)", testo, re.M | re.S)
    if not m:
        return []
    righe = [r.strip() for r in m.group(1).splitlines() if r.strip().startswith("|")]
    celle = [[c.strip() for c in r.strip("|").split("|")] for r in righe]
    return [r for r in celle[2:] if any(r)]


def roadmap(testo: str | None = None, oggi: date | None = None) -> dict:
    """
    Fasi, scadenze e cancello, letti da docs/roadmap.md: il pannello non ne tiene una copia.

    Le fasi dai titoli (`## Fase N — nome (periodo)`), le voci della fase dalle
    righe numerate in grassetto — fatta quando porta «*Fatto il» — e scadenze e
    cancello dalle due tabelle omonime. Una roadmap cambiata si legge al
    caricamento dopo, senza toccare il codice.
    """
    if testo is None:
        try:
            testo = ROADMAP.read_text(encoding="utf-8")
        except OSError:
            return {"fasi": [], "corrente": None, "scadenze": [], "cancello": []}
    oggi = oggi or date.today()
    anno_m = re.search(r"\b(20\d\d)\b", testo)
    anno = int(anno_m.group(1)) if anno_m else oggi.year
    fasi = []
    titoli = list(re.finditer(r"^## Fase (\d+) — (.+?) \((.+?)\)\s*$", testo, re.M))
    ultimo_mese = 0
    for m in titoli:
        fine_blocco = re.search(r"^## ", testo[m.end() :], re.M)
        corpo = testo[m.end() : m.end() + fine_blocco.start()] if fine_blocco else testo[m.end() :]
        inizio, fine = periodo_fase(m.group(3), anno)
        if inizio and inizio.month < ultimo_mese:  # la roadmap scavalca l'anno
            inizio = inizio.replace(year=inizio.year + 1)
            fine = fine.replace(year=fine.year + 1) if fine else None
        ultimo_mese = inizio.month if inizio else ultimo_mese
        voci = []
        pezzi = re.split(r"^(?=\d+\. \*\*)", corpo, flags=re.M)
        for pezzo in pezzi:
            v = re.match(r"(\d+)\. \*\*(.+?)\*\*", pezzo)
            if v:
                voci.append(
                    {
                        "numero": int(v.group(1)),
                        "titolo": v.group(2).rstrip(":. "),
                        "fatto": "*Fatto il" in pezzo,
                    }
                )
        fasi.append(
            {
                "numero": int(m.group(1)),
                "nome": m.group(2),
                "periodo": m.group(3),
                "inizio": inizio.isoformat() if inizio else None,
                "fine": fine.isoformat() if fine else None,
                "voci": voci,
            }
        )
    corrente = None
    for f in fasi:
        if (
            f["inizio"]
            and f["inizio"] <= oggi.isoformat()
            and (not f["fine"] or oggi.isoformat() <= f["fine"])
        ):
            corrente = f["numero"]
            break
    scadenze = []
    for riga in _tabella(testo, "Scadenze"):
        try:
            giorno = date.fromisoformat(riga[0])
        except (ValueError, IndexError):
            continue
        scadenze.append(
            {
                "data": giorno.isoformat(),
                "cosa": riga[1] if len(riga) > 1 else "",
                "esito": riga[2] if len(riga) > 2 else "",
                "giorni": (giorno - oggi).days,
            }
        )
    cancello = []
    for riga in _tabella(testo, "Cancello"):
        if len(riga) < 3:
            continue
        try:
            soglia = float(riga[2].replace(",", "."))
        except ValueError:
            continue
        cancello.append({"condizione": riga[0], "chiave": riga[1].strip("`"), "soglia": soglia})
    data_cancello = next((s["data"] for s in scadenze if "cancello" in s["cosa"].lower()), None)
    return {
        "fasi": fasi,
        "corrente": corrente,
        "scadenze": scadenze,
        "cancello": cancello,
        "data_cancello": data_cancello,
    }


def stato_cancello(rm: dict, n: dict) -> list[dict]:
    """Ogni condizione del cancello col valore di oggi: raggiunta, no, o non si sa."""
    mediane = [p["mediana"] for p in n.get("piattaforme", []) if p.get("mediana") is not None]
    valori = {
        "views_mediane": max(mediane) if mediane else None,
        "incassato": n.get("incassato"),
    }
    fuori = []
    for c in rm.get("cancello", []):
        v = valori.get(c["chiave"])
        fuori.append({**c, "valore": v, "raggiunta": None if v is None else v >= c["soglia"]})
    return fuori


# ------------------------------------------------- avvisi e cose da fare

ZERO_VIEWS = re.compile(r"^(\w+): 0 views a (\d+) (giorni|ore) dall'uscita$")


def avvisi_raggruppati(trovati: list[dict]) -> list[dict]:
    """
    Gli avvisi, con gli «0 views» di una stessa campagna e piattaforma in una riga sola.

    Sei righe gialle in cima alla pagina per sei post TikTok a zero dicevano una
    cosa sola — «TikTok non distribuisce» — e spingevano sotto tutto il resto.
    """
    gruppi: dict[tuple[str, str], list[int]] = {}
    fuori = []
    for a in trovati:
        m = ZERO_VIEWS.match(a.get("messaggio") or "")
        if not m:
            fuori.append(a)
            continue
        giorni = int(m.group(2)) if m.group(3) == "giorni" else 1
        gruppi.setdefault((a.get("campagna") or "", m.group(1)), []).append(giorni)
    for (campagna, piattaforma), giorni in gruppi.items():
        quanti = len(giorni)
        fuori.insert(
            0,
            {
                "livello": "avviso",
                "campagna": campagna,
                "clip": None,
                "piattaforma": piattaforma,
                "messaggio": f"{piattaforma}: {quanti} post a 0 views"
                + (
                    f", da {min(giorni)}-{max(giorni)} giorni"
                    if min(giorni) != max(giorni)
                    else f", da {giorni[0]} giorni"
                ),
            },
        )
    return fuori


def da_fare(
    calendario_righe: list[dict],
    invii_mano: list[dict],
    trovati: list[dict],
    giro: dict,
    rm: dict,
    lavoro_righe: list[dict],
    ora: datetime | None = None,
) -> list[dict]:
    """
    Le cose che chiedono una persona, in ordine di urgenza: il cuore di «Oggi».

    Ogni voce ha un `livello` (`adesso`, `oggi`, `presto`) e la sezione del
    pannello dove si fa (`vai`). Non e' una seconda lista di avvisi: ci entra
    solo cio' che si risolve con un gesto, e un avviso che si guarda e basta
    resta fra i segnali.
    """
    ora = ora or datetime.now()
    voci: list[dict] = []
    aperti = [i for i in invii_mano if not i["finita"]]
    subito = [i for i in aperti if i["in_finestra"]]
    if subito:
        voci.append(
            {
                "livello": "adesso",
                "titolo": f"{len(subito)} invii Whop da fare a mano, finestra aperta",
                "dettaglio": "; ".join(
                    f"clip {i['clip']} su {i['piattaforma']}, "
                    f"{max(0, FINESTRA_WHOP_MINUTI - (i['minuti'] or 0))} min rimasti"
                    for i in subito[:3]
                ),
                "vai": "pubblicazione",
            }
        )
    tardi = [i for i in aperti if not i["in_finestra"]]
    if tardi:
        voci.append(
            {
                "livello": "oggi",
                "titolo": f"{len(tardi)} pubblicazioni non registrate su Whop",
                "dettaglio": "oltre la finestra di 30 minuti: prova l'invio dal pannello del "
                "provider, poi segnalo fatto",
                "vai": "pubblicazione",
            }
        )
    for sc in rm.get("scadenze", []):
        if sc["esito"]:
            continue
        if sc["giorni"] < 0:
            livello, quando = "adesso", f"scaduta da {-sc['giorni']} giorni"
        elif sc["giorni"] == 0:
            livello, quando = "oggi", "oggi"
        elif sc["giorni"] <= 3:
            livello, quando = (
                "presto",
                "domani" if sc["giorni"] == 1 else f"fra {sc['giorni']} giorni",
            )
        else:
            continue
        voci.append(
            {
                "livello": livello,
                "titolo": sc["cosa"],
                "dettaglio": f"roadmap · {quando}",
                "vai": "oggi",
            }
        )
    if giro.get("girato_il"):
        eta = ora - (_iso(giro["girato_il"]) or ora)
        if eta.days >= 2:
            voci.append(
                {
                    "livello": "oggi",
                    "titolo": "Il radar ha " + f"{eta.days} giorni",
                    "dettaglio": "il catalogo delle campagne e' vecchio quanto l'ultimo «Aggiorna radar»",
                    "vai": "aggiorna-radar",
                }
            )
    ritardo = [r for r in calendario_righe if r.get("stato") == "in ritardo"]
    if ritardo:
        voci.append(
            {
                "livello": "oggi",
                "titolo": f"{len(ritardo)} clip in ritardo sul calendario",
                "dettaglio": "il momento è passato e non sono uscite dappertutto",
                "vai": "pubblicazione",
            }
        )
    senza = {}
    for r in calendario_righe:
        if not r.get("quando") and r.get("stato") not in ("pubblicata", "campagna finita"):
            senza[r.get("campagna")] = senza.get(r.get("campagna"), 0) + 1
    for campagna, quante in senza.items():
        voci.append(
            {
                "livello": "presto",
                "titolo": f"{quante} clip pronte senza calendario",
                "dettaglio": f"{campagna}: «Prepara la pubblicazione» scrive didascalie e date",
                "vai": "produzione",
            }
        )
    # Una campagna con una cartella di lavoro e' gia' preparata anche senza un
    # `publish.json`: Northwind Podcast era renderizzata e risultava «da preparare»,
    # perche' `lavoro()` conta solo le consegne.
    preparate = {(leggi(p, {}) or {}).get("id") for p in WORK_DIR.glob("*/campagna.json")}
    for r in lavoro_righe:
        if (
            r.get("stato") == "da-lanciare"
            and not r.get("video")
            and r.get("campagna_id") not in preparate
        ):
            voci.append(
                {
                    "livello": "presto",
                    "titolo": "Campagna scelta da preparare",
                    "dettaglio": r.get("titolo") or r.get("campagna_id"),
                    "vai": "produzione",
                }
            )
    for a in trovati:
        if a.get("livello") == "errore":
            voci.append(
                {
                    "livello": "oggi",
                    "titolo": a["messaggio"],
                    "dettaglio": " / ".join(x for x in (a.get("campagna"), a.get("clip")) if x),
                    "vai": "pubblicazione",
                }
            )
    ordine = {"adesso": 0, "oggi": 1, "presto": 2}
    return sorted(voci, key=lambda v: ordine[v["livello"]])


def freschezza(giro: dict) -> dict:
    """Quando e' stato letto ogni dato: il radar, le views, le analitiche per post, Whop. L'eta' di cio' che si guarda."""
    try:
        import metriche

        misure = [r["quando"] for r in metriche.ultime() if r.get("quando")]
        whop = [w["quando"] for w in metriche.ultimi_whop() if w.get("quando")]
        con = metriche.db()
        analisi = con.execute("SELECT MAX(quando) FROM analitiche").fetchone()[0]
        con.close()
    except Exception:
        misure, whop, analisi = [], [], None
    return {
        "radar": giro.get("girato_il"),
        "views": max(misure) if misure else None,
        "analitiche": analisi,
        "whop": max(whop) if whop else None,
    }


def snapshot() -> dict:
    campagne = campagne_output()
    invii = invii_whop()
    cal = calendario()
    trovati = avvisi(campagne, invii)
    lav = lavoro()
    giro = leggi(RADAR_DIR / "ultimo_giro.json", {})
    mano = invii_a_mano(campagne)
    rm = roadmap()
    num = numeri(campagne)
    return {
        "generato_il": datetime.now().isoformat(timespec="seconds"),
        "campagne": campagne,
        "calendario": cal,
        "avvisi": trovati,
        "avvisi_raggruppati": avvisi_raggruppati(trovati),
        "ritmo": ritmo_identita(),
        "gruppi": gruppi(),
        "lavoro": lav,
        "ultimo_giro": giro,
        "clip": clip_renderizzate(),
        "pannello": pannello(),
        "sparite": sparite(),
        "invii_a_mano": mano,
        "numeri": num,
        "roadmap": {**rm, "stato_cancello": stato_cancello(rm, num)},
        "da_fare": da_fare(cal, mano, trovati, giro, rm, lav),
        "freschezza": freschezza(giro),
    }
    # `campagne_radar`, `candidate` e `nicchie` non stanno piu' qui dentro.
    # Erano tre viste delle stesse 627 campagne — 2,1 MB su 2,5 di risposta —
    # e servivano alle schede «Radar» e «Catalogo», che adesso vivono dentro la
    # nicchia. Le funzioni restano: sono la fonte di `pannello()` e le serve
    # `main()` a riga di comando. Quello che se n'e' andato e' la **terza
    # copia**, non il dato.


def main() -> None:
    ap = argparse.ArgumentParser(description="Snapshot dello stato dell'operazione")
    ap.add_argument(
        "--sezione",
        default=None,
        choices=["oggi", "campagne", "clip", "lancio", "lavoro", "nicchie"],
    )
    args = ap.parse_args()


    # Le tre sezioni pesanti si calcolano **solo se chieste**: non stanno piu'
    # nello snapshot, che la pagina chiede a ogni giro.
    if args.sezione == "campagne":
        json.dump({"campagne_radar": campagne_radar()}, sys.stdout, ensure_ascii=False, indent=2)
        return
    if args.sezione == "nicchie":
        json.dump(
            {"nicchie": tassonomia.quadro(RADAR_DIR / "radar_dettagli.json")},
            sys.stdout,
            ensure_ascii=False,
            indent=2,
        )
        return
    if args.sezione == "lancio":
        json.dump(
            {"candidate": candidate(), "gruppi": gruppi()}, sys.stdout, ensure_ascii=False, indent=2
        )
        return

    s = snapshot()
    if args.sezione == "oggi":
        out = {"avvisi": s["avvisi"], "ritmo": s["ritmo"], "campagne": s["campagne"]}
    elif args.sezione == "clip":
        out = {"clip": s["clip"]}
    elif args.sezione == "lavoro":
        out = {"lavoro": s["lavoro"], "ultimo_giro": s["ultimo_giro"]}
    else:
        out = s
    json.dump(out, sys.stdout, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    for _f in (sys.stdout, sys.stderr):
        try:
            _f.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    main()
