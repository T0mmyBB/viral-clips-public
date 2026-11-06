#!/usr/bin/env python
"""
instagram_api.py — pubblica un Reel dall'API ufficiale di Meta, e ne legge i numeri. Senza browser.

    python instagram_api.py chi --gruppo business       # di chi e' il token, quota, scadenza
    python instagram_api.py incolla --gruppo business   # token nuovo, senza eco, provato prima di scriverlo
    python instagram_api.py rinnova --gruppo business   # rinnova il token adesso
    python instagram_api.py numeri --gruppo business    # i numeri grezzi di ogni reel
    python instagram_api.py stato --gruppo business --contenitore <id>
    python instagram_api.py pubblica --gruppo business --file clip.mp4 \
        --testo-da didascalia.txt [--davvero]

Perche' esiste
--------------
Dal browser Instagram si pubblica, ma a caro prezzo: il compositore parte su
1:1, i popup coprono i pulsanti, l'indirizzo del reel va cercato sul profilo
(`errore_url`), e i numeri per reel sul web non esistono. L'API toglie i
quattro: tre richieste, l'indirizzo arriva con la risposta, e per ogni reel ci
sono portata, tempo medio di visione, salvataggi, condivisioni e la quota di
chi scorre via nei primi tre secondi.

**Non alza le views.** I reel dei primi giorni si fermano a 40-170 perche'
l'account e' nuovo, non per come si carica (PROGRESS.md, 2 ottobre 2026). Se un
reel uscito da qui renda piu' o meno di uno dal browser non lo sa nessuno
finche' non lo misuriamo: per questo ogni record porta `via: api`.

Chi puo' usarla
---------------
Letto sulla documentazione il 2 ottobre 2026, e qui e' l'opposto di TikTok:
«If your app only serves your Instagram professional account or an account you
manage, Standard Access is all your app needs». Niente App Review, niente
verifica aziendale, app in modalita' sviluppo. Serve un account professionale
(Creator o Business) con un ruolo nell'app.

Il video non si carica: lo si lascia prendere
---------------------------------------------
Con Instagram Login l'API vuole un indirizzo pubblico e va a prendersi il file
(il caricamento diretto esiste solo con Facebook Login for Business). E' lo
stesso tunnel di Buffer, `ospita.py`: quel file solo, per i minuti che servono.

Il contenitore e' la ricevuta
-----------------------------
Meta lavora in due tempi: si crea un **contenitore** (scarica e controlla il
video), poi lo si pubblica. Un contenitore si pubblica una volta sola, e dice
sempre a che punto e': quindi dopo un giro finito male non si ricomincia, si
**chiede al contenitore** — lo stesso principio del post «in corso» di Buffer.
Senza `--davvero` ci si ferma a contenitore pronto: Meta ha scaricato e
accettato il video, niente e' uscito, e il contenitore scade da solo in 24 ore.

Il token
--------
Sta in `publish/.meta/<gruppo>.txt`, una riga, incollato dal pannello di Meta
(Instagram > API setup with Instagram login > Generate token). La cartella
comincia col punto: `.gitignore` ed `export_skill.py` la saltano, come la
chiave di Buffer. Dura 60 giorni e si rinnova da solo a ogni uso, dopo una
settimana dall'ultimo rinnovo: lo stato sta accanto, in `<gruppo>.json`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

import notifica
import ospita
import registro
import serratura

HERE = Path(__file__).resolve().parent
HOST = "https://graph.instagram.com"
# La versione degli esempi della documentazione il 2 ottobre 2026. Una versione
# che Meta ha ritirato risponde con un errore che lo dice: non ripiega.
VERSIONE = "v25.0"
CARTELLA = HERE / ".meta"

# I limiti che Meta dichiara per un Reel (reference IG User Media).
MAX_CARATTERI = 2200
MAX_HASHTAG = 30
MAX_MENZIONI = 20
MAX_BYTE = 300 * 1024 * 1024

# Gli stati di un contenitore. **Solo quelli elencati qui valgono quello che
# dicono**: un valore mai visto e' «in corso» finche' il tempo non scade — la
# stessa regola di `intake: open` su Whop e di `sent` su Buffer.
PRONTO = {"FINISHED"}
USCITO = {"PUBLISHED"}
FALLITO = {"ERROR", "EXPIRED"}

# Dopo quanto si richiede lo stato del contenitore, in secondi. Meta consiglia
# una volta al minuto per cinque minuti, ma il file sale dalla linea di casa:
# 25 MB in 188 s al collaudo di Buffer, nove minuti per la clip piu' pesante
# consegnata finora. Chiudere il tunnel a meta' scaricamento e' un contenitore
# fallito che sembra colpa di Meta. Qui il tempo non corre contro Whop: la
# mezz'ora parte quando il reel esce, e il reel esce dopo.
ATTESE = (20, 20, 30, 30, 45, 60, 60, 90, 90, 120, 120, 180, 180, 180, 180)
# Dopo quanto si richiede l'indirizzo a un reel appena uscito.
ATTESE_LINK = (0, 5, 15)

# Quello che si chiede per ogni reel. Se Meta ne rifiuta una rifiuta tutta la
# richiesta, e quattro sono segnate «in development»: per questo `insight`
# sa ripiegare su una richiesta per metrica.
#
# `reposts` sta nella documentazione ma non qui: alla prima lettura vera, il 3
# ottobre 2026, Meta l'ha rifiutata su tutti i dodici reel («does not support
# the metric»), e ogni reel costava dodici richieste invece di una.
METRICHE = (
    "views",
    "reach",
    "ig_reels_avg_watch_time",
    "ig_reels_video_view_total_time",
    "reels_skip_rate",
    "likes",
    "comments",
    "saved",
    "shares",
    "total_interactions",
)

# Il token dura 60 giorni e si puo' rinnovare da quando ha 24 ore. Una volta a
# settimana lascia sette settimane di margine a un rinnovo che non riesce.
DURATA_TOKEN = timedelta(days=60)
RINNOVA_OGNI = timedelta(days=7)
# Un rinnovo cosi' recente l'ha fatto un altro processo mentre si aspettava la
# serratura: non se ne fa un secondo.
APPENA_FATTO = timedelta(minutes=10)
# Sotto questi giorni un rinnovo fallito diventa una notifica: da li' in poi
# chi deve rigenerare il token ha bisogno di saperlo.
AVVISA_SOTTO = 14

_HASHTAG = re.compile(r"(?<!\w)#\w+")
_MENZIONE = re.compile(r"(?<!\w)@[\w.]+")
_REEL = re.compile(r"^https://(?:www\.)?instagram\.com/(?:reel|reels|p)/([\w-]+)/?(?:\?.*)?$")
_CODICE = re.compile(r"^[\w-]+$")


class ErroreMeta(RuntimeError):
    """
    Meta ha risposto di no, o non ha risposto.

    `incerto` distingue i due casi dove conta: una richiesta che non ha avuto
    risposta **puo' essere arrivata**. Su una lettura non cambia niente, su
    `media_publish` vuol dire non sapere se il reel e' online.
    """

    def __init__(self, messaggio: str, codice=None, sottocodice=None, incerto: bool = False):
        super().__init__(messaggio)
        self.codice = codice
        self.sottocodice = sottocodice
        self.incerto = incerto


def log(msg: str) -> None:
    riga = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(riga, file=sys.stderr, flush=True)
    cartella = HERE / "logs"
    cartella.mkdir(exist_ok=True)
    with (cartella / "instagram_api.log").open("a", encoding="utf-8") as fh:
        fh.write(riga + "\n")


# ------------------------------------------------------------ le regole pure


def difetti_testo(testo: str) -> list[str]:
    """
    Perche' Meta rifiuterebbe questa didascalia, o lista vuota.

    Si controlla qui e non si lascia dire a Meta: il suo rifiuto arriva dopo
    che il tunnel e' aperto e il file e' esposto.
    """
    fuori = []
    if not (testo or "").strip():
        fuori.append("didascalia vuota")
    if len(testo or "") > MAX_CARATTERI:
        fuori.append(f"{len(testo)} caratteri, il tetto e' {MAX_CARATTERI}")
    hashtag = len(_HASHTAG.findall(testo or ""))
    if hashtag > MAX_HASHTAG:
        fuori.append(f"{hashtag} hashtag, il tetto e' {MAX_HASHTAG}")
    menzioni = len(_MENZIONE.findall(testo or ""))
    if menzioni > MAX_MENZIONI:
        fuori.append(f"{menzioni} menzioni, il tetto e' {MAX_MENZIONI}")
    return fuori


def chi_e(risposta: dict | None) -> dict:
    """
    L'account di un token, dalla risposta di `/me`: `{user_id, username, tipo}`.

    La documentazione mostra la risposta dentro un elenco `data`, gli altri
    nodi della stessa API rispondono piatti: si leggono tutte e due le forme
    invece di scommettere su una.
    """
    risposta = risposta or {}
    dati = risposta.get("data")
    voce = dati[0] if isinstance(dati, list) and dati and isinstance(dati[0], dict) else risposta
    user_id = voce.get("user_id")
    return {
        "user_id": str(user_id) if user_id else None,
        "username": voce.get("username") or None,
        "tipo": voce.get("account_type") or None,
    }


def account_atteso(conto: dict, handle: str | None) -> dict:
    """
    L'account del token, se e' quello del registro; altrimenti `AccountSbagliato`.

    E' la quinta porta da cui una clip puo' uscire, e la regola e' la stessa
    delle altre quattro: prima di pubblicare si confronta chi e' collegato con
    chi ci si aspetta. Qui «chi e' collegato» lo dice il token, e il modo di
    sbagliare e' uno: nel file del gruppo c'e' il token di un altro account.
    """
    registro.controlla_handle(conto.get("username"), handle, "instagram (API)")
    return conto


def esito_contenitore(risposta: dict | None) -> tuple[str, str | None]:
    """
    Cosa dice Meta di un contenitore: (`pronto` | `uscito` | `fallito` | `in corso`, errore).

    `pronto` non e' `uscito`: il video e' stato accettato e niente e' online.
    `uscito` vuol dire che quel contenitore e' gia' stato pubblicato — e un
    contenitore si pubblica una volta sola.
    """
    risposta = risposta or {}
    codice = str(risposta.get("status_code") or "").upper()
    if codice in FALLITO:
        return "fallito", str(risposta.get("status") or codice)
    if codice in USCITO:
        return "uscito", None
    if codice in PRONTO:
        return "pronto", None
    return "in corso", None


def link_reel(permalink: str | None, shortcode: str | None = None) -> str | None:
    """
    L'indirizzo di **quel reel**, nella forma che lo stato usa gia'; altrimenti None.

    A Whop va l'indirizzo del reel: un profilo o una pagina qualunque verrebbero
    respinti dopo, a finestra di trenta minuti gia' aperta. Il codice si prende
    dal permalink, e dallo `shortcode` solo se il permalink manca.
    """
    m = _REEL.match(permalink or "")
    codice = m.group(1) if m else None
    if not codice and not permalink and shortcode and _CODICE.match(shortcode):
        codice = shortcode
    return f"https://www.instagram.com/reel/{codice}/" if codice else None


def nato_il(media: dict) -> datetime | None:
    """L'istante in cui un media e' stato creato (`2026-10-02T13:05:00+0000`), o None."""
    try:
        return datetime.strptime(str(media.get("timestamp")), "%Y-%m-%dT%H:%M:%S%z")
    except ValueError:
        return None


def nuovo_reel(media: list[dict], da: datetime, noti: set[str]) -> dict | None:
    """
    Il reel appena uscito, fra quelli che l'account elenca.

    Serve in un caso solo: il contenitore risulta pubblicato ma la risposta
    con l'id del reel non e' mai arrivata. «Quello nuovo» e' quello nato dopo
    `da` che lo stato non conosce ancora.

    **Se sono due non si sceglie**: mandare a Whop il link di un'altra clip e'
    peggio che non mandarne nessuno, perche' risulta inviato.
    """
    soglia = da if da.tzinfo else da.astimezone()
    nuovi = []
    for m in media or []:
        codice = m.get("shortcode")
        nato = nato_il(m)
        if codice and codice not in noti and nato and nato >= soglia:
            nuovi.append(m)
    return nuovi[0] if len(nuovi) == 1 else None


def valori_insight(risposta: dict | None) -> dict:
    """
    Le metriche di una risposta `insights`, nome -> valore grezzo.

    Una metrica che Meta non ha non compare, e resta fuori anche da qui: «If
    insights data you are requesting does not exist or is currently
    unavailable, the API returns an empty data set instead of 0».
    """
    fuori = {}
    for voce in (risposta or {}).get("data") or []:
        nome = voce.get("name")
        valori = voce.get("values") or []
        valore = valori[0].get("value") if valori and isinstance(valori[0], dict) else None
        if valore is None:
            valore = (voce.get("total_value") or {}).get("value")
        if nome and valore is not None:
            fuori[nome] = valore
    return fuori


def impronta(tok: str) -> str:
    """Dodici caratteri per riconoscere un token senza scriverlo da nessuna parte."""
    return hashlib.sha256(tok.encode("utf-8")).hexdigest()[:12]


def _data(testo) -> datetime | None:
    try:
        return datetime.fromisoformat(testo) if testo else None
    except (TypeError, ValueError):
        return None


def da_rinnovare(stato: dict, adesso: datetime) -> bool:
    """
    Se il token va rinnovato adesso.

    Si conta dall'ultimo rinnovo, o da quando il token e' stato visto la prima
    volta: l'eta' vera di un token incollato a mano non la sa nessuno, e
    contarla da quando lo vediamo sbaglia sempre dalla parte prudente — per
    Meta e' piu' vecchio di cosi', mai piu' giovane delle 24 ore che chiede.
    """
    riferimento = _data(stato.get("rinnovato_il")) or _data(stato.get("visto_il"))
    return bool(riferimento) and adesso - riferimento >= RINNOVA_OGNI


def giorni_rimasti(stato: dict, adesso: datetime) -> int | None:
    """
    Quanti giorni mancano alla scadenza del token, o None se non si sa niente.

    Dopo un rinnovo la scadenza l'ha detta Meta. Prima e' **al piu' tardi**
    sessanta giorni da quando il token e' stato visto: puo' essere prima.
    """
    scade = _data(stato.get("scade_il"))
    if not scade:
        visto = _data(stato.get("visto_il"))
        scade = visto + DURATA_TOKEN if visto else None
    return (scade - adesso).days if scade else None


# ------------------------------------------------------------------- il token


def file_token(gruppo: str) -> Path:
    return CARTELLA / f"{gruppo}.txt"


def file_stato(gruppo: str) -> Path:
    return CARTELLA / f"{gruppo}.json"


def ha_token(gruppo: str | None) -> bool:
    try:
        return bool(gruppo) and bool(file_token(gruppo).read_text(encoding="utf-8-sig").strip())
    except OSError:
        return False


def token(gruppo: str | None) -> str:
    if not gruppo:
        raise ErroreMeta("senza gruppo non si sa di quale account sia il token")
    try:
        valore = file_token(gruppo).read_text(encoding="utf-8-sig").strip()
    except OSError:
        valore = ""
    if not valore:
        raise ErroreMeta(
            f"manca il token di Instagram in {file_token(gruppo)}.\n"
            "  Si genera nel pannello dell'app Meta (Instagram > API setup with Instagram "
            "login > Generate token) e si incolla in quel file, una riga sola."
        )
    return valore


def stato_token(gruppo: str) -> dict:
    try:
        return json.loads(file_stato(gruppo).read_text(encoding="utf-8")) or {}
    except (OSError, ValueError):
        return {}


def _scrivi_stato_token(gruppo: str, stato: dict) -> None:
    CARTELLA.mkdir(exist_ok=True)
    serratura.scrivi_atomico(file_stato(gruppo), json.dumps(stato, ensure_ascii=False, indent=2))


def rinnova(gruppo: str, adesso: datetime | None = None) -> str:
    """
    Rinnova il token e lo riscrive. Torna quello nuovo.

    Sotto serratura e rileggendo dal disco: una pubblicazione e una lettura dei
    numeri possono arrivare qui insieme, e chi arriva secondo trova il lavoro
    gia' fatto invece di rinnovare un token che non e' piu' quello nel file.
    """
    adesso = adesso or datetime.now()
    with serratura.presa(f"meta-{gruppo}", attesa=60.0, scaduta=180.0):
        tok = token(gruppo)
        stato = stato_token(gruppo)
        fatto = _data(stato.get("rinnovato_il"))
        if stato.get("impronta") == impronta(tok) and fatto and adesso - fatto < APPENA_FATTO:
            return tok
        risposta = chiedi(
            "GET", "refresh_access_token", tok, {"grant_type": "ig_refresh_token"}, versione=False
        )
        nuovo = risposta.get("access_token")
        if not nuovo:
            raise ErroreMeta(f"Meta non ha dato un token nuovo: {sorted(risposta)}")
        durata = risposta.get("expires_in")
        serratura.scrivi_atomico(file_token(gruppo), nuovo + "\n")
        _scrivi_stato_token(
            gruppo,
            {
                "impronta": impronta(nuovo),
                "visto_il": stato.get("visto_il") or adesso.isoformat(timespec="seconds"),
                "rinnovato_il": adesso.isoformat(timespec="seconds"),
                "scade_il": (
                    (adesso + timedelta(seconds=int(durata))).isoformat(timespec="seconds")
                    if durata
                    else None
                ),
            },
        )
        log(f"token di {gruppo} rinnovato" + (f": vale {int(durata) // 86400} giorni" if durata else ""))
        return nuovo


def token_fresco(gruppo: str | None, adesso: datetime | None = None) -> str:
    """
    Il token del gruppo, rinnovato se e' ora.

    **Un rinnovo che non riesce non ferma chi chiama**: il token di prima vale
    ancora, e se non vale lo dira' Meta alla prima richiesta. Ma sotto le due
    settimane dalla scadenza lo si dice con una notifica, perche' da li' in poi
    serve una persona.
    """
    tok = token(gruppo)
    adesso = adesso or datetime.now()
    stato = stato_token(gruppo)
    if stato.get("impronta") != impronta(tok):
        # Un token mai visto: incollato adesso, o cambiato a mano.
        _scrivi_stato_token(
            gruppo,
            {
                "impronta": impronta(tok),
                "visto_il": adesso.isoformat(timespec="seconds"),
                "rinnovato_il": None,
                "scade_il": None,
            },
        )
        return tok
    if not da_rinnovare(stato, adesso):
        return tok
    try:
        return rinnova(gruppo, adesso)
    except (ErroreMeta, serratura.Occupata) as exc:
        giorni = giorni_rimasti(stato, adesso)
        log(f"token di {gruppo} non rinnovato ({exc}); giorni alla scadenza: {giorni}")
        if giorni is not None and giorni <= AVVISA_SOTTO:
            notifica.avvisa(
                f"Token Instagram di {gruppo}: scade fra {max(giorni, 0)} giorni",
                "Il rinnovo automatico non riesce. Va rigenerato dal pannello dell'app "
                f"Meta e incollato in publish/.meta/{gruppo}.txt.",
            )
        return tok


# ------------------------------------------------------------------ la rete


def chiedi(
    metodo: str,
    percorso: str,
    tok: str,
    dati: dict | None = None,
    versione: bool = True,
    attesa: float = 30,
) -> dict:
    """
    Una richiesta all'API. Torna il JSON, o alza `ErroreMeta` con il motivo.

    Il token viaggia come lo mostra la documentazione: parametro nelle GET,
    intestazione nelle POST. **Non entra mai in un messaggio**: gli errori di
    rete portano l'indirizzo, e l'indirizzo di una GET porta il token.
    """
    url = f"{HOST}/{VERSIONE + '/' if versione else ''}{percorso.lstrip('/')}"
    if metodo == "GET":
        url += "?" + urllib.parse.urlencode({**(dati or {}), "access_token": tok})
        richiesta = urllib.request.Request(url)
    else:
        richiesta = urllib.request.Request(
            url,
            data=json.dumps(dati or {}).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {tok}"},
            method=metodo,
        )
    try:
        with urllib.request.urlopen(richiesta, timeout=attesa) as r:
            return json.loads(r.read().decode("utf-8")) or {}
    except urllib.error.HTTPError as exc:
        testo = exc.read().decode("utf-8", "replace")
        try:
            errore = (json.loads(testo) or {}).get("error") or {}
        except ValueError:
            errore = {}
        codice, sotto = errore.get("code"), errore.get("error_subcode")
        messaggio = errore.get("message") or testo[:300]
        if codice == 190:
            messaggio += (
                " — il token non vale piu': va rigenerato nel pannello dell'app Meta "
                "(Instagram > API setup with Instagram login > Generate token)"
            )
        dove = f"{codice}" + (f"/{sotto}" if sotto else "")
        raise ErroreMeta(
            f"Meta ha risposto {exc.code} ({dove}): {messaggio}".replace(tok, "<token>"),
            codice=codice,
            sottocodice=sotto,
            # Un 5xx puo' arrivare dopo che la richiesta ha fatto effetto.
            incerto=exc.code >= 500,
        ) from None
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise ErroreMeta(
            f"Meta non risponde: {exc}".replace(tok, "<token>"), incerto=True
        ) from None


def account(tok: str) -> dict:
    # Solo i due campi dell'esempio della documentazione: da questa risposta
    # dipende la guardia, e un campo in piu' che Meta non conosce la farebbe
    # fallire tutta. Il tipo di account lo chiede `chi`, a parte.
    return chi_e(chiedi("GET", "me", tok, {"fields": "user_id,username"}))


def _nodo(conto: dict) -> str:
    # `user_id` e' l'id che la documentazione chiama <IG_ID>. Se `/me` non lo
    # da', `me` e' lo stesso nodo detto in un altro modo: non si prende un
    # altro campo sperando che sia quello.
    return conto.get("user_id") or "me"


def quota(conto: dict, tok: str) -> dict:
    """Quanti post dall'API nelle ultime 24 ore, e il tetto. Grezzo: serve solo a `chi`."""
    return chiedi(
        "GET", f"{_nodo(conto)}/content_publishing_limit", tok, {"fields": "quota_usage,config"}
    )


