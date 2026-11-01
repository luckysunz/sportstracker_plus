// =====================================================================
// Sportstracker Gehaeuse v2  (Montagering + Keilgehaeuse, ohne Fuss)
// Panel P2.5-2020-32S-64x64 (160 x 160 mm), Pi Zero WH + Adafruit RGB Matrix Bonnet
//
// Aenderungen gegenueber v0.3:
//  - Fuss entfaellt. Der Pi liegt im Keilgehaeuse, ganz hinten an der Rueckwand.
//  - Rueckwand 7 mm weiter hinten (Luft hinter dem Panel oben 21 statt 14 mm).
//  - Pi von unten durch den Boden verschraubt (M2,5), wie im Fuss.
//  - 5V-Buchse rechte Wand; Taster 6,4 mm + Kamera IMX219-120 (Waveshare Pi5) linke Wand,
//    ueber der Kameraoeffnung bleibt eine 0,6 mm Haut.
//
// Teile (Variable "part"):
//   ring        Montagering (unveraendert gegenueber v0.3)
//   wedge       Keilgehaeuse mit Pi-Aufnahme
//   wedge_print Keilgehaeuse in Druckausrichtung (Rueckwand flach auf dem Bett)
//   camtest     Probestueck linke Wand (Kamera + Taster), Druckausrichtung
//   assembly    Gesamtansicht mit Platzhaltern
//
// Koordinaten: X = rechts, von HINTEN gesehen; Y = nach vorn (Panel); Z = oben.
// Tiefe D = -Y (Abstand von der Panel-Unterkante nach hinten). Alle Masse in mm.
// Mit "(?)" markierte Werte sind Annahmen und noch zu pruefen.
// =====================================================================

part = "assembly";
cut  = "none";          // none | x | z   (nur Vorschau: Schnittebene)

// ---------------- Panel / Neigung ----------------
W = 160;  H = 160;      // Panel-Aussenmasse
alpha = 10;             // Neigung nach hinten (Grad)
wall = 2.4;             // Wandstaerke
panel_t = 15;           // Panel-Dicke Vorderseite bis Rueckflaeche
top_clear0 = 14;        // Luft hinter der Rueckflaeche oben in v0.3 (10 mm Stecker + 4 Luft)
extra_depth = 7;        // v2: Rueckwand 7 mm weiter hinten (Platz fuer Pi + Bonnet)
top_clear = top_clear0 + extra_depth;

// ---------------- Montagering ----------------
ring_t   = 5;
ring_w   = 9.6;
ring_clr = 0.4;
ins_edge   = 6;         // Abstand Einsatz-Mitte zur Aussenkante (geschaetzt) (?)
ins_corner = 35;
side_v   = [62, 98];

// ---------------- Arretierungsstifte des Panels ----------------
pin_pos = 55;
pin_off = 2.5;
pin_d   = 3.0;
pin_h   = 3.0;          // (?)
pin_clr = 2.0;

// ---------------- Elektronik ----------------
rear_gap  = 1.5;        // Luft zwischen Bonnet und Rueckwand
pi_standoff = 3;        // gedruckte Abstandshalter unter dem Pi
pi_spacer = 11;         // weisse Abstandshalter Pi -> Bonnet (?)
jack_z  = 25;           // Mitte Hohlstecker-Buchse ueber Boden (?)
jack_w  = 18;           // Oeffnung Breite (Tiefenrichtung)
jack_h  = 16;           // Oeffnung Hoehe
jack_dfront = 14;       // Buchse: Abstand Mitte ab Vorderkante Bonnet (?)
tunnel_start = 41;      // Fuehrungskanal beginnt bei X = 41 (hinter der Buchse) bis zur Wand
pi_screw_d  = 2.7;
pi_head_d   = 5.4;
pi_head_t   = 2.0;

