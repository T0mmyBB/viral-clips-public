#!/usr/bin/env python
"""
registro.py — da quale account si pubblica, e con quale profilo di browser.

    python registro.py                       # cosa sa, per gruppo e piattaforma
    python registro.py --gruppo business

Perche' esiste
--------------
Fino a ieri ogni agente aveva **una** cartella di profilo scritta nel codice
(`.browser-profile`, `.tiktok-canale_lab`, `.youtube-chrome`, `.whop-profile`),
perche' c'era un account per piattaforma e le due cose coincidevano. E' la
stessa specie di coincidenza del ritmo per account (vedi `ritmo.py`): vera per
caso, e smette di valere senza avvisare. Con tre gruppi da tre profili l'una
diventa falsa, e il modo in cui sarebbe fallita e' il peggiore possibile —
`orchestrator.pubblica_tiktok()` ripiegava su `.browser-profile` e assumeva
l'handle `canale_lab`, quindi una clip della nicchia business sarebbe uscita
**dall'account del laboratorio, senza un errore**.

Le identita' stanno gia' in `dashboard/identita.json`, con il campo
`profilo_browser` per ognuna: il modello dei dati era gia' giusto, era il codice
a ignorarlo. Qui si legge quel file e basta — non si aggiunge una seconda
verita', per la stessa ragione per cui `stato.py` non ne aggiunge una.

La guardia
----------
`controlla_handle()` e' la parte che conta davvero. Una clip pubblicata
sull'account sbagliato non torna indietro: si cancella, ma l'ha gia' vista
l'algoritmo di quel canale, ed e' esattamente il genere di danno che si scopre
settimane dopo senza poterlo attribuire. Quindi, prima di caricare, si confronta
l'handle **che il browser dice di avere** con quello che il registro si aspetta
e, se non coincidono, non si pubblica.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
REGISTRO = ROOT / "dashboard" / "identita.json"


class AccountSbagliato(RuntimeError):
    """Il browser e' loggato con un account che non e' quello previsto."""


def _leggi() -> dict:
    try:
        return json.loads(REGISTRO.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}


def identita(gruppo: str | None, piattaforma: str) -> dict | None:
    """L'identita' attiva di quel gruppo su quella piattaforma, se c'e'."""
    if not gruppo:
        return None
    piattaforma = (piattaforma or "").lower()
    for i in _leggi().get("identita", []):
        if (
            i.get("gruppo") == gruppo
            and (i.get("piattaforma") or "").lower() == piattaforma
            and i.get("attiva", True)
        ):
            return i
    return None


def piattaforme(gruppo: str | None) -> list[str]:
    """
    Le piattaforme su cui questo gruppo pubblica, in minuscolo.

    Una clip esce su tutte insieme — e' l'unita' della consegna, non la coppia
    clip-piattaforma — quindi chi vuole sapere «su quante deve uscire» lo chiede
    al gruppo, non al vecchio campo `platform` di una cartella, che sapeva solo
    di piattaforme singole perche' allora erano l'unica cosa che esisteva.
    """
    if not gruppo:
        return []
    for g in _leggi().get("gruppi", []):
        if g.get("id") == gruppo:
            return [p.lower() for p in g.get("piattaforme_attese", [])]
    return []


# La lingua che l'interfaccia del browser dichiara. `en-US` e non `it-IT`
# perche' le tre nicchie sono anglofone: il contenuto e' inglese, le pagine
# sono americane, e l'interfaccia in mezzo non ha ragione di essere italiana.
# I selettori la reggono gia' per costruzione — le etichette sono liste
# multilingue (`POST_LABELS`, `AVANTI`, `CONDIVIDI`, `trova(...)`) e quelle di
# Whop hanno l'inglese per primo, con almeno un `has-text("Submit clip")` che
# esiste **solo** in inglese.
LINGUA = "en-US"

# Il fuso invece resta europeo, ed e' la parte che sembra sbagliata e non lo
# e'. `locale` e `timezone_id` agiscono dentro la pagina — `Intl`, `Date`,
# `navigator.language` — mentre l'IP resta quello di casa: un fuso americano
# non fa sembrare americano nessuno, fa la coppia «orologio di New York,
# indirizzo di Milano», che e' l'euristica con cui si **rilevano** i proxy.
# Oggi il profilo e' coerente e spostare il solo fuso lo rende incoerente in
# cambio di niente. Il fuso di chi guarda e' gia' dove cambia qualcosa: negli
# orari di `publish/calendario.py`, che sono su ET.
FUSO = "Europe/Rome"


def ambiente() -> dict:
    """
    Lingua e fuso che ogni browser di pubblicazione dichiara.

    Sta qui e non nei cinque agenti perche' era gia' scritto cinque volte, e
    la copia che conta e' quella che **mancava**: il lancio TikTok di
    `orchestrator.pubblica_tiktok()` non passava ne' `locale` ne'
    `timezone_id`, quindi ereditava quelli di Windows — TikTok e Instagram,
    dallo stesso gruppo e a un minuto di distanza, dicevano due cose diverse.
    Un valore ripetuto diverge in silenzio; un valore solo no.

    Non prende il gruppo: un parametro che nessun dato riempie e' una promessa
    che qualcuno mantiene per sbaglio. Quando esistera' una nicchia non
    anglofona, quello e' il momento in cui il campo si aggiunge al registro.
    """
    return {"locale": LINGUA, "timezone_id": FUSO}


def profilo(gruppo: str | None, piattaforma: str, difetto: Path) -> Path:
    """
    La cartella del profilo browser da usare, assoluta.

    Senza gruppo, o senza identita' registrata, si torna al difetto storico
    dell'agente: chi lavora ancora a mano su un account solo continua a
    funzionare come prima. E' un ripiego dichiarato, non silenzioso — chi
    chiama lo dice a schermo.
    """
    voce = identita(gruppo, piattaforma)
    if not voce or not voce.get("profilo_browser"):
        return Path(difetto)
    p = Path(voce["profilo_browser"])
    return p if p.is_absolute() else (ROOT / p)


def profilo_obbligatorio(gruppo: str, piattaforma: str) -> Path:
    """
    Come `profilo()`, ma **senza ripiego**: se il gruppo e' stato chiesto e non
    ha un'identita' su quella piattaforma, si esce.

    Il ripiego silenzioso e' il difetto originale, non la cura: chiedere
    `--gruppo business` e ottenere il profilo del laboratorio significa aprire
    la finestra sull'account sbagliato con l'aria di aver fatto la cosa giusta.
    Meglio fermarsi e dire cosa manca.
    """
    voce = identita(gruppo, piattaforma)
    if not voce:
        gruppi = {g["id"] for g in _leggi().get("gruppi", [])}
        if gruppo not in gruppi:
            raise SystemExit(
                f"gruppo «{gruppo}» sconosciuto: {', '.join(sorted(gruppi)) or 'nessuno'}"
                f"\n  il registro e' {REGISTRO}"
            )
        raise SystemExit(
            f"il gruppo «{gruppo}» non ha ancora un'identita' {piattaforma} in "
            f"{REGISTRO.name}.\n"
            f"  Aggiungila (id, handle, profilo_browser, account_started) e rilancia:\n"
            f"  senza, questo comando aprirebbe il browser sul profilo di un altro account."
        )
    if not voce.get("profilo_browser"):
        raise SystemExit(
            f"l'identita' «{voce['id']}» non dice `profilo_browser` in {REGISTRO.name}"
        )
    return profilo(gruppo, piattaforma, Path(voce["profilo_browser"]))


def handle(gruppo: str | None, piattaforma: str) -> str | None:
    """Lo @handle atteso, senza chiocciola e in minuscolo."""
    voce = identita(gruppo, piattaforma)
    if not voce or not voce.get("handle"):
        return None
    return voce["handle"].lstrip("@").lower()


def controlla_handle(trovato: str | None, atteso: str | None, piattaforma: str) -> None:
    """
    Alza `AccountSbagliato` se il browser non e' l'account che ci aspettavamo.

    Il caso «non si sa» non e' un permesso: se il registro dice quale account
    dev'essere e il browser non sa dire chi e', non si pubblica. Il rilevamento
    dell'handle fallisce quando la sessione e' scaduta, che e' proprio quando
    un caricamento finirebbe dove non deve.

    **E «non so chi devo essere» non autorizza «non so nemmeno chi sono».** Con
    `atteso` vuoto — le cartelle nate prima dei gruppi, che un gruppo non ce
    l'hanno — qui non c'e' niente da confrontare, e la guardia usciva subito
    lasciando passare anche il caso in cui nemmeno il browser sa dire chi e'.
    Li' il chiamante ripiegava su un handle cablato, cioe' sul nome di un
    account che non c'entra: la clip usciva dal profilo aperto e il suo URL
    veniva cercato fra i video di un altro. Senza un nome non si sa dove sta
    finendo la clip, e non saperlo e' gia' abbastanza per fermarsi.
    """
    normale = (trovato or "").lstrip("@").lower()
    if not atteso:
        if not normale:
            raise AccountSbagliato(
                f"{piattaforma}: questa cartella non dice a che gruppo appartiene e il "
                f"browser non dice con quale account e' collegato. Non pubblico: non "
                f"saprei dove e' finita la clip. Rifai il login su questo profilo, "
                f"oppure scrivi il gruppo in publish.json."
            )
        return
    if not normale:
        raise AccountSbagliato(
            f"{piattaforma}: il registro si aspetta @{atteso} ma il browser non dice "
            f"con quale account e' collegato. Non pubblico: rifai il login su questo "
            f"profilo."
        )
    if normale != atteso:
        raise AccountSbagliato(
            f"{piattaforma}: collegato come @{normale}, ma questa cartella e' del "
            f"gruppo di @{atteso}. Non pubblico: e' il profilo browser sbagliato."
        )


# Le strade di ogni piattaforma. `api` e' l'API ufficiale della piattaforma
# stessa (`instagram_api.py`), `buffer` un servizio terzo (`buffer_agent.py`).
VIE = {
    "youtube": ("browser",),
    "tiktok": ("browser", "buffer"),
    "instagram": ("browser", "api"),
}


def via(gruppo: str | None, piattaforma: str) -> str:
    """
    Da quale strada esce una clip su quella piattaforma: `browser`, `buffer` o `api`.

    Lo dice `pubblica_via` nell'identita', e senza il campo e' `browser`: e' la
    strada su cui ogni identita' e' nata. Un valore che non e' fra quelli **di
    quella piattaforma** ferma: una strada scritta male non e' un permesso a
    prendere l'altra, perche' le strade non rendono uguale (PROGRESS.md, 30
    settembre 2026) e sbagliarla non da' nessun errore — la clip esce lo stesso.

    Per piattaforma e non in un elenco solo: `buffer` scritto su Instagram
    sarebbe uscito dal browser con `via: buffer` nel record, cioe' un confronto
    fra strade fatto su un'etichetta falsa.
    """
    voce = identita(gruppo, piattaforma) or {}
    valore = (voce.get("pubblica_via") or "browser").strip().lower()
    strade = VIE.get((piattaforma or "").lower(), ("browser",))
    if valore not in strade:
        raise SystemExit(
            f"l'identita' «{voce.get('id')}» dice `pubblica_via: {valore}` in "
            f"{REGISTRO.name}: su {piattaforma} le strade sono {', '.join(strade)}."
        )
    return valore


def sospesa(gruppo: str | None, piattaforma: str) -> str | None:
    """
    Perche' quell'identita' non pubblica adesso, o None se pubblica.

    Lo dice `pubblicazione_sospesa` nell'identita', col motivo scritto dentro.
    E' l'interruttore di una piattaforma intera, e sta nel registro perche' e'
    li' che si decide da dove e se un account pubblica: `sospesa` e `a_mano` di
    `publish.json` fermano una clip di una cartella, e una piattaforma ferma
    andrebbe riscritta clip per clip in ogni cartella, compresa quella che
    nasce domani.

    L'account resta attivo: le sue views si leggono ancora, e il calendario lo
    conta. Togliere il campo fa ripartire le clip rimaste indietro, dentro il
    ritmo.
    """
    motivo = (identita(gruppo, piattaforma) or {}).get("pubblicazione_sospesa")
    if not motivo:
        return None
    return str(motivo).strip() if isinstance(motivo, str) and motivo.strip() else "sospesa"


def buffer_canale(gruppo: str | None, piattaforma: str) -> str | None:
    """L'id del canale Buffer di quell'identita', se il registro lo sa."""
    return (identita(gruppo, piattaforma) or {}).get("buffer_canale") or None


