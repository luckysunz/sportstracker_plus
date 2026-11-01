// =====================================================================
// Sportstracker Gehäuse v0  (Rückbox mit Neigung, ohne Standbein)
// Panel P2.5-2020-32S-64x64 (160 x 160 mm), Pi Zero WH + Adafruit RGB Matrix Bonnet
//
// Teile (Variable "part"):
//   ring     Montagering, wird mit M3 an die Messing-Einsaetze des Panels geschraubt
//   wedge    Keilgehaeuse (Rueckschale), wird ueber den Ring geschoben, 4 Seitenschrauben
//   foot     Fuss mit Pi, 5V-Buchse (rechts), Taster + Kamera (links), 4 Schrauben an wedge
//   *_print  Teile in Druckausrichtung (fuer STL-Export)
//   assembly Gesamtansicht mit Platzhaltern fuer Panel, Pi, Bonnet, Kamera
//
// Koordinaten (Gesamtansicht): X = rechts, von HINTEN gesehen; Y = nach vorn (Panel),
// Z = oben. Tiefe D = -Y (Abstand von der Panel-Unterkante nach hinten).
// Alle Masse in mm. Mit "(?)" markierte Werte sind Annahmen und noch zu pruefen.
// =====================================================================

part = "assembly";
cut  = "none";          // none | x | z   (nur Vorschau: Schnittebene)

// ---------------- Panel / Neigung ----------------
W = 160;  H = 160;      // Panel-Aussenmasse
alpha = 10;             // Neigung nach hinten (Grad)
wall = 2.4;             // Wandstaerke
panel_t = 15;           // Panel-Dicke Vorderseite bis Rueckflaeche (25 mm Gesamthoehe minus 10 mm Stecker)
top_clear = 14;         // freie Tiefe hinter der Rueckflaeche oben (10 mm Stecker/Kabel + 4 Luft)

// ---------------- Fuss ----------------
foot_h = 52;            // Aussenhoehe Fuss
foot_D = 110;           // Standtiefe gesamt (ab Panel-Unterkante)

// ---------------- Montagering ----------------
ring_t   = 5;           // Dicke
ring_w   = 9.6;         // Rahmenbreite
ring_clr = 0.4;         // Spiel zur Schale
ins_edge   = 6;         // Abstand Einsatz-Mitte zur Aussenkante (aus Foto geschaetzt) (?)
ins_corner = 35;        // Abstand Einsatz entlang der Kante von der Ecke (Angabe Frank)
side_v   = [62, 98];    // Hoehe (auf Panel) der Seitenschrauben Schale->Ring

// ---------------- Arretierungsstifte des Panels ----------------
pin_pos = 55;           // Abstand von der Ecke entlang der Kante (Gegenrichtung: 105 mm), ein Zapfen je Kante
pin_off = 2.5;          // Stifte 2,5 mm weiter aussen als die M3-Einsaetze (Angabe Frank)
pin_d   = 3.0;          // Stift-Durchmesser (?)
pin_h   = 3.0;          // Stift-Hoehe ueber der Panelrueckflaeche (?)
pin_clr = 2.0;          // Aussparung = Stift-Durchmesser + pin_clr

// ---------------- Elektronik ----------------
pi_front_gap = 12;      // Platz vor der Platine (Flachkabel) ab Rueckwand des Keils
pi_spacer = 11;         // Hoehe der weissen Abstandshalter Pi -> Bonnet (?)
jack_z  = 25;           // Mitte Hohlstecker-Buchse ueber Boden = 2,4+3+1,4+Spacer+1,6+5,5 (?)
jack_w  = 18;           // Oeffnung Breite (in Tiefenrichtung)
jack_h  = 16;           // Oeffnung Hoehe
tunnel_len = 30;        // Fuehrungskanal fuer den Stecker
cam_z = 26; btn_z = 26; // Hoehe Kamera / Taster
btn_d = 6.4;            // Bohrung Taster (6 mm Taster) (?)
lens_d = 10;            // Bohrung Kameralinse
cam_skin = 0.6;         // duenne Haut aussen ueber der Kameraoeffnung (spaeter durchbohren/ausschneiden)
pi_screw_d  = 2.7;      // Durchgangsloch M2,5 von aussen (Boden)
pi_head_d   = 5.4;      // Senkung fuer den Schraubenkopf unten
pi_head_t   = 2.0;      // Tiefe der Senkung
win_w = 90; win_z0 = 8; // Fenster Keil -> Fuss (Flachkabel, Stromkabel)

