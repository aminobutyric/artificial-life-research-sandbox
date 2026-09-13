# V1 World and Environment Contract

## Boundary

The world owns physical and environmental truth. It does not import or know
about agents, brains, intentions, memory, or relationships. Later simulation
systems may observe the world through immutable views and request changes
through explicit world operations.

## Space

- The world is a bounded rectangular grid.
- Public coordinates are integer `Position(x, y)` values.
- Dense arrays are indexed internally as `[y, x]`.
- Positions outside the grid are rejected; the world does not wrap.
- Radius queries use Euclidean distance and return stable, deterministic order.

## State

Dense environmental arrays contain categorical terrain, elevation, baseline
and current temperature, moisture, and fertility.

V1 terrain types are plain, forest, water, and mountain. Terrain exposes
traversability, movement cost, and a fertility multiplier. These are facts an
agent can observe later; the world does not decide how an agent values them.

Food and water are sparse resource nodes with stable monotonic IDs. At most one
node of each resource type may occupy a cell. Extraction is atomic, never
returns more than the stored amount, and rejects negative requests. Regeneration
is applied in stable resource-ID order and cannot exceed capacity.

## Time and updates

`World.advance()` advances exactly one tick in this fixed order:

1. derive current temperature from baseline temperature and the diurnal cycle;
2. regenerate resource nodes in stable ID order;
3. commit the new tick;
4. return versioned world events.

Future climate systems must be added to this documented order. They must not
depend on rendering frequency, wall-clock time, hash iteration, or network
completion order.

## Generation and randomness

Procedural generation uses a configured seed. NumPy `SeedSequence` creates
separate streams for terrain, resource placement, and future runtime
randomness. Terrain is derived from smoothed elevation and moisture fields.
Food placement is weighted by fertility; water nodes prefer traversable shore
cells.

The same configuration, seed, code, and tick sequence must produce the same
complete-state hash. Runtime RNG state is included in snapshots and the hash.

## Snapshots

World snapshots are versioned compressed `.npz` files containing the resolved
configuration, current tick, every dense layer, every resource node, the next
available resource ID, and runtime RNG state. Loading a snapshot and continuing
must produce the same state hash as an uninterrupted run.

## Visualization boundary

Visualization is a read-only client of world state. A complete map payload is
sent on connection and regeneration. Normal ticks send compact updates with
only the tick, temperature offset, resource amounts, and dynamic statistics.

Each browser has a one-element update queue. When a client is slow, its stale
frame is replaced by the newest one, so it cannot slow world updates. The world
clock and browser rendering rate remain independent.

## Deferred until agent phases

- agent occupancy and spatial indexing;
- perception-specific filtering and line of sight;
- movement, pathfinding, action validation, and contention;
- seasons and stochastic weather;
- infinite or toroidal maps;
- rivers or fluid simulation.

