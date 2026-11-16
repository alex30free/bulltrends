"""
Backtest av trappan (A 100 % / B 90 % / C 50 % / D 0 %) på OMXSPI, beslut varje fredag.

Beslut tas på fredagens stängning och gäller från nästa veckas avkastning (ingen framtidsinformation).
Kassa ger 0 %. Prisindex – utdelningar ingår inte (gäller alla alternativ lika när man är investerad).

Kör:  python scripts/backtest_trappan.py   → docs/backtest.png, docs/backtest.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mtick
import numpy as np
import pandas as pd

ROT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROT / "scripts"))
from bulltrender import BG, DAMPAD, GRON, GUL, KANT, ORANGE, RUTNAT, ROD, TEXT, VIT, stil  # noqa: E402

UT = ROT / "docs"
SECTOR_DATA = Path(sys.argv[1]) if len(sys.argv) > 1 else None  # lokal data.json (annars hämtas den)


# ------------------------------------------------------------------ data
def ladda():
    s = pd.read_csv(ROT / "data" / "omxspi_daglig.csv", parse_dates=["date"]).set_index("date")["close"].sort_index()
    if SECTOR_DATA:
        D = json.loads(SECTOR_DATA.read_text())
    else:
        import requests
        D = requests.get("https://raw.githubusercontent.com/alex30free/sector-trends/main/data.json", timeout=60).json()
    return s, D


def bredd(D):
    """Veckovis: andel aktier över MA40 och A/D-linjen mot sitt MA40 (samma som sector-trends)."""
    start = pd.Timestamp(D["weeks"]["start"])
    idx = pd.date_range(start, periods=D["weeks"]["count"], freq="7D")
    px = pd.DataFrame({k: v["close"] for k, v in D["stocks"].items()}, index=idx).astype(float)
    m40 = px.rolling(40).mean()
    n = (px.notna() & m40.notna()).sum(axis=1)
    pct = ((px >= m40) & m40.notna()).sum(axis=1) / n * 100
    pct[n < 10] = np.nan
    ch = px.diff()
    adv, dec = (ch > 0).sum(axis=1), (ch < 0).sum(axis=1)
    netto = (adv - dec).where(adv + dec >= 10)
    ad = netto.fillna(0).cumsum().where(netto.notna().cummax())
    ad_ok = (ad >= ad.rolling(40).mean()).astype(float).where(ad.rolling(40).mean().notna())
    return pd.DataFrame({"pct": pct, "ad_ok": ad_ok})


# ------------------------------------------------------------------ signaler per fredag
def bygg_signaler(s, D):
    m200 = s.rolling(200).mean()
    # 2008 saknas i dagsdatan → räkna inte MA200 över luckan
    lucka = s.index.to_series().diff().dt.days > 10
    grupp = lucka.cumsum()
    m200 = s.groupby(grupp).transform(lambda x: x.rolling(200).mean())
    lut = m200 / m200.groupby(grupp).shift(20) - 1
    topp = s.groupby(grupp).transform(lambda x: x.rolling(252, min_periods=60).max())
    dd = s / topp - 1
    r = np.log(s / s.groupby(grupp).shift(1))
    v20 = r.groupby(grupp).transform(lambda x: x.rolling(20).std()) * np.sqrt(252)
    v100 = r.groupby(grupp).transform(lambda x: x.rolling(100).std()) * np.sqrt(252)
    kurs20 = s / s.groupby(grupp).shift(20) - 1

    dag = pd.DataFrame({"kurs": s, "ma200": m200, "lut": lut, "dd": dd, "v20": v20, "v100": v100, "k20": kurs20})
    v = dag.resample("W-FRI").last().dropna(subset=["kurs"])

    # veckosignaler (MA40 veckor m.m.) på fredagsstängningar
    w = s.resample("W-FRI").last().dropna()
    wg = (w.index.to_series().diff().dt.days > 14).cumsum()
    ma40 = w.groupby(wg).transform(lambda x: x.rolling(40).mean())
    sig_ma = (w >= ma40).astype(float).where(ma40.notna())
    sig_lut = (ma40 > ma40.groupby(wg).shift(4)).astype(float).where(ma40.notna())
    sig_mom = (w > w.groupby(wg).shift(52)).astype(float).where(w.groupby(wg).shift(52).notna())
    hi_b, lo_b = w.groupby(wg).transform(lambda x: x.rolling(13).max()), w.groupby(wg).transform(lambda x: x.rolling(13).min())
    sig_hh = ((hi_b > hi_b.groupby(wg).shift(13)) & (lo_b > lo_b.groupby(wg).shift(13))).astype(float)
    sig_hh = sig_hh.where(hi_b.groupby(wg).shift(13).notna())

    b = bredd(D)
    b.index = b.index + pd.offsets.Week(weekday=4) * 0  # redan fredagar
    b = b.reindex(w.index, method="nearest", tolerance=pd.Timedelta(days=3))
    sig_pct = np.select([b["pct"] >= 60, b["pct"] >= 40], [1.0, 0.5], 0.0)
    sig_pct = pd.Series(sig_pct, index=w.index).where(b["pct"].notna())

    pris = pd.concat([sig_ma, sig_lut, sig_mom, sig_hh], axis=1).sum(axis=1, min_count=4)
    bred = pd.concat([sig_pct, b["ad_ok"]], axis=1).sum(axis=1, min_count=2)
    # före 2007 saknas bredd → skala prissignalerna till 6
    poang = (pris + bred).where(bred.notna(), pris * 6 / 4)
    v["signaler"] = poang.reindex(v.index)
    v["veckoavk"] = w.reindex(v.index).pct_change()
    return v.dropna(subset=["ma200", "signaler"])


# ------------------------------------------------------------------ regler
NIVA = {"A": 1.0, "B": 0.9, "C": 0.5, "D": 0.0}


def trappa(v, p):
    niva, forra, over_i_rad = [], None, 0
    for _, x in v.iterrows():
        over = x.kurs > x.ma200
        over_i_rad = over_i_rad + 1 if over else 0
        if not over:
            bjorn = (x.lut <= 0 and p["d_lut"]) or x.dd <= p["d_dd"] or x.signaler <= p["d_sig"]
            n = "D" if bjorn else "C"
        else:
            varning = (x.lut <= 0) or x.signaler < p["a_sig"] or x.dd < p["a_dd"] \
                or (x.kurs / x.ma200 - 1) < p["nara"] or (p["regim"] and x.k20 < 0 and x.v20 / x.v100 > 1.05)
            n = "B" if varning else "A"
            if forra in ("C", "D") and over_i_rad < p["bekrafta"]:
                n = "C" if p["c_steg"] else "D"
        if not p["c_steg"] and n == "C":
            n = "D"
        if not p["b_steg"] and n == "B":
            n = "A"
        niva.append(n)
        forra = n
    return pd.Series(niva, index=v.index)


def utvardera(v, niva, namn):
    vikt = niva.map(NIVA).shift(1).fillna(1.0)     # beslutet gäller nästa vecka
    r = v["veckoavk"].fillna(0)
    # luckan 2008 (ingen data) – ingen avkastning bokförs över luckan
    lucka = v.index.to_series().diff().dt.days > 14
    r[lucka] = 0
    strat = (1 + vikt * r).cumprod()
    ar = (v.index[-1] - v.index[0]).days / 365.25
    dd = strat / strat.cummax() - 1
    byten = int((niva != niva.shift()).sum() - 1)
    vr = (vikt * r)
    return {
        "namn": namn,
        "cagr": float(strat.iloc[-1] ** (1 / ar) - 1),
        "maxdd": float(dd.min()),
        "vol": float(vr.std() * np.sqrt(52)),
        "byten_per_ar": byten / ar,
        "tid_investerad": float(vikt.mean()),
        "kurva": strat,
        "vikt": vikt,
    }


PERIODER = [  # (namn, start, slut) – björnfaser och stora korrektioner
    ("Hösten 1998", "1998-07-01", "1998-12-31"),
    ("IT-kraschen 2000–02", "2000-03-01", "2003-03-31"),
    ("Finanskrisen 2007–09", "2007-06-01", "2009-03-31"),
    ("Euro-krisen 2011", "2011-02-01", "2011-12-31"),
    ("2015–16", "2015-04-01", "2016-03-31"),
    ("Hösten 2018", "2018-08-01", "2019-01-31"),
    ("Covid 2020", "2020-01-15", "2020-06-30"),
    ("Björntrend 2022", "2021-11-01", "2022-10-31"),
    ("Tullraset 2025", "2025-02-01", "2025-06-30"),
]


def periodstat(v, res):
    rad = []
    for namn, a, b in PERIODER:
        m = (v.index >= a) & (v.index <= b)
        if m.sum() < 4:
            continue
        rr = {"period": namn}
        for x in res:
            k = x["kurva"][m]
            rr[x["namn"]] = float((k / k.cummax() - 1).min())
        rad.append(rr)
    return pd.DataFrame(rad)


# ------------------------------------------------------------------ main
def main():
    s, D = ladda()
    v = bygg_signaler(s, D)

    bas = dict(d_lut=True, d_dd=-0.20, d_sig=2, a_sig=5, a_dd=-0.10, nara=0.02, regim=True, bekrafta=2,
               c_steg=True, b_steg=True)
    varianter = {
        "Trappan (nu)": bas,
        "Trappan utan 90 %-steg": {**bas, "b_steg": False},
        "Trappan utan 50 %-steg": {**bas, "c_steg": False},
        "Bara MA200 (100/0)": {**bas, "b_steg": False, "c_steg": False, "d_lut": False, "d_dd": -9, "d_sig": -1,
                               "bekrafta": 1},
        "MA200 + 2 v bekräftelse": {**bas, "b_steg": False, "c_steg": False, "d_lut": False, "d_dd": -9,
                                    "d_sig": -1, "bekrafta": 2},
    }
    res = [utvardera(v, pd.Series("A", index=v.index), "Köp och behåll")]
    nivaer = {}
    for namn, p in varianter.items():
        n = trappa(v, p)
        nivaer[namn] = n
        res.append(utvardera(v, n, namn))

    def regel(fn):
        niv, st = [], "A"
        for _, x in v.iterrows():
            st = fn(x, st)
            niv.append(st)
        return pd.Series(niv, index=v.index)

    def buffert(pct):
        def f(x, st):
            r = x.kurs / x.ma200 - 1
            if st == "A" and r < -pct:
                return "D"
            if st == "D" and r > pct:
                return "A"
            return st
        return f

    def tresteg(x, st):  # över = 100, under = 50, under + MA200 faller = 0
        if x.kurs > x.ma200:
            return "A"
        return "D" if x.lut <= 0 else "C"

    for namn, fn in [("MA200 buffert 1 %", buffert(0.01)), ("3 steg (100/50/0)", tresteg)]:
        n = regel(fn)
        nivaer[namn] = n
        res.append(utvardera(v, n, namn))
    m = v.index.to_series().dt.month
    sista_fredag = m != m.shift(-1)
    n = nivaer["Bara MA200 (100/0)"].where(sista_fredag).ffill().fillna("A")
    nivaer["MA200 månadsvis (Faber)"] = n
    res.append(utvardera(v, n, "MA200 månadsvis (Faber)"))

    tab = pd.DataFrame([{k: x[k] for k in ("namn", "cagr", "maxdd", "vol", "byten_per_ar", "tid_investerad")}
                        for x in res])
    per = periodstat(v, res)
    pd.set_option("display.width", 200)
    print(f"Testperiod: {v.index[0].date()} – {v.index[-1].date()} ({len(v)} veckor)")
    print(tab.to_string(index=False, float_format=lambda x: f"{x:.3f}"))
    print(per.to_string(index=False, float_format=lambda x: f"{x:.1%}"))

    # fördelning och "falsklarm" för trappan
    n = nivaer["3 steg (100/50/0)"]
    print("\nTid per nivå:", (n.value_counts(normalize=True).sort_index() * 100).round(1).to_dict())
    # hur gick det veckorna efter varje sänkning till C/D?
    fram = v["kurs"].shift(-13) / v["kurs"] - 1
    for lvl in ("C", "D"):
        in_ = (n == lvl) & (n.shift() != lvl) & (n.shift().isin(["A", "B"]) if lvl == "C" else (n.shift() != "D"))
        f = fram[in_].dropna()
        if len(f):
            print(f"Sänkning till {lvl}: {len(f)} ggr, index 13 v senare: median {f.median():+.1%}, "
                  f"lägre i {(f < 0).mean():.0%} av fallen")

    # graf
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(16, 11), dpi=120, height_ratios=[2.2, 1], sharex=True)
    for ax in (ax1, ax2):
        stil(ax)
    farger = {"Köp och behåll": "#8fa3b5", "3 steg (100/50/0)": GRON, "Bara MA200 (100/0)": GUL}
    for x in res:
        if x["namn"] in farger:
            ax1.plot(x["kurva"].index, x["kurva"].values, color=farger[x["namn"]], linewidth=2.4 if "3 steg" in x["namn"] else 1.6,
                     label=f"{x['namn']}  ({x['cagr']:+.1%}/år, värsta fall {x['maxdd']:.0%})")
    ax1.set_yscale("log")
    ax1.yaxis.set_major_formatter(mtick.FuncFormatter(lambda y, _: f"{y:g}×"))
    ax1.set_title("TREND-REGELN PÅ OMXSPI 1999–2026 – 1 kr investerad (beslut varje fredag)", fontsize=17, color=VIT, loc="left")
    ax1.legend(loc="upper left", frameon=False, fontsize=12)
    lucka = v.index.to_series().diff().dt.days > 14
    for t in v.index[lucka]:
        ax1.axvspan(v.index[v.index.get_loc(t) - 1], t, color=KANT, alpha=0.6)
        ax1.text(t, ax1.get_ylim()[0] * 1.05, " data saknas 2008", color=DAMPAD, fontsize=9, rotation=90, va="bottom")
    vikt = next(x for x in res if x["namn"] == "3 steg (100/50/0)")["vikt"] * 100
    ax2.fill_between(vikt.index, vikt.values, step="post", color=GRON, alpha=0.35)
    ax2.plot(vikt.index, vikt.values, drawstyle="steps-post", color=GRON, linewidth=1)
    ax2.set_ylim(0, 105)
    ax2.set_ylabel("Aktier %", color=DAMPAD)
    ax2.set_title("AKTIEANDEL MED 3 STEG: över MA200 100 % · under 50 % · under och MA200 faller 0 %", fontsize=13, color=VIT, loc="left")
    fig.text(0.01, 0.005, "Prisindex utan utdelningar, kassa 0 %, inga avgifter. Historik garanterar inget om framtiden.",
             fontsize=9, color=DAMPAD)
    fig.tight_layout(rect=[0, 0.02, 1, 1])
    fig.savefig(UT / "backtest.png")

    out = {"period": [str(v.index[0].date()), str(v.index[-1].date())],
           "resultat": tab.to_dict(orient="records"), "perioder": per.to_dict(orient="records")}
    (UT / "backtest.json").write_text(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