def whop_handle(gruppo: str | None) -> str | None:
    """
    L'handle Whop atteso per il gruppo, senza chiocciola e in minuscolo.

    Sta sul **gruppo** e non fra le identita' perche' Whop non e' un posto dove
    si pubblica: e' il conto su cui si incassa, uno per nicchia perche' Whop
    collega una sola identita' per piattaforma.
    """
    for g in _leggi().get("gruppi", []):
        if g.get("id") == gruppo and g.get("whop_handle"):
            return g["whop_handle"].lstrip("@").lower()
    return None


def whop_profilo(gruppo: str | None) -> Path | None:
    """
    La cartella del profilo browser Whop di quel gruppo, assoluta.

    La regola normale e' `publish/.whop-<gruppo>`, che e' anche quella che
    `whop_agent.py` applica da solo quando gli si passa `--gruppo`. Il campo
    `whop_profilo` esiste per il caso che quella regola non copre: il
    laboratorio incassa su un profilo nato prima dei gruppi (`.whop-profile`),
    e rinominarlo vorrebbe dire rifare l'accesso a un conto che funziona.

    Torna `None` se il gruppo non e' nel registro: chi chiama deve accorgersene
    invece di puntare a una cartella vuota. Un profilo Whop inesistente non da'
    errore — apre un browser non collegato, e l'invio si perde con il video
    gia' online.
    """
    if not gruppo:
        return None
    for g in _leggi().get("gruppi", []):
        if g.get("id") == gruppo:
            p = Path(g.get("whop_profilo") or f"publish/.whop-{gruppo}")
            return p if p.is_absolute() else (ROOT / p)
    return None