// Taster (linke Wand): gleiche Hoehe wie die Kameralinse (btn_z wird unten abgeleitet)
btn_D = 15.2;           // Abstand von der Panel-Unterkante (Tiefe), vor der Kameratasche
btn_d = 7;              // Bohrung 7 mm

// Lueftung: Sechsecke in der Rueckwand auf Hoehe von Pi und Bonnet
vent_d = 6;             // Sechseck Ø 6 mm (Eckmass, Spitzen links/rechts)
vent_cols = [-12, 0, 12];
vent_rows = [8, 18, 28];

// Kamera Waveshare IMX219-120 (Pi5-Version), linke Wand
cam_w = 25;             // Platine in Tiefenrichtung (Anschlusskante unten)
cam_h = 24;             // Platine in Hoehenrichtung
cam_pcb = 1.0;          // Platinendicke (?)
cam_lens_h = 5.5;      // Bauhoehe Linse ueber der Platine (?)
cam_gap = 0.6;          // Luft zwischen Linse und Wand
cam_stand = cam_lens_h + cam_gap;   // Pfosten-Hoehe
cam_zbot = 18;          // Unterkante der Platine ueber Boden (Anschlusskante mit Kabel oben)
cam_clr = 0.4;          // Spiel der Platine in der Tasche
cam_rib = 1.5;          // Wand der Kameratasche
// Platinenkoordinaten (aus Frank's Fotos mit Lineal, +-0,3 mm): von der Lochseite/Linsenseite betrachtet,
// Anschlusskante (15-poliger Stecker) unten. u = Abstand von der hinteren Kante (Richtung Rueckwand),
// w = Abstand von der Oberkante. EINGEBAUT wird die Platine um 180 Grad gedreht: Kabel kommt von OBEN. Alle Loecher Durchmesser 1,95 mm.
cam_holes = [[1.6,1.4],[22.8,1.4],[1.6,15.0],[22.8,15.0]];   // vier Eckloecher (21,2 x 13,6 mm), werden genutzt
cam_holes_mid = [[2.9,8.2],[20.7,8.2]];                      // zwei mittlere Loecher, ungenutzt
cam_lens_uw = [12.1, 7.5];                                   // Linsenmitte
cam_hole_d  = 1.95;     // Loch in der Platine
peg_d  = 2.0;           // Zapfen unten (Durchmesser) -> Presssitz im Loch
peg_tip = 1.4;          // Zapfen oben (Spitze)
peg_h  = cam_pcb + 2.2; // Zapfenhoehe ueber dem Pfosten (Platine + 2,2 mm zum Warmverformen/Kleben)
post_d = 3.4;           // Pfosten (Auflage) um jedes Loch
lens_d = 6.5;           // Oeffnung innen (Objektiv 6,2 mm)
lens_cone = 40;         // Halbwinkel der leichten Aufweitung nach aussen (0 = glatte Bohrung)
cable_slot = 20;       // Ausschnitt fuer das Flachkabel (15-polig, 1 mm Raster) an der OBERkante der Tasche

// DHT22 / AM2302 mit Platine, rechte Wand oberhalb der 5V-Buchse.
// Platine innen an der Wand, Sensor (weisser Kaefig) steckt durch ein Fenster nach aussen.
dht_bw = 15.1;  dht_bl = 25.1;  dht_bh = 7.7;   // Sensorkoerper (Datenblatt AM2302)
dht_pw = 15.5;  dht_pl = 35;    dht_pt = 1.6;   // Platine (?)
dht_hole_s = 30.5;              // Montageloch der Platine: Abstand von der Unterkante des Sensors (?)
dht_clr = 0.4;                  // Spiel der Platine in der Tasche
dht_win_w = 15.6;               // Fenster: Breite (Tiefenrichtung)
dht_win_l = 20.0;               // Fenster: Laenge (Hoehe)
dht_ear_l = 5.1;                // Befestigungsohr am unteren Ende des Sensors bleibt innen (?)
dht_ear_t = 3.0;                // Dicke des Ohrs (?)
dht_recess = 1.0;               // Mulde in der Innenseite der Wand fuer das Ohr
dht_scr_d = 2.7;                // Befestigungsloch M2,5 (durchgebohrt)
dht_scr_cb = 5.2;               // Senkung aussen fuer den Schraubenkopf
dht_scr_cbt = 1.5;
dht_stand = 2.2;                // Abstand der Platine zur Wand (Ohr + Platz fuer Bauteile)
dht_rib = 1.5;
dht_D  = 36;                    // Mitte in Tiefenrichtung
dht_z0 = 38.5;                  // Unterkante Fenster ueber Boden (Buchsenkanal endet bei 35,4)