// ---------------- abgeleitet ----------------
ca = cos(alpha); sa = sin(alpha); ta = tan(alpha);
Zt = H*ca;  Dt = H*sa;
Db_in  = Dt + top_clear/ca;
Db_out = Db_in + wall;
Dc     = Db_out + pi_front_gap + 15.35;   // Mitte Pi in Tiefenrichtung
D_jack = Dc - 15.35 + 14;
D_cam  = Db_out + 34;
D_btn  = Db_out + 14;

function fz(z) = z*ta - 1;

// Profil (D,Z) ueber X-Breite w extrudieren (Y = -D)
module prof(pts, w) {
  translate([-w/2,0,0]) rotate([90,0,90])
    linear_extrude(height=w) polygon([for (p = pts) [-p[0], p[1]]]);
}
// lokale Panelkoordinaten (x, v nach oben auf dem Panel, d nach hinten) -> Welt
module to_world() {
  multmatrix([[1,0,0,0],[0,-sa,-ca,0],[0,ca,-sa,0],[0,0,0,1]]) children();
}

ins_top  = [[-(W/2-ins_corner), H-ins_edge], [(W/2-ins_corner), H-ins_edge]];
ins_side = [[-(W/2-ins_edge), ins_corner], [-(W/2-ins_edge), H-ins_corner],
            [ (W/2-ins_edge), ins_corner], [ (W/2-ins_edge), H-ins_corner]];
ins_bot  = [[-(W/2-ins_corner), ins_edge], [(W/2-ins_corner), ins_edge]];

pin_edge = ins_edge - pin_off;
pins = [[ (W/2-pin_pos), H-pin_edge],      // oben: 55 mm von der rechten Ecke
        [-(W/2-pin_edge),  H-pin_pos],      // links: 55 mm von der oberen Ecke
        [-(W/2-pin_pos),  pin_edge],        // unten: 55 mm von der linken Ecke
        [ (W/2-pin_edge), pin_pos]];        // rechts: 55 mm von der unteren Ecke

// ---------------- Montagering ----------------
module ring() {
  v0 = (wall + 0.4 + ring_t*sa)/ca;
  v1 = (Zt - wall - 0.4)/ca;
  xo = W/2 - wall - 0.2;
  difference() {
    translate([-xo, v0, 0]) cube([2*xo, v1-v0, ring_t]);
    translate([-(xo-ring_w), v0+ring_w, -1]) cube([2*(xo-ring_w), v1-v0-2*ring_w, ring_t+2]);
    for (p = concat(ins_top, ins_side, ins_bot))
      translate([p[0], p[1], -1]) cylinder(d=3.4, h=ring_t+2, $fn=24);
    // Aussparungen fuer die Arretierungsstifte
    for (p = pins)
      translate([p[0], p[1], -1]) cylinder(d=pin_d+pin_clr, h=ring_t+2, $fn=24);
    for (v = side_v) {
      translate([ xo+0.1, v, ring_t/2]) rotate([0,-90,0]) cylinder(d=2.6, h=9, $fn=20);
      translate([-xo-0.1, v, ring_t/2]) rotate([0, 90,0]) cylinder(d=2.6, h=9, $fn=20);
    }
  }
}

