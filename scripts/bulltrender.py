"""
Bulltrender OMXSPI – räknar ut och ritar graferna varje vecka.

Gör samma sak som Google Sheets-arket:
  1. Alla bulltrender omräknade till start = 10, x-axel = handelsdagar sedan botten
  2. Bulltrendsmönstret (snittet av tidigare trender) mot årets utfall, 0 % vid årsskiftet

Data:
  - data/omxspi_daglig.csv      (huvudfil 1998 → idag; nya dagar läggs till på slutet)
  - FRED serie NASDAQOMXSPI      (Nasdaq, från nov 2008) – via API-nyckel, CSV eller Nasdaq Nordic som reserv
  - data/omxspi_extra.csv        (valfri – egen data som fyller i eller ersätter)

Resultat hamnar i docs/ (GitHub Pages):
  docs/bulltrender.png, docs/ar.png, docs/volatilitet.png, docs/nyckeltal.json, docs/omxspi.csv, docs/index.html
"""

from __future__ import annotations

import io
import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import matplotlib.ticker as mtick
import numpy as np
import pandas as pd
import requests

ROT = Path(__file__).resolve().parent.parent
DATA = ROT / "data"
UT = ROT / "docs"

FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=NASDAQOMXSPI"
SECTOR_DAGLIG = "https://raw.githubusercontent.com/alex30free/sector-trends/main/daily_omxspi.json"
AR = int(os.environ.get("AR", date.today().year))  # år för årsjämförelsen

# Mörkt tema – samma palett som sector-trends
BG = "#0d1318"; RUTNAT = "#1c2830"; KANT = "#253340"
TEXT = "#c8d8e8"; DAMPAD = "#6a8090"; VIT = "#f0f4f8"; RUTA = "#111820"
TEAL = "#00c8d4"; BLA = "#0099ff"; GUL = "#f0c040"; GRON = "#00e5a0"; ROD = "#ff5c6c"; ORANGE = "#ff9f43"
FARGER = [BLA, GUL, "#8fa3b5", GRON, "#b48cff", ORANGE]
MONSTER_FARG = "#dfe7f0"
UTFALL_FARG = GRON

plt.rcParams.update({
    "figure.facecolor": BG, "axes.facecolor": BG, "savefig.facecolor": BG,
    "text.color": TEXT, "axes.labelcolor": DAMPAD, "xtick.color": DAMPAD, "ytick.color": DAMPAD,
    "legend.labelcolor": TEXT, "font.family": "DejaVu Sans",
})


# --------------------------------------------------------------------------
# Data
# --------------------------------------------------------------------------
def las_csv(path_or_buf) -> pd.Series:
    df = pd.read_csv(path_or_buf)
    df.columns = [c.strip().lower() for c in df.columns]
    datumkol = next(c for c in df.columns if "date" in c or "datum" in c)
    vardekol = next(c for c in df.columns if c != datumkol)
    s = pd.Series(
        pd.to_numeric(df[vardekol].astype(str).str.replace(",", ""), errors="coerce").values,
        index=pd.to_datetime(df[datumkol]),
    )
    return s.dropna().sort_index()


UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
                     "Chrome/124.0 Safari/537.36",
      "Accept": "text/csv,application/json,*/*"}


def _get(url, **kw):
    """GET med tre försök och lång timeout."""
    sista = None
    for forsok in range(3):
        try:
            r = requests.get(url, timeout=90, headers=UA, **kw)
            r.raise_for_status()
            return r
        except Exception as e:  # noqa: BLE001
            sista = e
            print(f"  försök {forsok + 1} misslyckades: {e}", file=sys.stderr)
    raise sista


def hamta_fred_api() -> pd.Series:
    """FRED:s officiella API – kräver gratis nyckel i secret FRED_API_KEY."""
    nyckel = os.environ.get("FRED_API_KEY", "").strip()
    if not nyckel:
        raise RuntimeError("ingen FRED_API_KEY satt")
    r = _get("https://api.stlouisfed.org/fred/series/observations",
             params={"series_id": "NASDAQOMXSPI", "api_key": nyckel, "file_type": "json"})
    obs = r.json()["observations"]
    s = pd.Series(pd.to_numeric([o["value"] for o in obs], errors="coerce"),
                  index=pd.to_datetime([o["date"] for o in obs]))
    return s.dropna().sort_index()


def hamta_fred_csv() -> pd.Series:
    return las_csv(io.StringIO(_get(FRED_URL).text))


def hamta_nasdaq() -> pd.Series:
    """Nasdaq Nordics publika API (OMXSPI, ISIN SE0000744195)."""
    r = _get("https://api.nasdaq.com/api/nordic/instruments/SE0000744195/chart",
             params={"assetClass": "INDEXES", "lang": "en",
                     "fromDate": "2008-01-01", "toDate": date.today().isoformat()})
    punkter = (r.json().get("data") or {}).get("CP") or []
    datum, varden = [], []
    for p in punkter:
        z = p.get("z") or {}
        if "dateTime" in z and "value" in z:
            datum.append(pd.to_datetime(z["dateTime"]))
            varden.append(float(str(z["value"]).replace(",", "")))
        elif "x" in p and "y" in p:
            datum.append(pd.to_datetime(p["x"], unit="ms"))
            varden.append(float(p["y"]))
    if not datum:
        raise RuntimeError("tomt svar från Nasdaq")
    s = pd.Series(varden, index=pd.DatetimeIndex(datum).normalize())
    return s[~s.index.duplicated(keep="last")].sort_index()


