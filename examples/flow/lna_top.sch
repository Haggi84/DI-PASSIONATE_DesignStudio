v {xschem version=3.4.5 file_version=1.2}
G {}
K {}
V {}
S {}
E {}
T {Example top cell of the SG13G2 LNA: only its pins matter here.
Package Studio reads ipin / opin / iopin labels as the die's pins.} -380 -260 0 0 0.3 0.3 {}
C {ipin.sym} -300 -60 0 0 {name=p1 lab=RFIN}
C {opin.sym} 300 -60 0 0 {name=p2 lab=RFOUT}
C {iopin.sym} 0 -200 0 0 {name=p3 lab=VCC}
C {ipin.sym} -300 40 0 0 {name=p4 lab=IDC}
C {ipin.sym} -300 100 0 0 {name=p5 lab=IHEAT}
C {iopin.sym} 0 200 0 0 {name=p6 lab=GNDHEAT}
C {opin.sym} 300 40 0 0 {name=p7 lab=VTMP}
