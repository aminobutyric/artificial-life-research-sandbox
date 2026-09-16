# Agent Phase 1 Contract

Historical movement-only milestone. The active lifecycle and tick semantics are
specified in [Survival and evolution](survival-evolution.md).

This phase introduces agents without survival, reproduction, combat, memory, or
communication. Its purpose is to prove the complete perception-to-action
boundary before adding evolutionary pressure.

## Ownership

```text
World             environmental and physical truth
AgentStore        mutable agent state
Brain             decision policy only
ActionResolver    validation and physical outcomes
SimulationEngine  tick order and orchestration
```

A brain receives an immutable `Observation` and returns an `ActionIntent`. It
cannot access or mutate `World`, `AgentStore`, or the spatial index.

## Agent state

Agent state is stored in typed NumPy arrays behind `AgentStore`. IDs are stable,
monotonic, and never reused. Public consumers receive frozen `AgentView`
instances instead of array references.

Phase 1 state consists of ID, integer position, health, energy, age, vision
radius, and alive status. Birth and death slots will be added with the survival
phase.

Multiple agents may occupy one cell. This deliberately avoids introducing a
collision policy before there is an experiment that needs one.

## Perception

Each observation contains the observing agent's state, nearby agents, nearby
resources, nearby cells, and the current tick. Radius checks use Euclidean
distance. The observing agent is excluded from `nearby_agents`.

## Decisions and randomness

Every initial agent uses `RandomBrain`. It chooses `IDLE` or a traversable
cardinal `MOVE`. Each decision receives an independent RNG derived from:

```text
experiment seed + namespace + tick + agent ID
```

One agent's random calls therefore cannot shift another agent's decision stream.

## Resolution

Intents are resolved in ascending agent-ID order. Movement must be cardinal,
adjacent, in bounds, traversable, and affordable. Invalid intents become
explicit rejected results and do not change state. Phase 1 permits shared cells,
so movement has no contention rule yet.

## Tick order

```text
1. Advance environment
2. Age living agents
3. Build observations
4. Ask brains for intents
5. Resolve and apply intents
6. Return the tick report
```

Visualization consumes full or compact snapshots from the engine and cannot
affect this order.

## Deferred

- metabolism and passive energy loss;
- eating and resource extraction;
- death and slot reuse;
- reproduction, genomes, and mutation;
- memory, relationships, and communication;
- movement contention or per-cell capacity;
- complete simulation snapshots containing agents.