// ---------------- abgeleitet ----------------
ca = cos(alpha); sa = sin(alpha); ta = tan(alpha);
Zt = H*ca;  Dt = H*sa;
Db_in  = Dt + top_clear/ca;
Db_out = Db_in + wall;
Dc     = Db_in - rear_gap - 15.35;     // Mitte Pi/Bonnet in Tiefenrichtung
fe     = Dc - 15.35;                   // Vorderkante Bonnet
D_jack = fe + jack_dfront;
cam_Drear = Db_in - 3;                 // hintere Kante der Kameraplatine
cam_Dfront = cam_Drear - cam_w;        // vordere Kante der Kameraplatine (u = 0 nach dem Drehen um 180 Grad)
xi     = W/2 - wall;                   // Innenkante Seitenwand
function cD(u) = cam_Dfront + u;       // Platinen-u -> Tiefe D (Platine um 180 Grad gedreht)
function cZ(w) = cam_zbot + w;         // Platinen-w -> Hoehe Z (Anschlusskante oben)
dht_zb = dht_z0 + dht_clr;             // Unterkante Sensorkoerper (s = 0)
btn_z  = cZ(cam_lens_uw[1]);           // Taster auf Hoehe der Linse (25,5 mm)
lens_D = cD(cam_lens_uw[0]);           // Linsenmitte in Tiefenrichtung
lens_Z = cZ(cam_lens_uw[1]);

// ---------------- Schiebeabdeckung Kamera (Schwalbenschwanz, senkrecht) ----------------
sl_din   = 7;           // Innenkante der Schienen ab Linsenmitte
sl_rw    = 2.0;         // Halsbreite der Schiene
sl_h0    = 1.0;         // Halshoehe
sl_lip   = 1.2;         // Hinterschnitt (45 Grad, ohne Stuetzen druckbar)
sl_ht    = sl_h0 + sl_lip;   // Schienenhoehe 2,2
sl_clr   = 0.2;         // Spiel Abdeckung/Schiene (Reibsitz)
sl_ceil  = 1.2;         // Decke der Abdeckung ueber der Schiene
sl_out   = 1.2;         // Rand der Abdeckung aussen
sl_ch    = 16;          // Hoehe der Abdeckung
sl_travel = 14;         // Schiebeweg (offen = Abdeckung um 14 mm hoeher)
sl_stop  = 1.5;         // Dicke des Anschlags unten
sl_ct    = sl_ht + sl_clr + sl_ceil;   // Aussenflaeche der Abdeckung (ab Wand) = 3,6
cover_pos = 0;          // Vorschau: 0 = zu, sl_travel = offen

function fz(z) = z*ta - 1;

module prof(pts, w) {
  translate([-w/2,0,0]) rotate([90,0,90])
    linear_extrude(height=w) polygon([for (p = pts) [-p[0], p[1]]]);
}
module to_world() {
  multmatrix([[1,0,0,0],[0,-sa,-ca,0],[0,ca,-sa,0],[0,0,0,1]]) children();
}

ins_top  = [[-(W/2-ins_corner), H-ins_edge], [(W/2-ins_corner), H-ins_edge]];
ins_side = [[-(W/2-ins_edge), ins_corner], [-(W/2-ins_edge), H-ins_corner],
            [ (W/2-ins_edge), ins_corner], [ (W/2-ins_edge), H-ins_corner]];