def elenco_media(conto: dict, tok: str, quanti: int = 50) -> list[dict]:
    """
    I media dell'account, dal piu' recente: la **prima pagina** e basta.

    Come le altre due piattaforme (`leggi_account.py`): un post piu' vecchio
    resta con la sua ultima misura, e non diventa zero.
    """
    risposta = chiedi(
        "GET",
        f"{_nodo(conto)}/media",
        tok,
        {
            "fields": "id,shortcode,permalink,timestamp,media_type,like_count,comments_count",
            "limit": quanti,
        },
    )
    return [m for m in risposta.get("data") or [] if isinstance(m, dict) and m.get("id")]


def leggi_media(media_id: str, tok: str) -> dict:
    return chiedi("GET", str(media_id), tok, {"fields": "id,shortcode,permalink,timestamp"})


def crea_contenitore(conto: dict, tok: str, url_video: str, testo: str) -> str:
    risposta = chiedi(
        "POST",
        f"{_nodo(conto)}/media",
        tok,
        {"media_type": "REELS", "video_url": url_video, "caption": testo, "share_to_feed": True},
    )
    if not risposta.get("id"):
        raise ErroreMeta(f"Meta non ha creato il contenitore: {risposta}")
    return str(risposta["id"])


def leggi_contenitore(contenitore: str, tok: str) -> dict:
    return chiedi("GET", str(contenitore), tok, {"fields": "status_code,status"})


