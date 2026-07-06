// SystemC-AMS behavioral model for opamp-gain-x4-noninverting (AMS tier, docs/SIM.md §7).
//
// A timed-dataflow (TDF) module mirroring the v0 Python model
// (catalog/core/opamp-gain-x4-noninverting/model/behavior.py): four independent
// non-inverting gain channels sharing one set of rails, each with hard rail
// clipping referenced to GND:
//
//     OUT{k} = clip(GND + gain{k}*(IN{k} - GND), VEE + margin, VCC - margin)
//
// Ports match cell.yaml exactly (IN1..IN4, OUT1..OUT4, VCC, VEE, GND). Channels
// are independent (no crosstalk in this ideal tier); rails are shared.
// Constructor arguments, by the AMS tier convention, are the gain-prefixed idiom
// params in sorted order (gain1, gain2, gain3, gain4) followed by the rail
// headroom `margin` (v0 DEFAULT_RAIL_MARGIN = 0.1 V). No timestamps, no I/O:
// a pure function of the cell, safe to golden.
//
// Targets SystemC-AMS 2.x. Standalone C++ (emit, don't bind — DESIGN.md §4):
// no cppyy/PySysC dependency; compiled out-of-process by the AMS gate.
#ifndef INFERSYNTH_AMS_OPAMP_GAIN_X4_NONINVERTING_H
#define INFERSYNTH_AMS_OPAMP_GAIN_X4_NONINVERTING_H

#include <systemc-ams>
#include <algorithm>

SCA_TDF_MODULE(opamp_gain_x4_noninverting)
{
    sca_tdf::sca_in<double> IN1;
    sca_tdf::sca_in<double> IN2;
    sca_tdf::sca_in<double> IN3;
    sca_tdf::sca_in<double> IN4;
    sca_tdf::sca_in<double> VCC;
    sca_tdf::sca_in<double> VEE;
    sca_tdf::sca_in<double> GND;
    sca_tdf::sca_out<double> OUT1;
    sca_tdf::sca_out<double> OUT2;
    sca_tdf::sca_out<double> OUT3;
    sca_tdf::sca_out<double> OUT4;

    const double gain1;
    const double gain2;
    const double gain3;
    const double gain4;
    const double margin;

    opamp_gain_x4_noninverting(sc_core::sc_module_name nm, double gain1_, double gain2_,
                               double gain3_, double gain4_, double margin_ = 0.1)
        : IN1("IN1"), IN2("IN2"), IN3("IN3"), IN4("IN4"),
          VCC("VCC"), VEE("VEE"), GND("GND"),
          OUT1("OUT1"), OUT2("OUT2"), OUT3("OUT3"), OUT4("OUT4"),
          gain1(gain1_), gain2(gain2_), gain3(gain3_), gain4(gain4_), margin(margin_)
    {
    }

    void processing()
    {
        const double gnd = GND.read();
        const double lo = VEE.read() + margin;
        const double hi = VCC.read() - margin;
        OUT1.write(std::min(std::max(gnd + gain1 * (IN1.read() - gnd), lo), hi));
        OUT2.write(std::min(std::max(gnd + gain2 * (IN2.read() - gnd), lo), hi));
        OUT3.write(std::min(std::max(gnd + gain3 * (IN3.read() - gnd), lo), hi));
        OUT4.write(std::min(std::max(gnd + gain4 * (IN4.read() - gnd), lo), hi));
    }
};

#endif  // INFERSYNTH_AMS_OPAMP_GAIN_X4_NONINVERTING_H