KALLOR = [("FRED API", hamta_fred_api), ("FRED CSV", hamta_fred_csv), ("Nasdaq", hamta_nasdaq)]


def hamta_ny_data() -> pd.Series | None:
    for namn, f in KALLOR:
        print(f"Hämtar kurser från {namn} …", file=sys.stderr)
        try:
            s = f()
            if len(s) > 100:
                print(f"  OK: {len(s)} kurser, senaste {s.index[-1].date()}", file=sys.stderr)
                return s
            print(f"  för lite data ({len(s)} rader)", file=sys.stderr)
        except Exception as e:  # noqa: BLE001
            print(f"  misslyckades: {e}", file=sys.stderr)
    return None


HUVUDFIL = DATA / "omxspi_daglig.csv"


def ladda_kurser() -> pd.Series:
    """Huvudfilen data/omxspi_daglig.csv är grunden. Nya dagar (efter sista raden) hämtas från
    sector-trends (Yahoo) och FRED och läggs till på slutet – gamla rader ändras aldrig."""
    bas = pd.read_csv(HUVUDFIL, parse_dates=["date"]).set_index("date").sort_index()
    sista = bas.index.max()

    nya = []
    try:  # sector-trends har dagens stängning redan på kvällen
        j = _get(SECTOR_DAGLIG).json()
        nya.append(("Yahoo", pd.Series(j["close"], index=pd.to_datetime(j["dates"])).dropna()))
        print(f"  sector-trends: senaste {j['dates'][-1]}", file=sys.stderr)
    except Exception as e:  # noqa: BLE001
        print(f"  sector-trends misslyckades: {e}", file=sys.stderr)
    ny = hamta_ny_data()
    if ny is not None:
        nya.append(("FRED", ny))
    extra = DATA / "omxspi_extra.csv"
    if extra.exists():
        nya.append(("egen", las_csv(extra)))

    tillagg = []
    for kalla, serie in nya:
        x = serie[serie.index > sista]
        x = x[~x.index.isin([d for t in tillagg for d in t.index])]
        if len(x):
            tillagg.append(pd.DataFrame({"close": x.round(2), "kalla": kalla}))
    if tillagg:
        t = pd.concat(tillagg).sort_index()
        # rimlighetskontroll: ingen dag får avvika mer än 15 % från föregående
        foreg = pd.concat([bas["close"].iloc[-1:], t["close"]]).pct_change().iloc[1:]
        t = t[foreg.abs() < 0.15]
        bas = pd.concat([bas, t])
        bas.index.name = "date"
        bas.to_csv(HUVUDFIL)
        print(f"  Huvudfilen: +{len(t)} nya dagar ({t.index.min().date()} – {t.index.max().date()})", file=sys.stderr)
    else:
        print(f"  Huvudfilen: inga nya dagar (senaste {sista.date()})", file=sys.stderr)
    return bas["close"].astype(float)


def ladda_trender() -> pd.DataFrame:
    t = pd.read_csv(DATA / "trender.csv", dtype=str).fillna("")
    t["start"] = pd.to_datetime(t["start"])
    t["slut"] = pd.to_datetime(t["slut"].replace("", None))
    return t


# --------------------------------------------------------------------------
# Beräkningar
# --------------------------------------------------------------------------
def trendserie(kurser: pd.Series, start, slut) -> pd.Series:
    """Kurser från start till slut (eller sista datum), omräknade till start = 10."""
    s = kurser[kurser.index >= start]
    if pd.notna(slut):
        s = s[s.index <= slut]
    if s.empty:
        return s
    return s / s.iloc[0] * 10


def bulltrendsmonster(avslutade: list[pd.Series]) -> np.ndarray:
    """Snittet av tidigare trenders dagsavkastning, dag för dag, ihopkedjat från 10."""
    langd = max(len(s) for s in avslutade)
    avk = np.full((len(avslutade), langd), np.nan)
    for i, s in enumerate(avslutade):
        v = s.values
        avk[i, 1 : len(v)] = v[1:] / v[:-1] - 1
    snitt = np.nanmean(avk[:, 1:], axis=0)
    return np.concatenate([[10.0], 10 * np.cumprod(1 + snitt)])


# --------------------------------------------------------------------------
# Grafer
# --------------------------------------------------------------------------
def stil(ax):
    ax.grid(axis="y", color=RUTNAT, linewidth=0.8)
    ax.grid(axis="x", visible=False)
    for k in ("top", "right"):
        ax.spines[k].set_visible(False)
    ax.spines["left"].set_color(KANT)
    ax.spines["bottom"].set_color(KANT)
    ax.tick_params(colors=DAMPAD)


