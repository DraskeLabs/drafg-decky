import {
  DropdownItem,
  PanelSection,
  PanelSectionRow,
  SliderField,
  ToggleField,
  ButtonItem,
  staticClasses,
} from "@decky/ui";
import { callable, definePlugin } from "@decky/api";
import { useEffect, useRef, useState } from "react";
import { FaFilm } from "react-icons/fa";

type Model = "max" | "mobile";
type Mode = "interpolate" | "extrapolate";
type Upscaler = "bilinear" | "bilinear_sharpen" | "fsr_edge" | "fsr";
type Backend = "auto" | "vulkan_fast" | "vulkan" | "cuda";
type Matrix = "auto" | "on" | "off";
type CaptureMemory = "fast" | "compatible";
type MatrixSupport = "none" | "emulated" | "hardware";

type Settings = {
  multiplier: number;
  resolution: string;
  stats: boolean;
  model: Model;
  mode: Mode;
  upscaler: Upscaler;
  sharpness: number;
  backend: Backend;
  matrix: Matrix;
  capture_memory: CaptureMemory;
};

type Notice = { code: string; params: Record<string, number> };

type Status = {
  state: "running" | "waiting_for_game" | "starting" | "paused";
  game_app_id: number | null;
  game_fps: number | null;
  output_fps: number | null;
  added_ms: number | null;
  cadence: "locked" | "steady" | "free" | "starting" | "idle" | null;
  notice: Notice | null;
  refresh_hz: number | null;
  generation_ms_p50: number | null;
  model: string | null;
  backend: Backend | null;
  resolution: { capture: string; generation: string; stretched: boolean } | null;
  options: {
    resolutions: string[];
    backends: { name: Backend; available: boolean }[];
    matrix: MatrixSupport | null;
  } | null;
  warnings: string[];
};

type State = {
  installed: boolean;
  enabled: boolean;
  running: boolean;
  error: string | null;
  settings: Settings;
  per_game: boolean;
  status: Status | null;
};

const getState = callable<[], State>("get_state");
const setEnabled = callable<[enabled: boolean], State>("set_enabled");
const setSettings = callable<[settings: Partial<Settings>, rememberForGame: boolean], State>(
  "set_settings",
);
const forgetGame = callable<[], State>("forget_game");

const ERRORS: Record<string, string> = {
  ownership: "Steam could not confirm you own DraFG. Make sure Steam is signed in.",
  not_game_mode: "DraFG runs here in Game Mode only. In Desktop Mode, open DraFG itself.",
  already_running: "Another DraFG is already running.",
  crashed: "DraFG stopped unexpectedly several times and was not restarted.",
  restarting: "DraFG stopped unexpectedly; restarting…",
  waiting_for_game_mode: "Waiting for Game Mode to start…",
  waiting_for_steam: "Waiting for Steam…",
  update_drafg: "This DraFG is older than the plugin. Update DraFG in Steam, then turn this on again.",
};

// The panel shows one notice: the first of these that DraFG reports, as its
// `notice` or among its `warnings`.
const NOTICE_ORDER = [
  "vrr_active",
  "gpu_busy",
  "no_room",
  "limit_hint",
  "performance_overlay_hidden",
  "no_supported_size",
  "pipeline_error",
];

const BACKEND_LABELS: Record<Backend, string> = {
  auto: "Automatic",
  vulkan_fast: "Vulkan Fast",
  vulkan: "Vulkan (compatibility)",
  cuda: "CUDA (NVIDIA)",
};

const MATRIX_NOTES: Record<MatrixSupport, string> = {
  hardware: "This GPU has matrix cores; Automatic uses them.",
  emulated: "This GPU's driver only emulates matrix cores, which is slower, so Automatic leaves them off.",
  none: "This GPU has no matrix cores.",
};

function whole(v: number | null | undefined): string {
  return v == null ? "–" : `${Math.round(v)}`;
}

function size(s: string | undefined): string {
  return s ? s.replace("x", "×") : "";
}

/** The status line under the toggle. DraFG measures every number; the panel
 *  only words them. */
