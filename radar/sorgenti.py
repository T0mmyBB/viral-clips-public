#!/usr/bin/env python
"""
sorgenti.py — da una campagna ai video da clippare, senza scaricarne nessuno.

Perche' esiste
--------------
Il pulsante «prepara» del pannello presuppone che una campagna sia **un link e
un video**. Le campagne vere consegnano cartelle di Drive, Doc con dentro i
link, canali interi, VOD: contato sulle schede il 28 settembre 2026, il caso
del pannello copre circa il 15% delle lavorabili. La specifica sta in
`docs/risolutore-sorgenti.md`; questo e' il suo primo passo, il giro **a
secco**: per ogni campagna lavorabile l'elenco dei video candidati in
`sorgenti/<id>.json`, e alla fine le quote `automatico`, `con_scelta`,
`a_mano`.

Constata, non decide
--------------------
La preselezione e' meccanica — durata minima, gia' lavorato, lingua, i piu'
recenti prima — e **un possibile esempio non si preseleziona mai**. Quando un
brief pone vincoli sul sorgente ("solo gli episodi di settembre") li legge
Claude, come il resto del brief: una regex che prova a capirli sarebbe una
decisione presa da uno script. Per questo l'esito ha tre valori e non due.

Trappole gia' pagate
--------------------
- **Il brief in testo perde i link.** `briefs.py` esporta i Doc in txt, e
  l'export testuale di Google tiene la parola e butta l'indirizzo: sulle
  campagne di Elina e Stanley Meng il canale e il video da clippare c'erano
  solo come collegamento sotto una parola. Qui i Doc si rileggono in html; il
  txt resta il ripiego.
- **Un esempio non e' un sorgente.** Sulla Acme Show un esempio del brief
  (`1P5Y7OcRyz8`) veniva scelto al posto dell'episodio (`WyCRc1TH7ek`). I link
  sotto un titolo come «Examples:», i file in una sottocartella «Example» e
  gli Shorts si marcano `forse_esempio` e restano fuori dalla preselezione.
- **Un canale non ha date nell'elenco piatto**, ma la scheda `/videos` e' gia'
  in ordine dal piu' recente: si tiene la posizione. Chiedere la data video
  per video costerebbe 2 s l'uno.

Uso
---
    python sorgenti.py                  # tutte le lavorabili attive, con la cache
    python sorgenti.py --id <uuid>      # una campagna sola, stampata per intero
    python sorgenti.py --rifai          # ignora la cache e rilegge la rete
    python sorgenti.py --lavori 8       # quante campagne in parallelo

Scrive `sorgenti/<id>.json` per campagna e `sorgenti/_riepilogo.json`; il
riepilogo esce anche su stdout. La cache si invalida con l'`impronta`
(materiali, descrizione, brief): preselezione ed esito si ricalcolano a ogni
giro, perche' cambiano con le cartelle di lavoro e non con la rete.
"""

from __future__ import annotations

import argparse
import functools
import hashlib
import json
import re
import sys
import threading
import time
import urllib.parse
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from html import unescape
from pathlib import Path

for _flusso in (sys.stdout, sys.stderr):
    try:
        _flusso.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / ".claude" / "skills" / "viral-clips" / "scripts"))

import briefs  # noqa: E402
import ingest  # noqa: E402
from campagna import TRASFERIMENTI  # noqa: E402

DETTAGLI = HERE / "radar_dettagli.json"
ASSEGNAZIONI = HERE / "assegnazioni.json"
BRIEFS = HERE / "briefs"
CARTELLA = HERE / "sorgenti"
WORK = ROOT / "work"

# Le tre scelte aperte della specifica, coi valori che propone.
DURATA_MIN_S = 600
PER_CLIC = 2
LINGUE = ("it", "en")

ULTIMI_DEL_CANALE = 15
PROFONDITA_DRIVE = 2
CARTELLE_MAX = 30
MISURATI_PER_CARTELLA = 6
# Una cartella di Drive puo' contenere migliaia di clip gia' montate (2.352 su
# una campagna del 28 settembre 2026): se ne tengono le piu' recenti.
FILE_PER_CARTELLA = 60

# Google risponde al rate limit con un 200 vuoto (vedi `briefs.py`): pochi alla
# volta, e una pausa fra un documento e l'altro.
_GOOGLE = threading.Semaphore(2)
_DRIVE = threading.Semaphore(4)
PAUSA_GOOGLE = 1.0

# ------------------------------------------------------------------ i link

_H = r"^https?://(?:www\.|m\.)?"
TIPI = (
    ("trasferimento", TRASFERIMENTI),
    ("frameio", re.compile(r"^https?://(?:[\w-]+\.)?(?:frame\.io|f\.io)/", re.I)),
    ("box", re.compile(r"^https?://(?:[\w-]+\.)*box\.com/s/", re.I)),
    (
        "abbreviato",
        re.compile(
            r"^https?://(?:www\.)?(?:tinyurl\.com|bit\.ly|rb\.gy|cutt\.ly|shorturl\.at|"
            r"tiny\.cc|is\.gd)/\S+",
            re.I,
        ),
    ),
    ("youtube_short", re.compile(_H + r"youtube\.com/shorts/[\w-]{11}", re.I)),
    (
        "youtube_video",
        re.compile(
            _H + r"(?:youtube\.com/(?:watch\?(?:[^#\s]*&)?v=|live/|embed/)|youtu\.be/)[\w-]{11}",
            re.I,
        ),
    ),
    ("youtube_playlist", re.compile(_H + r"youtube\.com/playlist\?(?:[^#\s]*&)?list=[\w-]+", re.I)),
    (
        "youtube_canale",
        re.compile(
            _H + r"youtube\.com/(?:@[^/?#\s]+|channel/[\w-]+|c/[^/?#\s]+|user/[^/?#\s]+)", re.I
        ),
    ),
    ("drive_cartella", re.compile(r"^https?://drive\.google\.com/drive/(?:u/\d+/)?folders/", re.I)),
    (
        "drive_file",
        re.compile(
            r"^https?://(?:drive|docs)\.google\.com/"
            r"(?:file/d/|open\?(?:[^#\s]*&)?id=|uc\?(?:[^#\s]*&)?id=)[\w-]{20,}",
            re.I,
        ),
    ),
    (
        "gdoc",
        re.compile(r"^https?://docs\.google\.com/document/(?:u/\d+/)?d/(?:e/)?[\w-]{20,}", re.I),
    ),
    (
        "gsheet",
        re.compile(r"^https?://docs\.google\.com/spreadsheets/(?:u/\d+/)?d/[\w-]{20,}", re.I),
    ),
    ("twitch_vod", re.compile(_H + r"twitch\.tv/videos/\d+", re.I)),
    # Anche le sottopagine del canale: «twitch.tv/x/clips?range=7d» nel brief
    # vuol dire «clippa questo canale». Una clip sola (`/clip/<slug>`) no.
    (
        "twitch_canale",
        re.compile(
            _H + r"twitch\.tv/\w+/?(?:(?:videos|clips|about|schedule|home)/?)?(?:[?#]\S*)?$",
            re.I,
        ),
    ),
    ("kick_vod", re.compile(_H + r"kick\.com/(?:video/|[\w-]+/videos/)[\w-]+", re.I)),
    ("kick_canale", re.compile(_H + r"kick\.com/[\w-]+/?(?:[?#]\S*)?$", re.I)),
    ("dropbox", re.compile(r"^https?://(?:www\.)?dropbox\.com/", re.I)),
    ("vimeo", re.compile(_H + r"vimeo\.com/\d+", re.I)),
    ("file", re.compile(r"^https?://[^\s?#]+\.(?:mp4|mov|m4v|webm|mkv)(?:[?#]|$)", re.I)),
)

