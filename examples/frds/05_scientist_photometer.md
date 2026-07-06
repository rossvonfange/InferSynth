Requirements for a field photometer — R. Okafor, limnology group
(email to the department's electronics shop, lightly edited)

We need six units of a submersible-ish (IP65 enclosure, we have these,
Hammond 1554) photometer for lake deployments, replacing the commercial
unit that was discontinued. What matters scientifically:

The instrument measures downwelling irradiance at four wavelengths (we use
interference filters we already own, 25 mm, centered 443/490/555/670 nm)
using a photodiode behind each filter. The critical requirement is DYNAMIC
RANGE: full sun at the surface down to ~0.1% of that at depth, which is
about 4 decades, and we need to resolve 1% changes at the bottom of that
range. Log amps were used in the old unit; we don't care how it's done.

Each channel must be sampled at least once per second, simultaneously
(within 10 ms) across channels — we compute ratios between channels, so
simultaneity matters more than absolute accuracy. Absolute cal we do
ourselves against the reference sensor twice a year; drift between cals
should stay under 2%.

Data goes to an SD card with a timestamp (RTC, battery backed, ±1 min/month
is fine). A cable to the surface provides 12 V from a gel cell; total draw
under 100 mA please, deployments run 14 h. No wireless — water.

Temperature of the sensor head matters (silicon responsivity), so include
a thermistor near the photodiodes; we correct in post.

If a channel saturates it must be OBVIOUS in the data (flag or railed
value), never silently clipped — this ruined a summer of data in 2019.
