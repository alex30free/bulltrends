"""
Veckobrev – skickar ett mejl på fredagskvällen med läget och vilken aktieandel reglerna anger.

Kör efter bulltrender.py (som skriver docs/nyckeltal.json). Hämtar dessutom:
  - sector-trends/data.json          (veckodata → de sex signalerna i "Bull-trend check")
  - sector-trends/daily_omxspi.json  (dagliga stängningar → MA200)

Reglerna (ändra fritt i REGLER nedan):
  A  100 % aktier  Säker bulltrend:   över MA200, MA200 stiger, minst 5 av 6 signaler, högst 10 % under toppen
  B   90 % aktier  Bulltrend med varningar: över MA200 men något av villkoren för A saknas
  C   50 % aktier  Brott på MA200:    under MA200, men inte tecken på björntrend
  D    0 % aktier  Trolig björntrend:  under MA200 och (MA200 faller ELLER minst 20 % under toppen ELLER högst 2 signaler)
  Återinträde: från C/D krävs två fredagsstängningar i rad över MA200 innan nivå A/B gäller igen.

Mejl skickas med SMTP (t.ex. Gmail med app-lösenord). Secrets: MAIL_USERNAME, MAIL_PASSWORD, MAIL_TO.
Endast information enligt dina egna regler – inte finansiell rådgivning.
"""

from __future__ import annotations

import json
import os
import smtplib
import sys
from datetime import date, datetime
from email.message import EmailMessage
from email.utils import make_msgid
from pathlib import Path

import numpy as np
import pandas as pd
import requests

ROT = Path(__file__).resolve().parent.parent
UT = ROT / "docs"
HISTORIK = UT / "signal_historik.csv"

RAW = "https://raw.githubusercontent.com/alex30free/sector-trends/main/"
SIDA = "https://alex30free.github.io/bulltrends/"
SECTOR_SIDA = "https://alex30free.github.io/sector-trends/"

REGLER = {
    "A": {"aktier": 100, "namn": "Säker bulltrend", "farg": "#00b37e"},
    "B": {"aktier": 90, "namn": "Bulltrend med varningar", "farg": "#d4a017"},
    "C": {"aktier": 50, "namn": "Brott på MA200", "farg": "#e67e22"},
    "D": {"aktier": 0, "namn": "Trolig björntrend", "farg": "#e74c3c"},
}
GRANS_DD_A = -0.10        # max nedgång från topp för nivå A
GRANS_DD_BJORN = -0.20    # björngräns
NARA_MA200 = 0.02         # inom 2 % över MA200 räknas som varning
SIGNALER_A = 5            # minst så många av 6 signaler för nivå A
SIGNALER_BJORN = 2        # högst så många → björntecken


# ---------------------------------------------------------------- data
def hamta(url):
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    return r.json()


def ma(a, n):
    out = [None] * len(a)
    for k in range(n - 1, len(a)):
        w = a[k - n + 1 : k + 1]
        if any(v is None for v in w):
            continue
        out[k] = sum(w) / n
    return out