# Indici, non video: si leggono per trovarci dentro i link.
INDICI = ("gdoc", "gsheet")
# Cosa non si apre da qui, e perche'. Finisce in «da risolvere a mano».
NON_ESPANDIBILI = {
    "trasferimento": "link di trasferimento: scade, e si scarica a mano",
    "frameio": "Frame.io: serve il browser",
    "box": "Box: si scarica a mano",
}
ESTENSIONI_VIDEO = re.compile(r"\.(?:mp4|mov|m4v|mkv|webm|avi|mxf)$", re.I)

URL = re.compile(r"https?://[^\s\"'<>()\[\]{}|]+")

# Le parole con cui un brief presenta un esempio invece del materiale. Tenute
# larghe apposta: un sorgente scambiato per esempio finisce in `con_scelta` e
# lo guarda qualcuno, un esempio scambiato per sorgente fa partire un'ora di
# lavoro sul video sbagliato.
PAROLE_ESEMPIO = re.compile(
    r"\b(?:examples?|e\.g\.|references?|inspo|inspiration|like this|viral clips?|"
    r"sample clips?|past clips|previous clips|top clips|good clips|best clips|"
    r"top performing|clips? that (?:went|did)|esemp[io])\b",
    re.I,
)


def tipo_link(url: str) -> str | None:
    """Che cosa e' un link, per la forma dell'indirizzo; None se non e' un sorgente."""
    for tipo, schema in TIPI:
        if schema.search(url):
            return tipo
    return None


def id_documento(url: str) -> str | None:
    """L'id di un Doc o di un Foglio; `e/<id>` per un Doc pubblicato sul web."""
    m = re.search(r"/d/((?:e/)?[\w-]{20,})", url)
    return m.group(1) if m else None


def id_sorgente(url: str, tipo: str | None) -> str:
    """La chiave con cui si riconosce lo stesso video da due campagne diverse."""
    if tipo and tipo.startswith("youtube"):
        m = re.search(r"(?:[?&]v=|youtu\.be/|live/|shorts/|embed/)([\w-]{11})", url)
        if m:
            return "yt:" + m.group(1)
    if tipo == "drive_file":
        m = re.search(r"(?:/d/|[?&]id=)([\w-]{20,})", url)
        if m:
            return "drive:" + m.group(1)
    if tipo == "twitch_vod":
        m = re.search(r"videos/(\d+)", url)
        if m:
            return "twitch:" + m.group(1)
    if tipo == "kick_vod":
        m = re.search(r"(?:video|videos)/([\w-]+)", url)
        if m:
            return "kick:" + m.group(1)
    return "url:" + hashlib.sha1(url.encode()).hexdigest()[:12]


def _senza_url(testo: str) -> str:
    return URL.sub(" ", testo)


def indizio_esempio(testo: str, inizio: int) -> str:
    """La parola che fa pensare a un esempio, cercata attorno al link; "" se niente.

    Guarda la riga del link e, sopra, il primo titolo che non sia a sua volta
    un elenco di link: il caso tipico e' «Examples:» seguito da cinque righe
    con un indirizzo ciascuna, e la quinta e' un esempio quanto la prima.
    Gli indirizzi stessi si tolgono prima di cercare, se no `example.com` o
    uno slug con «inspiration» basterebbero a marcare un link.
    """
    inizio_riga = testo.rfind("\n", 0, inizio) + 1
    fine_riga = testo.find("\n", inizio)
    riga = testo[inizio_riga : fine_riga if fine_riga >= 0 else len(testo)]
    m = PAROLE_ESEMPIO.search(_senza_url(riga))
    if m:
        return m.group(0)
    righe_sopra = testo[:inizio_riga].splitlines()
    for sopra in reversed(righe_sopra[-12:]):
        if not _senza_url(sopra).strip(" -•*:\t"):
            continue
        link = URL.search(sopra)
        if link:
            # Un'altra voce dello stesso elenco: si continua a salire, e le sue
            # parole parlano del suo link, non di questo — «post it like this
            # one <link>» non fa un esempio della riga sotto. Tranne «Examples:
            # <link>», dove il titolo apre l'elenco sulla stessa riga.
            prima = sopra[: link.start()].strip()
            m = PAROLE_ESEMPIO.search(prima) if prima.endswith(":") else None
            if m:
                return m.group(0)
            continue
        # Una riga di testo e' il titolo dell'elenco: decide lei, e ci si ferma.
        m = PAROLE_ESEMPIO.search(sopra)
        return m.group(0) if m else ""
    return ""


def link_da_testo(testo: str, da: str) -> list[dict]:
    """I link di un testo che possono portare a un video, in ordine e senza doppioni."""
    visti: set[str] = set()
    trovati = []
    for m in URL.finditer(testo or ""):
        url = m.group(0).rstrip(".,;:!?*'\"")
        tipo = tipo_link(url)
        if tipo is None or url in visti:
            continue
        visti.add(url)
        trovati.append(
            {
                "url": url,
                "tipo": tipo,
                "da": da,
                "indizio_esempio": indizio_esempio(testo, m.start()),
            }
        )
    return trovati