def pubblica_contenitore(conto: dict, tok: str, contenitore: str) -> str:
    # Novanta secondi: e' la richiesta che non si puo' ripetere alla cieca.
    risposta = chiedi(
        "POST", f"{_nodo(conto)}/media_publish", tok, {"creation_id": contenitore}, attesa=90
    )
    if not risposta.get("id"):
        raise ErroreMeta(f"Meta non dice l'id del reel: {risposta}", incerto=True)
    return str(risposta["id"])


def insight(media_id: str, tok: str) -> tuple[dict, dict]:
    """
    I numeri di un reel: (metrica -> valore grezzo, metrica -> perche' Meta l'ha rifiutata).

    Prima tutte insieme. Se Meta rifiuta la richiesta — basta una metrica che
    quel reel non ha — si chiedono una per una: cosi' una metrica nuova o
    ritirata toglie se stessa e non le altre dieci. Un token scaduto e una rete
    che non risponde invece non si aggirano: sono guasti di tutta la lettura.
    """
    try:
        risposta = chiedi("GET", f"{media_id}/insights", tok, {"metric": ",".join(METRICHE)})
        return valori_insight(risposta), {}
    except ErroreMeta as exc:
        if exc.incerto or exc.codice == 190:
            raise
    valori, rifiutate = {}, {}
    for metrica in METRICHE:
        try:
            valori.update(valori_insight(chiedi("GET", f"{media_id}/insights", tok, {"metric": metrica})))
        except ErroreMeta as exc:
            if exc.incerto or exc.codice == 190:
                raise
            rifiutate[metrica] = str(exc)
    return valori, rifiutate


