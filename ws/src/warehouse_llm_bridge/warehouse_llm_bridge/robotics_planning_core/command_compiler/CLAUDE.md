# command_compiler — Mode X-ER L3 Command Compiler (XER5, GitHub #341)

L3's final stage (doc02:200-242): the converter that drops resolved `navigate` tasks into the
EXISTING frozen `warehouse_interfaces.schemas.Command` so the Gemini Robotics-ER / OpenCV /
NetworkX world never leaks into the `Command -> action_map -> MCP -> Policy Gate -> Nav2/RMF`
world (doc02:236). Standalone, **bridge-local offline core**; it does NOT wire into
`pipeline.py` (that is XER6, doc02:19) and performs **no actuation** (pure data transform).

- **担当トラック / ブランチ**: Mode X-ER / `feat/mode-x-er-xer5`
- **Phase**: Mode X-ER L3 Planning Core (stage 4 of 4, doc02:14-16).
- **編集境界**: this subpackage dir + `tests/unit/test_command_compiler.py` ONLY. **Additive**:
  no existing file edited (not `validator/*`, `visual_resolver/*`, `task_graph_executor/*`,
  `models/*`, `pipeline.py`, `conftest.py`, docs/config). `warehouse_interfaces` is UNCHANGED.

## frozen vs bridge-local

doc02:5 declares everything in doc02 internal/illustrative. `ExecutionProfile` /
`CompilationResult` / `SkippedTask` are **bridge-local (発明)**. The compiler's PRODUCT — the
`Command` it emits — IS the frozen `warehouse_interfaces` contract; the compiler REUSES it, and
does NOT promote `RoboticsPlan` / `ValidationReport` into `warehouse_interfaces` (doc02:278).

## 提供 (produce)

- `CommandCompiler` — abstract plugin seam (doc02:240): `compile(tasks, targets, profile) -> Command`
  and `compile_with_audit(...) -> CompilationResult`.
- `WarehouseNavCompiler(CommandCompiler)` — the X-lite MVP. Compiles ONLY a `navigate` task
  whose visual target snapped to a `KNOWN_LOCATION` into a `CommandItem(bot, NAVIGATE,
  destination)`. **0-dispatch (doc02:231,68,151)**: non-navigate action / no robot|target /
  target absent from resolution / unresolved target / destination outside `KNOWN_LOCATIONS` are
  all skipped + audited, never dispatched. Never emits velocity (structurally absent) or
  coordinate goals (doc02:37,233); never mints `gen_id` / `idempotency_key` (doc02:230).
- `ExecutionProfile` StrEnum `{x_lite, x_rmf}` (doc02:234). `x_rmf` raises `NotImplementedError`
  (the `RmfTaskCompiler` plugin is deferred, #346).
- `CompilationResult{ command: Command, compiled: tuple[str,...], skipped: tuple[SkippedTask,...] }`
  and `SkippedTask{ task_id, reason }` — the 1:1 compile audit trail (doc02:242); every input
  ready task appears in exactly one of `compiled` / `skipped`.

## 消費 (consume)

- bridge-local `ReadyTask` (`task_graph_executor`, XER4): `task_id` / `action` /
  `payload{robot,target,after}`. Join key: `ReadyTask.payload["target"] == ResolvedTarget.target_id`
  (both are `Detection.id`).
- bridge-local `ResolutionResult` / `ResolvedTarget` / `Resolution` (`visual_resolver`, XER3).
- frozen `warehouse_interfaces.schemas` `Command` / `CommandItem` / `CommandAction` +
  `warehouse_interfaces.locations.KNOWN_LOCATIONS`.

## 依存

- `warehouse_interfaces` + the two landed bridge-local subpackages (`visual_resolver`,
  `task_graph_executor`). No other track's internals; no `config`; no ROS.

## テスト

- `tests/unit/test_command_compiler.py`: 22 offline unit tests — happy path, the 0-dispatch skip
  chain (R-26), fail-closed unknown destination, no velocity/idempotency, 1:1 audit
  completeness, `x_rmf` deferral, abstract-base guard, and one seam test driving the REAL
  `TaskGraphExecutor` on the red/blue fixture. `python3 -m pytest tests/unit/test_command_compiler.py`.

## 設計ドキュメント

- `docs/mode-x-er/02-l3-planning-core.md:200-242` (§4 Command Compiler) + `:263-269` (signature).
- `docs/mode-x-er/README.md` / `01-architecture-and-flow.md` (X-lite vs X-rmf).
