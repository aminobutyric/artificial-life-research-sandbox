# Implementation backlog

This is the agreed implementation order for the next milestone: agents that
remember and adapt to a changing world. Complete and verify each item before
starting the next. Items 1–3 are implemented and verified; items 4–5 are planned.

## Shared decisions

- Provide a finite, explicit catalog of decision cores.
- Select one core for the entire population before each run. Offspring inherit
  that core; mixed-core populations are outside this milestone.
- Keep core selection separate from genome variation and mutation settings.
- Freeze model and behavior settings at run start. Execution controls such as
  pause and speed remain available without changing model semantics.
- Preserve deterministic execution and record the resolved run configuration.
- Keep the existing food-regeneration study as a baseline. Freeze its protocol,
  runner, configurations, and model in a commit before the canonical rerun.

## 1. Run setup and management panel

Status: complete. Dependencies: none.

Implemented behavior and API semantics: [Run setup and management](run-setup.md).

Provide a pre-run panel for selecting the core, its supported parameters,
founder population, environment settings, seed, and tick horizon. Initially
expose the existing random-movement and reactive-forager cores; add the memory
core only when item 4 implements it.

Completion criteria:

- The UI lists supported cores and explains their behavior.
- Inputs are validated by the server and errors are visible in the UI.
- Starting a new run creates a fresh simulation with an explicit resolved
  configuration and one population-wide core.
- The selected core and configuration remain inspectable throughout the run.
- Model settings cannot silently change during a run; changes require a new run.
- Existing play, pause, step, speed, bounded-run, and agent-inspection controls
  continue to work.
- Tests cover configuration validation, core selection and inheritance, and
  the run-start boundary; the panel is checked in the browser.

## 2. Completion summary and saved results

Status: complete. Dependencies: item 1.

Implemented behavior: [Completion summaries and saved results](completion-results.md).

When a bounded experiment reaches its target tick, open a summary dialog and
retain its results for later review.

Completion criteria:

- Completing the target horizon opens the summary once for that completion.
- A manual pause does not trigger an experiment-completion summary.
- The summary shows configuration, core, seed, elapsed ticks, initial/final
  population, births, deaths, extinction outcome, and population trajectory.
- Dismissing the dialog does not discard results; the summary can be reopened.
- Results can be downloaded, and the full report is accessible when generated.
- Completed browser runs persist a manifest, sampled metrics, and summaries.
- Failed or interrupted runs have an explicit status and are not presented as
  completed experiments.
- Completion behavior, persisted results, and dialog interactions are verified.

## 3. Full simulation checkpoints

Status: complete. Dependencies: items 1 and 2.

Implemented behavior and format: [Full simulation checkpoints](checkpoints.md).

Extend environment-only snapshots to save and restore the complete simulation.

Completion criteria:

- Checkpoints preserve environment, agents, genomes, lineage, core identities,
  brain state, tick, lifecycle counters, ID allocators, resolved configuration,
  and every random state required for continuation.
- Restore rebuilds derived indexes without altering simulation semantics.
- Checkpoints have a versioned format with clear validation and compatibility
  errors; environment snapshots remain clearly distinguished.
- A save/restore continuation produces the same subsequent simulation state
  and outcomes as an uninterrupted run.
- The checkpoint interface supports the memory state introduced in item 4.

## 4. Changing habitats and memory foragers

Status: planned. Dependencies: items 1–3.

Add configurable food patches with changing availability and a memory-based
core that remembers resource locations and ages stale information.

Completion criteria:

- Stable and changing habitat modes are explicit, seeded, and reproducible.
- Patch depletion, recovery, and availability schedules have documented rules.
- Memory uses information discovered through the agent's observations.
- Memory capacity, aging/forgetting, failed-search updates, and destination
  selection have finite, documented rules.
- The memory core is selectable before a run and is inherited by offspring;
  inheritance/reset of learned memory is specified explicitly.
- Agent inspection shows remembered locations, current destination, and a
  concise decision reason; changing food availability is visible on the map.
- Memory checkpoints restore deterministically, and existing cores remain
  available as comparison policies.
- Tests and browser checks verify habitat dynamics and memory behavior.

## 5. Controlled memory comparison

Status: planned. Dependencies: items 1–4.

Define and execute a four-condition experiment comparing reactive and memory
foragers in stable and changing habitats, with one core per run.

Research question: does spatial memory improve survival and reproduction, and
does its advantage weaken when food locations become unreliable?

Completion criteria:

- Commit the protocol before inspecting outcomes: fixed seeds, horizon,
  sampling, primary/secondary outcomes, and failure/extinction handling.
- Match food supply across habitat modes as closely as the design permits;
  document remaining differences and how supply is measured.
- Hold other model settings fixed across the four conditions.
- Define and instrument search-efficiency measures before running the study.
- Run from a clean committed model and retain all prescribed replicates.
- Produce reproducible artifacts and a visual report covering population,
  reproduction, energy, search efficiency, and variation across seeds.
- Interpret findings within the implemented model and the predefined design.

## Later candidates

Communication, social behavior, mixed-core populations, and recorded mid-run
behavior interventions can be considered after this milestone. They are not
part of the five-item implementation sequence.
