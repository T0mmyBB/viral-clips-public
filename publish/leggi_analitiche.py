#!/usr/bin/env python
"""
leggi_analitiche.py — le analitiche di ogni post di un account, dalle pagine del proprietario.

    <interprete> leggi_analitiche.py --gruppo business --piattaforma tiktok
    <interprete> leggi_analitiche.py --gruppo business --piattaforma youtube --solo CigDWiQf6M8

Stampa un JSON su stdout e non scrive niente: lo storico lo tiene
`dashboard/analitiche.py`, che lancia questo file con l'interprete giusto per ogni
piattaforma (`leggi_account.INTERPRETE`). Esce con 3 se il profilo e' occupato da
una pubblicazione, con 1 su ogni altro guasto.

Perche' esiste
--------------
`leggi_account.py` porta views e like di tutti i post, ed e' bastato finche' la
domanda era «quante». Il 30 settembre 2026 la domanda e' diventata «perche'
questi a zero e quelli a ottocento», e la risposta stava un livello sotto, nella
pagina di analisi del singolo post: i post pubblicati dal telefono arrivavano al
99% dal feed For You e all'83% dal Regno Unito, quello automatico del 16 settembre
al 100% dal profilo e al 100% dall'Italia, e i tre post si sono fermati alla stessa
ora — cosa che solo la curva oraria mostra. Il riquadro di account diceva «dati
non ancora sufficienti»; quello del post li aveva dal primo giorno.

Cosa si legge, per piattaforma
------------------------------
- **TikTok** — Studio, `tiktokstudio/analytics/<id>`: le risposte di
  `/aweme/v2/data/insight/`. Fonti di traffico, paesi, permanenza secondo per
  secondo, tempo medio, quota di chi arriva in fondo, views ora per ora, eta' e
  genere, spettatori unici; piu' `getPenaltyDetails`.
- **YouTube** — Studio, `video/<id>/analytics/<scheda>`: le schede di
  `yta_web/get_cards`. Views, views coinvolte, spettatori unici, tempo medio e
  percentuale guardata, quota «guardato invece di scorrere via», fonti, paesi,
  dispositivi, iscritti. Studio a volte apre un riquadro «Verifica la tua
  identita'»: i dati arrivano lo stesso, e il riquadro **non si tocca** — e' una
  verifica di sicurezza dell'account, la fa una persona.
- **Instagram** — sul web le analitiche per post **non ci sono**: la pagina del
  post non ha «View insights» e quella dell'account risponde con un errore del
  server. Si legge cio' che c'e' (views, like, commenti dalla griglia dei reel) e
  lo si dice in `limiti`, invece di lasciare campi vuoti che sembrano zeri.
  **Se il gruppo ha il token dell'API di Meta** (`instagram_api.py`) i numeri
  vengono da li', senza browser: portata, tempo medio, chi scorre via nei primi
  tre secondi, salvataggi e condivisioni. Fonti e paesi per reel non li da'
  nemmeno l'API. Un reel per cui l'API non ha numeri si completa dal web.

Un valore che la piattaforma non da' resta `None`, mai zero: «non lo so» e «zero»
sono due risposte diverse, e un post a zero e' proprio quello che si cerca.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import instagram_api  # noqa: E402
import leggi_account  # noqa: E402
import registro  # noqa: E402
import serratura  # noqa: E402

LIMITE_INSTAGRAM = (
    "instagram: sul web non ci sono analitiche per post (nessun «View insights», e la "
    "pagina insights dell'account risponde con un errore del server). Qui ci sono solo "
    "views, like e commenti; il resto chiede l'API ufficiale o l'app"
)
LIMITE_API_INSTAGRAM = (
    "instagram: numeri dall'API di Meta. Per reel non ci sono ne' fonti del traffico ne' "
    "paesi, e i dati possono avere fino a 48 ore di ritardo"
)

# Due unita' che la documentazione di Meta non scrive (letta il 2 ottobre 2026).
# Non si sono prese per buone alla cieca: le ha dette la prima lettura vera, il
# 3 ottobre 2026, dodici reel di @canale_business.
#
# I tempi di visione sono in **millisecondi**: su ogni reel il tempo totale
# diviso le views torna col tempo medio (234.052 / 55 = 4.255 contro 4.681), e
# in secondi quel totale farebbe 71 minuti di visione a testa per una clip da
# un minuto. Un valore che in millisecondi non starebbe in piedi resta vuoto
# lo stesso: sotto `TEMPO_MINIMO_MS` un tempo medio sarebbe meno di un decimo
# di secondo, e se Meta cambiasse unita' la colonna uscirebbe vuota, non
# sbagliata.
TEMPO_MINIMO_MS = 100
DURATA_MASSIMA_REEL_S = 900
# La scala di `reels_skip_rate` e' **fra 0 e 100**: nella stessa lettura ci
# sono 68,1, 69,2 e 100, che una quota fra 0 e 1 non puo' valere. Con la scala
# detta si converte tutto; senza (`None`) si converte solo cio' che non e'
# ambiguo, e il resto resta vuoto col grezzo conservato.
SCALA_SCORRIMENTO: str | None = "percento"
SCHEDE_YOUTUBE = ("tab-overview", "tab-reach_viewers", "tab-interest_viewers", "tab-build_audience")


def log(msg: str) -> None:
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", file=sys.stderr, flush=True)


# ------------------------------------------------------------ lettura pura


def _ok(o):
    """Il valore di un campo TikTok `{status, value}`: `None` se lo status non e' 0.

    Lo status 2 vuol dire «non disponibile» (le metriche delle foto su un video, o
    quelle che arrivano dopo sette giorni): leggerlo come zero inventerebbe un dato.
    """
    if not isinstance(o, dict):
        return None
    if "status" in o:
        return o.get("value") if o.get("status") == 0 else None
    interno = o.get("value")
    if isinstance(interno, dict) and "status" in interno:
        return _ok(interno)
    return interno


def _quote(voci, chiave="key", valore="value") -> dict | None:
    if not isinstance(voci, list):
        return None
    fuori = {}
    for v in voci:
        if isinstance(v, dict) and v.get(chiave) is not None and v.get(valore) is not None:
            fuori[str(v[chiave])] = float(v[valore])
    return fuori or None


def unisci_tiktok(risposte: list[dict]) -> dict:
    """Le risposte di `/aweme/v2/data/insight/` fuse in un dizionario solo.

    La pagina le chiede a pezzi — un gruppo all'apertura, un altro sulla scheda
    Viewers — e ogni risposta porta tutte le chiavi, quasi tutte a `null`: vince
    il primo valore non nullo.
    """
    fuso: dict = {}
    for r in risposte:
        for k, v in (r or {}).items():
            if v is not None and k not in ("extra", "log_pb", "status_code", "status_msg"):
                fuso.setdefault(k, v)
    return fuso


def analitiche_tiktok(fuso: dict, penalita: dict | None = None) -> dict:
    """I campi comuni da una risposta fusa di Studio. Le quote sono fra 0 e 1."""
    luoghi = (fuso.get("video_viewer_location_percent_realtime") or {}).get("value") or {}
    paesi = {
        p["country_name"]: float(p["country_vv_percent"])
        for p in luoghi.get("country_percent_list") or []
        if p.get("country_name") and p.get("country_vv_percent") is not None
    } or None
    permanenza = None
    curva = ((fuso.get("video_retention_rate_realtime") or {}).get("value") or {}).get("list")
    if isinstance(curva, list):
        permanenza = {
            str(int(p["timestamp"]) // 1000): float(p["value"])
            for p in curva
            if p.get("timestamp") is not None and p.get("value") is not None
        } or None
    orarie = None
    storia = (fuso.get("realtime_video_view_history") or {}).get("list")
    if isinstance(storia, list):
        orarie = [
            [datetime.fromtimestamp(int(p["key"]), timezone.utc).isoformat(timespec="minutes"), p["value"]]
            for p in storia
            if p.get("key") is not None
        ] or None
    dettagli = (penalita or {}).get("top_penalty_details") or {}
    return {
        "views": _ok((fuso.get("realtime_total_video_views") or {}).get("value")),
        "spettatori_unici": _ok(fuso.get("video_uv")),
        "tempo_medio_s": _ok((fuso.get("video_per_duration_realtime") or {}).get("value")),
        "tempo_totale_s": _ok((fuso.get("video_total_duration_realtime") or {}).get("value")),
        "completamento": _ok((fuso.get("video_finish_rate_realtime") or {}).get("value")),
        "permanenza": permanenza,
        "fonti": _quote(_ok((fuso.get("video_traffic_source_percent_realtime") or {}).get("value"))),
        "paesi": paesi,
        "eta": _quote(_ok((fuso.get("video_viewer_age_percent_realtime") or {}).get("value"))),
        "genere": _quote(_ok((fuso.get("video_viewer_gender_percent_realtime") or {}).get("value"))),
        "follower_nuovi": _ok(fuso.get("video_new_followers")),
        "views_orarie": orarie,
        # penalty_type 0 e' «nessuna penalita'»: il resto si conserva grezzo, perche'
        # i valori non sono documentati e un avviso inventato insegna a ignorarli.
        "penalita": dettagli if dettagli.get("penalty_type") not in (None, 0) else None,
    }


def _colonna(c: dict) -> list | None:
    for k in ("percentages", "counts", "values"):
        if isinstance(c.get(k), dict) and isinstance(c[k].get("values"), list):
            return c[k]["values"]
    return None


def _etichette(d: dict) -> list | None:
    for k in ("enumValues", "strings", "timestamps"):
        if isinstance(d.get(k), dict) and isinstance(d[k].get("values"), list):
            return d[k]["values"]
    return None


def analitiche_youtube(schede: list[dict]) -> dict:
    """I campi comuni dalle schede di `get_cards`. Le quote sono fra 0 e 1, i tempi in secondi.

    Studio da' le percentuali fra 0 e 100 e i tempi in millisecondi; TikTok da' le
    quote fra 0 e 1 e i tempi in secondi. Qui si porta tutto alla forma di TikTok,
    perche' i due si leggono uno accanto all'altro.
    """
    totali: dict = {}
    tabelle: dict = {}
    fuori: dict = {}
    for s in schede:
        km = s.get("keyMetricCardData") or {}
        for tab in km.get("keyMetricTabs") or []:
            pc = tab.get("primaryContent") or {}
            if pc.get("metric") and pc.get("total") is not None:
                totali.setdefault(pc["metric"], pc["total"])
        tab_data = (s.get("tableCardData") or {}).get("mainTableData") or {}
        dims, mets = tab_data.get("dimensionColumns") or [], tab_data.get("metricColumns") or []
        if len(dims) == 1 and mets:
            tipo = (dims[0].get("dimension") or {}).get("type")
            nomi, valori = _etichette(dims[0]), _colonna(mets[0])
            if tipo and nomi and valori and tipo not in tabelle:
                tabelle[tipo] = {str(n): round(float(v) / 100, 4) for n, v in zip(nomi, valori) if v is not None}
        barra = (s.get("stackedBarCardData") or {}).get("overviewCardData") or {}
        dati = barra.get("cardData") or {}
        if dati.get("dimensionColumns") and dati.get("metricColumns"):
            tipo = (dati["dimensionColumns"][0].get("dimension") or {}).get("type")
            nomi, valori = _etichette(dati["dimensionColumns"][0]), _colonna(dati["metricColumns"][0])
            if tipo and nomi and valori and tipo not in tabelle:
                tabelle[tipo] = {str(n): round(float(v) / 100, 4) for n, v in zip(nomi, valori) if v is not None}
        elif dati.get("metricColumns"):
            tipo = (dati["metricColumns"][0].get("metric") or {}).get("type")
            valori = _colonna(dati["metricColumns"][0])
            if tipo == "SHORTS_FEED_IMPRESSIONS_VTR" and valori:
                fuori.setdefault("guardato_invece_di_scorrere", round(float(valori[0]) / 100, 4))
        for v in (s.get("audienceRetentionHighlightsCardData") or {}).get("videosData") or []:
            t = v.get("metricTotals") or {}
            if t.get("avgPercentageWatched") is not None:
                fuori.setdefault("percentuale_guardata", float(t["avgPercentageWatched"]))
            if t.get("shortsFeedImpressionsVtr") is not None:
                fuori.setdefault("guardato_invece_di_scorrere", round(float(t["shortsFeedImpressionsVtr"]) / 100, 4))

    def secondi(metrica):
        v = totali.get(metrica)
        return round(v / 1000, 2) if v is not None else None

    return {
        "views": totali.get("EXTERNAL_VIEWS"),
        "views_coinvolte": totali.get("ENGAGED_VIEWS"),
        "spettatori_unici": totali.get("ESTIMATED_UNIQUE_VIEWERS"),
        "tempo_medio_s": secondi("AVERAGE_WATCH_TIME"),
        "tempo_totale_s": secondi("EXTERNAL_WATCH_TIME"),
        "percentuale_guardata": fuori.get("percentuale_guardata"),
        "guardato_invece_di_scorrere": fuori.get("guardato_invece_di_scorrere"),
        "fonti": tabelle.get("TRAFFIC_SOURCE_TYPE"),
        "paesi": tabelle.get("COUNTRY"),
        "dispositivi": tabelle.get("DEVICE_PLATFORM_TYPE"),
        "iscritti": tabelle.get("SUBSCRIBED_TO_UPLOADER_STATE"),
        "iscritti_nuovi": totali.get("SUBSCRIBERS_NET_CHANGE"),
    }


def _quota_scorrimento(v, scala: str | None) -> float | None:
    if v is None:
        return None
    v = float(v)
    if scala == "percento":
        return round(v / 100, 4)
    if scala == "quota":
        return v
    if 1 < v <= 100:
        return round(v / 100, 4)
    return 0.0 if v == 0 else None


def analitiche_instagram(valori: dict, media: dict | None = None, scala: str | None = SCALA_SCORRIMENTO) -> dict:
    """I campi comuni dagli insight dell'API di Meta. Le quote sono fra 0 e 1, i tempi in secondi.

    `valori` e' metrica -> valore grezzo (`instagram_api.valori_insight`). Una
    metrica che Meta non ha dato resta `None`: l'API la omette invece di
    scriverla a zero, e qui non la si inventa.
    """
    media = media or {}
    medio_ms = valori.get("ig_reels_avg_watch_time")
    medio = totale = None
    if medio_ms is not None and medio_ms >= TEMPO_MINIMO_MS:
        secondi = round(float(medio_ms) / 1000, 2)
        if secondi <= DURATA_MASSIMA_REEL_S:
            medio = secondi
            # Il totale ha la stessa unita' del medio, e si converte solo se
            # quella del medio sta in piedi: da solo non la tradirebbe.
            totale_ms = valori.get("ig_reels_video_view_total_time")
            totale = round(float(totale_ms) / 1000, 2) if totale_ms is not None else None
    like = valori.get("likes")
    commenti = valori.get("comments")
    return {
        "views": valori.get("views"),
        "spettatori_unici": valori.get("reach"),
        "tempo_medio_s": medio,
        "tempo_totale_s": totale,
        "scorre_via_3s": _quota_scorrimento(valori.get("reels_skip_rate"), scala),
        "like": like if like is not None else media.get("like_count"),
        "commenti": commenti if commenti is not None else media.get("comments_count"),
        "salvataggi": valori.get("saved"),
        "condivisioni": valori.get("shares"),
        "repost": valori.get("reposts"),
        "interazioni": valori.get("total_interactions"),
    }


def righe_instagram_api(reel: list[dict]) -> tuple[list[dict], list[str]]:
    """Le righe d'uscita dai reel di `instagram_api.reel_con_numeri`, e cosa manca.

    Un reel senza nessun numero resta nell'uscita col motivo che ha dato Meta,
    invece di sparire o di diventare una riga di zeri.
    """
    righe, non_convertiti = [], 0
    for r in reel:
        post = {"id": r["id"], "url": r["url"]}
        grezzo = {"media": r["media"], "valori": r["valori"], "rifiutate": r["rifiutate"]}
        if not r["valori"]:
            motivo = next(iter(r["rifiutate"].values()), "l'API non ha numeri per questo reel")
            righe.append(_riga(post, None, grezzo, motivo))
            continue
        a = analitiche_instagram(r["valori"], r["media"])
        for grezza, campo in (("reels_skip_rate", "scorre_via_3s"), ("ig_reels_avg_watch_time", "tempo_medio_s")):
            if r["valori"].get(grezza) is not None and a[campo] is None:
                non_convertiti += 1
        righe.append(_riga(post, a, grezzo))
    limiti = [LIMITE_API_INSTAGRAM]
    if non_convertiti:
        limiti.append(
            f"instagram: {non_convertiti} valori di tempo o di skip rate lasciati vuoti perche' "
            "nella loro unita' non stanno in piedi (leggi_analitiche, TEMPO_MINIMO_MS e "
            "SCALA_SCORRIMENTO); il grezzo c'e'"
        )
    return righe, limiti


def completa_dal_web(righe: list[dict], post_web: list[dict]) -> int:
    """Riempie con views, like e commenti del web i reel per cui l'API non ha numeri.

    Sono quelli usciti prima che l'account diventasse professionale: l'API non
    li misura, la griglia dei reel si'. Torna quanti ne ha riempiti.
    """
    per_id = {p["id"]: p for p in post_web}
    riempiti = 0
    for riga in righe:
        p = per_id.get(riga["id"])
        if riga["analitiche"] is None and p:
            riga["analitiche"] = {"views": p["views"], "like": p["like"], "commenti": p["commenti"]}
            riga["guasto"] = None
            riempiti += 1
    return riempiti


def schede_pulite(risposte: list[dict]) -> list[dict]:
    """Le schede di tutte le risposte `get_cards`, senza i campi di servizio."""
    fuori = []
    for r in risposte:
        for c in (r or {}).get("cards") or []:
            fuori.append({k: v for k, v in c.items() if k not in ("loggingConfig", "sideEntities")})
    return fuori


# ------------------------------------------------------------ browser


def _aspetta(page, condizione, massimo_s: float) -> bool:
    fine = time.monotonic() + massimo_s
    while time.monotonic() < fine:
        if condizione():
            return True
        page.wait_for_timeout(1000)
    return condizione()


def analisi_tiktok(page, pid: str) -> tuple[dict, dict]:
    """Apre l'analisi di un post e torna (analitiche, grezzo)."""
    insight: list[dict] = []
    penalita: list[dict] = []

    def su_risposta(r):
        if "/aweme/v2/data/insight/" in r.url or "/mod/v1/getPenaltyDetails/" in r.url:
            try:
                (penalita if "getPenaltyDetails" in r.url else insight).append(r.json())
            except Exception:
                pass

    page.on("response", su_risposta)
    try:
        page.goto(f"https://www.tiktok.com/tiktokstudio/analytics/{pid}", wait_until="domcontentloaded")
        _aspetta(page, lambda: "video_traffic_source_percent_realtime" in unisci_tiktok(insight), 25)
        # Paesi, eta' e genere arrivano solo aprendo la scheda Viewers.
        try:
            page.get_by_text("Viewers", exact=True).first.click(timeout=5000)
            _aspetta(page, lambda: "video_viewer_location_percent_realtime" in unisci_tiktok(insight), 15)
        except Exception as exc:
            log(f"tiktok {pid}: scheda Viewers non aperta ({type(exc).__name__})")
    finally:
        page.remove_listener("response", su_risposta)
    fuso = unisci_tiktok(insight)
    if not fuso:
        raise SystemExit(f"tiktok {pid}: nessuna risposta di analisi. Pagina salvata in "
                         f"{leggi_account.dump(page, 'analitiche-tiktok')}")
    fuso.pop("video_info", None)  # l'autore e la descrizione: li ha gia' il post
    pen = penalita[-1] if penalita else None
    return analitiche_tiktok(fuso, pen), {"insight": fuso, "penalita": pen}