def canale_id(gruppo: str | None) -> str | None:
    """L'id del canale YouTube atteso (`UC...`), se il registro lo sa."""
    voce = identita(gruppo, "youtube")
    return (voce or {}).get("canale_id")


def controlla_canale(trovato: str | None, atteso: str | None) -> None:
    """
    La stessa guardia di `controlla_handle`, ma per YouTube e sull'**id**.

    Il nome del canale non serve a niente qui: Studio mostra «MioCanale»
    mentre il canale e' `@MioCanale67`, quindi un confronto per nome
    bloccherebbe l'account giusto. L'id `UC...` sta nell'indirizzo su cui
    Studio atterra, e' unico e non lo traduce nessuno.
    """
    if not atteso:
        return
    if not trovato:
        raise AccountSbagliato(
            f"youtube: il registro si aspetta il canale {atteso} ma l'indirizzo di "
            f"Studio non dice quale canale sia aperto. Non pubblico: rifai il login "
            f"su questo profilo."
        )
    if trovato != atteso:
        raise AccountSbagliato(
            f"youtube: Studio e' aperto sul canale {trovato}, ma questa cartella e' "
            f"del canale {atteso}. Non pubblico: e' il profilo browser sbagliato."
        )


def quadro() -> list[dict]:
    """Una riga per identita' attiva: gruppo, piattaforma, handle, profilo, esiste."""
    fuori = []
    dati = _leggi()
    for g in dati.get("gruppi", []):
        for piattaforma in [p.lower() for p in g.get("piattaforme_attese", [])]:
            voce = identita(g["id"], piattaforma)
            p = profilo(g["id"], piattaforma, Path("(nessuno)"))
            fuori.append(
                {
                    "gruppo": g["id"],
                    "nicchia": g.get("nicchia"),
                    "piattaforma": piattaforma,
                    "identita": voce["id"] if voce else None,
                    "handle": (voce or {}).get("handle"),
                    "profilo": str(p) if voce else None,
                    "profilo_esiste": bool(voce) and p.is_dir(),
                }
            )
    return fuori


