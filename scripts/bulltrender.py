"""
Bulltrender OMXSPI – räknar ut och ritar graferna varje vecka.

Gör samma sak som Google Sheets-arket:
  1. Alla bulltrender omräknade till start = 10, x-axel = handelsdagar sedan botten
  2. Bulltrendsmönstret (snittet av tidigare trender) mot årets utfall, 0 % vid årsskiftet

Data:
  - data/omxspi_2002_2007.csv   (investing.com, fast historik)
  - FRED serie NASDAQOMXSPI      (Nasdaq, från nov 2008, hämtas varje körning)
  - data/omxspi_extra.csv        (valfri – egen data som fyller i eller ersätter)

Resultat hamnar i docs/ (GitHub Pages):
  docs/bulltrender.png, docs/ar.png, docs/nyckeltal.json, docs/omxspi.csv, docs/index.html
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
AR = int(os.environ.get("AR", date.today().year))  # år för årsjämförelsen

FARGER = ["#4472C4", "#ED7D31", "#7F7F7F", "#70AD47", "#A5A5A5", "#FFC000"]
MONSTER_FARG = "#1F1F7A"
UTFALL_FARG = "#00B050"


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


def hamta_fred() -> pd.Series:
    r = requests.get(FRED_URL, timeout=60, headers={"User-Agent": "sector-trends-bulltrender"})
    r.raise_for_status()
    return las_csv(io.StringIO(r.text))


def ladda_kurser() -> pd.Series:
    delar = [las_csv(DATA / "omxspi_2002_2007.csv")]
    try:
        delar.append(hamta_fred())
    except Exception as e:  # noqa: BLE001
        print(f"VARNING: kunde inte hämta FRED ({e}). Använder sparad data.", file=sys.stderr)
        sparad = UT / "omxspi.csv"
        if sparad.exists():
            delar.append(las_csv(sparad))
    extra = DATA / "omxspi_extra.csv"
    if extra.exists():
        delar.append(las_csv(extra))
    s = pd.concat(delar)
    s = s[~s.index.duplicated(keep="last")].sort_index()
    return s


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
    ax.grid(axis="y", color="#E5E5E5", linewidth=0.8)
    ax.grid(axis="x", visible=False)
    for k in ("top", "right"):
        ax.spines[k].set_visible(False)
    ax.spines["left"].set_color("#BFBFBF")
    ax.spines["bottom"].set_color("#BFBFBF")
    ax.tick_params(colors="#595959")


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
                    fontsize=12, color="#262626", va="center", fontweight="bold" if ar_nuvarande else "normal")
    xm = np.arange(1, len(monster) + 1)
    ax.plot(xm, monster, color=MONSTER_FARG, linewidth=2.2, linestyle=(0, (6, 4)), label="Bulltrendsmönster (snitt)")

    ax.set_xlim(0, len(monster) * 1.06)
    lagst = min(float(x.min()) for x in serier if not x.empty)
    ax.set_ylim(bottom=min(8, np.floor(lagst * 0.95)))
    ax.set_xlabel("Handelsdagar sedan botten", color="#595959")
    ax.set_ylabel("Index (start = 10)", color="#595959")
    ax.set_title("BULLTRENDER 2000-TAL – OMXSPI SVERIGE", fontsize=18, color="#404040", pad=16)
    ax.legend(loc="upper left", frameon=False, fontsize=11)

    text = (f"Nuvarande trend: dag {nyck['nuvarande']['dagar']}\n"
            f"I tid {nyck['avverkat_i_tid']:.0%} avverkat av snittet\n"
            f"Uppgång hittills: {nyck['nuvarande']['uppgang']:+.1%}\n"
            f"Snitt per dag: {nyck['nuvarande']['snitt_per_dag']:+.3%}")
    ax.text(0.985, 0.04, text, transform=ax.transAxes, ha="right", va="bottom", fontsize=12,
            bbox=dict(boxstyle="round,pad=0.6", facecolor="#E2F0D9", edgecolor="none"))
    fig.text(0.01, 0.01, f"Källa: Nasdaq via FRED, investing.com · Uppdaterad {uppdaterad}",
             fontsize=9, color="#8C8C8C")
    fig.tight_layout(rect=[0, 0.03, 1, 1])
    fig.savefig(UT / "bulltrender.png")
    plt.close(fig)


def rita_ar(monster_pct, monster_datum, utfall_pct, utfall_datum, ar, uppdaterad):
    fig, ax = plt.subplots(figsize=(16, 9), dpi=120)
    stil(ax)
    ax.axhline(0, color="#BFBFBF", linewidth=1)
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
    ax.set_title(f"RESULTAT OMXSPI MOT BULLTRENDSMÖNSTER 2000-TAL – {ar}", fontsize=18, color="#404040", pad=16)
    ax.legend(loc="upper left", frameon=False, fontsize=12)
    fig.text(0.01, 0.01, f"Källa: Nasdaq via FRED, investing.com · Uppdaterad {uppdaterad}",
             fontsize=9, color="#8C8C8C")
    fig.tight_layout(rect=[0, 0.03, 1, 1])
    fig.savefig(UT / "ar.png")
    plt.close(fig)


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

    kurser.rename("close").to_csv(UT / "omxspi.csv", index_label="date")
    (UT / "nyckeltal.json").write_text(json.dumps(nyck, ensure_ascii=False, indent=2))
    skriv_html(nyck)
    print(json.dumps(nyck, ensure_ascii=False, indent=2))


def skriv_html(n):
    a = n["ar"]
    rader = "".join(
        f"<tr><td>{t}</td><td>{v['dagar']}</td><td>{v['uppgang']:+.1%}</td><td>{v['snitt_per_dag']:+.3%}</td></tr>"
        for t, v in n["trender"].items()
    )
    html = f"""<!DOCTYPE html>