// ---------------- Keilgehaeuse ----------------
module wedge() {
  difference() {
    prof([[0,0],[Dt,Zt],[Db_out,Zt],[Db_out,0]], W);
    prof([[fz(wall),wall],[fz(Zt-wall),Zt-wall],[Db_in,Zt-wall],[Db_in,wall]], W-2*wall);
    // Fenster zum Fuss
    translate([-win_w/2, -(Db_out+1), win_z0]) cube([win_w, wall+2, foot_h-2*win_z0]);
    // Schrauben Fuss
    for (sx=[-1,1]) for (z=[8, foot_h-8])
      translate([sx*70, -(Db_in-1), z]) rotate([90,0,0]) cylinder(d=3.4, h=wall+2, $fn=24);
    // Aussparungen fuer die Arretierungsstifte (waagerechte Nuten an der Vorderkante)
    for (p = pins) {
      D0 = p[1]*sa;  Z0 = p[1]*ca;  len = pin_h + 1.5;
      translate([p[0], -(D0-1), Z0]) rotate([90,0,0]) cylinder(d=pin_d+pin_clr, h=len+1, $fn=24);
    }
    // Seitenschrauben zum Ring
    for (v = side_v) {
      D = v*sa + ring_t/2*ca;  Z = v*ca - ring_t/2*sa;
      translate([ W/2-wall-0.5, -D, Z]) rotate([0,90,0]) cylinder(d=3.4, h=wall+1, $fn=24);
      translate([-(W/2+0.5),    -D, Z]) rotate([0,90,0]) cylinder(d=3.4, h=wall+1, $fn=24);
    }
  }
}

// ---------------- Fuss ----------------
module foot() {
  xi = W/2 - wall;
  difference() {
    union() {
      difference() {
        translate([-W/2, -foot_D, 0]) cube([W, foot_D-Db_out, foot_h]);
        translate([-xi, -(foot_D-wall), wall]) cube([2*xi, foot_D-wall-Db_out+1, foot_h-2*wall]);
      }
      // Ohren fuer die Schrauben zum Keil
      for (sx=[-1,1]) for (z=[wall, foot_h-14])
        translate([sx>0 ? 64 : -xi, -(Db_out+8), z]) cube([xi-64, 8, 14-wall]);
      // Abstandshalter fuer Pi Zero (Lochbild 58 x 23)
      for (ix=[-29,29]) for (iD=[-11.5,11.5])
        translate([ix, -(Dc+iD), wall-0.01]) cylinder(d=6, h=3.01, $fn=24);
      // Fuehrungskanal 5V-Stecker (rechte Wand)
      translate([xi-tunnel_len, -(D_jack+jack_w/2+2.4), jack_z-jack_h/2-2.4])
        cube([tunnel_len+0.5, jack_w+4.8, jack_h+4.8]);
      // Aufnahme Kameraplatine (4 Pfosten, Lochbild 21 x 12,5) (?)
      for (iD=[-10.5,10.5]) for (iz=[-6.25,6.25])
        translate([-xi-0.5, -(D_cam+iD), cam_z+iz]) rotate([0,90,0]) cylinder(d=4.5, h=3.5, $fn=20);
    }
    // Pi Zero von aussen verschraubt: Durchgangsloch M2,5 + Senkung im Boden
    for (ix=[-29,29]) for (iD=[-11.5,11.5]) {
      translate([ix, -(Dc+iD), -1]) cylinder(d=pi_screw_d, h=wall+3+2, $fn=20);
      translate([ix, -(Dc+iD), -1]) cylinder(d=pi_head_d, h=pi_head_t+1, $fn=28);
    }
    // Pilotloecher Ohren
    for (sx=[-1,1]) for (z=[8, foot_h-8])
      translate([sx*70, -(Db_out-1), z]) rotate([90,0,0]) cylinder(d=2.6, h=9, $fn=20);
    // Oeffnung 5V-Buchse
    translate([xi-tunnel_len-1, -(D_jack+jack_w/2), jack_z-jack_h/2]) cube([tunnel_len+wall+2, jack_w, jack_h]);
    // Schlitze fuer Kabelbinder (Zugentlastung)
    for (s=[-1,1])
      translate([xi-0.5, -(D_jack+s*15)-0.9, jack_z-2.1]) cube([wall+1, 1.8, 4.2]);
    // Taster + Kamera (linke Wand)
    translate([-W/2-1, -D_btn, btn_z]) rotate([0,90,0]) cylinder(d=btn_d, h=wall+2, $fn=32);
    // Kameraoeffnung nur von innen, aussen bleibt eine duenne Haut (cam_skin)
    translate([-W/2+cam_skin, -D_cam, cam_z]) rotate([0,90,0]) cylinder(d=lens_d, h=wall+1, $fn=32);
    for (iD=[-10.5,10.5]) for (iz=[-6.25,6.25])
      translate([-xi-1, -(D_cam+iD), cam_z+iz]) rotate([0,90,0]) cylinder(d=1.7, h=5, $fn=16);
  }
}

