"""Grafiche animate sopra la clip: i modelli di `grafiche/modelli`, resi da HyperFrames.

    python grafiche.py --prova parola "cinque milioni" --out prova.mov

**Modelli fissi, scelta di Claude.** Lo stile e sempre lo stesso — il font dei
sottotitoli, il giallo dell'hook — perche e quello a rendere riconoscibili le
clip, e perche un modello scritto una volta non si rompe quando la consegna gira
da sola sul server. Quale modello, dove e con che testo lo decide Claude in
`decisions.json` (campo `grafiche` di ogni clip, SKILL.md); qui si esegue.

**Una sola generazione di encode resta vera.** La grafica esce trasparente in
ProRes 4444 (quasi senza perdita, ed e solo la grafica) e `render_clip.py` la
appoggia sul girato dentro lo stesso filtergraph, come il logo.

**Il motore non tocca la rete.** HyperFrames di serie manda statistiche d'uso,
controlla gli aggiornamenti e scarica un suo Chrome: `AMBIENTE` spegne i primi
due, e il Chrome lo si trova fra quelli che Playwright ha gia scaricato per la
pubblicazione — se non c'e ci si ferma, perche col percorso indicato il motore
non ripiega sul download. I modelli caricano font e GSAP dalla cartella, mai da
un indirizzo. Le ragioni stanno in CLAUDE.md, «Grafiche animate».

**Le misure dei testi le fa Python, non il browser.** Dentro la pagina lo script
gira prima che il font sia caricato e misura il carattere di ripiego: la scritta
usciva dal quadro. Qui la larghezza si legge dal TTF, come per i sottotitoli.

**Una grafica che non si rende si salta, non ferma la clip.** E una decorazione:
una clip senza grafica si pubblica, una clip che non c'e no. Il salto si scrive
nel risultato del render (`grafiche_saltate`) e su stderr.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from fractions import Fraction
from itertools import combinations
from pathlib import Path

from common import FONTS_DIR, SKILL_ROOT, emit, log

GRAFICHE_DIR = SKILL_ROOT / "grafiche"
MODELLI_DIR = GRAFICHE_DIR / "modelli"
BASE_CSS = GRAFICHE_DIR / "assets" / "base.css"
CLI = GRAFICHE_DIR / "node_modules" / "hyperframes" / "bin" / "hyperframes.mjs"
GSAP = GRAFICHE_DIR / "node_modules" / "gsap" / "dist" / "gsap.min.js"
FONT = FONTS_DIR / "ArchivoBlack-Regular.ttf"

# La tela della grafica e una fascia, non il quadro intero: meno pixel da
# catturare per fotogramma. Copre il centro del 9:16 — da 640 a 1400 — cioe la
# zona dei sottotitoli (y 1290), che durante la grafica si sospendono. Sopra
# resta l'hook (250), sotto il logo (1500) e l'interfaccia delle app.
TELA_W, TELA_H = 1080, 760
TELA_Y = 640

DURATA_MIN, DURATA_MAX = 1.2, 6.0
# Fra una grafica e la successiva: due di fila sono un montaggio, non un accento.
STACCO_MIN = 2.0
# Quanto lontano da `at` si cerca la `parola` a cui agganciarsi.
AGGANCIO_S = 3.0
TIMEOUT_S = 600

AMBIENTE = {
    "HYPERFRAMES_NO_TELEMETRY": "1",
    "DO_NOT_TRACK": "1",
    "HYPERFRAMES_NO_UPDATE_CHECK": "1",
    "HYPERFRAMES_NO_AUTO_INSTALL": "1",
    "HYPERFRAMES_SKIP_SKILLS": "1",
}

ACCENTO_RE = re.compile(r"\*([^*]+)\*")
NUMERO_RE = re.compile(r"^(\D*?)(\d+)(?:([.,])(\d+))?(\D*)$")


# ------------------------------------------------------------------------------
# Misure del testo
# ------------------------------------------------------------------------------


def larghezza(testo: str, size: float) -> float:
    """Larghezza CSS in px: unita del font per `size / unitsPerEm`.

    `make_subs.text_width` usa la scala di libass (winAscent + winDescent), che
    per il browser e sbagliata del 10-20%: qui serve quella di CSS.
    """
    from make_subs import font_metrics

    m = font_metrics("Archivo Black")
    if m is None:
        return len(testo) * size * 0.75
    head = m.tables["head"][0]
    upem = int.from_bytes(m.data[head + 18 : head + 20], "big")
    return m.width(testo, 1.0) * m.em_height / upem * size


def adatta(testo: str, base: float, massima: float) -> int:
    """La dimensione del font: `base`, o meno se il testo non ci sta."""
    w = larghezza(testo, base)
    return int(base if w <= massima else base * massima / w)


def nudo(testo: str) -> str:
    return ACCENTO_RE.sub(r"\1", testo)


def righe_bilanciate(parole: list[str], max_righe: int, base: float, massima: float):
    """Spezza in righe con la piu larga piu stretta possibile.

    Si prende il minor numero di righe che sta in `massima` a dimensione
    piena; oltre le quattro parole almeno due righe, perche una riga sola da
    sei parole a 1080 px esce in corpo piccolo. Le parole sono poche (il campo
    e limitato), quindi si provano tutte le spezzature.
    """
    n = len(parole)
    migliore = None
    for k in range(1, min(max_righe, n) + 1):
        if k == 1 and n > 4 and max_righe > 1:
            continue
        for tagli in combinations(range(1, n), k - 1):
            bordi = (0, *tagli, n)
            righe = [parole[bordi[i] : bordi[i + 1]] for i in range(k)]
            w = max(larghezza(" ".join(nudo(p) for p in r).upper(), base) for r in righe)
            if migliore is None or w < migliore[0]:
                migliore = (w, righe)
        if migliore and migliore[0] <= massima:
            break
    assert migliore is not None, "nessuna parola"
    return migliore[1], migliore[0]


# ------------------------------------------------------------------------------
# I modelli
# ------------------------------------------------------------------------------


def _parola(g: dict) -> dict:
    testo = g["testo"].upper()
    return {"testo": testo, "size": adatta(testo, 150, 820)}


def _frase(g: dict) -> dict:
    parole = g["testo"].split()
    righe, w = righe_bilanciate(parole, 3, 96, 940)
    size = int(96 if w <= 940 else 96 * 940 / w)
    return {
        "righe": [
            [{"t": nudo(p).upper(), "a": bool(ACCENTO_RE.search(p))} for p in r] for r in righe
        ],
        "size": size,
    }


def _numero(g: dict) -> dict:
    testo = g["numero"].strip()
    m = NUMERO_RE.match(testo)
    dati = {
        "testo": testo,
        "conta": bool(m),
        "size": adatta(testo, 250, 940),
        "etichetta": (g.get("etichetta") or "").upper(),
    }
    if m:
        intero, sep, decimali = m.group(2), m.group(3) or "", m.group(4) or ""
        dati.update(
            prefisso=m.group(1),
            suffisso=m.group(5),
            valore=float(f"{intero}.{decimali}" if decimali else intero),
            decimali=len(decimali),
            separatore=sep,
        )
    if dati["etichetta"]:
        dati["size_etichetta"] = adatta(dati["etichetta"], 64, 940)
    return dati


def _prima_dopo(g: dict) -> dict:
    prima, dopo = g["prima"].upper(), g["dopo"].upper()
    return {
        "prima": prima,
        "dopo": dopo,
        "size_prima": adatta(prima, 120, 820),
        "size_dopo": adatta(dopo, 140, 800),
    }


def _elenco(g: dict) -> dict:
    voci = [v.upper() for v in g["voci"]]
    size = min(adatta(v, 80, 800) for v in voci)
    return {"voci": voci, "size": size}


def _bocciature(g: dict) -> dict:
    n = int(g["quante"])
    titolo, testo = g["titolo"].upper(), g["testo"].upper()
    finale = (g.get("finale") or "").upper()
    dati = {
        "titolo": titolo,
        "size_titolo": adatta(titolo, 64, 940),
        "testo": testo,
        "size_timbro": adatta(testo, 54, 230),
        "quante": n,
        "colonne": min(n, 3),
        "finale": finale,
    }
    if finale:
        dati["size_finale"] = adatta(finale, 92, 940)
    return dati


def _giorni(g: dict) -> dict:
    testo = g["numero"].strip()
    etichetta = (g.get("etichetta") or "").upper()
    dati = {
        "testo": testo,
        "size": adatta(testo, 230, 940),
        "etichetta": etichetta,
        "valore": int(testo),
        "tacche": 48,
    }
    if etichetta:
        dati["size_etichetta"] = adatta(etichetta, 64, 940)
    return dati


# campi: obbligatorio -> lunghezza massima. Le lunghezze sono geometria come per
# l'hook: oltre, il testo si rimpicciolisce fino a non leggersi.
MODELLI: dict[str, dict] = {
    "parola": {
        "campi": {"testo": 18},
        "durata": lambda g: 1.6,
        "layout": _parola,
        "uso": "una o due parole timbrate su una fascia gialla: la parola che fa la battuta",
    },
    "frase": {
        "campi": {"testo": 60},
        "durata": lambda g: min(4.0, max(1.8, 0.9 + 0.28 * len(g["testo"].split()))),
        "layout": _frase,
        "uso": "la frase ad effetto, parola per parola, con una *parola* in giallo",
    },
    "numero": {
        "campi": {"numero": 10},
        "facoltativi": {"etichetta": 28},
        "durata": lambda g: 2.4,
        "layout": _numero,
        "uso": "una cifra che sale fino al valore detto, con un'etichetta sotto",
    },
    "prima_dopo": {
        "campi": {"prima": 14, "dopo": 14},
        "durata": lambda g: 2.8,
        "layout": _prima_dopo,
        "uso": "un valore barrato che diventa un altro: da quanto a quanto",
    },
    "elenco": {
        "campi": {"voci": 22},
        "durata": lambda g: 1.2 + 1.0 * len(g.get("voci") or []),
        "layout": _elenco,
        "uso": "da due a tre voci che compaiono una alla volta",
    },
    "bocciature": {
        "campi": {"titolo": 22, "testo": 12, "quante": 1},
        "facoltativi": {"finale": 22},
        "durata": lambda g: round(0.3 + 0.28 * int(g["quante"]) + (1.5 if g.get("finale") else 1.0), 2),
        "layout": _bocciature,
        "uso": "tentativi respinti: un riquadro numerato per volta col timbro rosso, e il risultato",
    },
    "giorni": {
        "campi": {"numero": 5},
        "facoltativi": {"etichetta": 28},
        "durata": lambda g: 3.0,
        "layout": _giorni,
        "uso": "un conteggio di giorni: la cifra sale e una striscia di tacche si accende con lei",
    },
}


def durata(g: dict) -> float:
    if g.get("durata") is not None:
        return float(g["durata"])
    return round(MODELLI[g["modello"]]["durata"](g), 2)


def difetti(g: dict, i: int = 0) -> list[str]:
    """Cosa impedisce di rendere questa grafica. Lo chiama `validate.check_clip`."""
    dove = f"grafica {i + 1}"
    modello = g.get("modello")
    if modello not in MODELLI:
        return [f"{dove}: modello '{modello}' sconosciuto (ci sono: {', '.join(MODELLI)})"]
    out: list[str] = []
    spec = MODELLI[modello]
    if not isinstance(g.get("at"), (int, float)):
        out.append(f"{dove}: manca 'at', l'istante del sorgente in cui parte")
    for campo, massimo in {**spec["campi"], **spec.get("facoltativi", {})}.items():
        valore = g.get(campo)
        if valore is None or valore == "" or valore == []:
            if campo in spec["campi"]:
                out.append(f"{dove} ({modello}): manca '{campo}'")
            continue
        valori = valore if isinstance(valore, list) else [valore]
        for v in valori:
            if not isinstance(v, str) or not v.strip():
                out.append(f"{dove} ({modello}): '{campo}' dev'essere testo")
            elif len(nudo(v)) > massimo:
                out.append(
                    f"{dove} ({modello}): '{campo}' ha {len(nudo(v))} caratteri, "
                    f"il massimo e {massimo}"
                )
    if modello == "elenco" and isinstance(g.get("voci"), list) and not 2 <= len(g["voci"]) <= 3:
        out.append(f"{dove} (elenco): le voci sono {len(g['voci'])}, servono da 2 a 3")
    if modello == "bocciature" and str(g.get("quante", "")) not in tuple("2345678"):
        out.append(f"{dove} (bocciature): 'quante' dev'essere una cifra da 2 a 8")
    if modello == "giorni" and not str(g.get("numero", "")).strip().isdigit():
        out.append(f"{dove} (giorni): 'numero' dev'essere un intero, senza altro")
    if modello == "frase" and isinstance(g.get("testo"), str):
        accenti = len(ACCENTO_RE.findall(g["testo"]))
        if accenti > 1:
            out.append(f"{dove} (frase): {accenti} parole tra *asterischi*, al massimo una")
    if out:
        return out
    d = durata(g)
    if not DURATA_MIN <= d <= DURATA_MAX:
        out.append(f"{dove}: durata {d:.1f}s fuori da {DURATA_MIN}-{DURATA_MAX}s")
    return out


# ------------------------------------------------------------------------------
# Quando: dal sorgente alla clip montata
# ------------------------------------------------------------------------------


def istante(g: dict, words: list[dict]) -> float:
    """L'istante del sorgente in cui la grafica parte.

    `at` viene dalla vista di Claude, e i suoi tempi sono arrotondati; se c'e
    `parola`, la grafica parte sull'inizio di quella parola detta piu vicino ad
    `at`. Cosi la cifra compare quando la si sente, non mezzo secondo prima.
    """
    at = float(g["at"])
    cercata = str(g.get("parola") or "").strip().lower().strip(".,!?;:\"'")
    if not cercata:
        return at
    vicine = [
        w
        for w in words
        if abs(w["start"] - at) <= AGGANCIO_S
        and w["word"].strip().lower().strip(".,!?;:\"'()") == cercata
    ]
    if not vicine:
        raise ValueError(f"'{g['parola']}' non e detta entro {AGGANCIO_S:.0f}s da {at:.2f}")
    return min(vicine, key=lambda w: abs(w["start"] - at))["start"]


def piano(
    grafiche: list[dict], words: list[dict], start: float, end: float, tenuti: list
) -> tuple[list[dict], list[str]]:
    """Le grafiche nel tempo della clip montata, in ordine. E i problemi trovati.

    Una grafica che cade dentro un silenzio tolto parte all'inizio del tratto
    successivo; una che sborda oltre la fine si accorcia, e sotto il minimo si
    salta. Due che si accavallano: si tiene la prima.
    """
    import tagli

    tenuti = [tuple(t) for t in tenuti] if tenuti else [(0.0, end - start)]
    durata_clip = sum(b - a for a, b in tenuti)
    voci, problemi = [], []
    for i, g in enumerate(grafiche):
        try:
            t_src = istante(g, words) - start
        except ValueError as e:
            problemi.append(f"grafica {i + 1}: {e}")
            continue
        t = tagli.to_output(tenuti, t_src)
        if t is None:
            dopo = [o for o, (a, _) in zip(tagli.offsets(tenuti), tenuti) if a > t_src]
            if not dopo:
                problemi.append(f"grafica {i + 1}: cade dopo l'ultima parola della clip")
                continue
            t = dopo[0]
        d = min(durata(g), durata_clip - t - 0.1)
        if d < DURATA_MIN:
            problemi.append(f"grafica {i + 1}: a {t:.1f}s restano {d:.1f}s di clip")
            continue
        voci.append({**g, "indice": i, "t": round(t, 3), "durata": round(d, 2)})

    voci.sort(key=lambda v: v["t"])
    tenute: list[dict] = []
    for v in voci:
        if tenute and v["t"] < tenute[-1]["t"] + tenute[-1]["durata"] + STACCO_MIN:
            problemi.append(
                f"grafica {v['indice'] + 1}: a {v['t']:.1f}s e troppo vicina alla "
                f"grafica {tenute[-1]['indice'] + 1} (servono {STACCO_MIN:.0f}s fra le due)"
            )
            continue
        tenute.append(v)
    return tenute, problemi


# ------------------------------------------------------------------------------
# Il render
# ------------------------------------------------------------------------------


def trova_chrome() -> Path | None:
    """Il chrome-headless-shell piu recente fra quelli scaricati da Playwright.

    Si trova, non si scrive: sul portatile sta sotto LOCALAPPDATA, sul server
    sotto ~/.cache, e la versione cambia a ogni aggiornamento di Playwright.
    """
    radici = []
    if os.environ.get("LOCALAPPDATA"):
        radici.append(Path(os.environ["LOCALAPPDATA"]) / "ms-playwright")
    radici.append(Path.home() / ".cache" / "ms-playwright")
    nomi = ("chrome-headless-shell.exe", "chrome-headless-shell")

    def versione(p: Path) -> int:
        m = re.search(r"-(\d+)$", p.name)
        return int(m.group(1)) if m else 0

    for radice in radici:
        cartelle = sorted(radice.glob("chromium_headless_shell-*"), key=versione, reverse=True)
        for c in cartelle:
            for exe in c.glob("*/*"):
                if exe.name in nomi and exe.is_file():
                    return exe
    return None


def fps_razionale(fps: float) -> str:
    """23.976 -> 24000/1001: la grafica deve andare al passo del girato."""
    f = Fraction(fps).limit_denominator(1001)
    return str(f.numerator) if f.denominator == 1 else f"{f.numerator}/{f.denominator}"


def impronta(v: dict, fps: str) -> str:
    h = hashlib.sha1()
    h.update(json.dumps([v["modello"], v["dati"], v["durata"], fps], sort_keys=True).encode())
    for f in (MODELLI_DIR / f"{v['modello']}.html", BASE_CSS):
        h.update(f.read_bytes())
    return h.hexdigest()[:12]


def rendi(cartella: Path, modello: str, dati: dict, d: float, fps: str, uscita: Path) -> float:
    """Una grafica in ProRes 4444 trasparente. Ritorna i secondi impiegati."""
    chrome = trova_chrome()
    if chrome is None:
        raise RuntimeError(
            "nessun chrome-headless-shell di Playwright: si installa con "
            "`python -m playwright install chromium-headless-shell`"
        )
    if not CLI.exists():
        raise RuntimeError(f"HyperFrames non installato: npm ci in {GRAFICHE_DIR}")
    node = shutil.which("node")
    if node is None:
        raise RuntimeError("node non trovato nel PATH")

    shutil.rmtree(cartella, ignore_errors=True)
    (cartella / "assets").mkdir(parents=True)
    html = (MODELLI_DIR / f"{modello}.html").read_text(encoding="utf-8")
    html = html.replace("__DURATA__", f"{d:.3f}").replace("__FPS__", fps)
    (cartella / "index.html").write_text(html, encoding="utf-8")
    for f in (BASE_CSS, GSAP, FONT):
        shutil.copy2(f, cartella / "assets" / f.name)
    (cartella / "variabili.json").write_text(
        json.dumps({"dati": json.dumps(dati, ensure_ascii=False)}, ensure_ascii=False),
        encoding="utf-8",
    )

    env = {**os.environ, **AMBIENTE, "HYPERFRAMES_BROWSER_PATH": str(chrome)}
    cmd = [
        node,
        str(CLI),
        "render",
        ".",
        "--format",
        "mov",
        "--fps",
        fps,
        "--variables-file",
        "variabili.json",
        "--output",
        str(uscita.resolve()),
        "--quiet",
    ]
    t0 = time.monotonic()
    proc = subprocess.run(
        cmd,
        cwd=str(cartella),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=TIMEOUT_S,
    )
    if proc.returncode != 0 or not uscita.exists():
        coda = "\n".join((proc.stderr or proc.stdout).strip().splitlines()[-20:])
        raise RuntimeError(f"HyperFrames ha fallito ({modello}):\n{coda}")
    return time.monotonic() - t0


def prepara(
    work: Path,
    grafiche: list[dict],
    words: list[dict],
    start: float,
    end: float,
    tenuti: list,
    fps: float,
) -> dict:
    """Rende le grafiche di una clip e dice a `render_clip` dove e quando metterle.

    Ritorna `sovrapposte` (file, istante e durata nel tempo della clip montata),
    `pause` (gli intervalli in cui i sottotitoli tacciono) e `saltate`. Il file
    di una grafica gia resa con gli stessi dati si riusa: rirenderizzare una
    clip non rifa le grafiche.
    """
    voci, saltate = piano(grafiche, words, start, end, tenuti)
    fr = fps_razionale(fps)
    dest = work / "grafiche"
    sovrapposte = []
    for v in voci:
        v["dati"] = MODELLI[v["modello"]]["layout"](v)
        nome = f"{v['modello']}_{impronta(v, fr)}"
        mov = dest / f"{nome}.mov"
        if not mov.exists():
            try:
                secondi = rendi(dest / nome, v["modello"], v["dati"], v["durata"], fr, mov)
                log(f"  grafica {v['modello']} a {v['t']:.1f}s: resa in {secondi:.0f}s")
            except (RuntimeError, subprocess.TimeoutExpired) as e:
                saltate.append(f"grafica {v['indice'] + 1} ({v['modello']}): {e}")
                continue
            finally:
                shutil.rmtree(dest / nome, ignore_errors=True)
        else:
            log(f"  grafica {v['modello']} a {v['t']:.1f}s: gia resa")
        sovrapposte.append(
            {"file": mov, "t": v["t"], "durata": v["durata"], "modello": v["modello"]}
        )
    for s in saltate:
        log(f"  ATTENZIONE, grafica saltata — {s}")
    return {
        "sovrapposte": sovrapposte,
        "pause": [(s["t"], s["t"] + s["durata"]) for s in sovrapposte],
        "saltate": saltate,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Rende un modello di grafica, per provarlo")
    ap.add_argument("--prova", nargs="+", metavar=("MODELLO", "JSON"), required=True,
                    help='modello e campi in JSON, es. parola \'{"testo": "boom"}\'')
    ap.add_argument("--out", required=True)
    ap.add_argument("--fps", type=float, default=30.0)
    args = ap.parse_args()

    modello = args.prova[0]
    g = {"modello": modello, "at": 0.0, **(json.loads(args.prova[1]) if len(args.prova) > 1 else {})}
    errori = difetti(g)
    if errori:
        raise SystemExit("\n".join(errori))
    dati = MODELLI[modello]["layout"](g)
    out = Path(args.out)
    secondi = rendi(out.with_suffix(""), modello, dati, durata(g), fps_razionale(args.fps), out)
    shutil.rmtree(out.with_suffix(""), ignore_errors=True)
    emit({"path": str(out), "secondi": round(secondi, 1), "dati": dati})


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