def reel_con_numeri(gruppo: str, solo: set[str] | None = None) -> list[dict]:
    """
    Ogni reel della prima pagina con i suoi numeri grezzi, per `leggi_analitiche.py`.

    Una riga per reel: `{id, url, media, valori, rifiutate}`, dove `id` e' il
    codice che sta nell'indirizzo — e' cosi' che un post letto trova la sua
    clip. Leggere l'account sbagliato non sbaglia nessuna clip, ma le fa
    risultare «non lette» senza dire perche': la guardia vale anche qui.
    """
    tok = token_fresco(gruppo)
    conto = account_atteso(account(tok), registro.handle(gruppo, "instagram"))
    righe = []
    for m in elenco_media(conto, tok):
        url = link_reel(m.get("permalink"), m.get("shortcode"))
        # Foto e caroselli non hanno le metriche dei reel: non sono nostri post.
        if not url or m.get("media_type") in ("IMAGE", "CAROUSEL_ALBUM"):
            continue
        codice = _REEL.match(url)[1]
        if solo and codice not in solo:
            continue
        valori, rifiutate = insight(m["id"], tok)
        righe.append({"id": codice, "url": url, "media": m, "valori": valori, "rifiutate": rifiutate})
    return righe


# ------------------------------------------------------------ la pubblicazione


