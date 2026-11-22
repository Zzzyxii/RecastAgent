# RecastAgent

**Recasting Videos Across Worlds and Media with Cross-Task Experience Accumulation**

Recasting a video into another world or artistic medium, such as turning a writer in a
cafe into a pirate captain or a street scene into a hand-drawn cartoon, takes more than a
good prompt. For each source video, an agent must decide how to phrase the requested
changes, which video to edit next, and which candidate to deliver. The right decisions
depend on how a fixed editor behaves on that particular footage.

RecastAgent is a tool-using planner that grounds an instruction in the source video,
consults design references, edits either the source or an earlier candidate, and
selects a delivery using video feedback. After each development task it reviews its own
trajectory and contributes conditional, evidence-linked advice to an experience library,
which is frozen before test tasks. No model weights are updated.

## Method

| Component | What it does |
|---|---|
| **Planner** | Returns one tool call per step: `observe` (native frames, crops), `browse`/`search`/`read` (references and experience), `view_reference`, `video`/`history`, `note`, `generate`, `finish` |
| **Input choice** | Each generation edits the source *or any saved candidate*, so achieved effects can be kept and lost content recovered |
| **Online review** | An independent Critic (or the Planner itself, in the self-review variant) describes the visible outcome of every generation, without scores |
| **Delivery** | After the full generation budget (default N=4), the Planner may deliver any candidate, including an earlier one |
| **Design references** | 111 style entries (98 OPUSCAR cases with reference images, 13 world templates), 7 design guides and 5 domain references |
| **Experience library** | 21 seed entries in three layers (general principles, editor behaviour, task conditions) backed by 74 evidence resources |
| **Experience accumulation** | After each development task, maintenance analyses the trajectory, reviews how experience was used and appends contributions (new entries, comments, clarifications); existing text is never overwritten |

Supported editors: [Bernini](third_party/Bernini) and Ditto. Planner, Critic and judges
are called through an OpenRouter-compatible API.

## Benchmark

`benchmark_data/benchmark.json` defines 67 source videos, each with a short and a
detailed instruction for the same creative direction (134 conditions). Targets span film
and TV worlds, anime and hand-drawn animation, Western animation and comics, painting and
printmaking, and craft/material transformations. Sources are split by group into 40
development and 27 test videos. Prompts are included; source videos are distributed
separately and must match the recorded SHA-256 hashes. See
[benchmark_data/README.md](benchmark_data/README.md).

Evaluation uses two independent judges (GLM-5.3-Flash and Claude Sonnet 5.5) over a
source-fixed frame plan, scoring target realization, content correspondence, temporal
consistency, visual integrity and aesthetic coordination (0–4), task diagnostics
(completion, correspondence, unrequested damage) and order-swapped blind pairwise
comparisons. See [docs/low-high-evaluation-v2.md](docs/low-high-evaluation-v2.md).

## Quick start (CPU only, no credentials)

Requires Python 3.11+ and `ffmpeg`/`ffprobe` on `PATH`.

```bash
python3 -m venv .venv && source .venv/bin/activate
python -m pip install -r requirements.server-runtime.txt

# Print the task matrix of the main experiment; submits nothing
python scripts/run_experiment_campaign.py plan --backend both

# End-to-end smoke run with synthetic videos and scripted model responses
python scripts/smoke_experiment_campaign.py --output out/public-smoke

# Offline regression suite
python scripts/check_repository.py --suite current
```

The smoke run blocks network access and GPU libraries. It checks software behaviour,
not model quality.

## Running the experiments

1. Put the benchmark videos at the paths listed in `benchmark_data/benchmark.json`.
2. Install a renderer (Bernini-R or Ditto) and its weights; pin them with
   `scripts/deploy/pin_bernini_runtime.py` or `scripts/deploy/download_ditto_models.py`.
3. Copy `configs/*-campaign-host.example.json` to `*.local.json` and fill in devices and
   asset paths. Keep API keys outside the repository, e.g. in
   `~/.config/recast/openrouter.env` (`OPENROUTER_API_KEY`, `OPENROUTER_BASE_URL`).
4. Validate offline, then run:

```bash
python scripts/run_experiment_campaign.py check --backend bernini \
  --host-config configs/bernini-campaign-host.local.json --verify-assets
python scripts/run_experiment_campaign.py run --backend bernini --stage main \
  --host-config configs/bernini-campaign-host.local.json \
  --output out/formal/bernini --execute
python scripts/benchmark_results.py score-evaluation \
  --root out/formal/bernini/results --execute
```

The `main` stage runs, in order, the pre-experience Planner (frozen seed experience),
the full-experience Planner (seed plus development-task contributions), Direct editing
and a prompt-enhancement loop baseline, then independent scoring. `--stage self-review`
runs the self-review ablation. Multiple GPUs on one host are used with `--devices 0 1 2 3`.
The complete procedure is in [docs/public-experiment-guide.md](docs/public-experiment-guide.md).

## Repository layout

| Path | Contents |
|---|---|
| `src/planner_harness/` | Task state, Planner tools, working memory, delivery, accounting, recovery, experience maintenance |
| `src/critic_harness/` | Online qualitative Critic and shared video-evidence tools |
| `src/experiments/` | Campaign orchestration, methods (Planner, Direct, PE loop), multi-GPU scheduling, result registry |
| `src/evaluation/` | Independent judges, automatic metrics, pairwise comparison and statistics |
| `src/server_runtime/` | Persistent job queue and resident Bernini/Ditto renderers |
| `src/vlm/` | Model API clients and role settings |
| `knowledge/` | Design references, guides and the curated experience library |
| `opuscar/` | OPUSCAR 98 source records and reference stills used by the style entries |
| `configs/` | Task, host, campaign and judge configuration |
| `scripts/` | Command-line entry points and offline tests |
| `docs/` | Experiment, runtime, evaluation and experience documentation |
| `third_party/Bernini/` | Bernini renderer code (Apache-2.0) |

## Third-party material

`third_party/Bernini` keeps its own license. The OPUSCAR 98 records and stills in
`opuscar/` are attributed to their original source in [opuscar/README.md](opuscar/README.md).
Model weights and benchmark videos are not distributed here.

## Citation

```bibtex
@misc{recastagent,
  title  = {Recasting Videos Across Worlds and Media with Cross-Task Experience Accumulation},
  author = {Anonymous},
  year   = {2026}
}
```
