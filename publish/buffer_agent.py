#!/usr/bin/env python
"""
buffer_agent.py — pubblica una clip passando da Buffer, senza browser.

    python buffer_agent.py canali                      # organizzazioni e canali collegati
    python buffer_agent.py schema                      # lo schema vero contro quello atteso
    python buffer_agent.py stato --post <id>           # a che punto e' un post
    python buffer_agent.py pubblica --gruppo business --piattaforma tiktok \
        --file clip.mp4 --testo-da didascalia.txt [--davvero]

Perche' esiste
--------------
Il 30 settembre 2026 le clip caricate su TikTok dal browser automatico sono
rimaste a 0-1 views, quelle uscite a mano dal telefono a 723-836 (PROGRESS.md).
L'API ufficiale di TikTok non e' una strada per noi: senza audit pubblica solo
in privato, e l'audit rifiuta proprio «uno strumento per caricare sugli account
che gestisci tu». Buffer quell'audit l'ha passato, ed espone un'API anche nel
piano gratuito: la clip esce **in pubblico e da sola**, sul canale che TikTok
stesso mette a disposizione.

**Se un post cosi' viene distribuito quanto uno dal telefono non lo sa nessuno
finche' non lo misuriamo.** Per questo ogni record di stato scritto passando da
qui porta `via: buffer`: senza, fra un mese non si distinguerebbero le clip di
un canale da quelle dell'altro.

Cosa non fa
-----------
Non sceglie quando: `mode: shareNow`, cioe' subito. Il quando lo decidono
`calendario.py` e `ritmo.py`, e una seconda coda dentro Buffer sarebbe una
seconda verita' (oltre a un tetto di 10 post in attesa nel piano gratuito).

La chiave
---------
Sta in `publish/.buffer/chiave.txt`, una riga. La cartella comincia col punto:
`.gitignore` ed `export_skill.py` la saltano come i profili browser, per la
stessa ragione — e' una credenziale. La scrive l'utente nel file, non passa da
nessuna riga di comando.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

import ospita
import registro

HERE = Path(__file__).resolve().parent
ENDPOINT = "https://api.buffer.com"
FILE_CHIAVE = HERE / ".buffer" / "chiave.txt"

# I limiti che Buffer dichiara per TikTok (support.buffer.com, articolo 559).
MAX_CARATTERI = 2200
MAX_HASHTAG = 5

# Gli stati di un post. **Solo quelli elencati qui valgono quello che dicono**:
# un valore mai visto non e' ne' un'uscita ne' un fallimento, e' «in corso»
# finche' il tempo non scade — la stessa regola di `intake: open` su Whop.
# Letti dallo schema vero il 1 ottobre 2026: `PostStatus` vale draft, error,
# needs_approval, scheduled, sending, sent.
USCITO = {"sent"}
FALLITO = {"error"}

# Dopo quanto si richiede lo stato, in secondi. Le attese crescono perche' il
# piano gratuito concede 250 richieste al giorno: un controllo ogni dieci
# secondi per un quarto d'ora ne brucerebbe 90 per una clip sola. Cosi' sono
# al massimo quindici, in ventitre minuti.
#
# Ventitre e non dieci perche' il file sale dalla linea di casa: al collaudo
# del 1 ottobre 2026 una clip da 25 MB ha impiegato 188 s a passare dal tunnel,
# quindi quella piu' pesante consegnata finora (72 MB) ne chiede nove da sola,
# prima che TikTok cominci a lavorarla. Chiudere il tunnel a meta' scaricamento
# e' un post fallito che sembra colpa di Buffer.
ATTESE = (20, 20, 30, 30, 45, 60, 60, 90, 90, 120, 120, 180, 180, 180, 180)
# Quante altre volte si richiede l'indirizzo a un post gia' uscito.
PAZIENZA_LINK = 2

# Cosa questo file si aspetta dallo schema di Buffer. La documentazione e'
# stata letta, non provata: `schema` confronta queste attese con la risposta
# vera, e una differenza si scopre li' invece che a meta' di una pubblicazione.
ATTESO = {
    "CreatePostInput": {"text", "channelId", "schedulingType", "mode", "assets"},
    "Post": {"id", "status", "externalLink", "sentAt", "error"},
    "Channel": {"id", "name", "service", "isDisconnected"},
}

_ID = re.compile(r"^[A-Za-z0-9_-]{6,64}$")
_HASHTAG = re.compile(r"(?<!\w)#\w+")


class ErroreBuffer(RuntimeError):
    """Buffer ha risposto di no, o non ha risposto."""


def log(msg: str) -> None:
    riga = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(riga, file=sys.stderr, flush=True)
    cartella = HERE / "logs"
    cartella.mkdir(exist_ok=True)
    with (cartella / "buffer.log").open("a", encoding="utf-8") as fh:
        fh.write(riga + "\n")


# ------------------------------------------------------------ le regole pure


def difetti_testo(testo: str) -> list[str]:
    """
    Perche' Buffer rifiuterebbe questa didascalia, o lista vuota.

    Si controlla qui e non si lascia dire a Buffer: il suo rifiuto arriva dopo
    che il post e' stato creato, cioe' come un post fallito da interpretare.
    """
    fuori = []
    if not (testo or "").strip():
        fuori.append("didascalia vuota")
    if len(testo or "") > MAX_CARATTERI:
        fuori.append(f"{len(testo)} caratteri, il tetto e' {MAX_CARATTERI}")
    quanti = len(_HASHTAG.findall(testo or ""))
    if quanti > MAX_HASHTAG:
        fuori.append(f"{quanti} hashtag, il tetto e' {MAX_HASHTAG}")
    return fuori


def canale_atteso(canali: list[dict], canale_id: str | None, piattaforma: str, handle: str | None) -> dict:
    """
    Il canale Buffer da cui deve uscire la clip, o `AccountSbagliato`.

    E' la stessa guardia di `registro.controlla_handle`, su una porta nuova: la
    regola dice che ogni porta da cui si pubblica confronta chi e' collegato
    con chi ci si aspetta, e questa e' la quarta. Qui «chi e' collegato» lo
    dice Buffer, e i modi di sbagliare sono tre: il registro non sa quale
    canale, il canale non c'e' piu' (scollegato), il canale e' di un altro
    account o di un'altra piattaforma. In nessuno dei tre si pubblica.
    """
    if not canale_id:
        raise registro.AccountSbagliato(
            f"{piattaforma}: il registro non dice quale canale Buffer usare "
            f"(`buffer_canale` in identita.json). Non pubblico: lo elenca "
            f"`buffer_agent.py canali`."
        )
    canale = next((c for c in canali if c.get("id") == canale_id), None)
    if not canale:
        raise registro.AccountSbagliato(
            f"{piattaforma}: il canale Buffer {canale_id} non e' fra quelli collegati. "
            f"Non pubblico: l'account e' stato scollegato da Buffer, o la chiave e' "
            f"di un'altra organizzazione."
        )
    servizio = (canale.get("service") or "").lower()
    if servizio != piattaforma:
        raise registro.AccountSbagliato(
            f"{piattaforma}: il canale Buffer {canale_id} e' un canale «{servizio or '?'}». "
            f"Non pubblico: e' il canale sbagliato."
        )
    if canale.get("isDisconnected"):
        # Un canale scollegato resta in elenco: il post si creerebbe e
        # fallirebbe dopo, a clip gia' esposta.
        raise registro.AccountSbagliato(
            f"{piattaforma}: il canale Buffer {canale_id} risulta scollegato. "
            f"Non pubblico: va ricollegato dal sito di Buffer."
        )
    registro.controlla_handle(canale.get("name"), handle, f"{piattaforma} (Buffer)")
    return canale


def esito_post(post: dict | None) -> tuple[str, str | None, str | None]:
    """
    Cosa dice Buffer di un post: (`uscito` | `fallito` | `in corso`, url, errore).

    `uscito` senza url esiste, e non e' `in corso`: il post e' online e manca
    solo l'indirizzo. Confonderli vorrebbe dire ripubblicare.
    """
    post = post or {}
    stato = (post.get("status") or "").lower()
    if stato in FALLITO:
        errore = post.get("error")
        if isinstance(errore, dict):
            errore = errore.get("message") or json.dumps(errore, ensure_ascii=False)
        return "fallito", None, str(errore or "Buffer non dice perche'")
    if stato in USCITO:
        return "uscito", (post.get("externalLink") or None), None
    return "in corso", None, None


_VIDEO_TIKTOK = re.compile(r"^https://(?:www\.)?tiktok\.com/@[\w.]+/video/(\d+)")


def link_tiktok(url: str | None) -> str | None:
    """
    L'indirizzo, se e' quello di **un video** TikTok; altrimenti None.

    `externalLink` di Buffer e' un campo che puo' restare vuoto, e che cosa
    porti per TikTok non sta scritto da nessuna parte. A Whop va l'indirizzo
    del video: un profilo, un link corto o una pagina di Buffer verrebbero
    respinti dopo, a finestra di trenta minuti gia' aperta.
    """
    return url if url and _VIDEO_TIKTOK.match(url) else None


def nuovo_tiktok(post: list[dict], da: datetime, noti: set[str]) -> str | None:
    """
    L'indirizzo del video appena uscito, fra quelli che Studio elenca.

    L'id di un video TikTok porta l'istante in cui e' nato (i 32 bit alti sono
    secondi Unix: verificato sui sei post del 29 settembre - 1 ottobre, entro
    un minuto dall'ora registrata). Quindi «quello nuovo» e' quello nato dopo
    `da` che lo stato non conosce ancora.

    **Se sono due non si sceglie**: mandare a Whop il link di un'altra clip e'
    peggio che non mandarne nessuno, perche' risulta inviato.
    """
    soglia = da.timestamp()
    nuovi = [
        p
        for p in post
        if str(p.get("id", "")).isdigit()
        and str(p["id"]) not in noti
        and (int(p["id"]) >> 32) >= soglia
    ]
    return nuovi[0].get("url") if len(nuovi) == 1 else None


def differenze_schema(tipi: dict[str, set[str]]) -> list[str]:
    """Cosa manca nello schema vero rispetto a `ATTESO`, una riga per mancanza."""
    fuori = []
    for tipo, campi in ATTESO.items():
        veri = tipi.get(tipo)
        if veri is None:
            fuori.append(f"{tipo}: il tipo non esiste")
            continue
        for campo in sorted(campi - veri):
            fuori.append(f"{tipo}.{campo}: il campo non esiste")
    return fuori


# ------------------------------------------------------------------ la rete


def chiave() -> str:
    try:
        valore = FILE_CHIAVE.read_text(encoding="utf-8-sig").strip()
    except OSError:
        valore = ""
    if not valore:
        raise ErroreBuffer(
            f"manca la chiave di Buffer in {FILE_CHIAVE}.\n"
            "  Si crea in publish.buffer.com/settings/api e si incolla in quel file, "
            "una riga sola."
        )
    return valore


def chiedi(query: str, variabili: dict | None = None) -> dict:
    """Una richiesta GraphQL. Torna `data`, o alza `ErroreBuffer` con il motivo."""
    corpo = json.dumps({"query": query, "variables": variabili or {}}).encode("utf-8")
    richiesta = urllib.request.Request(
        ENDPOINT,
        data=corpo,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {chiave()}"},
    )
    try:
        with urllib.request.urlopen(richiesta, timeout=30) as r:
            risposta = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        testo = exc.read().decode("utf-8", "replace")[:400]
        if exc.code == 429:
            # Un 429 non consuma quota ma dice che e' finita: riprovare subito
            # non serve, e dentro una pubblicazione vuol dire non sapere piu'
            # com'e' andata. Lo si dice con il tempo che Buffer chiede.
            dopo = exc.headers.get("Retry-After", "?")
            raise ErroreBuffer(f"quota di Buffer esaurita, riprovare fra {dopo} s: {testo}") from exc
        raise ErroreBuffer(f"Buffer ha risposto {exc.code}: {testo}") from exc
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        raise ErroreBuffer(f"Buffer non risponde: {exc}") from exc
    if risposta.get("errors"):
        messaggi = "; ".join(str(e.get("message")) for e in risposta["errors"])
        raise ErroreBuffer(f"Buffer rifiuta la richiesta: {messaggi}")
    return risposta.get("data") or {}


def organizzazioni() -> list[dict]:
    dati = chiedi("query { account { organizations { id name } } }")
    return ((dati.get("account") or {}).get("organizations")) or []


def canali() -> list[dict]:
    """Tutti i canali di tutte le organizzazioni della chiave."""
    fuori = []
    for org in organizzazioni():
        dati = chiedi(
            "query($org: OrganizationId!) "
            "{ channels(input: { organizationId: $org }) { id name service isDisconnected } }",
            {"org": org["id"]},
        )
        for c in dati.get("channels") or []:
            fuori.append({**c, "organizzazione": org.get("name")})
    return fuori


def schema_vero() -> dict[str, set[str]]:
    """I campi che lo schema di Buffer ha davvero, per i tipi che usiamo."""
    fuori: dict[str, set[str]] = {}
    for tipo in ATTESO:
        dati = chiedi(
            "query($t: String!) { __type(name: $t) "
            "{ fields { name } inputFields { name } } }",
            {"t": tipo},
        )
        t = dati.get("__type")
        if t is None:
            continue
        voci = (t.get("fields") or []) + (t.get("inputFields") or [])
        fuori[tipo] = {v["name"] for v in voci}
    return fuori


def leggi_post(post_id: str) -> dict:
    if not _ID.match(post_id or ""):
        raise ErroreBuffer(f"id del post non valido: {post_id!r}")
    # L'id entra nella query come testo e non come variabile perche' il nome
    # del suo tipo non e' fra le cose verificate; la forma e' controllata sopra.
    dati = chiedi(
        f'query {{ post(input: {{ id: "{post_id}" }}) '
        "{ id status externalLink sentAt error { message } } }"
    )
    return dati.get("post") or {}


def crea_post(canale_id: str, testo: str, url_video: str) -> str:
    dati = chiedi(
        "mutation($input: CreatePostInput!) { createPost(input: $input) { "
        "... on PostActionSuccess { post { id status } } "
        "... on MutationError { message } } }",
        {
            "input": {
                "text": testo,
                "channelId": canale_id,
                "schedulingType": "automatic",
                "mode": "shareNow",
                "assets": [{"video": {"url": url_video}}],
            }
        },
    )
    esito = dati.get("createPost") or {}
    post = esito.get("post") or {}
    if not post.get("id"):
        raise ErroreBuffer(f"Buffer non ha creato il post: {esito.get('message') or esito}")
    return post["id"]


# ------------------------------------------------------------ la pubblicazione


def segui(post_id: str, attese=ATTESE, dormi=time.sleep) -> dict:
    """
    Aspetta che il post arrivi in fondo. Torna `{post, esito, url, errore}`.

    Se il tempo scade l'esito resta `in corso`, e **non e' un fallimento**: il
    post esiste dentro Buffer e puo' uscire dopo. Chi chiama conserva l'id e lo
    richiede al giro dopo invece di crearne un altro.
    """
    esito, url, errore = "in corso", None, None
    senza_link = 0
    for attesa in attese:
        dormi(min(attesa, ATTESE[0]) if senza_link else attesa)
        try:
            esito, url, errore = esito_post(leggi_post(post_id))
        except ErroreBuffer as exc:
            # Una lettura mancata non cambia quello che sappiamo del post.
            log(f"  stato non letto: {exc}")
            continue
        log(f"  post {post_id}: {esito}" + (f" — {url}" if url else ""))
        if esito == "fallito" or (esito == "uscito" and url):
            break
        if esito == "uscito":
            # Online ma senza indirizzo: lo si richiede ancora un paio di
            # volte e poi si esce. Da qui il tempo corre contro di noi — Whop
            # accetta l'invio per trenta minuti dalla pubblicazione, e
            # l'indirizzo lo si puo' leggere da Studio.
            senza_link += 1
            if senza_link > PAZIENZA_LINK:
                break
    return {"post": post_id, "esito": esito, "url": url, "errore": errore}


def pubblica(
    video: Path,
    testo: str,
    gruppo: str | None,
    piattaforma: str,
    davvero: bool = False,
    post_precedente: str | None = None,
    espositore=ospita.esposto,
) -> dict:
    """
    Fa uscire `video` sul canale Buffer del gruppo. Torna `{post, esito, url, errore}`.

    Con `post_precedente` non si crea niente finche' non si sa com'e' finito
    quello: un post rimasto `in corso` al giro prima puo' essere uscito nel
    frattempo, e crearne un secondo metterebbe la stessa clip online due volte.

    Senza `davvero` si fa tutto tranne creare il post: guardia sull'account,
    controllo della didascalia, indirizzo pubblico provato da fuori.
    """
    difetti = difetti_testo(testo)
    if difetti:
        raise ErroreBuffer("didascalia non pubblicabile: " + "; ".join(difetti))
    canale = canale_atteso(
        canali(),
        registro.buffer_canale(gruppo, piattaforma),
        piattaforma,
        registro.handle(gruppo, piattaforma),
    )
    log(f"Buffer: canale {canale['id']} ({canale.get('service')}, {canale.get('name')})")

    if post_precedente:
        esito, url, errore = esito_post(leggi_post(post_precedente))
        log(f"post precedente {post_precedente}: {esito}")
        if esito != "fallito":
            return {"post": post_precedente, "esito": esito, "url": url, "errore": errore}

    try:
        with espositore(video) as esposizione:
            log(f"clip raggiungibile da fuori: {Path(video).name}")
            if not davvero:
                log("[PROVA] non creo il post: account, didascalia e indirizzo sono a posto")
                return {"post": None, "esito": "prova", "url": None, "errore": None}
            post_id = crea_post(canale["id"], testo, esposizione.url)
            log(f"post creato: {post_id}")
            fuori = segui(post_id)
            if not esposizione.richieste:
                log("  nessuno e' venuto a prendersi il file")
            return fuori
    except RuntimeError as exc:
        if isinstance(exc, (ErroreBuffer, registro.AccountSbagliato)):
            raise
        raise ErroreBuffer(str(exc)) from exc


# ------------------------------------------------------------------- comandi


def cmd_canali(_args) -> None:
    righe = canali()
    if not righe:
        sys.exit("nessun canale collegato a questa chiave")
    json.dump(righe, sys.stdout, ensure_ascii=False, indent=2)
    print()


def cmd_schema(_args) -> None:
    veri = schema_vero()
    if not veri:
        sys.exit("lo schema non si lascia leggere: non verificabile da qui")
    mancanze = differenze_schema(veri)
    for tipo in sorted(veri):
        print(f"{tipo}: {', '.join(sorted(veri[tipo]))}")
    if mancanze:
        sys.exit("DIVERSO DA COME ATTESO:\n  " + "\n  ".join(mancanze))
    print("schema: come atteso")


def cmd_stato(args) -> None:
    post = leggi_post(args.post)
    esito, url, errore = esito_post(post)
    json.dump({"grezzo": post, "esito": esito, "url": url, "errore": errore}, sys.stdout,
              ensure_ascii=False, indent=2)
    print()


def cmd_pubblica(args) -> None:
    testo = Path(args.testo_da).read_text(encoding="utf-8").strip()
    fuori = pubblica(Path(args.file), testo, args.gruppo, args.piattaforma, davvero=args.davvero)
    json.dump(fuori, sys.stdout, ensure_ascii=False, indent=2)
    print()


def main() -> None:
    ap = argparse.ArgumentParser(description="Pubblicazione tramite Buffer")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("canali").set_defaults(func=cmd_canali)
    sub.add_parser("schema").set_defaults(func=cmd_schema)
    p = sub.add_parser("stato")
    p.add_argument("--post", required=True)
    p.set_defaults(func=cmd_stato)
    p = sub.add_parser("pubblica")
    p.add_argument("--gruppo", required=True)
    p.add_argument("--piattaforma", default="tiktok")
    p.add_argument("--file", required=True)
    # Da un file e non da riga di comando: una didascalia ha apostrofi e a capo.
    p.add_argument("--testo-da", required=True, help="file di testo con la didascalia")
    p.add_argument("--davvero", action="store_true")
    p.set_defaults(func=cmd_pubblica)
    args = ap.parse_args()
    try:
        args.func(args)
    except (ErroreBuffer, registro.AccountSbagliato) as exc:
        sys.exit(str(exc))


if __name__ == "__main__":
    for _f in (sys.stdout, sys.stderr):
        try:
            _f.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    main()