def _fuori(contenitore, esito, url=None, errore=None, media=None) -> dict:
    return {"contenitore": contenitore, "media": media, "esito": esito, "url": url, "errore": errore}


def segui_contenitore(
    contenitore: str, tok: str, attese=ATTESE, dormi=time.sleep
) -> tuple[str, str | None]:
    """
    Aspetta che Meta abbia scaricato e controllato il video.

    Se il tempo scade l'esito resta `in corso`, e **non e' un fallimento**: il
    contenitore vive 24 ore, e il giro dopo lo richiede invece di crearne un
    altro.
    """
    esito, errore = "in corso", None
    for attesa in attese:
        dormi(attesa)
        try:
            esito, errore = esito_contenitore(leggi_contenitore(contenitore, tok))
        except ErroreMeta as exc:
            # Una lettura mancata non cambia quello che sappiamo del contenitore.
            log(f"  stato non letto: {exc}")
            continue
        log(f"  contenitore {contenitore}: {esito}" + (f" — {errore}" if errore else ""))
        if esito != "in corso":
            break
    return esito, errore


def indirizzo(media_id: str, tok: str, attese=ATTESE_LINK, dormi=time.sleep) -> str | None:
    """L'indirizzo di un reel appena uscito. Qualche tentativo, poi si torna senza."""
    for attesa in attese:
        dormi(attesa)
        try:
            m = leggi_media(media_id, tok)
        except ErroreMeta as exc:
            log(f"  indirizzo non letto: {exc}")
            continue
        url = link_reel(m.get("permalink"), m.get("shortcode"))
        if url:
            return url
    return None