// ---------------- Platzhalter fuer Vorschau ----------------
module electronics() {
  zpi = wall + 3;
  zb  = zpi + 1.4 + pi_spacer;
  fe  = Dc - 15.35;
  color("seagreen")  translate([-32.5, -(Dc+15), zpi]) cube([65,30,1.4]);
  color("dimgray")   translate([-33.2, -(Dc+15.35), zb]) cube([66.4,30.7,1.6]);
  color("black")     translate([19.9, -(D_jack+7), zb+1.6]) cube([20,14,11]);          // DC-Buchse
  color("limegreen") translate([4.2, -(fe+9.5), zb+1.6]) cube([15,9,11.5]);            // Schraubklemme
  color("silver")    translate([-28.3, -(fe+10.5), zb+1.6]) cube([31.5,10.5,10]);      // HUB75-Stecker
  color("gray")      translate([-28.3, -(fe+10.5), zb+11.6]) cube([31.5,10.5,8]);
  color("darkgreen") translate([-W/2+wall+3.5, -(D_cam+12.5), cam_z-12]) cube([1,25,24]); // Kamera
  color("black")     translate([-W/2+wall, -(D_cam+4.25), cam_z-4.25]) cube([3.5,8.5,8.5]);
  color("gold")      translate([-W/2-3, -D_btn, btn_z]) rotate([0,90,0]) cylinder(d=6, h=12, $fn=24);
}
module panel_dummy() {
  to_world() {
    color("steelblue", 0.35) translate([-W/2, 0, -panel_t]) cube([W, H, panel_t]);
    color("orange") for (p = pins) translate([p[0], p[1], 0]) cylinder(d=pin_d, h=pin_h, $fn=20);
    color("red", 0.35) translate([-20, H-37-8, 0]) cube([40, 16, 10]);   // Bauraum Stecker JIN, 10 mm hinter der Rueckflaeche
  }
}

module assembly() {
  panel_dummy();
  color("lightgray") to_world() ring();
  color("silver", 0.55) wedge();
  color("slategray", 0.55) foot();
  electronics();
}

// ---------------- Ausgabe ----------------
module out() {
  if (part == "ring")        ring();
  if (part == "wedge")       wedge();
  if (part == "foot")        foot();
  if (part == "shell") { color("silver") wedge(); color("slategray") foot(); }
  if (part == "ring_world")  to_world() ring();
  if (part == "electronics") electronics();
  if (part == "panel")       to_world() translate([-W/2, 0, -panel_t]) cube([W, H, panel_t]);
  if (part == "pins")        to_world() for (p = pins) translate([p[0], p[1], 0]) cylinder(d=pin_d, h=pin_h, $fn=20);
  if (part == "ring_print")  translate([0,-(wall+0.4+ring_t*sa)/ca,0]) ring();
  if (part == "wedge_print") translate([0,0,Db_out]) rotate([90,0,0]) wedge();
  if (part == "foot_print")  translate([0,0,foot_D]) rotate([90,0,0]) foot();
  if (part == "assembly") difference() {
    assembly();
    if (cut == "x") translate([0,-300,-10]) cube([300,600,400]);
    if (cut == "z") translate([-300,-300,30]) cube([600,600,400]);
  }
}
out();