ins_bot  = [[-(W/2-ins_corner), ins_edge], [(W/2-ins_corner), ins_edge]];

pin_edge = ins_edge - pin_off;
pins = [[ (W/2-pin_pos), H-pin_edge],
        [-(W/2-pin_edge),  H-pin_pos],
        [-(W/2-pin_pos),  pin_edge],
        [ (W/2-pin_edge), pin_pos]];

// ---------------- Montagering (unveraendert) ----------------
module ring() {
  v0 = (wall + 0.4 + ring_t*sa)/ca;
  v1 = (Zt - wall - 0.4)/ca;
  xo = W/2 - wall - 0.2;
  difference() {
    translate([-xo, v0, 0]) cube([2*xo, v1-v0, ring_t]);
    translate([-(xo-ring_w), v0+ring_w, -1]) cube([2*(xo-ring_w), v1-v0-2*ring_w, ring_t+2]);
    for (p = concat(ins_top, ins_side, ins_bot))
      translate([p[0], p[1], -1]) cylinder(d=3.4, h=ring_t+2, $fn=24);
    for (p = pins)
      translate([p[0], p[1], -1]) cylinder(d=pin_d+pin_clr, h=ring_t+2, $fn=24);
    for (v = side_v) {
      translate([ xo+0.1, v, ring_t/2]) rotate([0,-90,0]) cylinder(d=2.6, h=9, $fn=20);
      translate([-xo-0.1, v, ring_t/2]) rotate([0, 90,0]) cylinder(d=2.6, h=9, $fn=20);
    }
  }
}

// ---------------- Kameratasche (linke Wand, innen) ----------------
module cam_frame() {
  hx = cam_stand + cam_pcb + 1;
  zlo = cam_zbot;
  translate([-xi-0.01, 0, 0]) {
    difference() {
      // Tasche ohne vordere Rippe (Platz fuer die Taster-Mutter), vorn offen
      translate([0, -(cam_Drear+cam_clr+cam_rib), zlo-cam_clr-cam_rib])
        cube([hx, cam_w+2*cam_clr+cam_rib, cam_h+2*(cam_clr+cam_rib)]);
      translate([-1, -(cam_Drear+cam_clr), zlo-cam_clr])
        cube([hx+2, cam_w+2*cam_clr+1, cam_h+2*cam_clr]);
      // Ausschnitt oben fuer das Flachkabel (mittig), Kabel kommt von oben
      translate([-1, -(cam_Drear-cam_w/2+cable_slot/2), zlo+cam_h+cam_clr-1])
        cube([hx+2, cable_slot, cam_rib+2]);
    }
    // Pfosten + Zapfen (Steckverbindung, Loch 1,95 mm) an den vier Eckloechern
    for (h = cam_holes) {
      translate([0, -cD(h[0]), cZ(h[1])]) rotate([0,90,0]) cylinder(d=post_d, h=cam_stand, $fn=24);
      translate([cam_stand-0.01, -cD(h[0]), cZ(h[1])]) rotate([0,90,0])
        cylinder(h=peg_h+0.01, r1=peg_d/2, r2=peg_tip/2, $fn=20);
    }
  }
}

