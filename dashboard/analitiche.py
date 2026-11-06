#!/usr/bin/env python
"""
analitiche.py — le analitiche di tutti i post, di tutti gli account, in un comando.

    python dashboard/analitiche.py aggiorna                    # tutti gli account attivi
    python dashboard/analitiche.py aggiorna --gruppo business --piattaforma tiktok
    python dashboard/analitiche.py mostra                      # l'ultima lettura di ogni post
    python dashboard/analitiche.py mostra --piattaforma tiktok --json

`aggiorna` lancia `publish/leggi_analitiche.py` per ogni identita' attiva del
registro, con l'interprete della sua piattaforma, e scrive la tabella `analitiche`
di `storico.sqlite` — una riga per post e per lettura, cosi' la storia resta — piu'
il grezzo dell'ultima lettura in `dashboard/analitiche/`, per i campi che oggi
nessuno interpreta. `mostra` legge solo il disco: non apre nessun browser.

Le cifre sono quelle della piattaforma, non dedotte: una quota che Studio non da'
resta vuota, e `mostra` scrive «—», non zero.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "publish"))

import leggi_account  # noqa: E402
import metriche  # noqa: E402

LETTORE = HERE.parent / "publish" / "leggi_analitiche.py"
GREZZO = HERE / "analitiche"
# Ogni post costa una pagina (TikTok) o quattro schede (YouTube), venti-quaranta
# secondi: un account da trenta post sta sotto il quarto d'ora, e oltre l'ora e' appeso.
ATTESA_LETTORE = 3600

FONTI_YOUTUBE = {
    "SHORTS": "feed Shorts",
    "YT_SEARCH": "ricerca",
    "EXT_URL": "esterna",
    "YT_CHANNEL": "pagina canale",
    "UNKNOWN_MOBILE_OR_DIRECT": "diretta o sconosciuta",
    "YT_RELATED": "video suggeriti",
    "SUBSCRIBER": "iscrizioni",
    "NOTIFICATION": "notifiche",
    "PLAYLIST": "playlist",
    "YT_OTHER_PAGE": "altre pagine YouTube",
    "END_SCREEN": "schermata finale",
    "HASHTAGS": "hashtag",
    "SOUND_PAGE": "pagina del suono",
}


# ------------------------------------------------------------ aggiorna


def salva(con, letture: dict, abbinati: dict) -> list[dict]:
    """Scrive le righe di una lettura e torna cio' che si e' scritto, post per post."""
    piattaforma, quando = letture["piattaforma"], letture["letto_il"]
    fuori = []
    for post in letture["post"]:
        clip = abbinati.get((piattaforma, post["id"])) or {}
        post_id = clip.get("url") or post["url"]
        dati = post.get("analitiche")
        con.execute(
            "INSERT INTO analitiche (post_id, cartella, clip_id, identita, piattaforma, quando, "
            "dati, guasto) VALUES (?,?,?,?,?,?,?,?)",
            (
                post_id,
                clip.get("cartella"),
                clip.get("clip_id"),
                letture.get("identita"),
                piattaforma,
                quando,
                json.dumps(dati, ensure_ascii=False) if dati is not None else None,
                post.get("guasto"),
            ),
        )
        fuori.append({"post_id": post_id, "cartella": clip.get("cartella"), "letto": dati is not None})
    con.commit()
    return fuori


def aggiorna(gruppo: str | None, piattaforma: str | None) -> int:
    abbinati = metriche.abbinamenti()
    con = metriche.db()
    guasti = 0
    GREZZO.mkdir(exist_ok=True)
    for ident in metriche.da_leggere():
        if (gruppo and ident["gruppo"] != gruppo) or (piattaforma and ident["piattaforma"] != piattaforma):
            continue
        p = ident["piattaforma"]
        interprete = leggi_account.INTERPRETE[p]
        nome = f"{ident['id']} ({p})"
        if not interprete.exists():
            print(f"  {nome}: manca l'interprete {interprete}", file=sys.stderr)
            guasti += 1
            continue
        print(f"  leggo {nome}...", file=sys.stderr, flush=True)
        try:
            esito = subprocess.run(
                [str(interprete), str(LETTORE), "--gruppo", ident["gruppo"], "--piattaforma", p],
                cwd=str(LETTORE.parent),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=ATTESA_LETTORE,
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired:
            print(f"  {nome}: nessuna risposta in {ATTESA_LETTORE} s", file=sys.stderr)
            guasti += 1
            continue
        coda = (esito.stderr or "").strip().splitlines()
        if esito.returncode == leggi_account.PROFILO_OCCUPATO:
            print(f"  {nome}: saltato, profilo in uso da una pubblicazione", file=sys.stderr)
            continue
        if esito.returncode != 0:
            print(f"  {nome}: {coda[-1] if coda else 'codice ' + str(esito.returncode)}", file=sys.stderr)
            guasti += 1
            continue
        letture = json.loads(esito.stdout)
        scritte = salva(con, letture, abbinati)
        # Il grezzo dell'ultima lettura, uno per post: sovrascritto a ogni giro.
        for post in letture["post"]:
            if post.get("grezzo") is not None:
                (GREZZO / f"{p}-{post['id']}.json").write_text(
                    json.dumps(post["grezzo"], ensure_ascii=False), encoding="utf-8"
                )
        for limite in letture.get("limiti") or []:
            print(f"  {nome}: {limite}", file=sys.stderr)
        non_letti = sum(not s["letto"] for s in scritte)
        print(
            f"  {nome}: {len(scritte)} post" + (f", {non_letti} non letti" if non_letti else ""),
            file=sys.stderr,
        )
    con.close()
    return guasti


# ------------------------------------------------------------ mostra


def ultime(piattaforma: str | None = None) -> list[dict]:
    """L'ultima lettura di ogni post, con la clip a cui appartiene."""
    con = metriche.db()
    righe = con.execute(
        """
        SELECT a.post_id, a.cartella, a.clip_id, a.identita, a.piattaforma, a.quando, a.dati, a.guasto
        FROM analitiche a
        JOIN (SELECT post_id, MAX(quando) AS q FROM analitiche GROUP BY post_id) u
          ON a.post_id = u.post_id AND a.quando = u.q
        ORDER BY a.piattaforma, a.identita, a.cartella, a.clip_id, a.post_id
        """
    ).fetchall()
    con.close()
    fuori = []
    for post_id, cartella, clip_id, identita, p, quando, dati, guasto in righe:
        if piattaforma and p != piattaforma:
            continue
        fuori.append(
            {
                "post_id": post_id,
                "cartella": cartella,
                "clip_id": clip_id,
                "identita": identita,
                "piattaforma": p,
                "quando": quando,
                "dati": json.loads(dati) if dati else None,
                "guasto": guasto,
            }
        )
    return fuori


