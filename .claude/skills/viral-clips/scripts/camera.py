"""La regia della camera virtuale: dove sta la finestra 9:16 in ogni fotogramma.

`track_subject.py` decide **chi** inquadrare e dove si trova, a 10 campioni al
secondo; questo modulo decide **come ci arriva la camera** e scrive il crop
fotogramma per fotogramma. Solo libreria standard: sono conti, e i conti si
provano senza video (`tests/test_camera.py`).

**Perche esiste separato, e perche e offline.** La versione precedente seguiva
il soggetto con una media mobile esponenziale e un limite di velocita, un passo
ogni decimo di secondo. Misurato sul moto esatto del crop, ricostruito dal file
`sendcmd` di quattro clip reali: **da 85 a 262 micro-salti isolati al minuto**,
e dentro ogni movimento decine di fotogrammi fermi e di strappi. Le cause erano
tre, tutte d'esecuzione e non di parametri:

1. il crop cambiava a 10 Hz, quindi una panoramica procedeva a gradini — fino a
   26 px ogni tre fotogrammi, 46 px dopo l'ingrandimento di un 1080p;
2. la spinta lenta arrotondava larghezza e altezza **separatamente** a pixel
   pari: ogni mezzo secondo cambiava per un fotogramma il rapporto d'aspetto e il
   soggetto scattava di un pixel, il tic di fondo di ogni clip;
3. un filtro causale arriva sempre tardi: parte quando il soggetto e gia andato,
   e con l'esponenziale parte alla velocita massima, cioe con uno strappo.

Il render conosce tutta la clip prima del primo fotogramma, quindi la camera si
**pianifica**, come fa un montatore davanti alla timeline: per ogni inquadratura
del girato, ferma se il soggetto non esce dalla zona morta; altrimenti tiene la
posa, e quando il soggetto si sposta davvero ci va con un movimento che accelera
e frena (smootherstep); oltre il 30% della finestra stacca. I parametri che
decidono *quando* muoversi sono gli stessi di prima — zona morta 18%, posa
minima 1 s, stacco al 30% — cambia solo il modo in cui il movimento viene
eseguito.
"""

from __future__ import annotations

import statistics
from bisect import bisect_right

# --- quando la camera si muove (parametri gia decisi, vedi CLAUDE.md) ---------

# Zona morta come frazione della larghezza della finestra: il riferimento montato
# a mano tiene il volto dentro il 15%, quindi finche il soggetto resta li la
# camera non ha ragione di muoversi.
DEAD_ZONE_FRAC = 0.18
# Dopo un movimento la posa si tiene almeno cosi: senza, chi si muove molto
# trascina la camera a raffica.
MIN_HOLD_S = 1.0
# Oltre questo spostamento non si insegue: si stacca.
CUT_INSTEAD_OF_PAN_FRAC = 0.30

# --- come si muove -------------------------------------------------------------

# Il rilevamento sbanda di qualche pixel da un campione all'altro: la mediana su
# mezzo secondo toglie il campione anomalo invece di mediarlo dentro.
MEDIAN_WINDOW_S = 0.5
# Uno spostamento conta solo se dura: un falso positivo sparisce al campione dopo.
CONFIRM_S = 0.4
# Dove andare lo dice la mediana del secondo successivo alla partenza, non il
# primo campione fuori zona: si arriva dove il soggetto si ferma, non dove passa.
SETTLE_WINDOW_S = 1.0
# Velocita media di un movimento, in larghezze di finestra al secondo, e i suoi
# limiti di durata. Sotto i 0.35 s si legge come uno scatto, sopra il secondo
# come una deriva.
MOVE_SPEED_FRAC = 0.45
MOVE_MIN_S = 0.35
MOVE_MAX_S = 0.9
# Il movimento parte un poco prima che il soggetto finisca di spostarsi: e il
# vantaggio di chi conosce il futuro, e da fuori si legge come intenzione.
MOVE_LEAD_FRAC = 0.4

# --- zoom ------------------------------------------------------------------------