function describe(state: State): string | undefined {
  if (!state.installed) return "DraFG is not installed. Install it from Steam first.";
  if (state.error && ERRORS[state.error]) return ERRORS[state.error];
  if (!state.enabled) return undefined;
  const s = state.status;
  if (!s) return "Starting…";
  if (s.state === "paused") return "Paused";
  if (s.state === "waiting_for_game") return "Waiting for a game";
  if (s.state === "starting" || s.cadence === "starting" || s.game_fps == null) return "Starting…";
  const game = s.game_fps;
  if (s.cadence === "idle") return `Game already runs at ${whole(game)} fps — nothing to add`;
  // Nothing added right now (a notice says why, when there is a reason).
  if (s.output_fps == null || Math.round(s.output_fps) <= Math.round(game)) return `${whole(game)} fps`;
  const refresh = s.refresh_hz;
  const screenMax =
    refresh != null && s.output_fps >= refresh * 0.98 && game * state.settings.multiplier > refresh * 1.02;
  const delay = s.added_ms == null ? "" : ` · +${whole(Math.max(0, s.added_ms))} ms`;
  return `${whole(game)} → ${whole(s.output_fps)} fps${screenMax ? " (screen max)" : ""}${delay}`;
}

function pickNotice(status: Status | null): Notice | null {
  if (!status) return null;
  const reported = [
    ...(status.notice ? [status.notice] : []),
    ...(status.warnings ?? []).map((code) => ({ code, params: {} })),
  ];
  for (const code of NOTICE_ORDER) {
    const found = reported.find((n) => n.code === code);
    if (found) return found;
  }
  return null;
}

function noticeText(notice: Notice, settings: Settings, status: Status | null): string {
  const p = (key: string) => whole(notice.params?.[key]);
  switch (notice.code) {
    case "vrr_active":
      return "Variable refresh is on, so generated frames can't show. Turn it off in Performance.";
    case "gpu_busy": {
      // The overlay's card gives the same advice: at the smallest size
      // offered (the first), there is no smaller one to suggest.
      const smallest = status?.options?.resolutions?.[0];
      const atSmallest = settings.resolution !== "native" && settings.resolution === smallest;
      const fix =
        settings.model === "mobile"
          ? atSmallest
            ? "Lower the game's graphics settings to leave room."
            : "A smaller frame size would fit."
          : atSmallest
            ? "Mobile would fit."
            : "Mobile or a smaller frame size would fit.";
      return `GPU busy — showing ${p("output")} fps. ${fix}`;
    }
    case "no_room":
      return `Steam's ${p("fps")} fps limit runs the screen at ${p("hz")} Hz — no room in between. A ${p("fps2")} fps limit leaves room for ×${p("m")}.`;
    case "limit_hint":
      return `Smoothest: set Steam's frame limit to ${p("fps")} (${p("hz")} Hz).`;
    case "performance_overlay_hidden":
      return `Steam's performance overlay is hidden while DraFG shows (they share one overlay slot).${
        settings.stats ? "" : " Turn on DraFG's own stats below instead."
      }`;
    case "no_supported_size": {
      const r = status?.resolution;
      return r
        ? `No supported frame size for this screen (${size(r.capture)}): DraFG runs at ${size(r.generation)}, stretched to fit, so the picture is softer.`
        : "No supported frame size for this screen: DraFG runs stretched to fit, so the picture is softer.";
    }
    default: // pipeline_error
      return "DraFG hit an error applying a setting; the previous settings are still running.";
  }
}