def analisi_youtube(page, vid: str) -> tuple[dict, dict]:
    risposte: list[dict] = []

    def su_risposta(r):
        if "/yta_web/get_cards" in r.url:
            try:
                risposte.append(r.json())
            except Exception:
                pass

    page.on("response", su_risposta)
    try:
        for scheda in SCHEDE_YOUTUBE:
            prima = len(risposte)
            page.goto(
                f"https://studio.youtube.com/video/{vid}/analytics/{scheda}/period-default",
                wait_until="domcontentloaded",
            )
            if _aspetta(page, lambda: len(risposte) > prima, 20):
                page.wait_for_timeout(2000)  # le schede arrivano in piu' risposte
    finally:
        page.remove_listener("response", su_risposta)
    schede = schede_pulite(risposte)
    if not schede:
        raise SystemExit(f"youtube {vid}: Studio non ha mandato le schede di analisi. Pagina "
                         f"salvata in {leggi_account.dump(page, 'analitiche-youtube')}")
    return analitiche_youtube(schede), {"schede": schede}


def _riga(post: dict, analitiche: dict | None, grezzo: dict | None, guasto: str | None = None) -> dict:
    return {
        "id": post["id"],
        "url": post["url"],
        "analitiche": analitiche,
        "grezzo": grezzo,
        "guasto": guasto,
    }