# Scatto d'enfasi sulle parole scelte da Claude. Curva e cadenza vengono da
# openshorts, che le ha misurate su 20 clip: 8,4 scatti al minuto si leggono come
# un tic, sotto i 3 come regia. Entra veloce, tiene, esce piano.
PUNCH_ZOOM = 1.10
PUNCH_RISE_S = 0.25
PUNCH_HOLD_S = 1.30
PUNCH_FALL_S = 0.55
PUNCH_LEAD_S = 0.15  # la camera arriva un attimo prima della parola
PUNCH_MIN_GAP_S = 15.0
PUNCH_NOT_BEFORE_S = 1.5  # i primi istanti sono dell'hook
PUNCH_CLEAR_S = 0.6  # quanto deve durare gia l'inquadratura prima dello scatto

# Ingrandimento alternato fra spezzoni della stessa inquadratura, quando i tagli
# dei silenzi li hanno avvicinati: il salto del soggetto al taglio si legge come
# un cambio di camera voluto invece che come un fotogramma mancante.
ZOOM_CUT = 1.10
# Tetto all'ingrandimento combinato: oltre, l'ingrandimento di un 1080p si vede.
ZOOM_MAX = 1.20

# Quanto spazio serve sopra il centro del volto, in altezze di volto: con meno,
# lo zoom taglia la testa.
FACE_HEADROOM = 0.75

# Gli occhi stanno bene poco sopra la meta del quadro, non al centro esatto.
FACE_VERTICAL_ANCHOR = 0.42


# ------------------------------------------------------------------------------
# Curve
# ------------------------------------------------------------------------------


def smootherstep(u: float) -> float:
    """Da 0 a 1 con velocita **e accelerazione** nulle ai due capi.

    Lo smoothstep semplice azzera solo la velocita: l'accelerazione salta al via,
    e su un movimento di mezzo secondo si sente come un colpo. Qui no.
    """
    u = min(1.0, max(0.0, u))
    return u * u * u * (u * (6.0 * u - 15.0) + 10.0)


def smoothstep(u: float) -> float:
    u = min(1.0, max(0.0, u))
    return u * u * (3.0 - 2.0 * u)


def rolling_median(values: list[float], half: int) -> list[float]:
    if half <= 0:
        return list(values)
    n = len(values)
    return [statistics.median(values[max(0, i - half) : min(n, i + half + 1)]) for i in range(n)]


# ------------------------------------------------------------------------------
# Piano di un asse
# ------------------------------------------------------------------------------
#
# Un piano e una lista di tratti (t0, t1, v0, v1) ordinati e contigui: dentro un
# tratto la posizione va da v0 a v1 con lo smootherstep; un tratto con v0 == v1 e
# una posa. Uno stacco e semplicemente un tratto che comincia da un valore
# diverso da quello con cui il precedente e finito.


def _indice(times: list[float], t: float) -> int:
    """Primo campione con tempo >= t."""
    lo, hi = 0, len(times)
    while lo < hi:
        mid = (lo + hi) // 2
        if times[mid] < t:
            lo = mid + 1
        else:
            hi = mid
    return lo