def gia_uscito(
    conto: dict,
    tok: str,
    contenitore: str,
    media_id: str | None,
    nato_dopo: datetime | None,
    noti: set[str] | None,
) -> dict:
    """
    L'esito di un contenitore che risulta pubblicato senza che ne sappiamo l'indirizzo.

    Dal reel, se ne conosciamo l'id; altrimenti dall'elenco dell'account —
    quello nuovo, e solo se e' uno (`nuovo_reel`). Se non si trova resta
    `uscito` senza indirizzo: il reel e' online, e dirlo «in corso» vorrebbe
    dire cercare di rifarlo.
    """
    url = indirizzo(media_id, tok) if media_id else None
    if not url and nato_dopo:
        try:
            nuovo = nuovo_reel(elenco_media(conto, tok), nato_dopo, noti or set())
        except ErroreMeta as exc:
            log(f"  elenco dei reel non letto: {exc}")
            nuovo = None
        if nuovo:
            url = link_reel(nuovo.get("permalink"), nuovo.get("shortcode"))
            media_id = nuovo.get("id")
    return _fuori(contenitore, "uscito", url=url, media=media_id)


def fai_uscire(
    conto: dict,
    tok: str,
    contenitore: str,
    nato_dopo: datetime | None = None,
    noti: set[str] | None = None,
) -> dict:
    """
    Pubblica un contenitore pronto e legge l'indirizzo del reel.

    Il passo che non torna indietro, e l'unico dove «non so» e' un esito vero.
    Se la richiesta non ha avuto risposta **si chiede al contenitore**: se dice
    `uscito` il reel e' online e manca solo l'indirizzo; se dice ancora `pronto`
    non e' successo niente e si riprova al giro dopo, senza rischio — un
    contenitore non si pubblica due volte. Un no esplicito di Meta invece e'
    un fallimento, e si dice.
    """
    try:
        media_id = pubblica_contenitore(conto, tok, contenitore)
    except ErroreMeta as exc:
        if not exc.incerto:
            return _fuori(contenitore, "fallito", errore=str(exc))
        log(f"  pubblicazione senza risposta ({exc}): chiedo al contenitore")
        try:
            esito, errore = esito_contenitore(leggi_contenitore(contenitore, tok))
        except ErroreMeta:
            return _fuori(contenitore, "in corso", errore=str(exc))
        if esito == "uscito":
            return gia_uscito(conto, tok, contenitore, None, nato_dopo, noti)
        if esito == "fallito":
            return _fuori(contenitore, "fallito", errore=errore)
        return _fuori(contenitore, "in corso", errore=str(exc))
    log(f"reel uscito: media {media_id}")
    return _fuori(contenitore, "uscito", url=indirizzo(media_id, tok), media=media_id)


