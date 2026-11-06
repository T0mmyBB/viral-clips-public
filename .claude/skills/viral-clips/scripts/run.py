"""Il driver della pipeline: prima prepara, poi renderizza.

    python run.py prepare "<url o percorso>" [--audio-lang en] [--campagna <uuid>]
    python run.py render work/<slug>

**Perche due comandi e non uno.** In mezzo c'e un giudizio: quali momenti valgano
una clip lo decide Claude leggendo, e un processo non puo restare bloccato ad
aspettare un giudizio che nessuno script sa produrre. `prepare` si ferma quando
c'e tutto per decidere; `render` riparte da `decisions.json`. Per l'utente resta
un comando solo — da il link e riceve le clip.

**Questo file non decide niente.** Non sceglie i momenti, non ordina le clip, non
scarta le deboli: mette in fila fasi che esistono gia. Se un giorno gli si volesse
insegnare a "valutare" le finestre, sarebbe il momento di fermarsi — e la cosa che
questa skill fa diversamente dagli strumenti in commercio.

Ogni fase ha la sua cache: rilanciare `prepare` dopo un'interruzione riprende da
dove si era fermato invece di ricominciare. `--force` la ignora.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import campagna
from check_material import as_text
from check_material import check as check_material
from common import (
    OUTPUT_DIR,
    WORK_DIR,
    emit,
    fmt_timecode,
    log,
    read_json,
    record_metric,
    sorveglia_alimentazione,
    work_file,
)

SCRIPTS = Path(__file__).resolve().parent

# Una riga per volta quando i render girano insieme: senza, due thread che
# scrivono si spezzano a meta riga.
_VOCE = threading.Lock()


def stage(
    name: str, script: str, *args: str, strict: bool = True, work: Path | None = None
) -> tuple[dict | None, float]:
    """Esegue uno script della pipeline e ne raccoglie il risultato.

    stdout viene catturato — e il JSON che serve a questo driver — mentre stderr
    scorre a schermo: su fasi che durano minuti un comando muto sembra bloccato,
    e la diagnostica dal vivo e l'unico modo per sapere che sta lavorando.

    Con `strict=False` un fallimento non ferma tutto. Serve al render: quando le
    clip sono trenta, perdere le altre ventinove perche una e andata storta e un
    danno molto peggiore dell'errore stesso.
    """
    log(f"\n=== {name}")
    cmd = [sys.executable, str(SCRIPTS / script), *args]
    started = time.time()
    proc = subprocess.run(
        cmd, stdout=subprocess.PIPE, text=True, encoding="utf-8", errors="replace"
    )
    elapsed = time.time() - started

    if proc.returncode != 0:
        if strict:
            raise SystemExit(
                f"\n'{name}' e fallito: {script} e uscito con {proc.returncode}.\n"
                f"La diagnostica qui sopra dice perche. Le fasi gia fatte restano: "
                f"rilanciando, riprende da qui."
            )
        log(f"  '{name}' e fallito ({proc.returncode}): vado avanti con le altre")
        return None, elapsed

    try:
        risultato = json.loads(proc.stdout)
    except json.JSONDecodeError:
        risultato = None

    # La misura si registra **qui**, dove gia esiste: il driver cronometra ogni
    # fase per stamparne il riepilogo, e scriverla su disco costa una riga. Le
    # fasi ripescate dalla cache si saltano — un tempo di 1.2 s non descrive
    # quanto costa trascrivere, descrive quanto costa non trascrivere.
    if work is not None and not (risultato or {}).get("cached"):
        record_metric(work, name, elapsed)

    return risultato, elapsed


def una_clip(
    work: Path,
    meta: dict,
    args: argparse.Namespace,
    slug: str,
    indice: int,
    totale: int,
    decise_il: float,
    silenzioso: bool,
) -> dict:
    """Renderizza una clip sola. Non solleva mai: l'esito sta nel valore.

    Esiste separata da `stage` perche il render ha due cose che le fasi lineari
    non hanno — il salto di cio che e gia pronto, e la possibilita di girare
    insieme ad altre. Con `--jobs > 1` la diagnostica va **raccolta e stampata a
    blocco**: lasciata scorrere dal vivo, tre render che scrivono insieme
    producono righe interlacciate che non si attribuiscono piu a nessuna clip.
    """
    destinazione = (
        (Path(args.out_dir) / f"{slug}.mp4")
        if args.out_dir
        else (OUTPUT_DIR / meta.get("slug", work.name) / f"{slug}.mp4")
    )

    # Una clip gia renderizzata dopo l'ultima modifica alle decisioni non va
    # rifatta: su trenta clip un'interruzione costerebbe mezz'ora di encode
    # per riottenere gli stessi file. Se decisions.json e piu recente, invece,
    # il giudizio e cambiato e la clip si rifa.
    if not args.force and destinazione.exists() and destinazione.stat().st_mtime >= decise_il:
        return {
            "slug": slug,
            "stato": "saltata",
            "secondi": 0.0,
            "diagnostica": f"=== clip {indice}/{totale}: {slug} — gia renderizzata, la salto",
        }

    cmd = [
        sys.executable,
        str(SCRIPTS / "render_clip.py"),
        str(work),
        "--decisions",
        args.decisions,
        "--slug",
        slug,
        "--auto-track",
        "--out",
        str(destinazione),
    ]
    if args.preview:
        cmd.append("--preview")

    intestazione = f"=== clip {indice}/{totale}: {slug}"
    if not silenzioso:
        log("\n" + intestazione)

    started = time.time()
    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE if silenzioso else None,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    elapsed = time.time() - started

    try:
        risultato = json.loads(proc.stdout)
    except json.JSONDecodeError:
        risultato = None

    diagnostica = intestazione
    if silenzioso and proc.stderr:
        diagnostica += "\n" + proc.stderr.rstrip()

    if silenzioso:
        # **Un segnale di vita a ogni clip che finisce.** La diagnostica completa
        # si legge in fondo, ma tenerla *tutta* per la fine vuol dire mezz'ora di
        # comando muto su una consegna da trenta clip — e un comando muto sembra
        # bloccato. Provato: e proprio cosi che si comportava alla prima prova su
        # una serie intera.
        esito = "ok" if proc.returncode == 0 else f"FALLITA ({proc.returncode})"
        with _VOCE:
            log(f"  [{indice}/{totale}] {slug:<40} {elapsed:6.1f}s  {esito}")

    if proc.returncode != 0 or not risultato:
        # Una clip che fallisce non porta giu le altre: quando sono trenta,
        # perdere le altre ventinove e un danno molto peggiore dell'errore.
        return {
            "slug": slug,
            "stato": "fallita",
            "secondi": elapsed,
            "diagnostica": diagnostica
            + f"\n  fallita ({proc.returncode}): vado avanti con le altre",
        }

    return {
        "slug": slug,
        "stato": "fatta",
        "secondi": elapsed,
        "risultato": risultato,
        "diagnostica": diagnostica,
    }


def report(times: list[tuple[str, float]], totale: float | None = None) -> None:
    """Il riepilogo dei tempi. `totale` va passato quando le fasi si sono
    sovrapposte: sommare le durate di lavori concorrenti da un numero che non
    corrisponde a nessun orologio."""
    log("")
    for name, seconds in times:
        log(f"  {name:<28} {seconds:6.1f}s")
    log(f"  {'totale':<28} {(sum(t for _, t in times) if totale is None else totale):6.1f}s")


def aggancia_campagna(work: Path, args: argparse.Namespace) -> dict | None:
    """`--campagna`, piu' `--brief`/`--fornitore`/`--titolo` se la campagna e' fuori catalogo."""
    if not args.campagna:
        return None
    dati = campagna.stage(work, args.campagna, **campagna.da_argomenti(args))
    log(
        f"campagna agganciata: {dati['titolo']}"
        + (
            f" ({dati['brief_righe']} righe di brief, "
            f"{len(dati['da_leggere'])} da leggere prima di scegliere)"
            if dati["brief"]
            else " — ATTENZIONE: nessun brief, ne in radar/briefs/ ne con --brief"
        )
        + (" — FUORI CATALOGO" if dati["origine"] == "fuori catalogo" else "")
    )
    for url in dati["da_scaricare_a_mano"]:
        log(f"  materiale da scaricare a mano, il link scade: {url}")
    return dati


def prepare(args: argparse.Namespace) -> None:
    times: list[tuple[str, float]] = []

    ingest_args = [args.source, "--work-root", args.work_root]
    if args.audio_lang:
        ingest_args += ["--audio-lang", args.audio_lang]
    partito = time.time()
    meta, elapsed = stage("ingest (audio + proxy)", "ingest.py", *ingest_args)
    times.append(("ingest", elapsed))
    if not meta:
        raise SystemExit("ingest.py non ha restituito i metadati: mi fermo qui.")
    work = Path(meta["work_dir"])

    # L'ingest si registra a parte perche la cartella di lavoro la si conosce
    # solo adesso — da un link il nome viene dal titolo del video. E si registra
    # solo se ha fatto davvero il lavoro: l'audio riscritto durante questa
    # esecuzione e la prova, mentre `meta.json` viene riscritto comunque.
    try:
        audio = work_file(work, meta.get("audio"), "audio.wav")
        if audio and audio.stat().st_mtime >= partito:
            record_metric(work, "ingest (audio + proxy)", elapsed)
    except (OSError, KeyError, TypeError):
        pass

    # I paletti della campagna si agganciano **subito**, appena la cartella
    # esiste: non servono alla trascrizione, ma se si aggancia solo alla fine
    # una preparazione interrotta a meta lascia una cartella che non sa di che
    # campagna e, e quel legame non lo ricostruisce piu nessuno. Costa la
    # lettura di due JSON.
    aggancia_campagna(work, args)

    # `--threads` si passa **solo se l'utente l'ha chiesto**. Altrimenti lo decide
    # `transcribe.py` dai core fisici: due default da tenere allineati a mano sono
    # esattamente il modo in cui un numero digitato sopravvive alla macchina per
    # cui era giusto.
    transcribe_args = [str(work)]
    if args.threads:
        transcribe_args += ["--threads", str(args.threads)]
    if args.language:
        transcribe_args += ["--language", args.language]
    if args.model:
        transcribe_args += ["--model", args.model]
    if args.force:
        transcribe_args.append("--force")
    transcript, elapsed = stage("trascrizione", "transcribe.py", *transcribe_args, work=work)
    times.append(("trascrizione", elapsed))

    for name, script in (("segnali", "signals.py"), ("volti (prescansione)", "faces.py")):
        extra = ["--force"] if args.force else []
        _, elapsed = stage(name, script, str(work), *extra, work=work)
        times.append((name, elapsed))

    report(times)

    duration = meta.get("duration") or 0
    log(f"\npronto: {meta['title']}")
    log(
        f"  {fmt_timecode(duration)} di video, "
        f"{(transcript or {}).get('word_count', '?')} parole, "
        f"lingua {(transcript or {}).get('language', '?')}"
    )
    log(f"  {work}")

    # Le anomalie si dicono due volte, e non e ridondanza: qui perche si vedano
    # subito, e in cima alla vista di windows.py perche arrivino ancora in tempo
    # nel momento in cui si sceglie. Un avviso stampato solo qui, sopra decine di
    # righe di diagnostica, e un avviso che nessuno legge.
    note = check_material(work)
    if note:
        log("")
        log(as_text(note))

    # Lo stesso vale per i paletti: qui si dice solo **se** ci sono, perche il
    # testo intero arriva in cima alla vista, che e dove serve. Dirlo qui e
    # dirlo li non e ridondanza — questa riga fa notare l'assenza, che e il caso
    # in cui si sta per scegliere alla cieca senza saperlo.
    if not campagna.load(work):
        log("\nNessuna campagna agganciata: la vista non portera nessun paletto.")
        log(f"  python campagna.py {work} --id <uuid della campagna>")

    log("\nOra tocca a Claude: leggere e scegliere.")
    log(f"  python windows.py {work}")
    if meta.get("source_pending"):
        # Il pesante non serve per decidere, ma servira al render: portarselo
        # avanti adesso vuol dire non sommare le due attese.
        log("\nIl sorgente a piena risoluzione non e ancora qui. Mentre si legge:")
        log(f"  python ingest.py {work} --fetch-source")

    emit(
        {"work_dir": str(work), "slug": meta["slug"], "seconds": round(sum(t for _, t in times), 1)}
    )


def render(args: argparse.Namespace) -> None:
    work = Path(args.work_dir)
    meta = read_json(work / "meta.json")
    times: list[tuple[str, float]] = []

    checked, elapsed = stage(
        "controllo delle decisioni", "validate.py", str(work), "--decisions", args.decisions
    )
    times.append(("validate", elapsed))
    if not checked:
        raise SystemExit("validate.py non ha restituito l'elenco delle clip.")

    # L'ordine e quello di decisions.json, cioe quello editoriale. `--limit N`
    # prende le prime N: "le migliori N" e "le prime N" sono la stessa cosa
    # proprio perche nessuno le riordina.
    slugs = list(checked["slugs"])
    if args.only:
        wanted = set(args.only)
        mancanti = wanted - set(slugs)
        if mancanti:
            raise SystemExit(f"slug assenti da {args.decisions}: {', '.join(sorted(mancanti))}")
        slugs = [s for s in slugs if s in wanted]
    if args.limit:
        slugs = slugs[: args.limit]

    log(f"\n{len(slugs)} clip da renderizzare: {', '.join(slugs)}")

    decise_il = (work / args.decisions).stat().st_mtime
    prodotte, saltate, fallite = [], [], []

    jobs = max(1, args.jobs)
    if jobs > 1:
        log(
            f"{jobs} render insieme. Attenzione: su una consegna vera da 38 clip "
            f"**--jobs 2 e stato il 7.7% piu lento** della sequenza; il guadagno "
            f"visto su sei clip non regge alla scala reale. I numeri in PROGRESS.md."
        )

    lavoro = [(i, slug) for i, slug in enumerate(slugs, 1)]
    partito = time.time()

    def esegui(coppia: tuple[int, str]) -> dict:
        i, slug = coppia
        return una_clip(work, meta, args, slug, i, len(slugs), decise_il, silenzioso=jobs > 1)

    if jobs > 1:
        with ThreadPoolExecutor(max_workers=jobs) as pool:
            esiti = list(pool.map(esegui, lavoro))
    else:
        esiti = [esegui(c) for c in lavoro]

    for esito in esiti:
        if esito["stato"] == "saltata":
            log("\n" + esito["diagnostica"])
            saltate.append(esito["slug"])
            continue

        if jobs > 1:
            log("\n" + esito["diagnostica"])
        elif esito["stato"] == "fallita":
            log(f"  '{esito['slug']}' e fallita: vado avanti con le altre")

        times.append((esito["slug"], esito["secondi"]))

        if esito["stato"] == "fallita":
            fallite.append(esito["slug"])
            continue

        # Il render si misura **per secondo di clip**: "33 secondi a clip" non
        # significa niente senza sapere se la clip durava 20 secondi o 60.
        #
        # E si misura **solo in sequenza**. Sotto concorrenza il tempo di una
        # clip non e piu il suo costo ma la sua latenza — a 3 job le stesse
        # clip passano da 32 a 95 secondi l'una mentre il totale scende —
        # quindi registrarlo scriverebbe in PROGRESS.md un numero che dice il
        # contrario di quello che sembra dire. Stessa famiglia della fase
        # ripescata dalla cache: si registra solo cio che misura quello che
        # dichiara.
        if not args.preview and jobs == 1:
            record_metric(
                work,
                "render per clip",
                esito["secondi"],
                durata_clip=esito["risultato"].get("duration"),
            )
        prodotte.append(esito["risultato"])

    trascorso = time.time() - partito
    if times:
        report(times, totale=trascorso if jobs > 1 else None)
        if jobs > 1:
            log("  (i tempi per clip sono latenze, non costi: si sovrappongono)")

    log("")
    for r in prodotte:
        log(
            f"  {Path(r['path']).name:<28} {r['duration']:>5.1f}s  "
            f"{r['size_mb']:>6.2f} MB  {r['resolution']}"
        )
    if saltate:
        log(f"  {len(saltate)} gia pronte, non rifatte (--force per rifarle)")
    if fallite:
        quante = (
            "1 clip non e uscita" if len(fallite) == 1 else f"{len(fallite)} clip non sono uscite"
        )
        log(f"\n{quante}: {', '.join(fallite)}")
        log("La diagnostica e qui sopra. Le altre sono a posto.")

    if prodotte or saltate:
        log(
            f"\nLa consegna non e finita: manca clips.md accanto agli mp4 — hook, "
            f"didascalia, hashtag e il perche di ogni scelta. Lo scrive Claude, poi\n"
            f"  python validate.py {work} --consegna\n"
            f"dice se e completa."
        )

    emit(
        {
            "clips": prodotte,
            "skipped": saltate,
            "failed": fallite,
            "jobs": jobs,
            "seconds": round(trascorso, 1),
        }
    )
    if fallite:
        sys.exit(1)


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Driver della pipeline: prepare si ferma per far decidere, "
        "render riparte da decisions.json"
    )
    sub = ap.add_subparsers(dest="comando", required=True)

    p = sub.add_parser("prepare", help="da un link o un file a tutto cio che serve per scegliere")
    p.add_argument("source", help="URL oppure percorso di un file video")
    p.add_argument("--work-root", default=str(WORK_DIR))
    p.add_argument(
        "--audio-lang",
        default=None,
        help="traccia audio da scaricare sui video doppiati (es. 'en')",
    )
    p.add_argument(
        "--language",
        default=None,
        help="lingua del parlato per la trascrizione. Se non la indichi, "
        "viene dedotta dalla traccia audio scelta.",
    )
    p.add_argument(
        "--model",
        default=None,
        help="modello Whisper. Senza, transcribe.py usa 'large-v3-turbo' per "
        "entrambe le lingue: con i modelli piu piccoli il testo esce "
        "bucato, non solo con refusi.",
    )
    p.add_argument(
        "--threads",
        type=int,
        default=None,
        help="thread della trascrizione. Senza, li decide transcribe.py dai "
        "**core fisici** della macchina: il valore giusto e quello che "
        "si ottiene non passando questo flag.",
    )
    p.add_argument(
        "--campagna",
        default=None,
        help="id della campagna: l'uuid del catalogo del radar, o quello del "
        "fornitore se e' fuori catalogo (allora passa anche --brief). Con questo, il "
        "brief del committente viene copiato nella cartella di lavoro "
        "e i suoi paletti escono in cima alla vista di windows.py: "
        "senza, si sceglie senza le regole della campagna.",
    )
    campagna.aggiungi_argomenti_fuori_catalogo(p)
    p.add_argument("--force", action="store_true", help="rifa le fasi gia in cache")
    p.set_defaults(func=prepare)

    r = sub.add_parser("render", help="renderizza le clip decise in decisions.json")
    r.add_argument("work_dir")
    r.add_argument("--decisions", default="decisions.json")
    r.add_argument(
        "--limit",
        type=int,
        default=None,
        help="renderizza solo le prime N clip, nell'ordine in cui sono state scelte",
    )
    r.add_argument("--only", nargs="+", default=None, help="solo questi slug")
    r.add_argument("--out-dir", default=None, help="cartella di uscita (default: output/<video>/)")
    r.add_argument(
        "--jobs",
        type=int,
        default=1,
        help="quante clip renderizzare insieme (default 1, ed e il valore "
        "giusto). Su 6 clip 2 sembrava guadagnare il 14%%, ma su una "
        "consegna vera da 38 e uscito il 7.7%% piu lento: la prova "
        "corta non misurava la consegna. Con piu di 1 il tempo per "
        "clip non viene registrato, perche sotto concorrenza e una "
        "latenza e non un costo.",
    )
    r.add_argument(
        "--preview", action="store_true", help="anteprime veloci a bassa qualita, per la revisione"
    )
    r.add_argument("--force", action="store_true", help="rifa anche le clip gia renderizzate")
    r.set_defaults(func=render)
    return ap


def main() -> None:
    sorveglia_alimentazione()  # per dire, a ogni fase misurata, se stava in carica
    args = parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