def plan_shot(
    times: list[float],
    values: list[float],
    t_end: float,
    dead_zone: float,
    cut_distance: float,
    move_speed: float,
) -> list[tuple]:
    """Il piano di una sola inquadratura, su campioni gia riempiti.

    Prima domanda: il soggetto sta tutto nella zona morta attorno alla sua
    mediana? Allora la camera e un cavalletto per l'intera inquadratura — ed e
    il caso normale di un podcast, dove la gente sta seduta.
    """
    t0 = times[0]
    if not values:
        return []
    step = (times[-1] - times[0]) / max(1, len(times) - 1) if len(times) > 1 else 0.1
    half = max(0, int(round(MEDIAN_WINDOW_S / max(step, 1e-3) / 2)))
    smooth = rolling_median(values, half)

    centre = statistics.median(smooth)
    if max(abs(v - centre) for v in smooth) <= dead_zone:
        return [(t0, t_end, centre, centre)]

    tratti: list[tuple] = []
    first = _indice(times, t0 + SETTLE_WINDOW_S)
    anchor = statistics.median(smooth[: max(1, first)])
    hold_from = t0
    i = 0
    n = len(smooth)
    while i < n:
        t = times[i]
        dentro = abs(smooth[i] - anchor) <= dead_zone
        # La posa minima vale dopo un movimento, non all'inizio dell'inquadratura:
        # li la camera e appena arrivata con lo stacco del girato.
        posa_corta = hold_from > t0 and t - hold_from < MIN_HOLD_S
        if dentro or posa_corta:
            i += 1
            continue

        # Lo spostamento deve durare, e dalla stessa parte.
        side = 1.0 if smooth[i] > anchor else -1.0
        j_confirm = _indice(times, t + CONFIRM_S)
        window = smooth[i : max(i + 1, j_confirm)]
        if any((v - anchor) * side <= dead_zone * 0.5 for v in window):
            i += 1
            continue

        j_settle = _indice(times, t + SETTLE_WINDOW_S)
        target = statistics.median(smooth[i : max(i + 1, j_settle)])
        distance = abs(target - anchor)
        if distance <= dead_zone * 0.5:
            i += 1
            continue

        if distance > cut_distance:
            # Troppo lontano per andarci inseguendo: si stacca dove parte.
            tratti.append((hold_from, t, anchor, anchor))
            anchor, hold_from = target, t
            i = j_settle if j_settle > i else i + 1
            continue

        duration = min(MOVE_MAX_S, max(MOVE_MIN_S, distance / max(move_speed, 1e-6)))
        # L'anticipo non puo accorciare la posa minima: la posa si legge solo se
        # dura, e un movimento che parte prima la mangerebbe.
        earliest = hold_from + MIN_HOLD_S if hold_from > t0 else hold_from
        start = max(earliest, t - duration * MOVE_LEAD_FRAC)
        end = min(t_end, start + duration)
        if end - start < 1e-3:
            i += 1
            continue
        tratti.append((hold_from, start, anchor, anchor))
        tratti.append((start, end, anchor, target))
        anchor, hold_from = target, end
        i = _indice(times, end)

    tratti.append((hold_from, t_end, anchor, anchor))
    return [tr for tr in tratti if tr[1] - tr[0] > 1e-6] or [(t0, t_end, anchor, anchor)]


def plan_axis(
    times: list[float],
    raw: list[float | None],
    cuts: list[float],
    fallback: float,
    dead_zone: float,
    cut_distance: float,
    move_speed: float,
    t_end: float,
) -> list[tuple]:
    """Il piano di un asse per tutta la clip, inquadratura per inquadratura.

    Agli stacchi del girato la camera non insegue mai: il primo tratto di ogni
    inquadratura comincia dalla sua posizione, qualunque fosse la precedente.
    Dentro un'inquadratura i buchi del rilevamento si coprono con la posizione
    nota **piu vicina dentro la stessa inquadratura** — anche successiva, perche
    il piano si fa a clip intera. Un'inquadratura senza nessun rilevamento va al
    centro: puntare dove stava il soggetto di prima vorrebbe dire inquadrare il
    vuoto con la sicurezza di chi sa dove guardare.
    """
    if not times:
        return [(0.0, t_end, fallback, fallback)]
    bordi = sorted(c for c in cuts if times[0] < c < t_end)
    piano: list[tuple] = []
    starts = [times[0]] + bordi
    ends = bordi + [t_end]
    for s, e in zip(starts, ends):
        idx = [k for k, t in enumerate(times) if s <= t < e]
        if not idx:
            piano.append(
                (s, e, piano[-1][3] if piano else fallback, piano[-1][3] if piano else fallback)
            )
            continue
        ts = [times[k] for k in idx]
        vs = [raw[k] for k in idx]
        noti = [(t, v) for t, v in zip(ts, vs) if v is not None]
        if not noti:
            piano.append((s, e, fallback, fallback))
            continue
        filled: list[float] = []
        for t, v in zip(ts, vs):
            if v is not None:
                filled.append(v)
            else:
                filled.append(min(noti, key=lambda p: abs(p[0] - t))[1])
        shot = plan_shot(ts, filled, e, dead_zone, cut_distance, move_speed)
        # Il primo tratto parte dal confine dell'inquadratura, non dal primo
        # campione: fra i due c'e al massimo un decimo di secondo.
        if shot:
            a, b, v0, v1 = shot[0]
            shot[0] = (s, b, v0, v1)
        piano.extend(shot)
    return piano