def main() -> None:
    ap = argparse.ArgumentParser(description="Chi pubblica cosa, e con quale profilo")
    ap.add_argument("--gruppo", default="", help="mostra solo questo gruppo")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    righe = [r for r in quadro() if not args.gruppo or r["gruppo"] == args.gruppo]
    if args.json:
        json.dump(righe, sys.stdout, ensure_ascii=False, indent=2)
        return
    if not righe:
        sys.exit("nessun gruppo: guarda dashboard/identita.json")
    print(f"{'gruppo':14s} {'piattaforma':11s} {'handle':20s} profilo")
    print("-" * 78)
    for r in righe:
        if not r["identita"]:
            print(f"{r['gruppo']:14s} {r['piattaforma']:11s} {'(da aprire)':20s} -")
            continue
        segno = "" if r["profilo_esiste"] else "   <- profilo assente: serve il login"
        # `handle: null` = registrato ma non ancora letto dal browser. La
        # guardia lo tratta gia' come «niente da confrontare»; qui va detto,
        # perche' un trattino sembrerebbe un'identita' rotta.
        chiocciola = ("@" + r["handle"].lstrip("@")) if r["handle"] else "(da rilevare)"
        print(
            f"{r['gruppo']:14s} {r['piattaforma']:11s} {chiocciola:20s} "
            f"{Path(r['profilo']).name}{segno}"
        )


if __name__ == "__main__":
    for _f in (sys.stdout, sys.stderr):
        try:
            _f.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    main()