def _prima(d: dict | None, nomi: dict | None = None) -> list | None:
    """La voce piu' grande di una ripartizione, come [nome, quota]; «Others» non e' un paese."""
    voci = [(k, v) for k, v in (d or {}).items() if v and k not in ("Others", "Other")]
    if not voci:
        return None
    k, v = max(voci, key=lambda kv: kv[1])
    return [(nomi or {}).get(k, k), v]


def sintesi(riga: dict) -> dict:
    """Un post in cinque numeri, per la tabella del pannello: quanto, quanto tiene, da dove.

    La tenuta non e' la stessa misura sulle tre piattaforme e non si finge che lo
    sia: su TikTok e' chi resta al terzo secondo, su YouTube la percentuale media
    guardata, su Instagram chi non scorre via nei primi tre secondi (l'API da'
    la quota di chi scorre via: qui se ne mostra il resto). Il nome viaggia col
    numero.
    """
    d = riga.get("dati") or {}
    p = riga["piattaforma"]
    tenuta = None
    if p == "tiktok" and (d.get("permanenza") or {}).get("3") is not None:
        tenuta = ["al 3° s", d["permanenza"]["3"]]
    elif p == "youtube" and d.get("percentuale_guardata") is not None:
        tenuta = ["guardato", d["percentuale_guardata"]]
    elif p == "instagram" and d.get("scorre_via_3s") is not None:
        tenuta = ["oltre i 3 s", round(1 - d["scorre_via_3s"], 4)]
    return {
        "piattaforma": p,
        "identita": riga.get("identita"),
        "cartella": riga.get("cartella"),
        "clip_id": riga.get("clip_id"),
        "post_id": riga["post_id"],
        "quando": riga.get("quando"),
        "letto": riga.get("dati") is not None,
        "guasto": riga.get("guasto"),
        "views": d.get("views"),
        "tempo_medio_s": d.get("tempo_medio_s"),
        "tenuta": tenuta,
        "fonte": _prima(d.get("fonti"), FONTI_YOUTUBE if p == "youtube" else None),
        "paese": _prima(d.get("paesi")),
    }


def _pc(v) -> str:
    return "—" if v is None else f"{v * 100:.0f}%"


def _n(v) -> str:
    return "—" if v is None else f"{v:,}".replace(",", ".")


def _s(v) -> str:
    return "—" if v is None else f"{v:.1f} s".replace(".", ",")