def _fuori_redirect(href: str) -> str:
    """`google.com/url?q=<vero>` e' il giro con cui un Doc esporta ogni link."""
    if "google.com/url?" in href:
        q = urllib.parse.parse_qs(urllib.parse.urlparse(href).query).get("q")
        if q:
            return q[0]
    return href


def testo_da_html(html: str) -> str:
    """Il testo di un Doc esportato in html, con ogni link scritto dopo la sua parola."""

    def ancora(m: re.Match) -> str:
        parola = re.sub(r"<[^>]+>", "", m.group(2))
        return f"{parola} {_fuori_redirect(unescape(m.group(1)))} "

    html = re.sub(r"<(style|script)\b.*?</\1>", " ", html, flags=re.S | re.I)
    html = re.sub(r'<a\b[^>]*?href="([^"]*)"[^>]*>(.*?)</a>', ancora, html, flags=re.S | re.I)
    html = re.sub(r"<(?:br|/p|/li|/h[1-6]|/tr|/div|/table)\b[^>]*>", "\n", html, flags=re.I)
    return unescape(re.sub(r"<[^>]+>", "", html))


@functools.cache
def leggi_doc(ident: str, tipo: str) -> str | None:
    """Il testo di un Doc (html, per non perdere i link) o di un Foglio (csv).

    Un Doc «pubblicato sul web» (`/d/e/2PACX…`) non ha l'export: la sua pagina
    `/pub` e' gia' html.
    """
    if tipo == "gdoc" and ident.startswith("e/"):
        url = f"https://docs.google.com/document/d/{ident}/pub"
    elif tipo == "gdoc":
        url = f"https://docs.google.com/document/d/{ident}/export?format=html"
    else:
        url = f"https://docs.google.com/spreadsheets/d/{ident}/export?format=csv"
    with _GOOGLE:
        grezzo = briefs.scarica(url, PAUSA_GOOGLE)
        time.sleep(PAUSA_GOOGLE)
    # Un documento non condiviso risponde con la pagina di accesso, e con 200.
    if grezzo is None or "ServiceLogin" in grezzo or "accounts.google.com/v3/signin" in grezzo:
        return None
    return testo_da_html(grezzo) if tipo == "gdoc" else grezzo


# I domini che compaiono in ogni scheda e non dicono niente sul materiale.
DOMINI_MUTI = (
    "contentrewards.com",
    "whop.com",
    "gstatic.com",
    "googleusercontent.com",
    "googleapis.com",
    "amazonaws.com",
)


def domini_estranei(testo: str) -> list[str]:
    """I domini dei link che non portano a un video, per dire a chi apre a mano dove guardare."""
    fuori: list[str] = []
    for m in URL.finditer(testo or ""):
        url = m.group(0).rstrip(".,;:!?*'\"")
        if tipo_link(url) is not None:
            continue
        host = urllib.parse.urlparse(url).netloc.lower().removeprefix("www.")
        if not host or host == "google.com" or any(host.endswith(d) for d in DOMINI_MUTI):
            continue
        if host not in fuori:
            fuori.append(host)
    return fuori