def rita_bulltrender(serier, namn, monster, nyck, uppdaterad):
    fig, ax = plt.subplots(figsize=(16, 9), dpi=120)
    stil(ax)
    for i, (s, n) in enumerate(zip(serier, namn)):
        if s.empty:
            continue
        x = np.arange(1, len(s) + 1)
        ar_nuvarande = i == len(serier) - 1
        ax.plot(x, s.values, color=FARGER[i % len(FARGER)], linewidth=2.6 if ar_nuvarande else 1.8, label=n)
        ax.annotate(n, (x[-1], s.values[-1]), xytext=(6, 0), textcoords="offset points",
                    fontsize=12, color=TEXT, va="center", fontweight="bold" if ar_nuvarande else "normal")
    xm = np.arange(1, len(monster) + 1)
    ax.plot(xm, monster, color=MONSTER_FARG, linewidth=2.2, linestyle=(0, (6, 4)), label="Bulltrendsmönster (snitt)")

    ax.set_xlim(0, len(monster) * 1.06)
    lagst = min(float(x.min()) for x in serier if not x.empty)
    ax.set_ylim(bottom=min(8, np.floor(lagst * 0.95)))
    ax.set_xlabel("Handelsdagar sedan botten", color=DAMPAD)
    ax.set_ylabel("Index (start = 10)", color=DAMPAD)
    ax.set_title("BULLTRENDER 2000-TAL – OMXSPI SVERIGE", fontsize=18, color=VIT, pad=16)
    ax.legend(loc="upper left", frameon=False, fontsize=11)

    text = (f"Nuvarande trend: dag {nyck['nuvarande']['dagar']}\n"
            f"I tid {nyck['avverkat_i_tid']:.0%} avverkat av snittet\n"
            f"Uppgång hittills: {nyck['nuvarande']['uppgang']:+.1%}\n"
            f"Snitt per dag: {nyck['nuvarande']['snitt_per_dag']:+.3%}")
    ax.text(0.985, 0.04, text, transform=ax.transAxes, ha="right", va="bottom", fontsize=12,
            bbox=dict(boxstyle="round,pad=0.6", facecolor=RUTA, edgecolor=KANT))
    fig.text(0.01, 0.01, f"Källa: Nasdaq via FRED, investing.com · Uppdaterad {uppdaterad}",
             fontsize=9, color=DAMPAD)
    fig.tight_layout(rect=[0, 0.03, 1, 1])
    fig.savefig(UT / "bulltrender.png")
    plt.close(fig)


def rita_ar(monster_pct, monster_datum, utfall_pct, utfall_datum, ar, uppdaterad):
    fig, ax = plt.subplots(figsize=(16, 9), dpi=120)
    stil(ax)
    ax.axhline(0, color=KANT, linewidth=1)
    ax.plot(monster_datum, monster_pct, color=MONSTER_FARG, linewidth=2.4, label="Bulltrendsmönster 2000-tal")
    if len(utfall_pct):
        ax.plot(utfall_datum, utfall_pct, color=UTFALL_FARG, linewidth=2.4, label=f"Bulltrend {ar} utfall")
        ax.annotate(f"{utfall_pct[-1]:+.1%}", (utfall_datum[-1], utfall_pct[-1]), xytext=(8, 0),
                    textcoords="offset points", color=UTFALL_FARG, fontsize=13, fontweight="bold", va="center")
    ax.annotate(f"{monster_pct[-1]:+.1%}", (monster_datum[-1], monster_pct[-1]), xytext=(8, 0),
                textcoords="offset points", color=MONSTER_FARG, fontsize=13, fontweight="bold", va="center")

    ax.yaxis.set_major_formatter(mtick.PercentFormatter(1.0, decimals=0))
    ax.xaxis.set_major_locator(mdates.MonthLocator())
    manader = ["JAN", "FEB", "MAR", "APR", "MAJ", "JUN", "JUL", "AUG", "SEPT", "OKT", "NOV", "DEC"]
    ax.xaxis.set_major_formatter(mtick.FuncFormatter(lambda v, _: manader[mdates.num2date(v).month - 1]))
    ax.set_xlim(pd.Timestamp(ar, 1, 1) - pd.Timedelta(days=3), pd.Timestamp(ar, 12, 31) + pd.Timedelta(days=12))
    ax.set_title(f"RESULTAT OMXSPI MOT BULLTRENDSMÖNSTER 2000-TAL – {ar}", fontsize=18, color=VIT, pad=16)
    ax.legend(loc="upper left", frameon=False, fontsize=12)
    fig.text(0.01, 0.01, f"Källa: Nasdaq via FRED, investing.com · Uppdaterad {uppdaterad}",
             fontsize=9, color=DAMPAD)
    fig.tight_layout(rect=[0, 0.03, 1, 1])
    fig.savefig(UT / "ar.png")
    plt.close(fig)



# --------------------------------------------------------------------------
# Volatilitet
# --------------------------------------------------------------------------
def rullande_vol(s: pd.Series, fonster: int, bara_nedsida: bool = False) -> pd.Series:
    """Rullande årlig volatilitet av dagliga log-avkastningar."""
    r = np.log(s / s.shift(1)).dropna()
    if bara_nedsida:
        r = r.clip(upper=0)
        return np.sqrt((r ** 2).rolling(fonster).mean()) * np.sqrt(252)
    return r.rolling(fonster).std() * np.sqrt(252)