def _quote(d: dict | None, nomi: dict | None = None, quante: int = 3) -> str:
    if not d:
        return "—"
    voci = sorted(((k, v) for k, v in d.items() if v), key=lambda kv: -kv[1])[:quante]
    if not voci:
        return "—"
    return " · ".join(f"{(nomi or {}).get(k, k)} {_pc(v)}" for k, v in voci)


def descrivi(riga: dict) -> list[str]:
    """Le righe di testo di un post: i numeri che rispondono a «perche' ha fatto cosi'»."""
    titolo = riga["post_id"]
    if riga["cartella"]:
        titolo = f"{riga['cartella'][:32]} {riga['clip_id']} — {riga['post_id']}"
    out = [f"  {titolo}"]
    d = riga["dati"]
    if d is None:
        out.append(f"    non letto: {riga['guasto'] or 'motivo sconosciuto'}")
        return out
    p = riga["piattaforma"]
    if p == "tiktok":
        perm = (d.get("permanenza") or {}).get("3")
        out.append(
            f"    views {_n(d.get('views'))} · unici {_n(d.get('spettatori_unici'))} · "
            f"medio {_s(d.get('tempo_medio_s'))} · al 3° s {_pc(perm)} · "
            f"in fondo {_pc(d.get('completamento'))} · follower +{_n(d.get('follower_nuovi'))}"
        )
        out.append(f"    fonti: {_quote(d.get('fonti'))}")
        out.append(f"    paesi: {_quote(d.get('paesi'))}")
        if d.get("penalita"):
            out.append(f"    penalita': {json.dumps(d['penalita'], ensure_ascii=False)}")
    elif p == "youtube":
        out.append(
            f"    views {_n(d.get('views'))} · coinvolte {_n(d.get('views_coinvolte'))} · "
            f"unici {_n(d.get('spettatori_unici'))} · medio {_s(d.get('tempo_medio_s'))} · "
            f"guardato {_pc(d.get('percentuale_guardata'))} · "
            f"guardato invece di scorrere {_pc(d.get('guardato_invece_di_scorrere'))}"
        )
        out.append(f"    fonti: {_quote(d.get('fonti'), FONTI_YOUTUBE)}")
        out.append(f"    paesi: {_quote(d.get('paesi'))}")
    else:
        out.append(
            f"    views {_n(d.get('views'))} · like {_n(d.get('like'))} · commenti {_n(d.get('commenti'))}"
        )
        # Il resto c'e' solo se la lettura e' passata dall'API di Meta: dal web
        # questi campi non esistono, e una riga di trattini direbbe il contrario.
        if any(d.get(k) is not None for k in ("spettatori_unici", "tempo_medio_s", "scorre_via_3s", "salvataggi")):
            out.append(
                f"    unici {_n(d.get('spettatori_unici'))} · medio {_s(d.get('tempo_medio_s'))} · "
                f"scorre via nei primi 3 s {_pc(d.get('scorre_via_3s'))} · "
                f"salvataggi {_n(d.get('salvataggi'))} · condivisioni {_n(d.get('condivisioni'))}"
            )
    return out


def mostra(piattaforma: str | None, come_json: bool) -> None:
    righe = ultime(piattaforma)
    if come_json:
        json.dump(righe, sys.stdout, ensure_ascii=False, indent=1)
        return
    if not righe:
        print("Nessuna analitica letta: lancia prima `analitiche.py aggiorna`.")
        return
    gruppo_corrente = None
    for r in righe:
        chiave = (r["piattaforma"], r["identita"])
        if chiave != gruppo_corrente:
            gruppo_corrente = chiave
            print(f"\n{r['piattaforma'].upper()} — {r['identita']} (letto il {r['quando'][:16]})")
            if r["piattaforma"] == "instagram":
                print(
                    "  (dal web solo views, like e commenti; dall'API di Meta anche portata, "
                    "tempo medio e chi scorre via. Fonti e paesi per reel non li da' nessuno dei due)"
                )
        print("\n".join(descrivi(r)))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    sub = ap.add_subparsers(dest="cosa", required=True)
    a = sub.add_parser("aggiorna", help="legge le analitiche dalle piattaforme")
    a.add_argument("--gruppo")
    a.add_argument("--piattaforma", choices=sorted(leggi_account.INTERPRETE))
    m = sub.add_parser("mostra", help="l'ultima lettura di ogni post, dal disco")
    m.add_argument("--piattaforma", choices=sorted(leggi_account.INTERPRETE))
    m.add_argument("--json", action="store_true")
    args = ap.parse_args()
    if args.cosa == "aggiorna":
        sys.exit(1 if aggiorna(args.gruppo, args.piattaforma) else 0)
    mostra(args.piattaforma, args.json)


if __name__ == "__main__":
    for _f in (sys.stdout, sys.stderr):
        try:
            _f.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    main()