def leggi(gruppo: str, piattaforma: str, solo: set[str], headless: bool) -> tuple[list[dict], list[str]]:
    profilo = registro.profilo_obbligatorio(gruppo, piattaforma)
    righe: list[dict] = []
    limiti: list[str] = []

    def per_post(page, post_list, funzione):
        for post in post_list:
            if solo and post["id"] not in solo:
                continue
            try:
                a, g = funzione(page, post["id"])
                righe.append(_riga(post, a, g))
            except SystemExit as exc:
                # Un post che non si legge non ferma gli altri, e resta nell'uscita
                # col suo guasto invece di sparire.
                log(str(exc))
                righe.append(_riga(post, None, None, str(exc)))
            log(f"{piattaforma} {post['id']}: letto")

    if piattaforma == "instagram" and instagram_api.ha_token(gruppo):
        # Dall'API, senza browser e senza la serratura del profilo. Il browser
        # serve solo se resta un reel senza numeri, e se non si apre non e' un
        # guasto della lettura: quei reel restano col motivo dato da Meta.
        righe, limiti = righe_instagram_api(instagram_api.reel_con_numeri(gruppo, solo))
        vuoti = sum(r["analitiche"] is None for r in righe)
        if vuoti:
            try:
                with serratura.presa(
                    serratura.nome_profilo(profilo),
                    attesa=leggi_account.ATTESA_PROFILO,
                    scaduta=leggi_account.SCADUTA_PROFILO,
                ):
                    web = leggi_account.leggi_instagram(profilo, registro.handle(gruppo, "instagram"), headless)
                riempiti = completa_dal_web(righe, web)
                limiti.append(
                    f"instagram: {vuoti} reel senza numeri dall'API, {riempiti} completati dal "
                    "web con views, like e commenti"
                )
            except (SystemExit, Exception) as exc:  # noqa: BLE001 - qualunque guasto vale «non completato»
                limiti.append(f"instagram: {vuoti} reel senza numeri dall'API, e il web non si legge ({str(exc)[:160]})")
        return righe, limiti

    with serratura.presa(
        serratura.nome_profilo(profilo),
        attesa=leggi_account.ATTESA_PROFILO,
        scaduta=leggi_account.SCADUTA_PROFILO,
    ):
        if piattaforma == "tiktok":
            with leggi_account.pagina_tiktok(profilo, headless) as page:
                post_list = leggi_account.post_da_studio(page, registro.handle(gruppo, "tiktok"))
                per_post(page, post_list, analisi_tiktok)
        elif piattaforma == "youtube":
            canale = registro.canale_id(gruppo)

            def lavoro(page):
                per_post(page, leggi_account.elenco_youtube(page, canale), analisi_youtube)

            leggi_account.sessione_youtube(profilo, canale, headless, lavoro)
        else:
            for post in leggi_account.leggi_instagram(profilo, registro.handle(gruppo, "instagram"), headless):
                if solo and post["id"] not in solo:
                    continue
                a = {"views": post["views"], "like": post["like"], "commenti": post["commenti"]}
                righe.append(_riga(post, a, post.get("grezzo")))
            limiti.append(LIMITE_INSTAGRAM)
    return righe, limiti