<html lang="sv"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Bulltrender OMXSPI</title>
<style>
 body{{font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;max-width:1100px;margin:2rem auto;padding:0 1rem;color:#262626}}
 h1{{font-weight:600;margin-bottom:.2rem}} .sub{{color:#737373;margin-top:0}}
 .kort{{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:1rem;margin:1.5rem 0}}
 .kort div{{background:#F5F7FA;border-radius:10px;padding:1rem}} .kort b{{display:block;font-size:1.6rem}}
 img{{width:100%;height:auto;border:1px solid #EEE;border-radius:8px;margin:1rem 0}}
 table{{border-collapse:collapse;width:100%}} td,th{{padding:.45rem .6rem;border-bottom:1px solid #EEE;text-align:right}}
 td:first-child,th:first-child{{text-align:left}}
</style></head><body>
<h1>Bulltrender OMXSPI</h1>
<p class="sub">Uppdateras automatiskt varje vecka · senaste kurs {n['senaste_kurs_datum']}</p>
<div class="kort">
 <div>Dag i nuvarande trend<b>{n['nuvarande']['dagar']}</b></div>
 <div>Avverkat i tid (mot snittet)<b>{n['avverkat_i_tid']:.0%}</b></div>
 <div>Utfall {a['ar']} hittills<b>{a['utfall_hittills']:+.1%}</b></div>
 <div>Mönstret vid samma dag<b>{a['monster_samma_dag']:+.1%}</b></div>
</div>
<img src="ar.png" alt="OMXSPI {a['ar']} mot bulltrendsmönster">
<img src="bulltrender.png" alt="Bulltrender 2000-tal OMXSPI">
<table><tr><th>Trend</th><th>Handelsdagar</th><th>Uppgång</th><th>Snitt per dag</th></tr>{rader}</table>
</body></html>"""
    (UT / "index.html").write_text(html, encoding="utf-8")


if __name__ == "__main__":
    main()
