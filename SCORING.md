# Readiness scoring rules (v1)

The code is [backend/app/scoring.py](backend/app/scoring.py) and the tests are [backend/tests/test_scoring.py](backend/tests/test_scoring.py). If you change a number in one place, change it here too and bump `SCORING_VERSION`. Every run records which version scored it.

These rules are invented for the exercise. They are meant to be explicit and testable, not realistic.

## Factors (100 points total)

| Factor | Source | Max | Rule |
|---|---|---|---|
| **Flood zone** | FEMA National Flood Hazard Layer | 35 | Zone **A\*** or **V\***, or flagged as a Special Flood Hazard Area → **0**. Zone **X** marked "0.2 PCT" (moderate) → **20**. Any other Zone **X** (minimal) → **35**. Zone **D** (not studied) → **15**. Open water → **0**. **Any other code → unavailable.** We don't guess. |
| **Elevation** | USGS Elevation Point Query Service | 25 | Under 10 ft → **0**. 10–49 ft → **12**. 50–6,999 ft → **25**. 7,000 ft or more → **10**. |
| **Very hot days** | Open-Meteo historical archive, last full calendar year | 25 | Days with a max temperature ≥ 35 °C: 0–7 → **25**. 8–30 → **15**. 31–60 → **5**. More than 60 → **0**. |
| **Annual precipitation** | Open-Meteo historical archive, last full calendar year | 15 | 300–1,500 mm → **15**. 150–299 mm or 1,501–2,500 mm → **7**. Under 150 mm or over 2,500 mm → **0**. |

## When a factor is unavailable

A factor is **unavailable** when its source timed out, was rate-limited, returned an error, returned something that isn't valid data (for example, USGS's `-1000000` no-data value, or a year of weather that is mostly empty), or had nothing for this point.

An unavailable factor:

- gets **no points, and is not scored as zero**. It is stored as `NULL`, and a database constraint rejects any unavailable factor that has points;
- is **left out of the denominator**, so the score is `points earned ÷ points of the factors we actually have × 100`;
- turns the run's status to **partial**. The UI then shows a banner that names each missing factor and why it's missing.

If less than **50 of the 100 points** could be assessed, there is **no score** at all. The run shows "not enough data".

## Verdict

1. **Knock-out rule:** if the flood zone is high risk (A/V/SFHA), the verdict is **reject**, whatever the other factors say.
2. With every factor available: score ≥ 70 → **pursue**, 40–69 → **review**, under 40 → **reject**.
3. With some factors unavailable, the machine works out the lowest and highest score the location *could* have had (every missing factor at 0, or every missing factor at full points). It commits to pursue or reject **only if both extremes land in the same band**. Otherwise the verdict is **review**, and the reason gives the range, for example "could be anywhere from 40 to 75 depending on Flood zone".
4. With no score (under 50% coverage): **review**.

An analyst can override the verdict with a written reason. The machine score and verdict on the run are never changed. The override is stored next to them, along with the run it was made against.