def sex_signaler(D):
    """Samma sex signaler som 'Bull-trend check' på sector-trends (veckodata, MA40)."""
    seg = next((g for g in D.get("segments", []) if g["id"] == "all" and any(v is not None for v in g["close"])), None)
    if seg is None:
        seg = next(g for g in D["segments"] if g["id"] == "large")
    mv = seg["close"]
    mma = ma(mv, 40)
    N = D["weeks"]["count"]
    closes = [s["close"] for s in D["stocks"].values()]
    mas = [ma(c, 40) for c in closes]

    pct, ad, cum = [], [], None
    for k in range(N):
        n = up = adv = dec = 0
        for c, m in zip(closes, mas):
            v = c[k]
            if v is not None and m[k] is not None:
                n += 1
                up += v >= m[k]
            if k > 0 and v is not None and c[k - 1] is not None:
                adv += v > c[k - 1]
                dec += v < c[k - 1]
        pct.append(up / n * 100 if n >= 10 else None)
        if adv + dec >= 10:
            cum = (cum or 0) + adv - dec
        ad.append(cum)
    adma = ma(ad, 40)

    k = len(mv) - 1
    while k > 52 and (mv[k] is None or mma[k] is None or pct[k] is None or adma[k] is None):
        k -= 1
    w = mv[k - 25 : k + 1]
    a, b = w[:13], w[13:]
    p = pct[k]
    sig = [
        ("Index över MA40 (veckor)", mv[k] >= mma[k]),
        ("MA40 stiger", mma[k] > mma[k - 4]),
        ("Högre än för ett år sedan", mv[k] > mv[k - 52]),
        ("Högre toppar och bottnar", max(b) > max(a) and min(b) > min(a)),
        (f"Bredd: {round(p)} % av aktierna över MA40", 1 if p >= 60 else (0.5 if p >= 40 else 0)),
        ("A/D-linjen över sitt MA40", ad[k] >= adma[k]),
    ]
    poang = float(sum(float(v) for _, v in sig))
    return poang, [(t, float(v)) for t, v in sig]


def dagligt(j):
    s = pd.Series(j["close"], index=pd.to_datetime(j["dates"])).dropna()
    m200 = s.rolling(200).mean()
    return s, m200


# ---------------------------------------------------------------- regler
def bestam_niva(st, forra, over_i_rad):
    over = st["kurs"] > st["ma200"]
    stiger = st["ma200_lutning"] > 0
    if not over:
        bjorn = (not stiger) or st["dd"] <= GRANS_DD_BJORN or st["signaler"] <= SIGNALER_BJORN
        return ("D" if bjorn else "C"), []
    # över MA200
    varningar = []
    if not stiger:
        varningar.append("MA200 stiger inte längre")
    if st["signaler"] < SIGNALER_A:
        varningar.append(f"Bara {st['signaler']:g} av 6 bulltrendsignaler")
    if st["dd"] < GRANS_DD_A:
        varningar.append(f"{st['dd']:.0%} under trendens topp")
    if st["kurs"] / st["ma200"] - 1 < NARA_MA200:
        varningar.append("Mindre än 2 % över MA200")
    if st["regim_farg"] == "rod":
        varningar.append("Volatilitetsregim: stress/korrektion")
    niva = "B" if varningar else "A"
    # återinträde: kom vi från C/D krävs två fredagar i rad över MA200
    if forra in ("C", "D") and over_i_rad < 2:
        return "C", ["Första veckan över MA200 – väntar på bekräftelse nästa fredag"]
    return niva, varningar