// ---------------- DHT22-Aufnahme (rechte Wand, innen) ----------------
// s = Laengsachse (nach oben) ab Unterkante des Sensorkoerpers; Sensor zeigt durch das Fenster nach aussen
module dht_frame() {
  hx = dht_stand + dht_pt + 0.8;
  zlo = dht_zb - dht_clr;
  translate([xi+0.01, 0, 0]) mirror([1,0,0]) {
    difference() {
      translate([0, -(dht_D+dht_pw/2+dht_clr+dht_rib), zlo-dht_rib])
        cube([hx, dht_pw+2*(dht_clr+dht_rib), dht_pl+2*dht_clr+dht_rib]);
      translate([-1, -(dht_D+dht_pw/2+dht_clr), zlo])
        cube([hx+2, dht_pw+2*dht_clr, dht_pl+2*dht_clr+5]);   // oben offen (Pins / Stecker)
    }
    // Auflagen am Platinenende und Schraubdom am Montageloch
    for (sd=[-1,1]) translate([0, -(dht_D+sd*5.3), dht_zb+28]) rotate([0,90,0]) cylinder(d=4, h=dht_stand, $fn=20);
    translate([0, -dht_D, dht_zb+dht_hole_s]) rotate([0,90,0]) cylinder(d=7, h=dht_stand, $fn=28);
  }
}

// ---------------- Schiebeabdeckung (lokales System: X = nach aussen ab Wand, Y = Tiefe ab Linse, Z = Hoehe ab Linse) ----------------
module cover_world() {
  multmatrix([[-1,0,0,-W/2],[0,-1,0,-lens_D],[0,0,1,lens_Z+cover_pos],[0,0,0,1]]) children();
}
module rail_poly(s) {
  polygon([for (p = [[-0.5,0],[-0.5,sl_rw],[sl_h0,sl_rw],[sl_ht,sl_rw+sl_lip],[sl_ht,0]]) [p[0], s*(sl_din+p[1])]]);
}
module slide_rails() {
  z0 = -sl_ch/2 - 0.1 - sl_stop;
  z1 = sl_ch/2 + sl_travel + 0.2;
  for (s=[-1,1]) translate([0,0,z0]) linear_extrude(height=z1-z0) rail_poly(s);
  // Anschlag unten
  translate([-0.5, -(sl_din+sl_rw+sl_lip), z0]) cube([3.5, 2*(sl_din+sl_rw+sl_lip), sl_stop]);
}
module cover_local() {
  hw = sl_din + sl_rw + sl_lip + sl_clr + sl_out;
  difference() {
    translate([sl_clr, -hw, -sl_ch/2]) cube([sl_ct-sl_clr, 2*hw, sl_ch]);
    for (s=[-1,1]) translate([0,0,-sl_ch/2-1]) linear_extrude(height=sl_ch+2) offset(delta=sl_clr) rail_poly(s);
    // Fingermulde aussen oben
    translate([sl_ct-1.0, -6, sl_ch/2-5]) cube([2, 12, 3]);
  }
}

