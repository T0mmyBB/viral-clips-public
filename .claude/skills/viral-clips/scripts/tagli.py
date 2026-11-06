"""I tagli dei silenzi dentro una clip: quali tratti restano, e dove finisce ogni istante.

`decisions.json` fissa i bordi di una clip; dentro, il parlato ha le sue pause, e
quelle lunghe sono il segno piu riconoscibile di un taglio fatto da una macchina
invece che da un montatore. Qui si decide quali togliere. Il render le toglie
**nello stesso passaggio ffmpeg** (trim + concat dentro il filtergraph): nessun
file intermedio, una sola generazione di encode.

Solo libreria standard: il livello audio lo misura il chiamante e passa una
funzione. Le regole, tutte conservative, vengono da due fonti — il pianificatore
di hotclip e le trappole gia pagate qui:

- **Si toglie solo cio che e senza parole E quieto.** Il buco fra due parole non
  e silenzio per definizione: aprendo `audio.wav`, sulle clip gia consegnate, i
  buchi avevano spesso energia pari al parlato — musica, stacchi sonori, folla,
  il colpo di montaggio che prepara la battuta. Il transcript non lo sa dire,
  l'audio si.
- **Le risate e gli applausi non si toccano**, nemmeno se il transcript tace: la
  pausa prima e dopo una battuta e il ritmo della battuta.
- **Si lascia respiro ai due lati.** I tempi per parola di Whisper sbagliano di
  qualche decina di millisecondi: tagliare sul valore scritto mangia l'attacco
  della consonante o la coda della parola. E un taglio che attacca una frase
  all'altra senza fiato e il difetto opposto, «troppo magro», che si riconosce
  come automatico quanto la pausa lunga.
- **I bordi della clip non si toccano.** Sono una decisione editoriale, e il
  tratto prima della prima parola e spesso proprio lo stacco che la prepara.
- **Mai sotto il minimo di durata.** Se i tagli porterebbero la clip sotto i 20
  secondi, si rinuncia prima ai piu piccoli: il minimo non e un vincolo di
  formato, e il tempo sotto cui un pensiero intero non ci sta.
"""

from __future__ import annotations

from collections.abc import Callable

# Una pausa fra due parole diventa candidata al taglio sopra questa durata.
GAP_MIN_S = 0.75
# Quanto si lascia dopo la fine della parola precedente e prima della successiva.
PAD_AFTER_S = 0.22
PAD_BEFORE_S = 0.12
# Un taglio che toglie meno di cosi non cambia il ritmo: aggiunge solo un salto.
CUT_MIN_S = 0.25
# Uno spezzone tenuto piu corto di cosi, fra due tagli, lampeggia. Era 0,8 s: sulla
# prima clip guardata un frammento di un secondo fra due tagli si leggeva come un
# inciampo.
KEEP_MIN_S = 1.5
# **Un taglio dei silenzi non cade vicino a uno stacco del girato.** Visto sulla
# prima clip guardata: un taglio 0,6 s dopo uno stacco e un doppio salto, e uno
# che finisce 0,17 s prima di uno stacco lascia cinque fotogrammi della vecchia
# inquadratura, cioe un lampo. Uno stacco *dentro* il tratto tolto invece va bene:
# il taglio e lo stacco diventano la stessa cosa.
SCENE_GUARD_S = 1.0


def plan(
    words: list[dict],
    start: float,
    end: float,
    quiet: Callable[[float, float], bool] | None = None,
    protected: list[tuple[float, float]] | None = None,
    min_duration: float = 20.0,
    scene_cuts: list[float] | None = None,
) -> dict:
    """I tratti da tenere, in secondi **relativi all'inizio della clip**.

    `words` sono le parole del transcript in tempo assoluto; `quiet(a, b)` dice
    se l'audio fra due istanti assoluti e davvero silenzio; `protected` sono
    intervalli assoluti da non toccare (risate, applausi); `scene_cuts` gli
    stacchi del girato, in tempo assoluto.
    """
    durata = end - start
    parole = sorted(
        (w for w in words if w["end"] > start and w["start"] < end), key=lambda w: w["start"]
    )
    protetti = protected or []
    stacchi = scene_cuts or []

    candidati: list[tuple[float, float]] = []
    for a, b in zip(parole, parole[1:]):
        gap = b["start"] - a["end"]
        if gap <= GAP_MIN_S:
            continue
        via_da = a["end"] + PAD_AFTER_S
        via_a = b["start"] - PAD_BEFORE_S
        if via_a - via_da < CUT_MIN_S:
            continue
        if via_da <= start or via_a >= end:
            continue
        if any(p0 < via_a and p1 > via_da for p0, p1 in protetti):
            continue
        if any(
            via_da - SCENE_GUARD_S < c < via_da or via_a < c < via_a + SCENE_GUARD_S
            for c in stacchi
        ):
            continue
        if quiet is not None and not quiet(via_da, via_a):
            continue
        candidati.append((via_da - start, via_a - start))

    tagli = _drop_flashes(candidati, durata)

    # Il minimo di durata: si rinuncia ai tagli piu piccoli finche basta.
    while tagli and durata - sum(b - a for a, b in tagli) < min_duration:
        piu_piccolo = min(tagli, key=lambda c: c[1] - c[0])
        tagli.remove(piu_piccolo)

    tenuti = _complement(tagli, durata)
    return {
        "tenuti": [(round(a, 3), round(b, 3)) for a, b in tenuti],
        "tolti": [(round(a, 3), round(b, 3)) for a, b in tagli],
        "durata": round(sum(b - a for a, b in tenuti), 3),
        "tolto_s": round(sum(b - a for a, b in tagli), 3),
    }