# ---------------------------------------------------------------- mejl
def bygg_mejl(st, niva, forra, varningar, sig, nyck):
    R = REGLER[niva]
    andrad = forra is not None and forra != niva
    ar = nyck.get("ar", {})
    reg = nyck.get("regim", {})
    vol = nyck.get("volatilitet", {})

    if andrad:
        fran = REGLER[forra]
        rubrik = f"ÄNDRING: {fran['aktier']} % → {R['aktier']} % aktier · {R['namn']}"
        atgard = (f"Reglerna anger en ändring från <b>{fran['aktier']} %</b> till <b>{R['aktier']} % aktier</b> "
                  f"({100 - R['aktier']} % kassa).")
    else:
        rubrik = f"Oförändrat: {R['aktier']} % aktier · {R['namn']}"
        atgard = f"Ingen ändring. Reglerna anger fortsatt <b>{R['aktier']} % aktier</b> och {100 - R['aktier']} % kassa."

    varn_html = "".join(f"<li>⚠ {v}</li>" for v in varningar) or "<li>Inga regelvarningar</li>"
    sig_html = "".join(
        f"<tr><td>{'✅' if v == 1 else ('➖' if v == 0.5 else '❌')}</td><td>{t}</td></tr>" for t, v in sig)
    flaggor = "".join(f"<li>{f['sv']}</li>" for f in reg.get("flaggor", []))
    trappa = "".join(
        f"<tr style='{'font-weight:bold;background:#f2f7f5' if k == niva else 'color:#888'}'>"
        f"<td>{k}</td><td>{v['aktier']} %</td><td>{v['namn']}</td></tr>" for k, v in REGLER.items())

    html = f"""<meta charset="utf-8"><div style="font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;max-width:680px;color:#222">
<div style="border-left:6px solid {R['farg']};padding:10px 16px;background:#fafafa">
 <div style="font-size:13px;color:#666">Veckobrev · OMXSPI · fredag {st['datum']}</div>
 <div style="font-size:26px;font-weight:700;color:{R['farg']}">{R['aktier']} % aktier · {100 - R['aktier']} % kassa</div>
 <div style="font-size:17px;font-weight:600">{R['namn']}{' – <span style="color:#c0392b">ÄNDRING</span>' if andrad else ''}</div>
 <p style="margin:8px 0 0">{atgard}</p>
</div>

<h3>Varför</h3>
<table style="border-collapse:collapse;font-size:14px">
<tr><td style="padding:3px 12px 3px 0">OMXSPI</td><td><b>{st['kurs']:.2f}</b> ({st['vecka']:+.1%} denna vecka)</td></tr>
<tr><td style="padding:3px 12px 3px 0">MA200</td><td>{st['ma200']:.2f} · kursen {st['kurs'] / st['ma200'] - 1:+.1%} {'över' if st['kurs'] > st['ma200'] else 'under'} · MA200 {'stiger' if st['ma200_lutning'] > 0 else 'faller'}</td></tr>
<tr><td style="padding:3px 12px 3px 0">Bulltrendsignaler</td><td>{st['signaler']:g} av 6</td></tr>
<tr><td style="padding:3px 12px 3px 0">Under toppen</td><td>{st['dd']:.1%}</td></tr>
<tr><td style="padding:3px 12px 3px 0">Dag i bulltrenden</td><td>{nyck.get('nuvarande', {}).get('dagar', '–')} · {nyck.get('avverkat_i_tid', 0):.0%} av snittlängden</td></tr>
<tr><td style="padding:3px 12px 3px 0">I år mot mönstret</td><td>{ar.get('utfall_hittills', 0):+.1%} mot {ar.get('monster_samma_dag', 0):+.1%}</td></tr>
<tr><td style="padding:3px 12px 3px 0">Volatilitet</td><td>{vol.get('vol_20d', 0):.1%} (kvot 20/100 d {vol.get('kvot_20_100', 0):.2f}) · {reg.get('rubrik', {}).get('sv', '')}</td></tr>
</table>
<ul style="font-size:14px">{varn_html}{flaggor}</ul>

<h3>De sex signalerna</h3>
<table style="font-size:14px">{sig_html}</table>

<h3>Trappan</h3>
<table style="border-collapse:collapse;font-size:14px" cellpadding="5">{trappa}</table>
<p style="font-size:12px;color:#666">D gäller under MA200 när MA200 faller, nedgången är minst 20 % eller högst 2 signaler är positiva.
Från C/D krävs två fredagar i rad över MA200 innan A/B gäller igen.</p>

<p><img src="cid:ARBILD" style="width:100%;max-width:680px"></p>
<p><img src="cid:VOLBILD" style="width:100%;max-width:680px"></p>
<p><a href="{SIDA}">Bulltrender</a> · <a href="{SECTOR_SIDA}">Sector Trends</a></p>
<p style="font-size:11px;color:#999">Automatiskt beräknat enligt dina egna regler. Endast information, inte finansiell rådgivning.
Historiska mönster garanterar inget om framtiden.</p>
</div>"""
    return rubrik, html