def volatilitet(serier, namn):
    """Nyckeltal + grafdata för volatilitet och nedgång från topp."""
    nu = serier[-1]
    v20, v100 = rullande_vol(nu, 20), rullande_vol(nu, 100)
    ned20 = rullande_vol(nu, 20, bara_nedsida=True)
    dd = nu / nu.cummax() - 1
    per_trenddag = [rullande_vol(s, 60).reset_index(drop=True) for s in serier]

    dag = len(nu)
    jamfor = [p.iloc[dag - 1] for p in per_trenddag[:-1] if len(p) >= dag and pd.notna(p.iloc[dag - 1])]
    nyck = {
        "vol_20d": float(v20.iloc[-1]),
        "vol_100d": float(v100.iloc[-1]),
        "kvot_20_100": float(v20.iloc[-1] / v100.iloc[-1]),
        "nedsidesvol_20d": float(ned20.iloc[-1]),
        "nedgang_fran_topp": float(dd.iloc[-1]),
        "storsta_nedgang_i_trenden": float(dd.min()),
        "vol_60d_nu": float(per_trenddag[-1].iloc[-1]),
        "vol_60d_tidigare_samma_dag": float(np.mean(jamfor)) if jamfor else None,
    }
    return nyck, dict(v20=v20, v100=v100, ned20=ned20, dd=dd, per_trenddag=per_trenddag)



def regim(serier, g, avverkat):
    """Vilken av de fyra rutorna befinner sig marknaden i? (kurs upp/ned × volatilitet upp/ned)"""
    nu = serier[-1]
    v20, v100, ned20, dd = g["v20"], g["v100"], g["ned20"], g["dd"]
    pris_20d = float(nu.iloc[-1] / nu.iloc[-21] - 1)
    kvot = float(v20.iloc[-1] / v100.iloc[-1])
    pris_upp, vol_upp = pris_20d >= 0, kvot > 1.05  # 0,95–1,05 räknas som oförändrad
    dag = len(nu)

    if pris_upp and not vol_upp:
        farg, sv, en = "gron", ("Frisk bulltrend", "Kursen stiger och volatiliteten är oförändrad eller sjunker – en lugn trappa uppåt. "
                                "Det vanligaste läget mitt i en bulltrend."), \
            ("Healthy bull trend", "Price rising with steady or falling volatility – a calm staircase higher. "
             "The most common state in the middle of a bull trend.")
    elif pris_upp and vol_upp:
        if dag < 250:
            farg, sv, en = "gul", ("Tidig trend", "Kursen stiger med stigande volatilitet. Normalt tidigt i en "
                                   "trend när marknaden lämnar en botten."), \
                ("Early trend", "Price rising with rising volatility. Normal early in a trend as the "
                 "market leaves a bottom.")
        else:
            farg, sv, en = "gul", ("Uppgång med oro", "Kursen stiger men volatiliteten ökar. Sent i en trend "
                                   "kan det tyda på eufori eller instabilitet – håll koll på nedsidan."), \
                ("Rising but unsettled", "Price rising but volatility increasing. Late in a trend this can "
                 "signal euphoria or instability – watch the downside.")
    elif not pris_upp and vol_upp:
        farg, sv, en = "rod", ("Stress / korrektion", "Kursen faller och volatiliteten stiger. Normalt i en "
                               "bulltrend om det går över inom några veckor – farligt om det består."), \
            ("Stress / correction", "Price falling and volatility rising. Normal in a bull trend if it "
             "fades within weeks – dangerous if it persists.")
    else:
        if float(v20.iloc[-40:].max()) > 1.3 * float(v100.iloc[-1]):
            farg, sv, en = "gul", ("Lugnar sig efter oro", "Kursen är svag men volatiliteten har toppat och "
                                   "sjunker – ofta en bottenprocess."), \
                ("Calming after stress", "Price still weak but volatility has peaked and is falling – "
                 "often a bottoming process.")
        else:
            farg, sv, en = "gul", ("Långsam nedglidning", "Kursen faller i lugn takt utan stress – en tyst "
                                   "försvagning att följa."), \
                ("Slow drift lower", "Price slipping quietly without stress – a silent weakening "
                 "worth watching.")

    flaggor = []
    if float(ned20.iloc[-1]) > 0.8 * float(v20.iloc[-1]):
        flaggor.append(("Nedsidan står för det mesta av rörligheten", "Downside moves dominate volatility"))
    if len(v100) > 63 and float(v100.iloc[-1] / v100.iloc[-63] - 1) > 0.15:
        flaggor.append(("Normalläget för volatiliteten stiger (100 d, 3 mån)", "Baseline volatility rising (100d, 3m)"))
    if kvot > 1.3:
        flaggor.append(("Kraftig ökning: 20 d mer än 30 % över 100 d", "Sharp rise: 20d more than 30% above 100d"))
    if float(dd.iloc[-1]) <= -0.10:
        flaggor.append(("Mer än 10 % under trendens topp", "More than 10% below the trend high"))
    if avverkat > 0.7 and float(dd.iloc[-1]) > -0.03 and float(v100.iloc[-1]) < 0.14:
        flaggor.append(("Lugnt nära toppen sent i trenden – toppar bildas ofta i lugn. Varning om volatiliteten "
                        "börjar stiga utan nya toppar.",
                        "Calm near the high late in the trend – tops often form in calm. Warning if volatility "
                        "starts rising without new highs."))
    return {
        "farg": farg, "rubrik": {"sv": sv[0], "en": en[0]}, "text": {"sv": sv[1], "en": en[1]},
        "flaggor": [{"sv": a, "en": b} for a, b in flaggor],
        "kurs_20d": pris_20d, "kvot_20_100": kvot,
    }


