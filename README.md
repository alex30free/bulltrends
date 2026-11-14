# Bulltrender OMXSPI

Räknar ut och ritar bulltrenderna på Stockholmsbörsen (OMXSPI) en gång i veckan, i samma stil som Kavastus grafer:

- **bulltrender.png**: alla bulltrender sedan 2002, omräknade till start = 10, plus bulltrendsmönstret (snittet av tidigare trender)
- **ar.png**: bulltrendsmönstret mot årets utfall, 0 % vid årsskiftet
- **nyckeltal.json**: dag i trenden, avverkat i tid, uppgång, snitt per dag
- **index.html**: en färdig sida med graferna och nyckeltalen

Allt hamnar i mappen `docs/` och publiceras med GitHub Pages.

## Kom igång (en gång)

1. Lägg alla filer i ett GitHub-repo (behåll mappstrukturen, även `.github/workflows/`).
2. **Settings → Pages**: välj *Deploy from a branch*, gren `main`, mapp `/docs`. Spara.
3. **Actions**: välj *Uppdatera bulltrender* och klicka **Run workflow** för att köra direkt första gången.
4. Efter någon minut finns sidan på `https://<ditt-användarnamn>.github.io/<repo-namn>/`.

Därefter körs jobbet automatiskt **varje måndag morgon**.

## Använda bilderna på hemsidan

Bilderna har fasta adresser som alltid visar senaste versionen:

```html
<img src="https://<användarnamn>.github.io/<repo>/ar.png" alt="OMXSPI mot bulltrendsmönster">
<img src="https://<användarnamn>.github.io/<repo>/bulltrender.png" alt="Bulltrender OMXSPI">
```

## Ändra trender

Redigera `data/trender.csv`. Sista raden är den pågående trenden (lämna `slut` tomt).

```
namn,start,slut
2002–2007,2002-10-09,2007-07-16
2008–2015,2008-11-20,2015-04-10
2016–2022,2016-02-11,2021-11-17
2022–,2022-09-29,
```

När en trend tar slut: fyll i slutdatum och lägg till en ny rad för nästa trend.

## Data

| Period | Källa | Fil |
|---|---|---|
| 2002–2007 | investing.com | `data/omxspi_2002_2007.csv` (fast) |
| nov 2008 → idag | Nasdaq via FRED (`NASDAQOMXSPI`) | hämtas varje körning |
| valfritt | egen data | `data/omxspi_extra.csv` (kolumner `date,close`, ersätter vid dubbletter) |

Om FRED inte svarar används den senast sparade datan (`docs/omxspi.csv`), så sidan går aldrig sönder.

## Köra lokalt

```bash
pip install -r requirements.txt
python scripts/bulltrender.py          # innevarande år
AR=2024 python scripts/bulltrender.py  # årsjämförelse för ett annat år
```

## Bra att veta

- GitHub stänger av schemalagda jobb i repon utan aktivitet på 60 dagar. Jobbets egna veckovisa commits brukar räcka, men får du ett mejl om det så klicka bara *Enable workflow* under Actions.
- FRED-datan kan ligga en dag efter.
