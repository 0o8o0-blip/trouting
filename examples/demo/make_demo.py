"""Build the demo board: a small USB-C microcontroller board, placed but not routed.

Run it with KiCad's Python (pcbnew), e.g. with the KiCad snap:

    snap run --shell kicad -c "python3 $PWD/examples/demo/make_demo.py"

It writes demo.kicad_pcb and demo.kicad_pro next to itself. The design is made
up for this demo: a 32-pin QFN microcontroller, USB-C with a 3.3 V regulator,
a crystal, two LEDs, a button, an I/O header and a debug header, with a GND
pour on the back.
"""
import json
import os
import sys

import pcbnew

HERE = os.path.dirname(os.path.abspath(__file__))
LIBS = os.environ.get("KICAD9_FOOTPRINT_DIR", "/snap/kicad/current/usr/share/kicad/footprints")
X0, Y0, W, H = 100.0, 100.0, 40.0, 26.0          # board outline, mm

mm = pcbnew.FromMM
board = pcbnew.BOARD()
nets = {}


def net(name):
    if name not in nets:
        nets[name] = pcbnew.NETINFO_ITEM(board, name)
        board.Add(nets[name])
    return nets[name]


def part(ref, lib, name, x, y, rot=0, value="", pins=None):
    """Place a footprint; pins maps pad number -> net name (every pad with that number)."""
    fp = pcbnew.FootprintLoad(os.path.join(LIBS, lib + ".pretty"), name)
    fp.SetReference(ref)
    fp.SetValue(value or name)
    fp.Value().SetVisible(False)
    fp.Reference().SetTextSize(pcbnew.VECTOR2I(mm(0.8), mm(0.8)))
    fp.Reference().SetTextThickness(mm(0.12))
    fp.Reference().SetVisible(ref[0] not in "RCHD")     # the 0402 passives would bury the picture in labels
    board.Add(fp)
    fp.SetPosition(pcbnew.VECTOR2I(mm(X0 + x), mm(Y0 + y)))
    fp.SetOrientationDegrees(rot)
    for p in fp.Pads():
        if pins and p.GetNumber() in pins:
            p.SetNet(net(pins[p.GetNumber()]))
    return fp


R = ("Resistor_SMD", "R_0402_1005Metric")
C = ("Capacitor_SMD", "C_0402_1005Metric")
C6 = ("Capacitor_SMD", "C_0603_1608Metric")


def two(ref, kind, x, y, a, b, rot=0, value=""):
    return part(ref, kind[0], kind[1], x, y, rot, value, {"1": a, "2": b})


# ---- the microcontroller: pins grouped towards what they connect to ----
mcu = {"1": "+3V3", "2": "USB_DM", "3": "USB_DP", "4": "GND", "5": "XIN", "6": "XOUT", "7": "NRST", "8": "+3V3",
       "9": "ADC0", "10": "ADC1", "11": "TX", "12": "RX", "13": "SDA", "14": "SCL", "16": "GND",
       "17": "+3V3", "18": "SWDIO", "19": "SWCLK", "24": "GND",
       "25": "+3V3", "26": "LED1", "27": "LED2", "28": "BTN", "33": "GND"}
part("U1", "Package_DFN_QFN", "QFN-32-1EP_5x5mm_P0.5mm_EP3.45x3.45mm", 20, 13, 0, "MCU", mcu)
two("C3", C, 16.2, 10.6, "+3V3", "GND", 90, "100n")
two("C4", C, 16.2, 15.6, "+3V3", "GND", 90, "100n")
two("C5", C, 23.8, 15.8, "+3V3", "GND", 90, "100n")
two("C6", C, 22.4, 9.4, "+3V3", "GND", 0, "100n")

# ---- USB-C, regulator ----
usb = {"A1": "GND", "B12": "GND", "A12": "GND", "B1": "GND", "A4": "VBUS", "B9": "VBUS", "A9": "VBUS", "B4": "VBUS",
       "A5": "CC1", "B5": "CC2", "A6": "USB_DP_C", "B6": "USB_DP_C", "A7": "USB_DM_C", "B7": "USB_DM_C", "S1": "GND"}
part("J1", "Connector_USB", "USB_C_Receptacle_HRO_TYPE-C-31-M-12", 3.9, 13, -90, "USB-C", usb)
two("R1", R, 10.4, 8.0, "CC1", "GND", 0, "5k1")
two("R2", R, 10.4, 16.8, "CC2", "GND", 0, "5k1")
two("R8", R, 12.6, 12.2, "USB_DM_C", "USB_DM", 0, "22")
two("R9", R, 12.6, 13.8, "USB_DP_C", "USB_DP", 0, "22")
part("U2", "Package_TO_SOT_SMD", "SOT-23-5", 11.6, 4.2, 0, "LDO 3.3V",
     {"1": "VBUS", "2": "GND", "3": "VBUS", "5": "+3V3"})
two("C1", C6, 8.0, 4.2, "VBUS", "GND", 90, "1u")
two("C2", C6, 15.2, 4.2, "+3V3", "GND", 90, "1u")

# ---- crystal and reset ----
part("Y1", "Crystal", "Crystal_SMD_3225-4Pin_3.2x2.5mm", 13.2, 19.6, 0, "16MHz",
     {"1": "XIN", "3": "XOUT", "2": "GND", "4": "GND"})
