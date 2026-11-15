"""
Bulltrender OMXSPI – räknar ut och ritar graferna varje vecka.

Gör samma sak som Google Sheets-arket:
  1. Alla bulltrender omräknade till start = 10, x-axel = handelsdagar sedan botten
  2. Bulltrendsmönstret (snittet av tidigare trender) mot årets utfall, 0 % vid årsskiftet

Data:
  - data/omxspi_2002_2007.csv   (investing.com, fast historik)
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


def ladda_kurser() -> pd.Series:
    delar = [las_csv(DATA / "omxspi_2002_2007.csv")]
    sparad = UT / "omxspi.csv"
    if sparad.exists():
        delar.append(las_csv(sparad))
    ny = hamta_ny_data()
    if ny is not None:
        delar.append(ny)
    else:
        print("VARNING: ingen källa svarade – använder sparad data.", file=sys.stderr)
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
    ax1.set_xlabel("Handelsdagar sedan botten", color="#595959")
    ax1.set_title("VOLATILITET PER TRENDDAG (60 dagar, årstakt)", fontsize=15, color="#404040", loc="left")
    ax1.legend(loc="upper right", frameon=False, fontsize=11, ncol=4)

    # 2) Nuvarande trend: 20 d mot 100 d (+ nedsidesvolatilitet)
    ax2 = fig.add_subplot(gs[1])
    stil(ax2)
    v20, v100, ned = g["v20"], g["v100"], g["ned20"]
    ax2.plot(v100.index, v100.values, color="#7F7F7F", linewidth=2, label="100 dagar (normalläge)")
    ax2.plot(v20.index, v20.values, color="#C00000", linewidth=1.8, label="20 dagar")
    ax2.plot(ned.index, ned.values, color="#ED7D31", linewidth=1.2, linestyle=(0, (3, 2)), label="Nedsida 20 dagar")
    upp = (v20 > v100).reindex(v20.index).fillna(False)
    ax2.fill_between(v20.index, v100.reindex(v20.index), v20, where=upp, color="#C00000", alpha=0.12,
                     label="Stigande (20 d > 100 d)")
    ax2.yaxis.set_major_formatter(mtick.PercentFormatter(1.0, decimals=0))
    ax2.set_title("NUVARANDE BULLTREND – STIGER ELLER SJUNKER VOLATILITETEN?", fontsize=15,
                  color="#404040", loc="left")
    ax2.legend(loc="upper right", frameon=False, fontsize=10, ncol=4)
    kvot = nyck["kvot_20_100"]
    lage = "STIGANDE" if kvot > 1.05 else ("SJUNKANDE" if kvot < 0.95 else "OFÖRÄNDRAD")
    ax2.text(0.01, 0.95, f"Kvot 20d/100d: {kvot:.2f} → {lage}\n20 d: {nyck['vol_20d']:.1%} · 100 d: {nyck['vol_100d']:.1%}",
             transform=ax2.transAxes, va="top", fontsize=12,
             bbox=dict(boxstyle="round,pad=0.5", facecolor="#F2F2F2", edgecolor="none"))

    # 3) Nedgång från trendens topp
    ax3 = fig.add_subplot(gs[2], sharex=ax2)
    stil(ax3)
    dd = g["dd"]
    ax3.fill_between(dd.index, dd.values, 0, color="#C00000", alpha=0.25)
    ax3.plot(dd.index, dd.values, color="#C00000", linewidth=1.2)
    ax3.axhline(-0.10, color="#ED7D31", linewidth=1, linestyle="--")
    ax3.axhline(-0.20, color="#7F0000", linewidth=1, linestyle="--")
    ax3.text(dd.index[0], -0.10, " −10 % korrektion", va="bottom", fontsize=9, color="#ED7D31")
    ax3.text(dd.index[0], -0.20, " −20 % björngräns", va="bottom", fontsize=9, color="#7F0000")
    ax3.set_ylim(min(-0.22, dd.min() * 1.1), 0.01)
    ax3.yaxis.set_major_formatter(mtick.PercentFormatter(1.0, decimals=0))
    ax3.set_title(f"NEDGÅNG FRÅN TRENDENS TOPP – nu {nyck['nedgang_fran_topp']:.1%}, "
                  f"största {nyck['storsta_nedgang_i_trenden']:.1%}", fontsize=15, color="#404040", loc="left")

    fig.suptitle("VOLATILITET I BULLTRENDEN – OMXSPI", fontsize=18, color="#404040", y=0.97)
    fig.text(0.01, 0.015, f"Källa: Nasdaq via FRED, investing.com · Uppdaterad {uppdaterad}",
             fontsize=9, color="#8C8C8C")
    fig.savefig(UT / "volatilitet.png", bbox_inches="tight")
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

    vol_nyck, vol_data = volatilitet(serier, namn)
    nyck["volatilitet"] = vol_nyck
    rita_volatilitet(vol_data, namn, vol_nyck, uppdaterad)

    kurser.rename("close").to_csv(UT / "omxspi.csv", index_label="date")
    (UT / "nyckeltal.json").write_text(json.dumps(nyck, ensure_ascii=False, indent=2))
    skriv_html(nyck)
    print(json.dumps(nyck, ensure_ascii=False, indent=2))


def skriv_html(n):
    a = n["ar"]
    v = n["volatilitet"]
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
<div class="kort">
 <div>Volatilitet 20 d<b>{v['vol_20d']:.1%}</b></div>
 <div>Kvot 20 d / 100 d<b>{v['kvot_20_100']:.2f}</b></div>
 <div>Nedgång från topp<b>{v['nedgang_fran_topp']:.1%}</b></div>
 <div>Största nedgång i trenden<b>{v['storsta_nedgang_i_trenden']:.1%}</b></div>
</div>
<img src="volatilitet.png" alt="Volatilitet i bulltrenden">
<table><tr><th>Trend</th><th>Handelsdagar</th><th>Uppgång</th><th>Snitt per dag</th></tr>{rader}</table>
</body></html>"""
    (UT / "index.html").write_text(html, encoding="utf-8")
    (UT / ".nojekyll").write_text("")  # GitHub Pages: hoppa över Jekyll


if __name__ == "__main__":
    main()