def _apri_abbreviato(url: str) -> str | None:
    """Dove porta un tinyurl o un bit.ly: la pagina non serve, basta l'ultimo indirizzo."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": briefs.UA})
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.geturl()
    except Exception:
        return None


def raccogli_link(
    scheda: dict, brief: str, leggi=leggi_doc, apri=_apri_abbreviato
) -> tuple[list[dict], list[dict], list[str]]:
    """Tutti i link che possono portare a un video, nell'ordine in cui fidarsene.

    Prima i materiali della scheda (senza quelli che la scheda stessa dice
    `example`), poi la descrizione, poi i Doc — quelli dei materiali e,
    un livello sotto, quelli linkati dentro di loro — e per ultimo il brief in
    testo, che copre i Doc che l'html non ha dato. Torna anche i domini degli
    altri link: quando niente porta a un video, sono la risposta a «dove sta
    allora il materiale?».
    """
    link: list[dict] = []
    problemi: list[dict] = []
    altri: list[str] = []
    visti: set[str] = set()

    def estranei(testo: str) -> None:
        altri.extend(d for d in domini_estranei(testo) if d not in altri)

    def pulisci(nuovi: list[dict]) -> list[dict]:
        # Un link abbreviato si apre subito: quello che c'e' dietro puo' essere
        # un Doc, e un Doc va letto con gli altri.
        fuori = []
        for voce in nuovi:
            if voce["tipo"] == "abbreviato":
                vero = apri(voce["url"])
                tipo = tipo_link(vero) if vero else None
                if tipo in (None, "abbreviato"):
                    estranei(vero or "")
                    continue
                voce = {**voce, "url": vero, "tipo": tipo}
            fuori.append(voce)
        return fuori

    def aggiungi(nuovi: list[dict]) -> None:
        for voce in pulisci(nuovi):
            if voce["url"] not in visti:
                visti.add(voce["url"])
                link.append(voce)

    materiali = scheda.get("materiali_dett") or [
        {"url": u, "tipo": None} for u in scheda.get("materiali") or []
    ]
    for m in materiali:
        if m.get("tipo") in ("example", "image") or not m.get("url"):
            continue
        tipo = tipo_link(m["url"])
        if tipo:
            # Letti dalla scheda (`whop_agent.py materiali`) i materiali non hanno
            # `tipo` ma l'etichetta che la pagina mette sul link: «Example clips
            # + starter content» e' l'esempio che non si preseleziona.
            trovato = PAROLE_ESEMPIO.search(m.get("etichetta") or "")
            indizio = trovato.group(0) if trovato else ""
            aggiungi([{"url": m["url"], "tipo": tipo, "da": "materiali", "indizio_esempio": indizio}])
        else:
            estranei(m["url"])
    aggiungi(link_da_testo(scheda.get("descrizione") or "", "descrizione"))
    estranei(scheda.get("descrizione") or "")

    letti: set[str] = set()
    coda = [voce for voce in link if voce["tipo"] in INDICI]
    for _livello in range(2):
        prossimi = []
        for doc in coda:
            ident = id_documento(doc["url"])
            if not ident or ident in letti:
                continue
            letti.add(ident)
            testo = leggi(ident, doc["tipo"])
            if testo is None:
                problemi.append(
                    {
                        "categoria": "documento non pubblico o non letto",
                        "url": doc["url"],
                        "dettaglio": "",
                    }
                )
                continue
            nuovi = pulisci(link_da_testo(testo, f"doc:{ident}"))
            for voce in nuovi:
                voce["indizio_esempio"] = voce["indizio_esempio"] or doc["indizio_esempio"]
            aggiungi(nuovi)
            estranei(testo)
            prossimi += [voce for voce in nuovi if voce["tipo"] in INDICI]
        coda = prossimi
    aggiungi(link_da_testo(brief, "brief"))
    estranei(brief)
    return link, problemi, altri


# ------------------------------------------------------------ espansione


class _Zitto:
    def debug(self, msg):
        pass

    info = warning = error = debug


def _ytdlp(url: str, piatto: int = 0) -> dict:
    import yt_dlp  # nel venv del progetto; qui dentro perche' i test non lo vogliono

    opzioni = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "socket_timeout": 30,
        "logger": _Zitto(),
    }
    if piatto:
        opzioni.update(extract_flat="in_playlist", playlistend=piatto)
    with yt_dlp.YoutubeDL(opzioni) as ydl:
        return ydl.extract_info(url, download=False)


def candidato(url: str, tipo: str, da: str, indizio: str = "", **campi) -> dict:
    voce = {
        "id_sorgente": id_sorgente(url, tipo),
        "url": url,
        "tipo": tipo,
        "da": da,
        "titolo": None,
        "durata_s": None,
        "data": None,
        "lingua": None,
        "posizione": None,
        "indizio_esempio": indizio,
    }
    voce.update({k: v for k, v in campi.items() if v is not None})
    return voce


def _data_ytdlp(info: dict) -> str | None:
    d = info.get("upload_date") or info.get("release_date")
    if d and len(d) == 8:
        return f"{d[:4]}-{d[4:6]}-{d[6:]}"
    if info.get("timestamp"):
        return date.fromtimestamp(info["timestamp"]).isoformat()
    return None


def creator_ytdlp(info: dict) -> dict:
    """Chi ha pubblicato il video e quanto pesa: lo Scout ci giudica la viralita'.

    Sono campi che yt-dlp da' gia' nella lettura che si fa comunque, quindi non
    costano una chiamata in piu'. Nell'elenco piatto di un canale il canale e i
    follower stanno in cima all'elenco, e ogni voce ha solo le sue views.
    """
    return {
        "canale": info.get("channel") or info.get("uploader"),
        "follower": info.get("channel_follower_count"),
        "views": info.get("view_count"),
        "like": info.get("like_count"),
        "commenti": info.get("comment_count"),
    }


def da_info(voce: dict, info: dict) -> dict:
    lingua = lingua_breve(info.get("language"))
    durata = info.get("duration")
    return candidato(
        voce["url"],
        voce["tipo"],
        voce["da"],
        voce["indizio_esempio"],
        titolo=info.get("title"),
        durata_s=round(durata) if durata else None,
        data=_data_ytdlp(info),
        lingua=lingua,
        **creator_ytdlp(info),
    )


def url_elenco(url: str, tipo: str) -> str:
    """L'indirizzo da elencare: la scheda dei video di un canale, non la sua home."""
    if tipo == "youtube_canale":
        m = re.match(
            r"(https?://(?:www\.|m\.)?youtube\.com/"
            r"(?:@[^/?#]+|channel/[^/?#]+|c/[^/?#]+|user/[^/?#]+))",
            url,
            re.I,
        )
        base = m.group(1) if m else url
        return base + ("/streams" if "/streams" in url else "/videos")
    if tipo == "twitch_canale":
        nome = re.search(r"twitch\.tv/(\w+)", url, re.I).group(1)
        return f"https://www.twitch.tv/{nome}/videos?filter=archives&sort=time"
    return url


def elenco(voce: dict) -> list[dict]:
    url = url_elenco(voce["url"], voce["tipo"])
    try:
        info = _ytdlp(url, piatto=ULTIMI_DEL_CANALE)
    except Exception as exc:
        # Un canale che fa solo dirette non ha la scheda «Video»: i suoi girati
        # stanno in «Live».
        if voce["tipo"] != "youtube_canale" or "videos tab" not in str(exc):
            raise
        info = _ytdlp(url.removesuffix("/videos") + "/streams", piatto=ULTIMI_DEL_CANALE)
    canale = creator_ytdlp(info)
    trovati = []
    for i, e in enumerate(info.get("entries") or []):
        if not e or e.get("live_status") in ("is_upcoming", "is_live"):
            continue
        url = e.get("url") or e.get("webpage_url")
        if not url:
            continue
        durata = e.get("duration")
        trovati.append(
            candidato(
                url,
                tipo_link(url) or voce["tipo"],
                f"{voce['tipo']}:{voce['url']}",
                voce["indizio_esempio"],
                titolo=e.get("title"),
                durata_s=round(durata) if durata else None,
                data=_data_ytdlp(e),
                posizione=i,
                canale=canale["canale"],
                follower=canale["follower"],
                views=e.get("view_count"),
            )
        )
    return trovati


