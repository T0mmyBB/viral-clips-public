"""Misura il moto della camera virtuale di una clip dal suo piano, fotogramma per fotogramma.

    python check_camera.py work/<slug> --slug 01-il-rospo
    python check_camera.py work/<slug> --piano crop_1350_1421.sendcmd

**Perche esiste.** Il crop che salta era l'unico difetto probabile della pipeline che
nessuno strumento misurava: SKILL.md diceva «si vede solo scorrendo la clip». Il 14
settembre 2026 la tentazione era ritoccare i parametri della camera; rigiocando invece
il file `sendcmd` come lo esegue ffmpeg e uscito che i micro-salti — decine o centinaia
al minuto — venivano da difetti di esecuzione — comandi a 10 Hz, larghezza e altezza arrotondate
ognuna per conto suo — che nessun parametro avrebbe toccato. Una stima sui pixel della
clip renderizzata non l'avrebbe isolato: su un crop fermo misura comunque un pixel di
moto, perche e la persona che si muove.

**Cosa rigioca.** `sendcmd` applica un comando al primo fotogramma con tempo maggiore o
uguale, quindi il fotogramma `k` usa l'ultimo comando con `t <= k / fps`. Da la finestra
`(w, h, x, y)` di ogni fotogramma d'uscita, e da due finestre consecutive lo spostamento
del contenuto ai quattro angoli del quadro, in pixel d'uscita: un numero esatto, senza il
rumore della scena.

**Cosa conta.**

- **micro-salti**: un fotogramma che si sposta fra due fermi, fuori da uno stacco. Sono il
  tremolio. Quelli **sugli** stacchi del girato o sui tagli dei silenzi (`bordi` nel piano)
  sono la finestra che cambia apposta, dove l'immagine cambia comunque, e si contano a
  parte;
- **inciampi**: un fotogramma fermo in mezzo a un movimento — la panoramica a gradini;
- **tremolio d'aspetto**: fotogrammi in cui l'altezza non e quella che la larghezza
  imporrebbe, cioe il quadro si deforma di un pixel per un istante;
- **fermi**: la quota di fotogrammi in cui la camera non si muove.

Esce con 1 se i micro-salti o il tremolio d'aspetto superano le soglie qui sotto. Le
soglie vengono dalla misura, non dal gusto: vedi `SOGLIA_MICRO_AL_MINUTO`.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from common import emit, log, read_json

OUT_W, OUT_H = 1080, 1920

# Sotto mezzo pixel d'uscita un fotogramma e fermo: l'occhio non lo vede, e gli
# arrotondamenti del filtro stanno tutti li sotto.
FERMO_PX = 0.5
# Un cambio di finestra entro un fotogramma da un bordo e voluto.
TOLLERANZA_BORDO_FOTOGRAMMI = 1
# Un salto isolato di almeno questi pixel d'uscita e la finestra che cambia
# posizione, non un tic. Misurato su 83 piani: nei 72 di prima del 14 settembre
# i salti isolati sono in gran parte sotto i 12 px (gradini d'arrotondamento e
# di comandi a 10 Hz); negli 11 di dopo, 100 su 110 sono sopra i 48 px — la
# posizione per inquadratura che cambia sullo stacco. Serve quando il piano non
# porta i `bordi`; quando li porta decide la vicinanza allo stacco.
SALTO_FINESTRA_PX = 48.0

# **Le soglie sono misurate**, con questo script, su tutti gli 83 piani in `work/`
# il 15 settembre 2026. Gli 11 resi dopo il 14 settembre: da 0 a 6,1 micro-salti
# al minuto. I 49 di prima con la camera che si muove: da 32,3 a 331,4, mediana
# 165,7 (gli altri 23 hanno un comando solo, camera ferma, e fanno 0). Dieci sta
# nel buco fra i due gruppi. Il tremolio d'aspetto non ha una quota accettabile:
# 49 piani vecchi su 72 ce l'hanno, nessuno dei nuovi — con l'altezza ricavata
# dalla larghezza non capita.
SOGLIA_MICRO_AL_MINUTO = 10.0
SOGLIA_ASPETTO = 0

RIGA_RE = re.compile(
    r"^\s*([\d.]+)\s+\w+\s+w\s+(\d+),\s*\w+\s+h\s+(\d+),\s*\w+\s+x\s+(-?\d+),\s*\w+\s+y\s+(-?\d+);"
)


def leggi_sendcmd(testo: str) -> list[tuple[float, int, int, int, int]]:
    """Le righe del piano: `(t, w, h, x, y)` in ordine di tempo."""
    righe = []
    for linea in testo.splitlines():
        m = RIGA_RE.match(linea)
        if m:
            t, w, h, x, y = m.groups()
            righe.append((float(t), int(w), int(h), int(x), int(y)))
    return sorted(righe)


def finestre(
    comandi: list[tuple[float, int, int, int, int]], fps: float, n: int, pari: bool = False
) -> list[tuple[int, int, int, int]]:
    """La finestra di ogni fotogramma d'uscita, come la applica `sendcmd`.

    `pari` rigioca il filtro senza `exact=1`, che allinea la posizione a numeri
    pari: serve solo per le clip rese prima che il render lo passasse.
    """
    uscita = []
    i = -1
    for k in range(n):
        t = k / fps
        while i + 1 < len(comandi) and comandi[i + 1][0] <= t + 1e-9:
            i += 1
        _, w, h, x, y = comandi[max(i, 0)]
        if pari:
            x, y = x - x % 2, y - y % 2
        uscita.append((w, h, x, y))
    return uscita


def spostamento(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    """Di quanti pixel d'uscita si sposta il contenuto fra due finestre, all'angolo peggiore."""
    wa, ha, xa, ya = a
    wb, hb, xb, yb = b
    peggiore = 0.0
    for u in (0.0, 1.0):
        dx = (xb + u * wb) - (xa + u * wa)
        dy = (yb + u * hb) - (ya + u * ha)
        peggiore = max(peggiore, abs(dx) * OUT_W / wb, abs(dy) * OUT_H / hb)
    return peggiore


def misura(
    fin: list[tuple[int, int, int, int]],
    fps: float,
    base: tuple[int, int],
    bordi: list[float] | None,
) -> dict:
    """I numeri del moto: micro-salti, inciampi, tremolio d'aspetto, fermi."""
    n = len(fin)
    moto = [0.0] + [spostamento(fin[k - 1], fin[k]) for k in range(1, n)]
    vicino = set()
    for b in bordi or []:
        c = int(round(b * fps))
        vicino.update(range(c - TOLLERANZA_BORDO_FOTOGRAMMI, c + TOLLERANZA_BORDO_FOTOGRAMMI + 1))

    micro, sugli_stacchi, cambi_finestra, inciampi = [], [], [], []
    for k in range(1, n - 1):
        prima, ora, dopo = moto[k - 1] >= FERMO_PX, moto[k] >= FERMO_PX, moto[k + 1] >= FERMO_PX
        if ora and not prima and not dopo:
            if k in vicino:
                sugli_stacchi.append(k)
            elif moto[k] >= SALTO_FINESTRA_PX:
                cambi_finestra.append(k)
            else:
                micro.append(k)
        elif not ora and prima and dopo and k not in vicino:
            inciampi.append(k)

    bw, bh = base
    aspetto = [k for k, (w, h, _, _) in enumerate(fin) if abs(h - w * bh / bw) > 1.0]
    minuti = max(n / fps / 60.0, 1e-9)
    return {
        "fotogrammi": n,
        "secondi": round(n / fps, 2),
        "micro_salti": len(micro),
        "micro_al_minuto": round(len(micro) / minuti, 1),
        "cambi_sugli_stacchi": len(sugli_stacchi),
        "cambi_di_finestra_altrove": len(cambi_finestra),
        "inciampi": len(inciampi),
        "tremolio_aspetto": len(aspetto),
        "fermi_percento": round(100.0 * sum(1 for m in moto if m < FERMO_PX) / max(n, 1), 1),
        "primi_micro_s": [round(k / fps, 2) for k in micro[:10]],
        "bordi_noti": bordi is not None,
    }