def pubblica(
    video: Path,
    testo: str,
    gruppo: str | None,
    davvero: bool = False,
    contenitore_precedente: str | None = None,
    media_precedente: str | None = None,
    nato_dopo: datetime | None = None,
    noti: set[str] | None = None,
    espositore=ospita.esposto,
) -> dict:
    """
    Fa uscire `video` come Reel dall'account del gruppo.

    Torna `{contenitore, media, esito, url, errore}`, con esito `uscito`,
    `fallito`, `in corso` o `prova`. `uscito` senza `url` esiste: il reel e'
    online e manca solo l'indirizzo.

    Con `contenitore_precedente` non si crea niente finche' non si sa com'e'
    finito quello. Senza `davvero` si fa tutto tranne pubblicare: guardia
    sull'account, didascalia, tunnel, e il contenitore fino a `pronto`.
    """
    difetti = difetti_testo(testo)
    try:
        peso = Path(video).stat().st_size
    except OSError:
        peso = 0
    if peso > MAX_BYTE:
        difetti.append(f"{peso // 2**20} MB, il tetto e' {MAX_BYTE // 2**20}")
    if difetti:
        raise ErroreMeta("reel non pubblicabile: " + "; ".join(difetti))

    tok = token_fresco(gruppo)
    conto = account_atteso(account(tok), registro.handle(gruppo, "instagram"))
    log(f"Instagram (API): @{conto['username']}")

    if contenitore_precedente:
        esito, errore = esito_contenitore(leggi_contenitore(contenitore_precedente, tok))
        log(f"contenitore precedente {contenitore_precedente}: {esito}")
        if esito == "uscito":
            # Online da un giro prima: non si rifa', se ne cerca l'indirizzo.
            return gia_uscito(conto, tok, contenitore_precedente, media_precedente, nato_dopo, noti)
        if esito == "pronto":
            if not davvero:
                log("[PROVA] il contenitore e' pronto, non lo pubblico")
                return _fuori(contenitore_precedente, "prova")
            return fai_uscire(conto, tok, contenitore_precedente, nato_dopo, noti)
        if esito == "in corso":
            return _fuori(contenitore_precedente, "in corso")
        log(f"  fallito ({errore}): ne creo un altro")

    try:
        with espositore(video) as esposizione:
            log(f"clip raggiungibile da fuori: {Path(video).name}")
            contenitore = crea_contenitore(conto, tok, esposizione.url, testo)
            log(f"contenitore creato: {contenitore}")
            esito, errore = segui_contenitore(contenitore, tok)
            if not esposizione.richieste:
                log("  nessuno e' venuto a prendersi il file")
    except RuntimeError as exc:
        if isinstance(exc, (ErroreMeta, registro.AccountSbagliato)):
            raise
        raise ErroreMeta(str(exc)) from exc

    if esito != "pronto":
        return _fuori(contenitore, esito, errore=errore)
    if not davvero:
        log("[PROVA] Meta ha accettato il video. Non pubblico: il contenitore scade in 24 ore")
        return _fuori(contenitore, "prova")
    return fai_uscire(conto, tok, contenitore, nato_dopo, noti)


# ------------------------------------------------------------------- comandi


def _stampa(dati) -> None:
    json.dump(dati, sys.stdout, ensure_ascii=False, indent=2)
    print()