def _kick_api(percorso: str):
    req = urllib.request.Request(
        f"https://kick.com/api/{percorso}",
        headers={"User-Agent": briefs.UA, "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


LINGUE_PER_NOME = {"english": "en", "italian": "it", "italiano": "it"}


def lingua_breve(lingua: str | None) -> str | None:
    """`en-US`, `English (India)`, `italian` -> `en`/`it`; il resto resta com'e'."""
    if not lingua:
        return None
    lingua = lingua.strip().lower()
    per_nome = LINGUE_PER_NOME.get(re.split(r"[\s(]", lingua)[0])
    if per_nome:
        return per_nome
    return re.split(r"[-_]", lingua)[0] if re.match(r"^[a-z]{2}(?:[-_]|$)", lingua) else lingua


def canale_kick(slug: str, api=_kick_api) -> dict:
    """Nome e follower di un canale Kick; vuoto se l'API non risponde."""
    try:
        d = api(f"v2/channels/{slug}")
    except Exception:
        return {}
    if not isinstance(d, dict):
        return {}
    return {
        "canale": (d.get("user") or {}).get("username") or slug,
        "follower": d.get("followers_count"),
    }


def _vod_kick(
    e: dict, url: str, da: str, indizio: str, posizione: int | None = None, canale=None
) -> dict:
    durata = e.get("duration")
    return candidato(
        url,
        "kick_vod",
        da,
        indizio,
        titolo=e.get("session_title"),
        durata_s=round(durata / 1000) if durata else None,
        data=(e.get("created_at") or "")[:10] or None,
        lingua=lingua_breve(e.get("language")),
        posizione=posizione,
        # yt-dlp risponde 404 sui VOD di Kick (28 settembre 2026): chi scarichera'
        # dovra' partire da qui, il flusso HLS che l'API da' gia'.
        flusso=e.get("source"),
        views=e.get("views"),
        **(canale or {}),
    )


def kick(voce: dict, api=_kick_api) -> list[dict]:
    """I VOD di un canale Kick, o uno solo, dall'API pubblica.

    yt-dlp legge un canale Kick solo mentre e' in diretta («The channel is not
    currently live») e sui VOD risponde 404: l'API che usa la pagina stessa
    risponde senza login, con durata, lingua e data di ogni VOD.
    """
    slug = re.search(r"kick\.com/([\w-]+)", voce["url"], re.I).group(1)
    canale = canale_kick(slug, api) if slug.lower() != "video" else {}
    if voce["tipo"] == "kick_canale":
        trovati = []
        for i, e in enumerate(api(f"v2/channels/{slug}/videos")[:ULTIMI_DEL_CANALE]):
            uuid = (e.get("video") or {}).get("uuid")
            if uuid:
                trovati.append(
                    _vod_kick(
                        e,
                        f"https://kick.com/{slug}/videos/{uuid}",
                        f"kick_canale:{voce['url']}",
                        voce["indizio_esempio"],
                        i,
                        canale,
                    )
                )
        return trovati
    uuid = re.search(r"(?:video|videos)/([\w-]+)", voce["url"]).group(1)
    e = api(f"v1/video/{uuid}")
    diretta = {**(e.get("livestream") or {}), "source": e.get("source"), "views": e.get("views")}
    return [_vod_kick(diretta, voce["url"], voce["da"], voce["indizio_esempio"], canale=canale)]


MESI = {m: i for i, m in enumerate("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(), 1)}
DATA_VOCE = re.compile(r'id="entry-([\w-]+)".*?flip-entry-last-modified"><div>([^<]*)<', re.S)
# Il tipo che Drive mette nell'icona di ogni voce (`.../type/video/mp4`). Conta
# piu' del nome: il 7 ottobre 2026 la cartella di Elliott Caldwell aveva dodici
# episodi interi chiamati col solo nome dell'ospite, senza estensione, e il
# risolutore ne vedeva uno — l'unico che finiva in `.mp4`.
TIPO_VOCE = re.compile(r'/type/([\w.+-]+/[\w.+-]+)')


def tipi_drive(html: str) -> dict[str, str]:
    """Il tipo di ogni voce di una cartella, per identificativo. Vuoto se Drive non lo dice."""
    tipi = {}
    for pezzo in html.split('class="flip-entry"')[1:]:
        ident = re.search(r'id="entry-([\w-]+)"', pezzo)
        tipo = TIPO_VOCE.search(pezzo)
        if ident and tipo:
            tipi[ident.group(1)] = tipo.group(1)
    return tipi


def data_drive(testo: str, oggi: date | None = None) -> str | None:
    """«Sep 14», «Sep 14, 2025» o un'ora (oggi): la data di modifica di un file di Drive."""
    oggi = oggi or date.today()
    testo = testo.strip()
    if re.fullmatch(r"\d{1,2}:\d{2}(?:\s*[AP]M)?", testo, re.I):
        return oggi.isoformat()
    m = re.fullmatch(r"([A-Z][a-z]{2}) (\d{1,2})(?:, (\d{4}))?", testo)
    if not m or m.group(1) not in MESI:
        return None
    anno = int(m.group(3)) if m.group(3) else oggi.year
    d = date(anno, MESI[m.group(1)], int(m.group(2)))
    if not m.group(3) and d > oggi:
        d = date(anno - 1, d.month, d.day)
    return d.isoformat()


def _html_cartella(folder_id: str) -> str | None:
    with _DRIVE:
        try:
            req = urllib.request.Request(
                ingest.VISTA_CARTELLA.format(folder_id), headers={"User-Agent": briefs.UA}
            )
            with urllib.request.urlopen(req, timeout=30) as r:
                html = r.read().decode("utf-8", "replace")
        except Exception:  # rete, cartella privata, pagina cambiata
            return None
    return html if "flip-entries" in html else None


def _info_drive(url: str) -> dict:
    """yt-dlp su un file di Drive, pochi alla volta e con un'attesa sul 429."""
    for giro in range(3):
        try:
            with _DRIVE:
                return _ytdlp(url)
        except Exception as exc:
            if "429" not in str(exc) or giro == 2:
                raise
            time.sleep(10 * (giro + 1))
    raise AssertionError("non si arriva qui")


def _durata_drive(url: str) -> int | None:
    durata = _info_drive(url).get("duration")
    return round(durata) if durata else None


def _titolo_drive(ident: str) -> str | None:
    """Il nome di un file di Drive dal titolo della sua pagina, senza yt-dlp."""
    with _DRIVE:
        try:
            req = urllib.request.Request(
                f"https://drive.google.com/file/d/{ident}/view", headers={"User-Agent": briefs.UA}
            )
            with urllib.request.urlopen(req, timeout=30) as r:
                html = r.read().decode("utf-8", "replace")
        except Exception:
            return None
    m = re.search(r"<title>(.*?) - Google Drive</title>", html, re.S)
    return unescape(m.group(1)).strip() if m else None


def file_drive(voce: dict, titolo_di=_titolo_drive, info_di=_info_drive) -> list[dict]:
    """Un file di Drive, se e' un video.

    I materiali delle campagne linkano anche le guide in PDF e i loghi: su quelli
    yt-dlp risponde «HTTP Error 400», che sembra un guasto e non lo e'. Il nome
    del file lo dice prima, dalla pagina `/view`, e un PDF non e' un sorgente ne'
    un problema.
    """
    ident = re.search(r"(?:/d/|[?&]id=)([\w-]{20,})", voce["url"]).group(1)
    titolo = titolo_di(ident)
    if titolo and re.search(r"\.\w{2,4}$", titolo) and not ESTENSIONI_VIDEO.search(titolo):
        return []
    trovato = da_info(voce, info_di(voce["url"]))
    trovato["titolo"] = titolo or trovato["titolo"]
    return [trovato]


def cartella(
    voce: dict, html_di=_html_cartella, misura=_durata_drive
) -> tuple[list[dict], dict | None]:
    """I video di una cartella di Drive, con le sottocartelle fino a `PROFONDITA_DRIVE`.

    Il nome delle cartelle conta quanto quello dei file: «Example», «Reference
    clips», «Inspo» dicono che cosa c'e' dentro, e un file li' sotto e' un
    esempio anche se si chiama `final_v2.mp4`.
    """
    radice = ingest.CARTELLA_DRIVE.search(voce["url"])
    if not radice:
        return [], {"categoria": "cartella non riconosciuta", "url": voce["url"], "dettaglio": ""}
    file: list[tuple[str, str, str | None, list[str]]] = []
    altro = Counter()
    visitate = 0

    def visita(folder_id: str, percorso: list[str], profondita: int) -> bool:
        nonlocal visitate
        visitate += 1
        html = html_di(folder_id)
        if html is None:
            return False
        date_voci = dict(DATA_VOCE.findall(html))
        tipi = tipi_drive(html)
        for tipo, ident, nome in ingest.voci_da_html(html):
            if tipo == "cartella":
                if profondita < PROFONDITA_DRIVE and visitate < CARTELLE_MAX:
                    visita(ident, [*percorso, nome], profondita + 1)
            elif ESTENSIONI_VIDEO.search(nome) or tipi.get(ident, "").startswith("video/"):
                file.append((ident, nome, data_drive(date_voci.get(ident, "")), percorso))
            else:
                altro[nome.rsplit(".", 1)[-1].lower() if "." in nome else "senza estensione"] += 1
        return True

    if not visita(radice.group(1), [], 0):
        return [], {
            "categoria": "cartella Drive privata o illeggibile",
            "url": voce["url"],
            "dettaglio": "",
        }
    if not file:
        # Loghi, guide, grafiche: una cartella c'e', ma non e' il girato.
        contenuto = ", ".join(f"{n} {est}" for est, n in altro.most_common(4)) or "vuota"
        return [], {
            "categoria": "cartella Drive senza video",
            "url": voce["url"],
            "dettaglio": contenuto,
        }

    trovati = []
    for ident, nome, data, percorso in file:
        indizio = voce["indizio_esempio"]
        for pezzo in [*percorso, nome]:
            m = PAROLE_ESEMPIO.search(pezzo.replace("_", " "))
            if m and not indizio:
                indizio = f"{m.group(0)} (in «{'/'.join([*percorso, nome])}»)"
        trovati.append(
            candidato(
                f"https://drive.google.com/file/d/{ident}/view",
                "drive_file",
                f"cartella:{radice.group(1)}",
                indizio,
                titolo="/".join([*percorso, nome]),
                data=data,
            )
        )
    # La durata costa una lettura per file: si misurano i piu' recenti fra
    # quelli che potrebbero essere preselezionati, gli altri restano senza.
    da_misurare = sorted(
        (c for c in trovati if not c["indizio_esempio"]),
        key=lambda c: c["data"] or "",
        reverse=True,
    )[:MISURATI_PER_CARTELLA]
    with ThreadPoolExecutor(4) as pool:
        for c, durata in zip(da_misurare, pool.map(_misura_quieta(misura), da_misurare)):
            c["durata_s"] = durata
    if len(trovati) <= FILE_PER_CARTELLA:
        return trovati, None
    # Si tengono i misurati e poi i piu' recenti; quanti ne restano fuori si dice.
    misurati = {id(c) for c in da_misurare}
    tenuti = sorted(trovati, key=lambda c: (id(c) not in misurati, -_giorno(c)))
    return tenuti[:FILE_PER_CARTELLA], {
        "categoria": "cartella Drive grande",
        "url": voce["url"],
        "dettaglio": f"{len(trovati)} video, tenuti i {FILE_PER_CARTELLA} piu' recenti",
    }


def _giorno(c: dict) -> int:
    return date.fromisoformat(c["data"]).toordinal() if c.get("data") else 0


def _misura_quieta(misura):
    def f(c: dict) -> int | None:
        try:
            return misura(c["url"])
        except Exception:
            return None

    return f


def file_diretto(voce: dict) -> tuple[list[dict], dict | None]:
    import common  # ffprobe si trova dal registro: vedi `common.tool()`

    url = voce["url"]
    if voce["tipo"] == "dropbox":
        if re.search(r"dropbox\.com/(?:scl/fo|sh)/", url):
            return [], {"categoria": "cartella Dropbox", "url": url, "dettaglio": ""}
        url = re.sub(r"([?&])dl=0", r"\1dl=1", url)
        if "dl=1" not in url:
            url += ("&" if "?" in url else "?") + "dl=1"
    durata = common.video_info(url).get("duration")
    nome = urllib.parse.unquote(urllib.parse.urlparse(url).path.rsplit("/", 1)[-1])
    return [
        candidato(
            voce["url"],
            voce["tipo"],
            voce["da"],
            voce["indizio_esempio"],
            titolo=nome,
            durata_s=round(durata) if durata else None,
        )
    ], None


def espandi(voce: dict) -> tuple[list[dict], dict | None]:
    """Da un link ai video che contiene. Legge soltanto: niente si scarica."""
    tipo = voce["tipo"]
    if tipo in NON_ESPANDIBILI:
        return [], {"categoria": tipo, "url": voce["url"], "dettaglio": NON_ESPANDIBILI[tipo]}
    if tipo == "youtube_short":
        # Uno Short e' una clip finita, non un girato da tagliare.
        indizio = voce["indizio_esempio"] or "e' uno Short"
        return [candidato(voce["url"], tipo, voce["da"], indizio)], None
    try:
        if tipo in ("youtube_video", "twitch_vod", "vimeo"):
            return [da_info(voce, _ytdlp(voce["url"]))], None
        if tipo == "drive_file":
            return file_drive(voce), None
        if tipo in ("kick_canale", "kick_vod"):
            trovati = kick(voce)
        elif tipo in ("youtube_canale", "youtube_playlist", "twitch_canale"):
            trovati = elenco(voce)
        elif tipo == "drive_cartella":
            return cartella(voce)
        elif tipo in ("file", "dropbox"):
            return file_diretto(voce)
        else:
            return [], {
                "categoria": f"tipo {tipo} non gestito",
                "url": voce["url"],
                "dettaglio": "",
            }
    except Exception as exc:  # yt-dlp e ffprobe hanno cento modi di dire di no
        riga = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
        return [], {"categoria": f"{tipo} non letto", "url": voce["url"], "dettaglio": riga[:200]}
    if not trovati:
        # Molti streamer non tengono i VOD: il canale c'e' e non ha niente da tagliare.
        return [], {"categoria": f"{tipo} senza video", "url": voce["url"], "dettaglio": ""}
    return trovati, None


# Un link a un video solo costa una lettura di rete (2-5 s). Un brief con
# centocinquanta video e' quasi sempre un elenco di esempi: oltre questo tetto i
# link restano candidati, ma senza misure.
TIPI_SINGOLI = ("youtube_video", "drive_file", "twitch_vod", "kick_vod", "vimeo")
MAX_SINGOLI = 25


def risolvi(
    scheda: dict, brief: str, leggi=leggi_doc, espandi_link=espandi, apri=_apri_abbreviato
) -> dict:
    """I candidati grezzi di una campagna e i link che non si sono aperti."""
    link, problemi, altri = raccogli_link(scheda, brief, leggi, apri)
    candidati: list[dict] = []
    visti: set[str] = set()
    singoli = 0
    for voce in link:
        if voce["tipo"] in INDICI:
            continue
        if voce["tipo"] in TIPI_SINGOLI:
            # Lo stesso video con un `?t=` diverso e' lo stesso video: sul
            # brief di MoonPay la stessa diretta tornava trenta volte.
            chiave = id_sorgente(voce["url"], voce["tipo"])
            if chiave in visti:
                continue
            singoli += 1
            if singoli > MAX_SINGOLI:
                c = candidato(voce["url"], voce["tipo"], voce["da"], voce["indizio_esempio"])
                visti.add(chiave)
                candidati.append(c)
                continue
        nuovi, problema = espandi_link(voce)
        if problema:
            problemi.append(problema)
        for c in nuovi:
            if c["id_sorgente"] not in visti:
                visti.add(c["id_sorgente"])
                candidati.append(c)
    return {
        "candidati": candidati,
        "problemi": problemi,
        "altri_link": altri,
        "link_letti": len(link),
    }


# ------------------------------------------------------------ la scelta


def _ordine(c: dict) -> tuple:
    giorno = _giorno(c)
    posizione = c["posizione"] if c.get("posizione") is not None else 10**6
    return (not c.get("data"), -giorno, posizione, -(c.get("durata_s") or 0))


def preseleziona(
    candidati: list[dict],
    gia_lavorati: set[str] = frozenset(),
    durata_min: int = DURATA_MIN_S,
    per_clic: int = PER_CLIC,
) -> None:
    """Segna `preselezionato` e `perche` su ogni candidato. Constata, non decide."""
    buoni = []
    for c in candidati:
        c["forse_esempio"] = bool(c.get("indizio_esempio"))
        c["gia_lavorato"] = c["id_sorgente"] in gia_lavorati
        c["preselezionato"] = False
        if c["forse_esempio"]:
            c["perche"] = f"forse un esempio: {c['indizio_esempio']}"
        elif c["gia_lavorato"]:
            c["perche"] = "gia' lavorato"
        elif c.get("durata_s") is None and c.get("da", "").startswith("cartella:"):
            # Non una lettura fallita: in una cartella si misurano solo i piu'
            # recenti, e se quelli sono clip brevi il resto e' quasi sempre uguale.
            c["perche"] = "non misurato"
        elif c.get("durata_s") is None:
            c["perche"] = "durata non letta"
        elif c["durata_s"] < durata_min:
            c["perche"] = f"sotto i {durata_min // 60} minuti"
        elif c.get("lingua") and lingua_breve(c["lingua"]) not in LINGUE:
            c["perche"] = f"lingua {c['lingua']}"
        else:
            buoni.append(c)
    buoni.sort(key=_ordine)
    for i, c in enumerate(buoni):
        if i >= per_clic:
            c["perche"] = f"oltre i {per_clic} per clic"
            continue
        c["preselezionato"] = True
        if c.get("data"):
            quando = f"del {c['data']}"
        elif c.get("posizione") is not None:
            quando = f"{c['posizione'] + 1}o piu' recente dell'elenco"
        else:
            quando = "senza data"
        c["perche"] = f"{quando}, {c['durata_s'] // 60} min"


def esito(candidati: list[dict], problemi: list[dict], altri=()) -> tuple[str, str]:
    """`automatico` se qualcosa e' preselezionato, `con_scelta` se c'e' ma non passa.

    Senza nessun candidato: `a_mano`, col motivo dei link che non si sono aperti
    e, se non ce n'e' nessuno, i domini degli altri link — «nessun link a un
    video» da solo manda a rileggere un brief che dice gia' «notion.so».
    """
    if any(c["preselezionato"] for c in candidati):
        return "automatico", ""
    senza_esito = "; ".join(dict.fromkeys(p["categoria"] for p in problemi))
    if candidati:
        motivi = Counter(c["perche"].split(":")[0] for c in candidati)
        testo = ", ".join(f"{n} {m}" for m, n in motivi.most_common())
        return "con_scelta", f"nessun candidato passa: {testo}" + (
            f" — e inoltre: {senza_esito}" if senza_esito else ""
        )
    if senza_esito:
        return "a_mano", senza_esito
    fuori = f"; altri link: {', '.join(list(altri)[:5])}" if altri else ""
    return "a_mano", "nessun link a un video nei materiali e nel brief" + fuori


# --------------------------------------------------------------- il giro


def gia_lavorati() -> set[str]:
    """I sorgenti che hanno gia' una cartella di lavoro, da `meta.json`."""
    fatti = set()
    for meta in WORK.glob("*/meta.json"):
        try:
            sorgente = json.loads(meta.read_text(encoding="utf-8")).get("source") or ""
        except (OSError, json.JSONDecodeError):
            continue
        if sorgente.startswith("http"):
            fatti.add(id_sorgente(sorgente, tipo_link(sorgente)))
    return fatti


# Entra nell'impronta: quando il risolutore impara a vedere cose che prima
# perdeva, le risoluzioni vecchie non valgono piu' anche a scheda invariata.
# 2: i video di Drive senza estensione (7 ottobre 2026).
# 3: canale, follower, views, like e commenti del creator (31 ottobre 2026).
VERSIONE = 3

# Una cartella di Drive cresce senza che la scheda cambi: Elliott Caldwell,
# risolta il 28 settembre con un episodio, il 7 ottobre ne aveva quattordici.
# Oltre questa eta' la risoluzione si rifa' anche a impronta uguale.
SCADENZA = timedelta(days=7)


def scaduta(risolto_il: str | None, adesso: datetime) -> bool:
    try:
        return adesso - datetime.fromisoformat(risolto_il) > SCADENZA
    except (TypeError, ValueError):
        return True


def impronta(scheda: dict, brief: str) -> str:
    base = json.dumps(
        [
            VERSIONE,
            scheda.get("materiali_dett") or scheda.get("materiali"),
            scheda.get("descrizione"),
            brief,
        ],
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha1(base.encode()).hexdigest()[:16]


def una(scheda: dict, fatti: set[str], args) -> dict:
    percorso_brief = BRIEFS / f"{scheda['id']}.txt"
    brief = (
        percorso_brief.read_text(encoding="utf-8", errors="replace")
        if percorso_brief.exists()
        else ""
    )
    firma = impronta(scheda, brief)
    destinazione = CARTELLA / f"{scheda['id']}.json"
    vecchio = None
    if destinazione.exists() and not args.rifai:
        try:
            vecchio = json.loads(destinazione.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            vecchio = None
    if (
        vecchio
        and vecchio.get("impronta") == firma
        and "candidati" in vecchio
        and not scaduta(vecchio.get("risolto_il"), datetime.now())
    ):
        grezzo = {
            k: vecchio[k]
            for k in ("candidati", "problemi", "altri_link", "link_letti")
            if k in vecchio
        }
        risolto_il = vecchio.get("risolto_il")
    else:
        try:
            grezzo = risolvi(scheda, brief)
        except Exception as exc:  # una campagna che si rompe non ferma le altre
            grezzo = {
                "candidati": [],
                "problemi": [{"categoria": "errore", "url": "", "dettaglio": repr(exc)[:200]}],
            }
        risolto_il = datetime.now().isoformat(timespec="seconds")

    preseleziona(grezzo["candidati"], fatti, args.durata_min, args.per_clic)
    quale, motivo = esito(
        grezzo["candidati"], grezzo.get("problemi", []), grezzo.get("altri_link", [])
    )
    voce = {
        "campagna_id": scheda["id"],
        "titolo": (scheda.get("titolo") or "").strip(),
        "risolto_il": risolto_il,
        "impronta": firma,
        "esito": quale,
        "motivo": motivo,
        **grezzo,
    }
    destinazione.write_text(json.dumps(voce, ensure_ascii=False, indent=2), encoding="utf-8")
    return voce


def riassumi(voci: list[dict]) -> dict:
    esiti = Counter(v["esito"] for v in voci)
    totale = len(voci) or 1

    def primo_tipo(v: dict) -> str:
        scelti = [c for c in v["candidati"] if c["preselezionato"]] or v["candidati"]
        if not scelti:
            return "nessuno"
        da = scelti[0]["da"].split(":")[0]
        return da if da in dict(TIPI) else scelti[0]["tipo"]

    return {
        "girato_il": datetime.now().isoformat(timespec="seconds"),
        "campagne": len(voci),
        "esiti": dict(esiti),
        "quote": {k: round(n / totale, 3) for k, n in esiti.items()},
        "criterio": "almeno il 75% automatico (docs/risolutore-sorgenti.md)",
        "da_dove_automatico": dict(
            Counter(primo_tipo(v) for v in voci if v["esito"] == "automatico").most_common()
        ),
        "motivi_a_mano": dict(
            Counter(v["motivo"].split(";")[0] for v in voci if v["esito"] == "a_mano").most_common()
        ),
        "motivi_con_scelta": dict(
            Counter(
                m.split(" ", 1)[1]
                for v in voci
                if v["esito"] == "con_scelta"
                for m in v["motivo"]
                .removeprefix("nessun candidato passa: ")
                .split(" — ")[0]
                .split(", ")
            ).most_common()
        ),
    }


def lavorabili() -> list[dict]:
    schede = json.loads(DETTAGLI.read_text(encoding="utf-8"))
    giudizi = json.loads(ASSEGNAZIONI.read_text(encoding="utf-8"))["assegnazioni"]
    si = {g["id"] for g in giudizi if g.get("lavorabile") == "si"}
    return [s for s in schede if s["id"] in si]


def main() -> None:
    ap = argparse.ArgumentParser(description="Risolutore delle sorgenti, a secco")
    ap.add_argument("--id", help="una campagna sola, stampata per intero")
    ap.add_argument("--rifai", action="store_true", help="ignora la cache")
    ap.add_argument("--lavori", type=int, default=6, help="campagne in parallelo")
    ap.add_argument("--durata-min", type=int, default=DURATA_MIN_S, help="secondi")
    ap.add_argument("--per-clic", type=int, default=PER_CLIC)
    args = ap.parse_args()

    schede = lavorabili()
    if args.id:
        schede = [s for s in schede if s["id"] == args.id] or [
            s for s in json.loads(DETTAGLI.read_text(encoding="utf-8")) if s["id"] == args.id
        ]
        if not schede:
            sys.exit(f"campagna {args.id} non trovata in {DETTAGLI.name}")
    CARTELLA.mkdir(exist_ok=True)
    fatti = gia_lavorati()
    print(f"{len(schede)} campagne, {len(fatti)} sorgenti gia' lavorati", file=sys.stderr)

    voci = []
    inizio = time.monotonic()
    with ThreadPoolExecutor(args.lavori) as pool:
        futuri = [pool.submit(una, s, fatti, args) for s in schede]
        for n, futuro in enumerate(as_completed(futuri), 1):
            voci.append(futuro.result())
            if n % 10 == 0 or n == len(futuri):
                print(f"  [{n}/{len(futuri)}] {time.monotonic() - inizio:.0f} s", file=sys.stderr)

    if args.id:
        print(json.dumps(voci[0], ensure_ascii=False, indent=2))
        return
    riepilogo = riassumi(voci)
    (CARTELLA / "_riepilogo.json").write_text(
        json.dumps(riepilogo, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(riepilogo, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