def skicka(rubrik, html):
    anv, lösen, till = (os.environ.get(k, "").strip() for k in ("MAIL_USERNAME", "MAIL_PASSWORD", "MAIL_TO"))
    if not (anv and lösen and till):
        print("Inga mejluppgifter (MAIL_USERNAME/MAIL_PASSWORD/MAIL_TO) – skriver bara docs/veckobrev.html")
        return False
    msg = EmailMessage()
    msg["Subject"] = f"OMXSPI veckobrev · {rubrik}"
    msg["From"] = anv
    msg["To"] = till
    msg.set_content("Öppna mejlet i HTML-läge för att se veckobrevet.")
    ar_id, vol_id = make_msgid()[1:-1], make_msgid()[1:-1]
    msg.add_alternative(html.replace("cid:ARBILD", f"cid:{ar_id}").replace("cid:VOLBILD", f"cid:{vol_id}"),
                        subtype="html")
    del_html = msg.get_payload()[1]
    for fil, cid in (("ar.png", ar_id), ("volatilitet.png", vol_id)):
        p = UT / fil
        if p.exists():
            del_html.add_related(p.read_bytes(), "image", "png", cid=f"<{cid}>")
    vard = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    with smtplib.SMTP_SSL(vard, int(os.environ.get("SMTP_PORT", "465"))) as s:
        s.login(anv, lösen)
        s.send_message(msg)
    print(f"Mejl skickat till {till.split('@')[0]}@…")
    return True


# ---------------------------------------------------------------- main
def main():
    D = hamta(RAW + "data.json")
    dag = hamta(RAW + "daily_omxspi.json")
    nyck = json.loads((UT / "nyckeltal.json").read_text()) if (UT / "nyckeltal.json").exists() else {}

    s, m200 = dagligt(dag)
    poang, sig = sex_signaler(D)
    topp = s[s.index >= pd.Timestamp("2022-09-29")].cummax()
    st = {
        "datum": s.index[-1].strftime("%Y-%m-%d"),
        "kurs": float(s.iloc[-1]),
        "vecka": float(s.iloc[-1] / s.iloc[-6] - 1),
        "ma200": float(m200.iloc[-1]),
        "ma200_lutning": float(m200.iloc[-1] / m200.iloc[-21] - 1),
        "signaler": poang,
        "dd": float(s.iloc[-1] / topp.iloc[-1] - 1),
        "regim_farg": nyck.get("regim", {}).get("farg", ""),
    }
    idag = date.today()
    if s.index[-1].date() < idag and os.environ.get("KRAV_DAGENS_DATA") == "1":
        print(f"Dagens stängning saknas ännu (senaste {st['datum']}) – avbryter, backup-körningen tar det.")
        sys.exit(0)

    hist = pd.read_csv(HISTORIK) if HISTORIK.exists() else pd.DataFrame(columns=["datum", "niva", "over_ma200"])
    hist = hist[hist["datum"] != st["datum"]]
    forra = hist["niva"].iloc[-1] if len(hist) else None
    over_i_rad = 1 if st["kurs"] > st["ma200"] else 0
    if over_i_rad:
        for v in reversed(hist["over_ma200"].tolist()):
            if int(v) != 1:
                break
            over_i_rad += 1

    niva, varningar = bestam_niva(st, forra, over_i_rad)
    rubrik, html = bygg_mejl(st, niva, forra, varningar, sig, nyck)
    print(rubrik)
    for v in varningar:
        print("  ⚠", v)

    UT.mkdir(exist_ok=True)
    (UT / "veckobrev.html").write_text(html, encoding="utf-8")
    ny = pd.DataFrame([{"datum": st["datum"], "niva": niva, "over_ma200": int(st["kurs"] > st["ma200"]),
                        "aktier": REGLER[niva]["aktier"], "kurs": round(st["kurs"], 2),
                        "ma200": round(st["ma200"], 2), "signaler": poang}])
    pd.concat([hist, ny]).to_csv(HISTORIK, index=False)

    redan = (UT / "senaste_mejl.txt")
    if os.environ.get("SKICKA_MAIL", "1") == "1":
        if redan.exists() and redan.read_text().strip() == st["datum"] and os.environ.get("TVINGA") != "1":
            print("Mejl för denna dag redan skickat.")
        elif skicka(rubrik, html):
            redan.write_text(st["datum"])


if __name__ == "__main__":
    main()