def rita_volatilitet(g, namn, nyck, uppdaterad):
    fig = plt.figure(figsize=(16, 14), dpi=120)
    gs = fig.add_gridspec(3, 1, height_ratios=[1.25, 1, 0.75], hspace=0.38, top=0.92, bottom=0.05)

    # 1) 60-dagars volatilitet per trenddag, alla trender
    ax1 = fig.add_subplot(gs[0])
    stil(ax1)
    for i, (p, n) in enumerate(zip(g["per_trenddag"], namn)):
        nu = i == len(namn) - 1
        ax1.plot(np.arange(1, len(p) + 1), p.values, color=FARGER[i % len(FARGER)],
                 linewidth=2.6 if nu else 1.5, alpha=1 if nu else 0.85, label=n)
    ax1.yaxis.set_major_formatter(mtick.PercentFormatter(1.0, decimals=0))
    ax1.set_xlabel("Handelsdagar sedan botten", color=DAMPAD)
    ax1.set_title("VOLATILITET PER TRENDDAG (60 dagar, årstakt)", fontsize=15, color=VIT, loc="left")
    ax1.legend(loc="upper right", frameon=False, fontsize=11, ncol=4)

    # 2) Nuvarande trend: 20 d mot 100 d (+ nedsidesvolatilitet)
    ax2 = fig.add_subplot(gs[1])
    stil(ax2)
    v20, v100, ned = g["v20"], g["v100"], g["ned20"]
    ax2.plot(v100.index, v100.values, color="#8fa3b5", linewidth=2, label="100 dagar (normalläge)")
    ax2.plot(v20.index, v20.values, color=ROD, linewidth=1.8, label="20 dagar")
    ax2.plot(ned.index, ned.values, color=GUL, linewidth=1.2, linestyle=(0, (3, 2)), label="Nedsida 20 dagar")
    upp = (v20 > v100).reindex(v20.index).fillna(False)
    ax2.fill_between(v20.index, v100.reindex(v20.index), v20, where=upp, color=ROD, alpha=0.18,
                     label="Stigande (20 d > 100 d)")
    ax2.set_ylim(0, float(np.nanmax(v20.values)) * 1.3)
    ax2.yaxis.set_major_formatter(mtick.PercentFormatter(1.0, decimals=0))
    ax2.set_title("NUVARANDE BULLTREND – STIGER ELLER SJUNKER VOLATILITETEN?", fontsize=15,
                  color=VIT, loc="left")
    ax2.legend(loc="upper right", frameon=False, fontsize=10, ncol=4)
    kvot = nyck["kvot_20_100"]
    lage = "STIGANDE" if kvot > 1.05 else ("SJUNKANDE" if kvot < 0.95 else "OFÖRÄNDRAD")
    r = nyck["regim"]
    rf = {"gron": GRON, "gul": GUL, "rod": ROD}[r["farg"]]
    ax2.text(0.01, 0.95, f"● {r['rubrik']['sv'].upper()}", transform=ax2.transAxes, va="top", fontsize=13,
             color=rf, fontweight="bold",
             bbox=dict(boxstyle="round,pad=0.5", facecolor=RUTA, edgecolor=rf))
    ax2.text(0.01, 0.80, f"Kurs 20 d: {r['kurs_20d']:+.1%} · Kvot 20d/100d: {kvot:.2f} → {lage}\n"
             f"20 d: {nyck['vol_20d']:.1%} · 100 d: {nyck['vol_100d']:.1%}",
             transform=ax2.transAxes, va="top", fontsize=11, color=TEXT)

    # 3) Nedgång från trendens topp
    ax3 = fig.add_subplot(gs[2], sharex=ax2)
    stil(ax3)
    dd = g["dd"]
    ax3.fill_between(dd.index, dd.values, 0, color=ROD, alpha=0.22)
    ax3.plot(dd.index, dd.values, color=ROD, linewidth=1.2)
    ax3.axhline(-0.10, color=ORANGE, linewidth=1, linestyle="--")
    ax3.axhline(-0.20, color="#c0392b", linewidth=1, linestyle="--")
    ax3.text(dd.index[0], -0.10, " −10 % korrektion", va="bottom", fontsize=9, color=ORANGE)
    ax3.text(dd.index[0], -0.20, " −20 % björngräns", va="bottom", fontsize=9, color="#e06666")
    ax3.set_ylim(min(-0.22, dd.min() * 1.1), 0.01)
    ax3.yaxis.set_major_formatter(mtick.PercentFormatter(1.0, decimals=0))
    ax3.set_title(f"NEDGÅNG FRÅN TRENDENS TOPP – nu {nyck['nedgang_fran_topp']:.1%}, "
                  f"största {nyck['storsta_nedgang_i_trenden']:.1%}", fontsize=15, color=VIT, loc="left")

    fig.suptitle("VOLATILITET I BULLTRENDEN – OMXSPI", fontsize=18, color=VIT, y=0.97)
    fig.text(0.01, 0.015, f"Källa: Nasdaq via FRED, investing.com · Uppdaterad {uppdaterad}",
             fontsize=9, color=DAMPAD)
    fig.savefig(UT / "volatilitet.png", bbox_inches="tight")
    plt.close(fig)