def main() -> None:
    ap = argparse.ArgumentParser(description="Analitiche di ogni post di un account")
    ap.add_argument("--gruppo", required=True, help="il gruppo di dashboard/identita.json")
    ap.add_argument("--piattaforma", required=True, choices=sorted(leggi_account.INTERPRETE))
    ap.add_argument("--solo", nargs="*", default=[], help="solo questi id di post")
    ap.add_argument("--vedi", action="store_true", help="con la finestra, per guardare")
    args = ap.parse_args()

    voce = registro.identita(args.gruppo, args.piattaforma) or {}
    try:
        righe, limiti = leggi(args.gruppo, args.piattaforma, set(args.solo), not args.vedi)
    except serratura.Occupata as exc:
        log(f"{args.piattaforma}: salto, profilo in uso — {exc}")
        sys.exit(leggi_account.PROFILO_OCCUPATO)
    except (registro.AccountSbagliato, RuntimeError) as exc:
        sys.exit(f"{args.piattaforma}: {exc}")

    json.dump(
        {
            "gruppo": args.gruppo,
            "piattaforma": args.piattaforma,
            "identita": voce.get("id"),
            "letto_il": datetime.now().isoformat(timespec="seconds"),
            "limiti": limiti,
            "post": righe,
        },
        sys.stdout,
        ensure_ascii=False,
    )
    log(f"{args.piattaforma}: {len(righe)} post")


if __name__ == "__main__":
    for _f in (sys.stdout, sys.stderr):
        try:
            _f.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    main()