def position(piano: list[tuple], t: float) -> float:
    """Dove sta la camera all'istante t secondo il piano."""
    if not piano:
        return 0.0
    starts = [tr[0] for tr in piano]
    k = max(0, bisect_right(starts, t) - 1)
    t0, t1, v0, v1 = piano[k]
    if v0 == v1 or t1 <= t0:
        return v0
    v = v0 + (v1 - v0) * smootherstep((t - t0) / (t1 - t0))
    # Ai due capi dello smootherstep la velocita e quasi nulla: per qualche
    # fotogramma il valore resta a meno di mezzo pixel dall'estremo, e il pixel
    # intero arriva da solo un fotogramma dopo che la camera si e gia fermata —
    # uno scatto isolato di un pixel, misurato sul vlog del Mondiale. Sotto il mezzo
    # pixel il movimento non e ancora cominciato, o e gia finito.
    if abs(v - v1) < 0.5:
        return v1
    if abs(v - v0) < 0.5:
        return v0
    return v


# ------------------------------------------------------------------------------
# Montaggio veloce: una posizione ferma per inquadratura
# ------------------------------------------------------------------------------

# Quando, in un girato che stacca piu spesso di un montatore umano, un'inquadratura
# merita una posizione sua: dura abbastanza da rilevare il soggetto con piu
# campioni, il soggetto c'e in almeno meta dei campioni, e il suo volto e grande
# abbastanza da non essere un passante sullo sfondo (frazione dell'altezza).
SHOT_LOCK_MIN_S = 0.8
SHOT_LOCK_COVERAGE = 0.5
SHOT_LOCK_FACE_H = 0.08


def shot_position(xs: list[float], face_h: float, base_w: float) -> float:
    """Dove fermare la finestra perche il soggetto resti dentro **tutta** l'inquadratura.

    La mediana e giusta per chi sta fermo, e sbagliata per chi attraversa il
    quadro: su un vlog, un soggetto passato da destra a sinistra in un secondo e
    mezzo usciva a sinistra alla fine dell'inquadratura. Se il tratto percorso
    (10°-90° percentile, piu la larghezza del volto) sta nella finestra, la finestra
    si centra sul tratto; altrimenti non c'e posizione che lo tenga tutto, e resta la
    mediana, cioe dove il soggetto passa piu tempo.
    """
    ordinati = sorted(xs)
    n = len(ordinati)
    lo = ordinati[int(0.1 * (n - 1))]
    hi = ordinati[int(round(0.9 * (n - 1)))]
    if hi - lo + 0.8 * face_h <= base_w:
        return (lo + hi) / 2.0
    return statistics.median(xs)


def lock_per_shot(
    times: list[float],
    raw_x: list[float | None],
    raw_y: list[float | None],
    raw_h: list[float | None],
    cuts: list[float],
    duration: float,
    src_h: float,
    base_w: float,
    fallback: tuple[float, float],
) -> tuple[list[float], list[float], int]:
    """Una posizione per ogni inquadratura sicura, quella della clip per le altre.

    Nessun movimento dentro un'inquadratura: la finestra cambia solo sugli stacchi,
    dove l'immagine cambia comunque. Ritorna le posizioni per campione e quante
    inquadrature hanno avuto una posizione propria.
    """
    bordi = [0.0] + sorted(cuts) + [duration]
    out_x = [fallback[0]] * len(times)
    out_y = [fallback[1]] * len(times)
    proprie = 0
    for a, b in zip(bordi, bordi[1:]):
        idx = [i for i, t in enumerate(times) if a <= t < b]
        if not idx:
            continue
        xs = [raw_x[i] for i in idx if raw_x[i] is not None]
        ys = [raw_y[i] for i in idx if raw_y[i] is not None]
        hs = [raw_h[i] for i in idx if raw_h[i]]
        sicuro = (
            b - a >= SHOT_LOCK_MIN_S
            and len(xs) >= SHOT_LOCK_COVERAGE * len(idx)
            and bool(hs)
            and statistics.median(hs) >= SHOT_LOCK_FACE_H * src_h
        )
        if not sicuro:
            continue
        proprie += 1
        x = shot_position(xs, statistics.median(hs), base_w)
        y = statistics.median(ys) if ys else fallback[1]
        for i in idx:
            out_x[i], out_y[i] = x, y
    return out_x, out_y, proprie


# ------------------------------------------------------------------------------
# Zoom
# ------------------------------------------------------------------------------


