// SystemC-AMS behavioral model for opamp-gain-noninverting (AMS tier, docs/SIM.md §7).
//
// A timed-dataflow (TDF) module mirroring the v0 Python model
// (catalog/core/opamp-gain-noninverting/model/behavior.py): ideal non-inverting
// gain with hard rail clipping, referenced to GND:
//
//     OUT = clip(GND + gain*(IN - GND), VEE + margin, VCC - margin)
//
// Ports match cell.yaml exactly (IN, OUT, VCC, VEE, GND). Constructor arguments,
// by the AMS tier convention, are the gain-prefixed idiom params in sorted order
// (here: gain) followed by the rail headroom `margin` (the v0 tier's
// DEFAULT_RAIL_MARGIN = 0.1 V; not a cell.yaml param). No timestamps, no I/O:
// this header is a pure function of the cell, safe to golden.
//
// Targets SystemC-AMS 2.x (Accellera reference / proof-of-concept). This file is
// standalone C++ (emit, don't bind — DESIGN.md §4, RECON_HARVEST §1): it carries
// no cppyy/PySysC dependency and is compiled out-of-process by the AMS gate.
#ifndef INFERSYNTH_AMS_OPAMP_GAIN_NONINVERTING_H
#define INFERSYNTH_AMS_OPAMP_GAIN_NONINVERTING_H

#include <systemc-ams>
#include <algorithm>

SCA_TDF_MODULE(opamp_gain_noninverting)
{
    sca_tdf::sca_in<double> IN;
    sca_tdf::sca_in<double> VCC;
    sca_tdf::sca_in<double> VEE;
    sca_tdf::sca_in<double> GND;
    sca_tdf::sca_out<double> OUT;

    const double gain;
    const double margin;

    opamp_gain_noninverting(sc_core::sc_module_name nm, double gain_, double margin_ = 0.1)
        : IN("IN"), VCC("VCC"), VEE("VEE"), GND("GND"), OUT("OUT"),
          gain(gain_), margin(margin_)
    {
    }

    void processing()
    {
        const double gnd = GND.read();
        const double ideal = gnd + gain * (IN.read() - gnd);
        const double lo = VEE.read() + margin;
        const double hi = VCC.read() - margin;
        OUT.write(std::min(std::max(ideal, lo), hi));
    }
};

#endif  // INFERSYNTH_AMS_OPAMP_GAIN_NONINVERTING_H
