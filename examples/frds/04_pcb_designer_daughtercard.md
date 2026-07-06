# Redline notes — RF daughtercard for existing baseboard (rev A2)
# (author: layout, not systems. I know what I need, mostly where, not why.)

Board: 4-layer, 55 x 35 mm hard limit, 1.6 mm, ENIG. Castellated or
board-to-board — see mating section, this is fixed by the enclosure.

What goes on it:
- The LoRa radio section from the app note (SX1262 ref design, basically
  verbatim) — keep the RF half EXACTLY as the app note floorplan, 50R match
  out to a u.FL. I want the pi network footprint even if we DNP it.
- Level shift everything to the baseboard's 3V3. Radio side is 1V8.
  8 signals: SPI x4, BUSY, DIO1, DIO2, NRESET.
- Local LDO from the baseboard's 5V. Radio wants clean 1V8, budget 150 mA.
  Separate analog feed for the PA per app note. Ferrite ok.
- TCXO not crystal (we drift too much in the van installs, learned that
  the hard way on rev A1).
- Mating: 2x10 1.27mm header on the BOTTOM, pin 1 top-left when viewed
  from top. Stack height 6 mm. Keep-out under the RF section on the
  baseboard — I will handle that side.
- Test points: SPI + 1V8 + RF_EN, top side, 1 mm pads, labeled.
- No parts under the shield can taller than 2 mm. Shield can footprint:
  Laird BMI-S-203 or compatible.

Don't care: exact LDO part (pick something stocked), LED color, whether
DIO2 antenna switch control is used (route it anyway).