def pick_punches(
    candidates: list[float], duration: float, cuts: list[float] | None = None
) -> list[float]:
    """Gli istanti su cui scattare, fra quelli candidati, rispettando la cadenza.

    I candidati arrivano in ordine di tempo (le parole chiave nella clip); si
    tengono i primi che distano abbastanza dall'ultimo tenuto.

    **Uno scatto sta dentro un'inquadratura, lontano dai suoi bordi.** `cuts`
    sono gli stacchi del girato e i tagli dei silenzi, nel tempo della clip
    montata. Visto sulla prima clip guardata: uno scatto partito 0,15 s dopo uno
    stacco, e un taglio dei silenzi a meta della sua tenuta — tre movimenti in
    sette decimi di secondo, che da fuori sembrano un inciampo.
    """
    bordi = sorted(cuts or [])
    span = PUNCH_RISE_S + PUNCH_HOLD_S + PUNCH_FALL_S
    tenuti: list[float] = []
    for t in sorted(candidates):
        if t < PUNCH_NOT_BEFORE_S or t > duration - (PUNCH_RISE_S + 0.2):
            continue
        if tenuti and t - tenuti[-1] < PUNCH_MIN_GAP_S:
            continue
        attacco = t - PUNCH_LEAD_S
        if any(attacco - PUNCH_CLEAR_S < c < attacco + span + 0.1 for c in bordi):
            continue
        tenuti.append(t)
    return tenuti


def punch_envelope(t: float, punches: list[float]) -> float:
    """Fattore d'ingrandimento dovuto agli scatti, 1 fuori da ogni scatto."""
    best = 1.0
    span = PUNCH_RISE_S + PUNCH_HOLD_S + PUNCH_FALL_S
    for p in punches:
        d = t - (p - PUNCH_LEAD_S)
        if d < 0 or d > span:
            continue
        if d < PUNCH_RISE_S:
            k = smoothstep(d / PUNCH_RISE_S)
        elif d < PUNCH_RISE_S + PUNCH_HOLD_S:
            k = 1.0
        else:
            k = 1.0 - smoothstep((d - PUNCH_RISE_S - PUNCH_HOLD_S) / PUNCH_FALL_S)
        best = max(best, 1.0 + (PUNCH_ZOOM - 1.0) * k)
    return best


def max_zoom_for_face(base_h: float, face_h: float | None) -> float:
    """Oltre questo ingrandimento la testa esce dal quadro in alto."""
    if not face_h or face_h <= 0:
        return ZOOM_MAX
    return max(1.0, min(ZOOM_MAX, FACE_VERTICAL_ANCHOR * base_h / (FACE_HEADROOM * face_h)))


# ------------------------------------------------------------------------------
# Geometria e comandi
# ------------------------------------------------------------------------------


def window(
    cx: float, cy: float, zoom: float, base_w: int, base_h: int, src_w: int, src_h: int
) -> tuple[int, int, int, int]:
    """La finestra di crop, **sempre con le proporzioni della finestra base**.

    L'altezza si ricava dalla larghezza, non si arrotonda per conto suo: due
    arrotondamenti indipendenti cambiavano il rapporto d'aspetto di un soffio a
    istanti diversi, e dopo lo scale verso 1080x1920 quel soffio era un tic.
    Niente allineamento a pixel pari: il render usa `crop=...:exact=1`.
    """
    w = int(round(base_w / max(zoom, 1e-6)))
    w = max(16, min(w, src_w))
    h = int(round(w * base_h / base_w))
    h = max(16, min(h, src_h))
    x = int(round(cx - w / 2.0))
    y = int(round(cy - h * FACE_VERTICAL_ANCHOR))
    x = max(0, min(x, src_w - w))
    y = max(0, min(y, src_h - h))
    return w, h, x, y


def sendcmd_lines(
    frames: list[tuple[int, int, int, int]], fps: float, target: str = "crop"
) -> list[str]:
    """Una riga per ogni fotogramma in cui la finestra cambia.

    L'istante e mezzo fotogramma prima di quello a cui si applica: `sendcmd`
    esegue il comando sul primo fotogramma con tempo maggiore o uguale, e un
    confronto fra float esattamente sul bordo lo farebbe slittare di uno.
    """
    lines: list[str] = []
    prev = None
    for k, cur in enumerate(frames):
        if cur == prev:
            continue
        t = 0.0 if k == 0 else (k - 0.5) / fps
        w, h, x, y = cur
        lines.append(f"{t:.4f} {target} w {w}, {target} h {h}, {target} x {x}, {target} y {y};")
        prev = cur
    return lines
