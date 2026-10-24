# CAD Sketcher for IngeTrazo

Constraint-based sketches that drive solids — the idea of the Blender add-on
[CAD_Sketcher](https://hlorus.github.io/CAD_Sketcher/), rebuilt on IngeTrazo's
extension API. Draw on a workplane, dimension it, then **Extrude / Loft /
Sweep**. The solid keeps its sketch: change a dimension (or a fillet radius) and
it is rebuilt, and one Undo takes back sketch and solid together.

info CAD sketcher -Blender can find in link [https://www.cadsketcher.com/]

Open it from the **CAD Sketcher** tab at the bottom of the side tray, or
*Extensions ▸ CAD Sketcher*.

> These are the sketch's own tools and dimensions. IngeTrazo's Line, Dimension,
> Push/Pull… do not act on a sketch, and a sketch dimension is not an IngeTrazo
> dimension: it is a rule the solver enforces, not an annotation.

---
*
Sequence practice step on CAD sketcher will follow step 1 to step 7.
Sketch can setup on plan XY, YZ, ZX. To click "eye" symbol for invisible/ visible of sketches/ shapes
*
---

## 1. Create a sketch

1. In the panel: **New ▾ ▸ On a standard plane…**
2. Choose the workplane — **XY** (plan), **XZ** (front) or **YZ** (side) — and an
   **Offset** along its normal (e.g. XY with offset 3 m = a plan at height 3 m,
   which is how loft sections are stacked). OK.
   Or **New ▾ ▸ On a face of the model — click it…** and click any face: of
   loose geometry, of a group, or of a solid built here (see §8).
3. The sketch is now being edited (✎ beside its name, button reads **Finish**).
   Its origin is a square purple point: it is **fixed**, start your drawing
   there so the sketch cannot drift.
4. **Align view** looks straight at the plane. You cannot draw while the view is
   edge-on to the plane (e.g. an XZ sketch seen from the top).

**Finish** leaves the sketch; double-click it in the list (or **Edit**) to come back.

**Placement…** sets where a sketch is and how it is turned: **Origin X, Y, Z**
(where its fixed purple point sits in the model) and **Rotate about X, Y, Z**
(degrees). The angles turn the plane the sketch was made on — first about the
model's X axis, then Y, then Z — around the sketch origin; the window shows
live which way the result faces. Everything drawn moves with it, and the
solids made from the sketch are rebuilt. *Reset* puts the angles back to 0.

A sketch made on **YZ** faces along X, which makes the angles easy to read:
*about Y* tilts it up / down, *about Z* turns it left / right, *about X* spins
it in its own plane. Example — a section square to a path that runs 2150 along
X while rising 1250: draw the section on YZ around its origin, then Placement:
origin = the path's start, **Rotate about Y = −30.17°** (atan 1250/2150). The
Sweep is then exact. (If it tilts the wrong way, use +30.17.)

**Duplicate…** copies the selected sketch (or the one being edited) with ALL its
entities, constraints and dimensions, and opens the copy for editing. Choose
where it goes: on the same plane moved by *Offset* along its normal (0 = right
on top of the original), or on XY / XZ / YZ at *Offset* from the origin. The
copy is independent — change it freely, the original and its solids stay as
they are. Typical uses: the next section of a loft (offset 3 m up), the same
profile at another level, or a profile to sweep on another plane.

**The eye** at the right of each sketch row hides or shows that sketch while it
is not being edited (the one being edited is always drawn). A sketch is hidden
automatically when a solid is made from it — click its eye to see it again.

## 2. Draw

IngeTrazo style: click – move – click (no press-and-drag).

| Tool | Key | How |
|---|---|---|
| Select | S | click entities or dimension labels; click empty space or Esc to clear |
| Move | M | click a point, line or circle, move, click to drop (Esc puts it back) |
| Point | P | click |
| Line | L | click point after point; type a length + Enter; Esc ends the chain |
| Rectangle | R | two opposite corners — its sides get Horizontal/Vertical |
| Circle | C | centre, then radius (or type the radius + Enter) |
| Arc | A | centre, start, end — counter-clockwise |
| Centre rect | B | centre, then a corner — it stays centred when dimensioned |
| Slot | U | the centres of its two round ends, then its half-width (or type it + Enter) |
| Semicircle | H | the two ends of its diameter; it bulges to the right of that direction |
| Reference | E | click a marked spot of another sketch to bring it into this one (§6) |
| Fillet | F | click a corner where two lines meet, give the radius |
| Chamfer | K | click a corner where two lines meet, give the distance |
| Trim | T | click the stretch to cut away, between its crossings |
| Extend | X | click a line or arc near an end: it grows to the next thing in its way |
| Offset | O | click an outline, then click to its side (or type the distance + Enter) |

Shortcuts work while the 3D view has the keyboard and a sketch is being edited
(they then take precedence over IngeTrazo's own L, C, R, G…). Finish the sketch
and the keys are IngeTrazo's again.

**Grid** (in *Draw*): *Grid* draws it on the sketch plane, *Snap* (or **G**)
makes clicks land on it, the box sets the spacing. A click on an existing point
or curve still goes to that point or curve, not to the grid.

The panel is in folding blocks — *Sketches, Draw, Modify, Constraints,
Dimensions, Selection and constraint list, Solids from sketches*. Click a
header (▾ / ▸) to fold or unfold it; the choice is remembered.

- To **close a profile**, end on an existing point (the cursor says «on point»).
  Only a closed outline can become a solid.
- With **Automatic constraints** ticked, a nearly level/plumb line becomes
  Horizontal/Vertical and a point dropped on a line or circle stays on it.
- A length **typed while drawing** is kept as a dimension.

## 3. Dimension the sketch

A dimension is added to what is **selected**:

1. Tool **Select** (S). Click what you want to dimension — it turns orange.
2. Click the dimension button in *Constrain the selection* (or its shortcut).
3. Type the value in the box that opens (document units) and press Enter. The
   sketch moves to obey it and a green label appears on the drawing.

| You want | Select | Button | Key |
|---|---|---|---|
| Length of a line | the line | Distance… | Alt+D |
| Distance between two points | two points | Distance… | Alt+D |
| Distance from a point to a line | a point and a line | Distance… | Alt+D |
| Only the horizontal / vertical part | two points (or a line) | Horiz. distance… / Vert. distance… | Alt+H / Alt+V |
| Radius / diameter | a circle or an arc | Radius… / Diameter… | Alt+R / Alt+O |
| Angle between two lines | two lines | Angle… | Alt+A |
| One line a multiple of another | two lines | Ratio… | Shift+R |

**Example — a 6 × 2 m rectangle from the origin**

1. *Rectangle*: click the origin point, click the opposite corner anywhere.
2. *Select*, click the bottom side, **Distance…**, type `6`, Enter.
3. Click the left (or right) side, **Distance…**, type `2`, Enter.
4. The status line under the list now reads **Fully constrained** (green).

**To change a dimension**: double-click its label on the drawing, or its row in
the constraints list, and type the new value. **To remove one**: select its
label (or row) and press Del.

**Reading the status line**

- *Solved — N degree(s) of freedom* (blue): N things can still move. Use *Move*
  to see what is loose, then add dimensions or constraints until it is 0.
- *Fully constrained* (green): every size and position is decided.
- *Solver failed* (red): the last dimension contradicts another; the ones in
  conflict are red and marked ⚠. Delete or change one.

Geometric constraints work the same way (select, then click): Coincident,
Horizontal, Vertical, Parallel, Perpendicular, Equal, Tangent, Midpoint,
Symmetric (Shift+C/H/V/P/N/E/T/M/S). Hover a button to see what it needs selected.
They are in the **Constraints** block — if you cannot see them, that block is
folded: click its header. **Vertical** on two points puts one straight above
the other (same X); *Vert. distance…* in *Dimensions* is something else — it
asks for the height between them.

**Construction** marks the selection as helper geometry: solved, drawn dashed,
left out of the solid. **Fix point** pins a point.

## 4. Fillet and chamfer

Choose **Fillet** (or **Chamfer**), click the corner point, type the radius
(distance).

- The fillet is an arc tangent to both lines with a **Radius** dimension
  (label `R …`). Double-click that label and type a new radius: the corner and
  every solid made from the sketch update.
- The chamfer is a line with two **Distance** dimensions, one along each side;
  they can be different.
- The original corner stays as a small construction point held on both lines,
  so dimensions to the corner (the overall width, say) keep working.
- To remove a fillet: select the arc, press Del, and close the corner again
  with a line.

## 5. Trim and offset

**Trim** (T) — click the part of a line, arc or circle you want gone. It is cut
back to the nearest crossing on each side of the click:

- crossings on both sides: the middle goes, two pieces stay;
- a crossing on one side only: that end is cut off;
- no crossing: the whole entity is deleted;
- a circle needs two crossings and becomes an arc.

The new end is held on the curve that cut it, and when two curves are trimmed
at the same crossing they end up sharing ONE point — which is what closes an
outline. Typical use: draw overlapping shapes, trim away what is inside, and
the rest is one closed profile.

**Offset** (O) — click an outline (the whole connected run of lines and arcs
through the entity you click, or a circle). Move the cursor to the side you
want — a dashed preview follows — and click, or type the distance + Enter.

- Lines stay parallel to their source; arcs and circles keep the same centre.
- A closed outline offset inwards gives a second outline inside the first:
  extruded, that is a hollow section (tube, frame) with the wall you chose.
- The distance is not stored as a dimension; add *Distance…* between a point
  of the copy and a line of the source if you want to drive it later.
- An offset so large that a side would vanish is refused.

### Copy, mirror, pattern (in *Modify*)

All of them act on the **selection** (double-click a line to select its whole
outline, or use *Chain*; Ctrl+A selects everything).

- **Copy / Paste** (Ctrl+C / Ctrl+V): after Paste, a preview follows the cursor;
  click where the copy goes. You can paste into another sketch.
- **Mirror X / Mirror Y**: a mirrored copy across the sketch's own X or Y axis.
- **Mirror line**: select the shapes, then the mirror line LAST. The copy is
  tied to the original with Symmetric constraints, so it follows every edit.
- **Linear pattern…**: count (original included) and the spacing along X and Y.
- **Circular pattern…**: count and total angle (360 = evenly all the way
  round). The centre is the point selected LAST, else the sketch origin.
  Bolt holes: draw one hole, select it, *Circular pattern…*, 6.

Copies of a circle, or of a line or arc that has its own size dimension, are
tied to the original with **Equal** — change the one radius and every copy
follows. Their positions are free: constrain them if they must not move.

## 6. Line up sketches on different levels (Reference)

Loft sections live in different sketches, so a constraint cannot reach from one
to the other directly. **Reference** (E, in *Draw*) brings a spot of another
sketch into the one you are editing:

1. Edit the sketch that should follow (say the top one). Choose **Reference**.
2. Every other sketch is drawn, with small teal diamonds on what it offers:
   its points (circle centres too), the **midpoint** of each line, the four
   **quadrant** points of each circle, and its **outline centre** (the middle
   of its bounding box — the centre of a rectangle).
3. Click a diamond. A reference point appears in your sketch, straight
   above/below the spot (projected square onto your plane), already selected.
4. Click what should go with it and pick an ordinary constraint.

A reference point is fixed and follows its source: edit the other sketch and
this one is solved again, and the loft rebuilt. Delete it like any point.

**Centre over centre** (circle on top, rectangle below): edit the top sketch ▸
*Reference* ▸ click the rectangle's outline-centre diamond ▸ *Select*, click the
circle's centre point too ▸ **Coincident**.

**A point of the circle over the middle of a side**: edit the top sketch ▸
*Reference* ▸ click the midpoint diamond of that side ▸ *Point*, click on the
circle (the point stays on it) ▸ *Select* both ▸ **Vertical** (or Horizontal).
With the centre already lined up, that point is the circle's quadrant.

Other uses: **Coincident** reference + circle = the circle passes over that
spot; **Distance…** from a reference = a set-back from the outline below.

## 7. Make the solid

Dimension first, then build — the solid follows the sketch afterwards anyway.

- **Extrude** — one closed sketch, a distance along the plane's normal (negative
  = the other way; *Half to each side* = symmetric). An outline inside another
  becomes a hole.
- **Revolve** — one closed sketch turned about an axis in its plane: the
  sketch's X or Y axis (the red / green dashes from its origin) or any line of
  the sketch (draw it, select it, click *Construction* so it is not part of
  the profile). Angle up to 360°; less than a full turn leaves the two cut
  faces. The profile must stay on one side of the axis; it may touch it.
- **Loft** — two or more closed sketches on different planes (make them with
  different *Offsets*). Tick the sections **in the order the loft goes through
  them** — each gets its number (1., 2., 3.…) and the run is spelt out below
  the list (e.g. «Loft: Sec1 → Sec3 → Sec2»). Untick and tick again to move a
  section to the end.
- **Sweep** — a closed *profile* sketch and a second sketch whose lines/arcs are
  the *path*. Draw the profile on a plane **across the start** of the path
  (profile on XZ at the origin, path on XY starting at the origin).
- **Face** — the sketch as a flat face.

Double-click a solid in the list to change its distance, sections or path.
**The eye** at the right of each solid row hides or shows that solid in the
model. It is IngeTrazo's own Hide (an undo step; *Edit ▸ Unhide* brings it back
too), and a hidden solid stays hidden when its sketch is edited.
*Circle segments* sets how finely arcs and circles are faceted.

After that, any edit of the sketch — a dimension, a moved point, a new hole, a
fillet radius — rebuilds the solid. Ctrl+Z undoes sketch and solid together.

---

## 8. Sketch on a solid: a new part on it, or a pocket in it

1. **New ▾ ▸ On a face of the model — click it…**, then click the face. The
   sketch starts on that face, its origin at the face's centre.
2. Draw and dimension as usual.
3. **A new solid standing on the face**: *Extrude* with a distance pointing out
   of the face (if it goes inwards, edit the solid and change the sign).
4. **A pocket or a hole**: *Extrude* INTO the solid (the other sign), deep
   enough. Then use IngeTrazo's own **Solid Tools ▸ Subtract**: click the new
   solid (the cutter) first, then the solid to cut.

After a Subtract (or Union, Intersect…) the result is an ordinary IngeTrazo
group: it no longer follows its sketches. The two solids it was made from show
*(no longer in the model)* in the list and are NOT rebuilt behind your back; to
get one again, double-click it in the list and confirm — it is built afresh.
So: get the dimensions right first, cut last. If a cut must change, Undo the
Subtract, edit the sketch, and Subtract again.

The sketch stays where the face WAS: it is not tied to the solid, and does not
move if that solid later changes size.

---

## What differs from CAD_Sketcher

- The solver is NumPy (`solver.py`), not SolveSpace: IngeTrazo's frozen Python
  cannot load `py_slvs`. Same constraint set, minimum-change Newton steps.
- Sketches drive parametric solids instead of a mesh/bezier converter.
- Revolve is an addition (CAD_Sketcher has none).
- Not ported yet: 3D points/lines.

## Limits

- A solid is regenerated from its sketch: paint per face and manual edits of
  the solid's geometry are lost at the next rebuild (name, layer, group material
  and visibility are kept). A copy of the solid is a plain, static group.
- Outlines that cross each other are refused with a message.
- Loft uses each section's outer outline only.
- Fillet/chamfer need a corner where two straight lines of the outline share
  one point. Construction lines ending on the same corner (the diagonal of a
  centre rectangle) do not count and stay attached to the corner point.

## Install

Installing: copy all file in this repo to C:\Users\AppData\Roaming\IngeTrazo\plugins 


## Pics demo

### Working menu tab

<img width="333" height="675" alt="image" src="https://github.com/user-attachments/assets/4aec463d-055d-433d-9cd4-590f66794d6c" />

### Section with defined constraints

<img width="508" height="322" alt="image" src="https://github.com/user-attachments/assets/03bab25b-f1a9-462e-9e9b-1b21bfa39d0e" />


### Lofting
<img width="405" height="306" alt="image" src="https://github.com/user-attachments/assets/69c6f9ad-1108-4134-882d-faec26193833" />