def _complement(tagli: list[tuple[float, float]], durata: float) -> list[tuple[float, float]]:
    tenuti, cur = [], 0.0
    for a, b in sorted(tagli):
        if a > cur:
            tenuti.append((cur, a))
        cur = max(cur, b)
    if cur < durata:
        tenuti.append((cur, durata))
    return tenuti or [(0.0, durata)]


def _drop_flashes(tagli: list[tuple[float, float]], durata: float) -> list[tuple[float, float]]:
    """Toglie i tagli che lascerebbero in mezzo uno spezzone troppo corto.

    Fra due tagli vicini si tiene il piu lungo: e quello che cambia il ritmo.
    """
    tagli = sorted(tagli)
    cambiato = True
    while cambiato:
        cambiato = False
        for i in range(len(tagli) - 1):
            if tagli[i + 1][0] - tagli[i][1] < KEEP_MIN_S:
                corto = (
                    i
                    if (tagli[i][1] - tagli[i][0]) < (tagli[i + 1][1] - tagli[i + 1][0])
                    else i + 1
                )
                del tagli[corto]
                cambiato = True
                break
    return tagli


# ------------------------------------------------------------------------------
# Tempi: dalla clip montata al sorgente, e ritorno
# ------------------------------------------------------------------------------


def offsets(tenuti: list[tuple[float, float]]) -> list[float]:
    """Dove comincia ogni tratto tenuto, nel tempo della clip montata."""
    out, acc = [], 0.0
    for a, b in tenuti:
        out.append(acc)
        acc += b - a
    return out


def to_source(tenuti: list[tuple[float, float]], t_out: float) -> float:
    """Istante della clip montata -> istante relativo alla clip nel sorgente."""
    acc = 0.0
    for a, b in tenuti:
        if t_out < acc + (b - a):
            return a + max(0.0, t_out - acc)
        acc += b - a
    return tenuti[-1][1] if tenuti else t_out


def to_output(tenuti: list[tuple[float, float]], t_src: float) -> float | None:
    """Istante relativo alla clip nel sorgente -> clip montata. None se tagliato."""
    acc = 0.0
    for a, b in tenuti:
        if a <= t_src < b:
            return acc + (t_src - a)
        acc += b - a
    return None


def segment_of(tenuti: list[tuple[float, float]], t_out: float) -> int:
    """Indice del tratto tenuto che contiene questo istante della clip montata."""
    acc = 0.0
    for i, (a, b) in enumerate(tenuti):
        acc += b - a
        if t_out < acc:
            return i
    return max(0, len(tenuti) - 1)


def remap_words(words: list[dict], tenuti: list[tuple[float, float]]) -> list[dict]:
    """Parole in tempo relativo alla clip -> tempo della clip montata.

    Ogni parola porta con se l'indice del tratto in cui cade: i sottotitoli non
    devono tenere a schermo la stessa riga attraverso un taglio, perche il
    silenzio che separava le due frasi non esiste piu.
    """
    out: list[dict] = []
    for w in words:
        mid = (w["start"] + w["end"]) / 2.0
        for i, (a, b) in enumerate(tenuti):
            if a - 1e-6 <= mid < b + 1e-6:
                base = offsets(tenuti)[i]
                s = base + max(0.0, w["start"] - a)
                e = base + min(b, w["end"]) - a
                out.append(
                    {**w, "start": round(s, 3), "end": round(max(e, s + 0.05), 3), "tratto": i}
                )
                break
    return out