two("C7", C, 10.4, 19.6, "XIN", "GND", 90, "12p")
two("C8", C, 16.0, 19.6, "XOUT", "GND", 90, "12p")
two("R10", R, 18.2, 18.0, "+3V3", "NRST", 0, "10k")
two("C9", C, 20.2, 18.8, "NRST", "GND", 90, "100n")

# ---- LEDs and the button ----
two("R3", R, 26.0, 3.4, "LED1", "LED1_A", 0, "1k")
two("R4", R, 26.0, 5.4, "LED2", "LED2_A", 0, "1k")
part("D1", "LED_SMD", "LED_0603_1608Metric", 29.4, 3.4, 180, "green", {"2": "LED1_A", "1": "GND"})
part("D2", "LED_SMD", "LED_0603_1608Metric", 29.4, 5.4, 180, "red", {"2": "LED2_A", "1": "GND"})
part("SW1", "Button_Switch_SMD", "SW_Push_1P1T_NO_CK_KMR2", 30.6, 9.8, 0, "BOOT", {"1": "BTN", "2": "GND"})
two("R5", R, 26.6, 9.4, "+3V3", "BTN", 90, "10k")

# ---- headers: I/O along the bottom (in a different order than the pins), debug on the right ----
part("J2", "Connector_PinHeader_2.54mm", "PinHeader_1x08_P2.54mm_Vertical", 10.6, 23.4, 90, "I/O",
     {"1": "+3V3", "2": "GND", "3": "SDA", "4": "SCL", "5": "TX", "6": "RX", "7": "ADC0", "8": "ADC1"})
two("R6", R, 25.6, 18.8, "+3V3", "SDA", 90, "4k7")
two("R7", R, 27.0, 18.8, "+3V3", "SCL", 90, "4k7")
part("J3", "Connector_PinHeader_2.54mm", "PinHeader_1x04_P2.54mm_Vertical", 37.0, 8.2, 0, "SWD",
     {"1": "+3V3", "2": "SWDIO", "3": "SWCLK", "4": "GND"})

for i, (x, y) in enumerate([(3.2, 3.2), (W - 3.2, 3.2), (3.2, H - 3.2), (W - 3.2, H - 3.2)]):
    part("H%d" % (i + 1), "MountingHole", "MountingHole_2.7mm_M2.5", x, y, 0, "M2.5")

# ---- outline, title, GND pour on the back ----
edge = pcbnew.PCB_SHAPE(board)
edge.SetShape(pcbnew.SHAPE_T_RECT)
edge.SetStart(pcbnew.VECTOR2I(mm(X0), mm(Y0)))
edge.SetEnd(pcbnew.VECTOR2I(mm(X0 + W), mm(Y0 + H)))
edge.SetLayer(pcbnew.Edge_Cuts)
edge.SetWidth(mm(0.1))
board.Add(edge)

txt = pcbnew.PCB_TEXT(board)
txt.SetText("Trouting demo")
txt.SetLayer(pcbnew.F_SilkS)
txt.SetPosition(pcbnew.VECTOR2I(mm(X0 + 33.6), mm(Y0 + 18.6)))
txt.SetTextSize(pcbnew.VECTOR2I(mm(0.9), mm(0.9)))
txt.SetTextThickness(mm(0.18))
board.Add(txt)

zone = pcbnew.ZONE(board)
zone.SetLayer(pcbnew.B_Cu)
zone.SetNet(net("GND"))
ol = zone.Outline()
ol.NewOutline()
for x, y in [(0, 0), (W, 0), (W, H), (0, H)]:
    ol.Append(mm(X0 + x), mm(Y0 + y))
zone.SetLocalClearance(mm(0.25))
zone.SetMinThickness(mm(0.25))
zone.SetThermalReliefGap(mm(0.4))
zone.SetThermalReliefSpokeWidth(mm(0.4))
board.Add(zone)

out = os.path.join(HERE, "demo.kicad_pcb")
board.Save(out)

# net classes live in the project file: power nets 0.4 mm
pro = {
    "meta": {"filename": "demo.kicad_pro", "version": 3},
    "net_settings": {
        "meta": {"version": 4},
        "classes": [
            {"name": "Default", "clearance": 0.2, "track_width": 0.2, "via_diameter": 0.6, "via_drill": 0.3,
             "microvia_diameter": 0.3, "microvia_drill": 0.1, "diff_pair_gap": 0.25, "diff_pair_via_gap": 0.25,
             "diff_pair_width": 0.2, "bus_width": 12, "wire_width": 6, "line_style": 0, "priority": 2147483647,
             "pcb_color": "rgba(0, 0, 0, 0.000)", "schematic_color": "rgba(0, 0, 0, 0.000)"},
            {"name": "Power", "clearance": 0.2, "track_width": 0.4, "via_diameter": 0.6, "via_drill": 0.3,
             "microvia_diameter": 0.3, "microvia_drill": 0.1, "diff_pair_gap": 0.25, "diff_pair_via_gap": 0.25,
             "diff_pair_width": 0.2, "bus_width": 12, "wire_width": 6, "line_style": 0, "priority": 0,
             "pcb_color": "rgba(0, 0, 0, 0.000)", "schematic_color": "rgba(0, 0, 0, 0.000)"},
        ],
        "netclass_patterns": [{"netclass": "Power", "pattern": "VBUS"}, {"netclass": "Power", "pattern": "+3V3"}],
    },
}
json.dump(pro, open(os.path.join(HERE, "demo.kicad_pro"), "w"), indent=2)
print("wrote", out)
sys.stdout.flush()
os._exit(0)