# --------------------------------------------------------------------------
# År för år
# --------------------------------------------------------------------------
def rita_arsvis(kurser: pd.Series, fran_ar: int, uppdaterad):
    """Varje år från 0 % vid årsskiftet, lagda bredvid varandra (som Kavastus årsvisa graf).
    Fler än 15 år delas upp på två rader med samma skala."""
    alla_ar = list(range(fran_ar, kurser.index[-1].year + 1))
    halft = (len(alla_ar) + 1) // 2 if len(alla_ar) > 15 else len(alla_ar)
    rader = [alla_ar[:halft], alla_ar[halft:]] if halft < len(alla_ar) else [alla_ar]

    def arsdata(ar):
        fore = kurser[kurser.index < pd.Timestamp(ar, 1, 1)]
        x = kurser[(kurser.index >= pd.Timestamp(ar, 1, 1)) & (kurser.index < pd.Timestamp(ar + 1, 1, 1))]
        if x.empty:
            return None
        bas = fore.iloc[-1] if not fore.empty else x.iloc[0]  # första året: från första handelsdagen
        pct = np.concatenate([[0.0], x.values / bas - 1])
        dagar = np.concatenate([[0.0], [(d - pd.Timestamp(ar, 1, 1)).days / 365.0 for d in x.index]])
        return pct, dagar

    data = {ar: arsdata(ar) for ar in alla_ar}
    data = {k: v for k, v in data.items() if v is not None}
    lo = min(v[0].min() for v in data.values())
    hi = max(v[0].max() for v in data.values())
    spann = hi - lo

    fig, axs = plt.subplots(len(rader), 1, figsize=(18, 7.2 * len(rader)), dpi=120, squeeze=False)
    resultat = {}
    for ax, ar_lista in zip(axs[:, 0], rader):
        stil(ax)
        ax.axhline(0, color=KANT, linewidth=1)
        for i, ar in enumerate(ar_lista):
            if ar not in data:
                continue
            pct, dagar = data[ar]
            xs = i + 0.04 + dagar * 0.92
            slut = float(pct[-1])
            resultat[ar] = slut
            ax.fill_between(xs, pct, 0, where=pct >= 0, interpolate=True, color=GRON, alpha=0.18, linewidth=0)
            ax.fill_between(xs, pct, 0, where=pct < 0, interpolate=True, color=ROD, alpha=0.20, linewidth=0)
            ax.plot(xs, pct, color=BLA, linewidth=1.3)
            pagaende = ar == kurser.index[-1].year
            ax.text(i + 0.5, hi + spann * 0.05, f"{slut:+.0%}" + ("*" if pagaende else ""), ha="center", va="center",
                    fontsize=11, color=GRON if slut >= 0 else ROD, fontweight="bold")
            ax.axvspan(i, i + 1, ymin=0, ymax=0.03, color=(GUL if pagaende else (GRON if slut >= 0 else ROD)), alpha=0.55)
        ax.set_ylim(lo - spann * 0.06, hi + spann * 0.10)
        ax.set_xlim(0, len(rader[0]))
        ax.set_xticks([i + 0.5 for i in range(len(ar_lista))])
        ax.set_xticklabels([str(a) for a in ar_lista], fontsize=10)
        ax.tick_params(axis="x", length=0)
        for i in range(1, len(ar_lista)):
            ax.axvline(i, color=RUTNAT, linewidth=0.8)
        ax.yaxis.set_major_formatter(mtick.PercentFormatter(1.0, decimals=0))
    axs[0, 0].set_title(f"STOCKHOLM OMXSPI ÅR FÖR ÅR {alla_ar[0]}–{alla_ar[-1]}  ·  varje år från 0 % vid årsskiftet",
                        fontsize=17, color=VIT, pad=14)
    upp = [r for r in resultat.values() if r >= 0]
    ned = [r for r in resultat.values() if r < 0]
    fig.text(0.01, 0.006, f"Plusår: {len(upp)} av {len(resultat)} · snitt plusår {np.mean(upp):+.0%} · "
             f"snitt minusår {np.mean(ned):+.0%} · * hittills i år · {alla_ar[0]} räknat från första handelsdagen · Samma skala på båda raderna · "
             f"Källa: investing.com, Nasdaq via FRED, Yahoo · Uppdaterad {uppdaterad}", fontsize=9, color=DAMPAD)
    fig.tight_layout(rect=[0, 0.015, 1, 1])
    fig.savefig(UT / "arsvis.png")
    plt.close(fig)
    return resultat