def cmd_chi(args) -> None:
    tok = token_fresco(args.gruppo)
    conto = account(tok)
    try:
        conto["tipo"] = chiedi("GET", "me", tok, {"fields": "account_type"}).get("account_type")
    except ErroreMeta as exc:
        conto["tipo"] = f"non letto: {exc}"
    atteso = registro.handle(args.gruppo, "instagram")
    try:
        account_atteso(conto, atteso)
        guardia = "ok"
    except registro.AccountSbagliato as exc:
        guardia = str(exc)
    try:
        uso = quota(conto, tok)
    except ErroreMeta as exc:
        uso = {"non letta": str(exc)}
    stato = stato_token(args.gruppo)
    _stampa(
        {
            "account": conto,
            "atteso": atteso,
            "guardia": guardia,
            "quota": uso,
            "token": {**stato, "giorni_alla_scadenza_al_massimo": giorni_rimasti(stato, datetime.now())},
        }
    )
    if guardia != "ok":
        sys.exit(guardia)


def cmd_incolla(args) -> None:
    """
    Il token nuovo, chiesto senza eco e scritto solo se Meta lo riconosce.

    Esiste perche' il token va rigenerato a mano dopo ogni evento di sicurezza
    di Meta, e la via piu' corta — incollarlo in chat — lo lascia nel
    transcript. Qui passa dal terminale al file e basta. Si scrive **dopo** la
    guardia: un token di un altro account nel file farebbe pubblicare di la'.
    """
    import getpass

    grezzo = getpass.getpass("Token di Instagram (non si vede mentre lo incolli): ")
    # Il terminale di VS Code incolla fra i marcatori del «bracketed paste»
    # (ESC[200~ ... ESC[201~): su un input senza eco arrivano dentro il token,
    # e Meta risponde «Cannot parse», che sembra un token sbagliato.
    nuovo = re.sub(r"\x1b\[20[01]~", "", grezzo)
    nuovo = "".join(c for c in nuovo if c.isprintable() and not c.isspace())
    if not nuovo:
        sys.exit("nessun token: niente cambiato")
    if not re.fullmatch(r"[A-Za-z0-9_\-.|]+", nuovo):
        strani = sorted({c for c in nuovo if not re.match(r"[A-Za-z0-9_\-.|]", c)})
        sys.exit(
            f"il testo incollato non sembra un token: {len(nuovo)} caratteri, "
            f"inizia con {nuovo[:4]!r}, caratteri inattesi {strani}. Niente cambiato."
        )
    log(f"ricevuti {len(nuovo)} caratteri, inizia con {nuovo[:4]!r}")
    conto = account(nuovo)
    account_atteso(conto, registro.handle(args.gruppo, "instagram"))
    CARTELLA.mkdir(mode=0o700, exist_ok=True)
    serratura.scrivi_atomico(file_token(args.gruppo), nuovo + "\n")
    try:
        file_token(args.gruppo).chmod(0o600)
    except OSError:
        pass
    log(f"token di {args.gruppo} scritto: account @{conto.get('username')}")
    cmd_chi(args)


def cmd_rinnova(args) -> None:
    rinnova(args.gruppo)
    _stampa(stato_token(args.gruppo))


def cmd_numeri(args) -> None:
    righe = reel_con_numeri(args.gruppo, set(args.solo))
    if not righe:
        sys.exit("nessun reel nella prima pagina dell'account")
    _stampa(righe)


def cmd_stato(args) -> None:
    grezzo = leggi_contenitore(args.contenitore, token_fresco(args.gruppo))
    esito, errore = esito_contenitore(grezzo)
    _stampa({"grezzo": grezzo, "esito": esito, "errore": errore})


def cmd_pubblica(args) -> None:
    testo = Path(args.testo_da).read_text(encoding="utf-8").strip()
    _stampa(pubblica(Path(args.file), testo, args.gruppo, davvero=args.davvero))


def main() -> None:
    ap = argparse.ArgumentParser(description="Instagram dall'API ufficiale di Meta")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for nome, funzione in (("chi", cmd_chi), ("incolla", cmd_incolla), ("rinnova", cmd_rinnova)):
        p = sub.add_parser(nome)
        p.add_argument("--gruppo", required=True)
        p.set_defaults(func=funzione)
    p = sub.add_parser("numeri")
    p.add_argument("--gruppo", required=True)
    p.add_argument("--solo", nargs="*", default=[], help="solo questi codici di reel")
    p.set_defaults(func=cmd_numeri)
    p = sub.add_parser("stato")
    p.add_argument("--gruppo", required=True)
    p.add_argument("--contenitore", required=True)
    p.set_defaults(func=cmd_stato)
    p = sub.add_parser("pubblica")
    p.add_argument("--gruppo", required=True)
    p.add_argument("--file", required=True)
    # Da un file e non da riga di comando: una didascalia ha apostrofi e a capo.
    p.add_argument("--testo-da", required=True, help="file di testo con la didascalia")
    p.add_argument("--davvero", action="store_true")
    p.set_defaults(func=cmd_pubblica)
    args = ap.parse_args()
    try:
        args.func(args)
    except (ErroreMeta, registro.AccountSbagliato, serratura.Occupata) as exc:
        sys.exit(str(exc))


if __name__ == "__main__":
    for _f in (sys.stdout, sys.stderr):
        try:
            _f.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    main()