function Content() {
  const [state, setState] = useState<State | null>(null);
  const [rememberForGame, setRemember] = useState(false);
  const [advanced, setAdvanced] = useState(false);
  const synced = useRef<string | null>(null);

  useEffect(() => {
    let alive = true;
    const tick = async () => {
      try {
        const next = await getState();
        if (alive) {
          setState(next);
          // The toggle follows what is saved only when the game, or whether
          // it has settings saved, changes: turned on for a game with
          // nothing saved yet, it stays on.
          const key = `${next.status?.game_app_id ?? ""}:${next.per_game}`;
          if (key !== synced.current) {
            synced.current = key;
            setRemember(next.per_game);
          }
        }
      } catch (e) {
        console.error("DraFG: get_state failed", e);
      }
    };
    tick();
    const timer = setInterval(tick, 1000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

  if (!state) {
    return (
      <PanelSection>
        <PanelSectionRow>Loading…</PanelSectionRow>
      </PanelSection>
    );
  }

  const change = async (settings: Partial<Settings>) => {
    setState(await setSettings(settings, rememberForGame));
  };
  const s = state.settings;
  const status = state.status;
  const notice = pickNotice(status);
  const mobile = s.model === "mobile";
  const chooseModel = (model: Model) =>
    change(model === "mobile" && s.mode === "extrapolate" ? { model, mode: "interpolate" } : { model });

  // Native first, then the sizes DraFG offers on this screen (and the
  // chosen one, should it be from a bigger screen).
  const native = status?.resolution?.capture;
  const sizes = status?.options?.resolutions ?? [];
  const sizeOptions = [
    { data: "native", label: native ? `Native · ${size(native)}` : "Native" },
    ...[...sizes, ...(s.resolution !== "native" && !sizes.includes(s.resolution) ? [s.resolution] : [])].map(
      (r) => ({ data: r, label: size(r) }),
    ),
  ];

  const backends = status?.options?.backends;
  // Automatic, and each backend this device has (CUDA only where it is).
  const backendOptions = (["auto", "vulkan_fast", "vulkan", "cuda"] as Backend[])
    .filter((b) => b === "auto" || b === s.backend || (backends ? backends.some((o) => o.name === b && o.available) : b !== "cuda"))
    .map((b) => ({ data: b, label: BACKEND_LABELS[b] }));

  const matrix = status?.backend === "vulkan_fast" ? status?.options?.matrix ?? null : null;
  const matrixNote =
    matrix &&
    (s.matrix === "auto" ? `Automatic chose: ${matrix === "hardware" ? "On" : "Off"}. ` : "") + MATRIX_NOTES[matrix];

  return (
    <>
      <PanelSection>
        <PanelSectionRow>
          <ToggleField
            label="Frame generation"
            description={describe(state)}
            checked={state.enabled}
            disabled={!state.installed}
            onChange={async (on) => setState(await setEnabled(on))}
          />
        </PanelSectionRow>
        {notice && (
          <PanelSectionRow>
            <div style={{ fontSize: "12px", color: "#ffc857" }}>{noticeText(notice, s, status)}</div>
          </PanelSectionRow>
        )}
        {notice?.code === "gpu_busy" && !mobile && (
          <PanelSectionRow>
            <ButtonItem layout="below" onClick={() => chooseModel("mobile")}>
              Use Mobile
            </ButtonItem>
          </PanelSectionRow>
        )}
      </PanelSection>
      <PanelSection title="Settings">
        <PanelSectionRow>
          <DropdownItem
            label="Model"
            description={
              mobile ? "Faster, for handhelds: softer in fast motion and around HUD text. Interpolation only." : undefined
            }
            rgOptions={[
              { data: "max", label: "Max" },
              { data: "mobile", label: "Mobile" },
            ]}
            selectedOption={s.model}
            onChange={(o) => chooseModel(o.data)}
          />
        </PanelSectionRow>
        <PanelSectionRow>
          <SliderField
            label="Multiplier"
            description="At most this many times the game's frame rate."
            value={s.multiplier}
            min={2}
            max={16}
            step={1}
            showValue
            valueSuffix="×"
            onChange={(v) => change({ multiplier: v })}
          />
        </PanelSectionRow>
        <PanelSectionRow>
          <DropdownItem
            label="Frame size"
            description="Smaller is faster and softer."
            rgOptions={sizeOptions}
            selectedOption={s.resolution}
            onChange={(o) => change({ resolution: o.data })}
          />
        </PanelSectionRow>
        {s.resolution !== "native" && (
          <>
            <PanelSectionRow>
              <DropdownItem
                label="Upscaling"
                rgOptions={[
                  { data: "bilinear_sharpen", label: "Bilinear + Sharpen" },
                  { data: "bilinear", label: "Bilinear" },
                  { data: "fsr_edge", label: "FSR Edge" },
                  { data: "fsr", label: "FSR (Edge + Sharpen)" },
                ]}
                selectedOption={s.upscaler}
                onChange={(o) => change({ upscaler: o.data })}
              />
            </PanelSectionRow>
            {(s.upscaler === "bilinear_sharpen" || s.upscaler === "fsr") && (
              <PanelSectionRow>
                <SliderField
                  label="Sharpness"
                  value={s.sharpness}
                  min={0}
                  max={1}
                  step={0.05}
                  showValue
                  onChange={(v) => change({ sharpness: Math.round(v * 100) / 100 })}
                />
              </PanelSectionRow>
            )}
          </>
        )}
        <PanelSectionRow>
          <ToggleField label="Show stats" checked={s.stats} onChange={(v) => change({ stats: v })} />
        </PanelSectionRow>
        <PanelSectionRow>
          <ToggleField
            label="Remember for this game"
            description="Changes apply to the game that is running only."
            checked={rememberForGame}
            onChange={async (v) => {
              setRemember(v);
              if (!v && state.per_game) setState(await forgetGame());
            }}
          />
        </PanelSectionRow>
        <PanelSectionRow>
          <ToggleField label="Advanced settings" checked={advanced} onChange={setAdvanced} />
        </PanelSectionRow>
      </PanelSection>
      {advanced && (
        <PanelSection title="Advanced">
          <PanelSectionRow>
            <DropdownItem
              label="Generation"
              description={
                mobile
                  ? "Mobile interpolates only: choose Max to extrapolate."
                  : s.mode === "extrapolate"
                    ? "Experimental: no added delay, but fast motion can judder. Interpolate is smoother."
                    : undefined
              }
              disabled={mobile}
              rgOptions={[
                { data: "interpolate", label: "Interpolate" },
                { data: "extrapolate", label: "Extrapolate (experimental)" },
              ]}
              selectedOption={mobile ? "interpolate" : s.mode}
              onChange={(o) => change({ mode: o.data })}
            />
          </PanelSectionRow>
          <PanelSectionRow>
            <DropdownItem
              label="Processing backend"
              description={
                s.backend === "auto" && status?.backend ? `Automatic chose: ${BACKEND_LABELS[status.backend]}.` : undefined
              }
              rgOptions={backendOptions}
              selectedOption={s.backend}
              onChange={(o) => change({ backend: o.data })}
            />
          </PanelSectionRow>
          {matrix && (
            <PanelSectionRow>
              <DropdownItem
                label="Matrix acceleration"
                description={matrixNote || undefined}
                rgOptions={[
                  { data: "auto", label: "Automatic" },
                  ...(matrix !== "none" ? [{ data: "on", label: "On" }] : []),
                  { data: "off", label: "Off" },
                ]}
                selectedOption={s.matrix}
                onChange={(o) => change({ matrix: o.data })}
              />
            </PanelSectionRow>
          )}
          <PanelSectionRow>
            <DropdownItem
              label="Capture memory"
              description="Fast keeps frames on the GPU. Try Compatible if the picture is black or capture fails."
              rgOptions={[
                { data: "fast", label: "Fast" },
                { data: "compatible", label: "Compatible" },
              ]}
              selectedOption={s.capture_memory}
              onChange={(o) => change({ capture_memory: o.data })}
            />
          </PanelSectionRow>
        </PanelSection>
      )}
      {status?.model && (
        <PanelSection>
          <PanelSectionRow>
            <div style={{ fontSize: "12px", opacity: 0.7 }}>
              {status.model}
              {status.generation_ms_p50 != null &&
                ` · ${status.generation_ms_p50.toFixed(1)} ms per generated frame`}
            </div>
          </PanelSectionRow>
        </PanelSection>
      )}
    </>
  );
}

export default definePlugin(() => ({
  name: "DraFG",
  titleView: <div className={staticClasses.Title}>DraFG</div>,
  content: <Content />,
  icon: <FaFilm />,
}));