# --------------------------------------------------------------------------
def main():
    UT.mkdir(exist_ok=True)
    kurser = ladda_kurser()
    trender = ladda_trender()
    uppdaterad = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")

    serier = [trendserie(kurser, r.start, r.slut) for r in trender.itertuples()]
    namn = list(trender["namn"])
    tomma = [n for n, s in zip(namn, serier) if s.empty]
    if tomma:
        sys.exit(f"FEL: ingen kursdata för trend(er): {', '.join(tomma)}")

    avslutade, nuvarande = serier[:-1], serier[-1]
    monster = bulltrendsmonster(avslutade)

    def stats(s):
        n = len(s)
        return {"dagar": n, "uppgang": float(s.iloc[-1] / 10 - 1),
                "snitt_per_dag": float((s.iloc[-1] / 10) ** (1 / n) - 1)}

    nyck = {
        "uppdaterad": uppdaterad,
        "senaste_kurs_datum": kurser.index[-1].strftime("%Y-%m-%d"),
        "senaste_kurs": float(kurser.iloc[-1]),
        "trender": {n: stats(s) for n, s in zip(namn, serier)},
        "nuvarande": stats(nuvarande),
        "snittlangd_tidigare": float(np.mean([len(s) for s in avslutade])),
    }
    nyck["avverkat_i_tid"] = nyck["nuvarande"]["dagar"] / nyck["snittlangd_tidigare"]

    # ---- Årsjämförelse --------------------------------------------------
    n0 = max(1, int((nuvarande.index < pd.Timestamp(AR, 1, 1)).sum()))  # dag i trenden vid årsskiftet
    k = np.arange(0, 261)
    giltig = n0 - 1 + k < len(monster)
    monster_pct = monster[n0 - 1 + k[giltig]] / monster[n0 - 1] - 1
    monster_datum = pd.bdate_range(pd.Timestamp(AR - 1, 12, 31), periods=len(monster_pct))

    i_ar = nuvarande[nuvarande.index >= pd.Timestamp(AR, 1, 1)]
    bas = nuvarande.iloc[n0 - 1]
    utfall = pd.concat([pd.Series([bas], index=[pd.Timestamp(AR - 1, 12, 31)]), i_ar])
    utfall_pct = (utfall / bas - 1).values

    nyck["ar"] = {
        "ar": AR,
        "utfall_hittills": float(utfall_pct[-1]),
        "monster_samma_dag": float(monster_pct[min(len(utfall_pct) - 1, len(monster_pct) - 1)]),
        "monster_helar": float(monster_pct[min(252, len(monster_pct) - 1)]),
    }

    rita_bulltrender(serier, namn, monster, nyck, uppdaterad)
    rita_ar(monster_pct, monster_datum, utfall_pct, utfall.index, AR, uppdaterad)

    vol_nyck, vol_data = volatilitet(serier, namn)
    nyck["volatilitet"] = vol_nyck
    nyck["regim"] = regim(serier, vol_data, nyck["avverkat_i_tid"])
    vol_nyck["regim"] = nyck["regim"]
    rita_volatilitet(vol_data, namn, vol_nyck, uppdaterad)

    nyck["arsvis"] = {str(k): v for k, v in rita_arsvis(kurser, 1998, uppdaterad).items()}

    kurser.rename("close").to_csv(UT / "omxspi.csv", index_label="date")
    (UT / "nyckeltal.json").write_text(json.dumps(nyck, ensure_ascii=False, indent=2))
    skriv_html(nyck)
    print(json.dumps(nyck, ensure_ascii=False, indent=2))