// ---------------- Keilgehaeuse ----------------
module wedge() {
  difference() {
    union() {
      difference() {
        prof([[0,0],[Dt,Zt],[Db_out,Zt],[Db_out,0]], W);
        prof([[fz(wall),wall],[fz(Zt-wall),Zt-wall],[Db_in,Zt-wall],[Db_in,wall]], W-2*wall);
      }
      // Abstandshalter fuer den Pi Zero (Lochbild 58 x 23)
      for (ix=[-29,29]) for (iD=[-11.5,11.5])
        translate([ix, -(Dc+iD), wall-0.01]) cylinder(d=6, h=pi_standoff+0.01, $fn=24);
      // Fuehrungskanal 5V-Stecker (rechte Wand)
      translate([tunnel_start, -(D_jack+jack_w/2+2.4), jack_z-jack_h/2-2.4])
        cube([xi-tunnel_start+0.5, jack_w+4.8, jack_h+4.8]);
      cam_frame();
      dht_frame();
      cover_world() slide_rails();
    }
    // Pi von aussen verschraubt: Durchgangsloch M2,5 + Senkung im Boden
    for (ix=[-29,29]) for (iD=[-11.5,11.5]) {
      translate([ix, -(Dc+iD), -1]) cylinder(d=pi_screw_d, h=wall+pi_standoff+2, $fn=20);
      translate([ix, -(Dc+iD), -1]) cylinder(d=pi_head_d, h=pi_head_t+1, $fn=28);
    }
    // Oeffnung 5V-Buchse
    translate([tunnel_start-1, -(D_jack+jack_w/2), jack_z-jack_h/2]) cube([xi-tunnel_start+wall+2, jack_w, jack_h]);
    // Schlitze fuer Kabelbinder (Zugentlastung)
    for (s=[-1,1])
      translate([xi-0.5, -(D_jack+s*15)-0.9, jack_z-2.1]) cube([wall+1, 1.8, 4.2]);
    // Taster (linke Wand)
    translate([-W/2-1, -btn_D, btn_z]) rotate([0,90,0]) cylinder(d=btn_d, h=wall+2, $fn=32);
    // Kameraoeffnung offen durch die Wand (Abdeckung schliesst sie), nach aussen aufgeweitet
    translate([-xi+0.02, -lens_D, lens_Z]) rotate([0,-90,0])
      cylinder(h=wall+1, r1=lens_d/2, r2=lens_d/2+(wall+1)*tan(lens_cone), $fn=48);
    // Lueftung: 9 Sechsecke (Ø 6 mm) in der Rueckwand (Pi + Bonnet)
    for (x = vent_cols) for (z = vent_rows)
      translate([x, -(Db_out+1), z]) rotate([-90,0,0]) cylinder(d=vent_d, h=wall+2, $fn=6);
    // DHT22: Fenster 15,6 x 20,0 fuer den Sensorkoerper (weisser Kaefig nach aussen); das Befestigungsohr bleibt innen
    translate([xi-3, -(dht_D+dht_win_w/2), dht_zb+dht_ear_l]) cube([wall+4, dht_win_w, dht_win_l]);
    // Mulde in der Wandinnenseite fuer das Ohr
    translate([xi-1, -(dht_D+dht_win_w/2), dht_zb-0.2]) cube([dht_recess+1, dht_win_w, dht_ear_l+0.2+0.01]);
    // Befestigungsloch M2,5 durchgebohrt (Dom + Wand) mit Senkung aussen
    translate([xi-dht_stand-1, -dht_D, dht_zb+dht_hole_s]) rotate([0,90,0]) cylinder(d=dht_scr_d, h=dht_stand+wall+2, $fn=24);
    translate([xi+wall-dht_scr_cbt, -dht_D, dht_zb+dht_hole_s]) rotate([0,90,0]) cylinder(d=dht_scr_cb, h=dht_scr_cbt+1, $fn=28);
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

// ---------------- Platzhalter fuer Vorschau ----------------
module electronics() {
  zpi = wall + pi_standoff;
  zb  = zpi + 1.4 + pi_spacer;
  color("seagreen")  translate([-32.5, -(Dc+15), zpi]) cube([65,30,1.4]);
  color("dimgray")   translate([-33.2, -(Dc+15.35), zb]) cube([66.4,30.7,1.6]);
  color("black")     translate([19.9, -(D_jack+7), zb+1.6]) cube([20,14,11]);          // DC-Buchse
  color("limegreen") translate([4.2, -(fe+9.5), zb+1.6]) cube([15,9,11.5]);            // Schraubklemme
  color("silver")    translate([-28.3, -(fe+10.5), zb+1.6]) cube([31.5,10.5,10]);      // HUB75-Stecker
  color("gray")      translate([-28.3, -(fe+10.5), zb+11.6]) cube([31.5,10.5,8]);
  // Kamera: Linse an der Wand, Platine auf den Pfosten
  color("black")     translate([-xi+cam_gap, -(cD(cam_lens_uw[0])+4.25), cZ(cam_lens_uw[1])-4.25]) cube([cam_lens_h,8.5,8.5]);
  color("darkgreen") translate([-xi+cam_stand, -cam_Drear, cam_zbot]) cube([cam_pcb,cam_w,cam_h]);
  // DHT22: Platine innen, Sensor durch das Fenster nach aussen, Steckerleiste oben
  color("darkgreen") translate([xi-dht_stand-dht_pt, -(dht_D+dht_pw/2), dht_zb]) cube([dht_pt, dht_pw, dht_pl]);
  color("white")     translate([xi-dht_stand, -(dht_D+dht_bw/2), dht_zb+dht_ear_l]) cube([dht_bh, dht_bw, dht_bl-dht_ear_l]);   // Hauptkoerper
  color("white")     translate([xi-dht_stand+dht_ear_t, -(dht_D+dht_bw/2), dht_zb]) mirror([1,0,0]) cube([dht_ear_t, dht_bw, dht_ear_l]);   // Ohr (innen)
  color("black")     translate([xi-dht_stand-dht_pt-2.5, -(dht_D+4), dht_zb+dht_pl]) cube([2.5+dht_pt, 8, 14]);
  // Taster
  color("gold")      translate([-W/2-3, -btn_D, btn_z]) rotate([0,90,0]) cylinder(d=6.8, h=3+wall+14, $fn=24);
  color("silver")    translate([-xi, -btn_D, btn_z]) rotate([0,90,0]) cylinder(d=11, h=3.5, $fn=6);  // Mutter (SW ca. 9,5)
}
module panel_dummy() {
  to_world() {
    color("steelblue", 0.35) translate([-W/2, 0, -panel_t]) cube([W, H, panel_t]);
    color("orange") for (p = pins) translate([p[0], p[1], 0]) cylinder(d=pin_d, h=pin_h, $fn=20);
    color("red", 0.35) translate([-20, H-37-8, 0]) cube([40, 16, 10]);
  }
}

module assembly() {
  panel_dummy();
  color("lightgray") to_world() ring();
  color("silver", 0.55) wedge();
  color("orange") cover_world() cover_local();
  electronics();
}

// ---------------- Ausgabe ----------------
module out() {
  if (part == "ring")        ring();
  if (part == "wedge")       wedge();
  if (part == "electronics") electronics();
  if (part == "ring_world")  to_world() ring();
  if (part == "panel")       to_world() translate([-W/2, 0, -panel_t]) cube([W, H, panel_t]);
  if (part == "pins")        to_world() for (p = pins) translate([p[0], p[1], 0]) cylinder(d=pin_d, h=pin_h, $fn=20);
  if (part == "ring_print")  translate([0,-(wall+0.4+ring_t*sa)/ca,0]) ring();
  if (part == "wedge_print") translate([0,0,Db_out]) rotate([90,0,0]) wedge();
  // Probestueck: linke Wand mit Kamera und Taster, gleiche Druckausrichtung wie das Keilgehaeuse (Rueckwand flach)
  if (part == "camtest")
    translate([0,0,Db_out]) rotate([90,0,0])
      intersection() { wedge(); translate([-W/2-4, -(Db_out+1), -1]) cube([15, Db_out+2, 52]); }
  // Schiebeabdeckung, Druckausrichtung: Aussenflaeche auf dem Bett, Nut oben, Schiebeachse in Y
  if (part == "cover_print")
    translate([0, sl_ch/2, 0]) multmatrix([[0,1,0,0],[0,0,-1,0],[-1,0,0,sl_ct],[0,0,0,1]]) cover_local();
  if (part == "cover") cover_world() cover_local();
  // Probestueck rechte Wand: DHT22-Fenster (ab Z=36, ohne Buchsenkanal), gleiche Druckausrichtung wie das Keilgehaeuse
  if (part == "dhttest")
    translate([0,0,Db_out]) rotate([90,0,0])
      intersection() { wedge(); translate([W/2-12, -(Db_out+1), 36]) cube([13, Db_out+2, 60]); }
  if (part == "assembly") difference() {
    assembly();
    if (cut == "x") translate([0,-300,-10]) cube([300,600,400]);
    if (cut == "z") translate([-300,-300,30]) cube([600,600,400]);
  }
}
out();