def durata_uscita(work: Path, start: float, end: float) -> float:
    """Quanto dura la clip montata: il piano dei tagli la sa, altrimenti e start-end."""
    tagli = work / f"tagli_{start:.0f}_{end:.0f}.json"
    if tagli.exists():
        tenuti = read_json(tagli).get("tenuti") or []
        if tenuti:
            return sum(b - a for a, b in tenuti)
    return end - start


def main() -> None:
    ap = argparse.ArgumentParser(description="Misura il moto della camera dal piano del crop")
    ap.add_argument("work_dir")
    ap.add_argument("--slug", help="quale clip di decisions.json")
    ap.add_argument("--decisions", default="decisions.json")
    ap.add_argument("--piano", help="il file .sendcmd, se non lo si vuole dedurre dalla clip")
    ap.add_argument(
        "--fps",
        type=float,
        default=None,
        help="fps d'uscita, se il piano non lo dichiara (piani prima del 14 settembre)",
    )
    ap.add_argument(
        "--pari",
        action="store_true",
        help="rigioca il crop senza exact=1, per le clip rese prima che il render lo passasse",
    )
    args = ap.parse_args()

    work = Path(args.work_dir)
    if args.piano:
        cmd = work / args.piano if not Path(args.piano).is_absolute() else Path(args.piano)
        diag_path = cmd.with_suffix(".json")
        diag = read_json(diag_path) if diag_path.exists() else {}
        start, end = diag.get("start", 0.0), diag.get("end")
    elif args.slug:
        decisions = read_json(work / args.decisions)
        clip = next((c for c in decisions.get("clips", []) if c.get("slug") == args.slug), None)
        if clip is None:
            raise SystemExit(f"slug '{args.slug}' assente da {args.decisions}")
        start, end = float(clip["start"]), float(clip["end"])
        cmd = work / f"crop_{int(start)}_{int(end)}.sendcmd"
        diag_path = cmd.with_suffix(".json")
        diag = read_json(diag_path) if diag_path.exists() else {}
    else:
        raise SystemExit("serve --slug oppure --piano")

    if not cmd.exists():
        raise SystemExit(f"{cmd} non trovato: la clip e stata resa con --auto-track?")

    comandi = leggi_sendcmd(cmd.read_text(encoding="utf-8"))
    if not comandi:
        raise SystemExit(f"{cmd.name} non contiene comandi di crop leggibili")

    fps = args.fps or diag.get("fps_out")
    if not fps:
        fps = 30.0
        log("  il piano non dichiara gli fps d'uscita: uso 30 (passa --fps per un altro valore)")
    durata = durata_uscita(work, start, end) if end is not None else comandi[-1][0] + 1.0
    base = tuple(diag.get("base_window") or comandi[0][1:3])
    bordi = diag.get("bordi")
    if bordi is None:
        log(
            f"  il piano non ha i bordi (reso prima del 15 settembre): i salti sopra "
            f"{SALTO_FINESTRA_PX:g} px si contano come cambi di finestra, non come tic"
        )

    fin = finestre(comandi, fps, max(2, int(round(durata * fps))), pari=args.pari)
    esito = misura(fin, fps, base, bordi)

    log(f"{cmd.name}: {esito['secondi']} s a {fps:g} fps")
    log(f"  micro-salti      {esito['micro_salti']:>5}  ({esito['micro_al_minuto']} al minuto)")
    log(f"  cambi sugli stacchi {esito['cambi_sugli_stacchi']:>2}")
    log(
        f"  cambi di finestra altrove {esito['cambi_di_finestra_altrove']:>2}"
        + ("  (stacchi non noti: probabilmente sono quelli)" if bordi is None else "")
    )
    log(f"  inciampi         {esito['inciampi']:>5}")
    log(f"  tremolio d'aspetto {esito['tremolio_aspetto']:>3}")
    log(f"  camera ferma     {esito['fermi_percento']:>5} % dei fotogrammi")
    if esito["primi_micro_s"]:
        log(f"  primi micro-salti a {esito['primi_micro_s']} s della clip")

    problemi = []
    if esito["micro_al_minuto"] > SOGLIA_MICRO_AL_MINUTO:
        problemi.append(
            f"{esito['micro_al_minuto']} micro-salti al minuto (soglia {SOGLIA_MICRO_AL_MINUTO:g})"
        )
    if esito["tremolio_aspetto"] > SOGLIA_ASPETTO:
        problemi.append(f"{esito['tremolio_aspetto']} fotogrammi con il quadro deformato")
    for p in problemi:
        log(f"  DIFETTO: {p}")

    emit({"piano": str(cmd), "fps": fps, **esito, "ok": not problemi})
    if problemi:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
