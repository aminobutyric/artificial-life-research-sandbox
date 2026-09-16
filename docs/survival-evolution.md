# Survival and evolution phase

The default experiment now runs 500 genetic foragers. `agents.brain: random`
retains the movement-only random policy as a starvation baseline. Set
`life.enabled: false` to disable metabolism, death, eating, and reproduction.
Water remains an environmental resource; thirst is deferred.

## Tick contract

1. Advance the environment and regenerate resources.
2. Age existing agents and deduct metabolism.
3. Remove agents with zero energy/health or age at the configured limit.
4. Build immutable local observations for all survivors, then collect intents.
5. Resolve one intent per agent in increasing ID order.
6. Create the children of successful reproduction intents.
7. Remove agents whose movement spent their remaining energy.

Newborns have age zero and first observe/act on the next tick. Multiple agents
can occupy the same cell. They may compete for that cell's food. Stable ID order
gives earlier IDs priority, an explicit experimental bias to revisit with a
seeded contention policy. Death removes state from the live spatial and brain
registries. Storage slots are reused, but IDs never are. Historical identity and
genomes are available through lifecycle events, rather than retained corpses.

## Energy accounting

All costs are per simulation tick or successful action, independent of wall time.
Defaults are configured in `experiments/world.yaml`.

- Metabolism costs `0.15 * (0.5 + metabolism_gene)` energy per tick.
- Cardinal movement costs the configured flat `move_energy_cost`; terrain
  movement multipliers are still deferred.
- `IDLE` has no additional cost and does not restore energy.
- `EAT` extracts at most four food units from the current cell. Each food unit
  restores `5 * (0.75 + 0.5 * metabolism_gene)` energy. The extraction is reduced
  to fit the energy cap of 160, so full agents waste no food.
- `REPRODUCE` deducts 60 energy: 50 becomes the child's energy and 10 is lost as
  reproduction cost. A parent must retain positive energy, be at least 50 ticks
  old, and respect a 50-tick cooldown. Population is capped at 2,000. Rejected
  births cost nothing, including when earlier births reserve the final slots.
- Death occurs at zero energy/health or age 2,000. Health currently has no damage
  mechanic. Remaining energy at old-age death leaves the system.

The metabolic gene trades a larger upkeep cost for better food conversion.
This is a designed selection pressure, not a biological claim.

## Genome and decisions

Five immutable genes lie in [0, 1]: exploration, food attraction, reproduction
threshold, metabolism, and vision. Founders receive seeded independent genomes.
Vision radius is `max(1, configured_radius * (0.5 + vision_gene))`.

The genetic policy reproduces when mature, out of cooldown, and above its
gene-dependent energy threshold. Otherwise it eats available food on its cell,
then probabilistically follows a shortest path to visible food or explores.
Paths traverse only observed, traversable cells. It has no knowledge of hidden
food or a global map. Exploration and food-attraction genes affect these choices.
The resolver remains authoritative about energy, food availability, and births.

Each child copies the parent's genome. Independently for each gene, mutation
occurs with probability 0.10, adds normal noise with sigma 0.08, and clips to
[0, 1]. Mutation RNG is derived from seed, tick, parent ID, and new child ID.
The child's generation is the parent's generation plus one. There is no global
fitness function, crossover, or learned neural policy in this phase.

## Inspection and artifacts

The live UI uses larger orange dots for founders and gold dots for descendants.
It shows cumulative births/deaths, highest living generation, and gene diversity
(the mean population standard deviation across five genes). Hover over a cell
for lineage and the first resident's genome. Empty populations display an
extinction message; environmental time can continue.

```console
uv run alife --config experiments/world.yaml run --ticks 1000 --events life.jsonl
uv run alife --config experiments/starvation.yaml run --ticks 300
```

The optional buffered JSONL log refuses to overwrite an existing file. It records
resolved config and initial agent state, then per-tick world events, intents,
action results, births and deaths, and a final state hash. A partial run lacks
the completion record. Per-tick metabolism can be derived from the recorded
configuration and genomes. This is an inspectable run log, not an event-replay
implementation. No histories accumulate in memory during headless execution.

`generate --output world.npz` still saves only a **world** snapshot. Full
simulation checkpoints (agents, lineage, brains, counters, and allocation state)
remain a future phase. Do not use a world snapshot as an agent-run checkpoint.
Determinism is scoped to matching code and dependency versions. Visualization
settings are excluded from the simulation state hash.

Passing lifecycle tests demonstrates rule consistency, not population stability
or successful adaptation. Those require longer experiments across multiple seeds.