def skriv_html(n):
    a, v, r = n["ar"], n["volatilitet"], n["regim"]
    rf = {"gron": GRON, "gul": GUL, "rod": ROD}[r["farg"]]
    flaggor = "".join(f"<li>⚠ {f['sv']}</li>" for f in r["flaggor"]) or "<li class='ok'>Inga varningsflaggor</li>"
    rader = "".join(
        f"<tr><td>{t}</td><td>{x['dagar']}</td><td>{x['uppgang']:+.1%}</td><td>{x['snitt_per_dag']:+.3%}</td></tr>"
        for t, x in n["trender"].items()
    )
    ver = n["senaste_kurs_datum"]
    html = f"""<!DOCTYPE html>
<html lang="sv"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Bulltrender OMXSPI</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@300;400;600&display=swap" rel="stylesheet">
<style>
 :root{{--bg:#080c10;--surface:#0d1318;--surface2:#111820;--border:#1c2830;--border2:#253340;--text:#c8d8e8;--muted:#6a8090;--white:#f0f4f8;--teal:#00c8d4}}
 *{{box-sizing:border-box}} body{{background:var(--bg);color:var(--text);font-family:'IBM Plex Sans',sans-serif;font-weight:300;max-width:1240px;margin:0 auto;padding:32px 24px 48px}}
 a{{color:#0099ff}} h1{{font-family:'IBM Plex Mono',monospace;font-weight:500;color:var(--white);margin:0}} h1 em{{font-style:normal;color:var(--teal)}}
 .sub{{color:var(--muted);font-family:'IBM Plex Mono',monospace;font-size:12px;margin:8px 0 24px}}
 .card{{background:var(--surface);border:1px solid var(--border2);border-radius:6px;padding:20px;margin-bottom:20px}}
 h2{{font-family:'IBM Plex Mono',monospace;font-weight:500;font-size:16px;color:var(--white);margin:0 0 12px}}
 .kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px}}
 .kpi{{background:var(--surface2);border:1px solid var(--border);border-radius:5px;padding:12px 14px}}
 .kpi span{{font-family:'IBM Plex Mono',monospace;font-size:11px;letter-spacing:.1em;text-transform:uppercase;color:var(--muted)}}
 .kpi b{{display:block;font-family:'IBM Plex Mono',monospace;font-weight:400;font-size:24px;color:var(--white);margin-top:6px}}
 .regim{{border-left:4px solid {rf};padding-left:14px}} .regim h3{{color:{rf};font-family:'IBM Plex Mono',monospace;margin:0 0 6px}}
 .regim ul{{margin:10px 0 0;padding-left:0;list-style:none;color:var(--muted)}}
 img{{width:100%;height:auto;border:1px solid var(--border2);border-radius:6px;margin-top:12px}}
 table{{border-collapse:collapse;width:100%;font-family:'IBM Plex Mono',monospace;font-size:13px}}
 td,th{{padding:8px 10px;border-bottom:1px solid var(--border);text-align:right}} th{{color:var(--muted);font-weight:400}}
 td:first-child,th:first-child{{text-align:left}}
 details{{margin-top:12px;color:var(--muted);font-size:14px;line-height:1.6}} summary{{cursor:pointer;color:var(--text)}}
</style></head><body>
<h1>Bulltrender <em>OMXSPI</em></h1>
<p class="sub">Uppdateras automatiskt varje vecka · senaste kurs {ver} · <a href="../sector-trends/">← Sector Trends</a></p>

<div class="card regim"><h3>● {r['rubrik']['sv']}</h3><div>{r['text']['sv']}</div><ul>{flaggor}</ul></div>

<div class="card"><div class="kpis">
 <div class="kpi"><span>Dag i trenden</span><b>{n['nuvarande']['dagar']}</b></div>
 <div class="kpi"><span>Avverkat i tid</span><b>{n['avverkat_i_tid']:.0%}</b></div>
 <div class="kpi"><span>Utfall {a['ar']}</span><b>{a['utfall_hittills']:+.1%}</b></div>
 <div class="kpi"><span>Mönstret samma dag</span><b>{a['monster_samma_dag']:+.1%}</b></div>
</div>
<img src="ar.png?v={ver}" alt="OMXSPI {a['ar']} mot bulltrendsmönster"></div>

<div class="card"><h2>År för år</h2>
<img src="arsvis.png?v={ver}" alt="OMXSPI år för år"></div>

<div class="card"><h2>Alla bulltrender sedan 2002</h2>
<img src="bulltrender.png?v={ver}" alt="Bulltrender 2000-tal OMXSPI">
<table style="margin-top:14px"><tr><th>Trend</th><th>Handelsdagar</th><th>Uppgång</th><th>Snitt per dag</th></tr>{rader}</table></div>

<div class="card"><h2>Regeln testad 1999–2026</h2>
<p>Över MA200 = 100 % aktier · under MA200 = 50 % · under och MA200 faller = 0 %. Beslut på fredagens stängning.</p>
<img src="backtest.png?v={ver}" alt="Backtest av trendregeln" onerror="this.parentElement.style.display='none'"></div>

<div class="card"><h2>Volatilitet</h2><div class="kpis">
 <div class="kpi"><span>Volatilitet 20 d</span><b>{v['vol_20d']:.1%}</b></div>
 <div class="kpi"><span>Kvot 20 d / 100 d</span><b>{v['kvot_20_100']:.2f}</b></div>
 <div class="kpi"><span>Under toppen</span><b>{v['nedgang_fran_topp']:.1%}</b></div>
 <div class="kpi"><span>Största nedgång</span><b>{v['storsta_nedgang_i_trenden']:.1%}</b></div>
</div>
<img src="volatilitet.png?v={ver}" alt="Volatilitet i bulltrenden">
<details><summary>Så tolkar du volatiliteten</summary>
<p><b>Fyra lägen:</b> kurs upp + volatilitet ned = frisk bulltrend · kurs upp + volatilitet upp = tidig trend eller oro sent i trenden · kurs ned + volatilitet upp = stress/korrektion · kurs ned + volatilitet ned = lugnande efter oro eller långsam nedglidning.</p>
<p><b>Toppen i volatiliteten sammanfaller ofta med kursbotten.</b> När 20-dagarslinjen spikar och vänder ned brukar det värsta säljtrycket vara över.</p>
<p><b>Kort spik eller nytt läge?</b> En tillfällig spik som faller tillbaka under 100-dagarslinjen inom veckor är normalt. Stiger 100-dagarslinjen i månader samtidigt som nedgångarna blir djupare är det en varning.</p>
<p><b>Låg volatilitet betyder inte att det är säkert.</b> Toppar bildas ofta i lugn. Varningen är när volatiliteten börjar stiga utan att kursen gör nya toppar.</p>
<p><b>Följ nedsidan.</b> När nedsidesvolatiliteten (gul streckad) närmar sig den totala tar säljarna över rörligheten.</p>
<p>Volatilitet beskriver risken nu – inte riktningen. Endast information, inte finansiell rådgivning.</p>
</details></div>
</body></html>"""
    (UT / "index.html").write_text(html, encoding="utf-8")
    (UT / ".nojekyll").write_text("")  # GitHub Pages: hoppa över Jekyll


if __name__ == "__main__":
    main()
