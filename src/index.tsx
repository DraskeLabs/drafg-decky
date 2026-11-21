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
import { Key, t } from "./i18n";

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
    /** DraFG Demo stops at 4; the full product says 16 (absent before 1.0). */
    max_multiplier?: number;
  } | null;
  edition?: "full" | "demo";
  warnings: string[];
};

type State = {
  installed: boolean;
  /** The installed DraFG is DraFG Demo. */
  demo: boolean;
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

const ERRORS: Record<string, Key> = {
  ownership: "error.ownership",
  not_game_mode: "error.not_game_mode",
  already_running: "error.already_running",
  crashed: "error.crashed",
  restarting: "error.restarting",
  waiting_for_game_mode: "error.waiting_for_game_mode",
  waiting_for_steam: "error.waiting_for_steam",
  update_drafg: "error.update_drafg",
};

// The panel shows one notice: the first of these that DraFG reports, as its
// `notice` or among its `warnings`.
/** The multiplier's top in the full product, and DraFG Demo's (the plugin's
 * guess before DraFG reports `options.max_multiplier`). */
const FULL_MAX_MULTIPLIER = 16;
const DEMO_MAX_MULTIPLIER = 4;

const NOTICE_ORDER = [
  "vrr_active",
  "gpu_busy",
  "no_room",
  "limit_hint",
  "performance_overlay_hidden",
  "no_supported_size",
  "pipeline_error",
];

function backendLabel(b: Backend): string {
  switch (b) {
    case "auto":
      return t("backend.automatic");
    case "vulkan_fast":
      return "Vulkan Fast";
    case "vulkan":
      return t("backend.vulkan_compat");
    case "cuda":
      return "CUDA (NVIDIA)";
  }
}

const MATRIX_NOTES: Record<MatrixSupport, Key> = {
  hardware: "matrix.hardware",
  emulated: "matrix.emulated",
  none: "matrix.none",
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
  if (!state.installed) return t("status.not_installed");
  if (state.error && ERRORS[state.error]) return t(ERRORS[state.error]);
  if (!state.enabled) return undefined;
  const s = state.status;
  if (!s) return t("status.starting");
  if (s.state === "paused") return t("status.paused");
  if (s.state === "waiting_for_game") return t("status.waiting_for_game");
  if (s.state === "starting" || s.cadence === "starting" || s.game_fps == null) return t("status.starting");
  const game = s.game_fps;
  if (s.cadence === "idle") return t("status.already_at_rate", { fps: whole(game) });
  // Nothing added right now (a notice says why, when there is a reason).
  if (s.output_fps == null || Math.round(s.output_fps) <= Math.round(game)) return `${whole(game)} fps`;
  const refresh = s.refresh_hz;
  const screenMax =
    refresh != null && s.output_fps >= refresh * 0.98 && game * state.settings.multiplier > refresh * 1.02;
  const delay = s.added_ms == null ? "" : ` · +${whole(Math.max(0, s.added_ms))} ms`;
  return `${whole(game)} → ${whole(s.output_fps)} fps${screenMax ? t("status.screen_max") : ""}${delay}`;
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
      return t("notice.vrr_active");
    case "gpu_busy": {
      // The overlay's card gives the same advice: at the smallest size
      // offered (the first), there is no smaller one to suggest.
      const smallest = status?.options?.resolutions?.[0];
      const atSmallest = settings.resolution !== "native" && settings.resolution === smallest;
      const fix =
        settings.model === "mobile"
          ? atSmallest
            ? t("notice.fix.game_settings")
            : t("notice.fix.smaller_size")
          : atSmallest
            ? t("notice.fix.mobile")
            : t("notice.fix.mobile_or_smaller");
      return t("notice.gpu_busy", { fps: p("output"), fix });
    }
    case "no_room":
      return t("notice.no_room", { fps: p("fps"), hz: p("hz"), fps2: p("fps2"), m: p("m") });
    case "limit_hint":
      return t("notice.limit_hint", { fps: p("fps"), hz: p("hz") });
    case "performance_overlay_hidden":
      return t("notice.performance_overlay_hidden") + (settings.stats ? "" : t("notice.turn_on_stats"));
    case "no_supported_size": {
      const r = status?.resolution;
      return r
        ? t("notice.no_supported_size", { capture: size(r.capture), generation: size(r.generation) })
        : t("notice.no_supported_size_short");
    }
    default: // pipeline_error
      return t("notice.pipeline_error");
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
        <PanelSectionRow>{t("panel.loading")}</PanelSectionRow>
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
  const chooseModel = (model: Model) => change({ model });

  // Native first, then the sizes DraFG offers on this screen (and the
  // chosen one, should it be from a bigger screen).
  const native = status?.resolution?.capture;
  const sizes = status?.options?.resolutions ?? [];
  const sizeOptions = [
    { data: "native", label: native ? t("size.native_with", { size: size(native) }) : t("size.native") },
    ...[...sizes, ...(s.resolution !== "native" && !sizes.includes(s.resolution) ? [s.resolution] : [])].map(
      (r) => ({ data: r, label: size(r) }),
    ),
  ];

  const backends = status?.options?.backends;
  // Automatic, and each backend this device has (CUDA only where it is).
  const backendOptions = (["auto", "vulkan_fast", "vulkan", "cuda"] as Backend[])
    .filter((b) => b === "auto" || b === s.backend || (backends ? backends.some((o) => o.name === b && o.available) : b !== "cuda"))
    .map((b) => ({ data: b, label: backendLabel(b) }));

  const matrix = status?.backend === "vulkan_fast" ? status?.options?.matrix ?? null : null;
  // DraFG Demo stops at its limit: what DraFG says, or the demo's 4 before it runs.
  const maxMultiplier = status?.options?.max_multiplier ?? (state.demo ? DEMO_MAX_MULTIPLIER : FULL_MAX_MULTIPLIER);
  const matrixNote =
    matrix &&
    (s.matrix === "auto" ? t("matrix.automatic_chose", { state: t(matrix === "hardware" ? "matrix.on" : "matrix.off") }) : "") +
      t(MATRIX_NOTES[matrix]);

  return (
    <>
      <PanelSection>
        <PanelSectionRow>
          <ToggleField
            label={t("panel.frame_generation")}
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
              {t("panel.use_mobile")}
            </ButtonItem>
          </PanelSectionRow>
        )}
      </PanelSection>
      <PanelSection title={t("panel.settings")}>
        <PanelSectionRow>
          <DropdownItem
            label={t("panel.model")}
            description={
              mobile ? t("panel.model_mobile_note") : undefined
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
            label={t("panel.multiplier")}
            description={
              maxMultiplier < FULL_MAX_MULTIPLIER
                ? `${t("panel.multiplier_note")} ${t("panel.multiplier_full_version", { max: FULL_MAX_MULTIPLIER })}`
                : t("panel.multiplier_note")
            }
            value={Math.min(s.multiplier, maxMultiplier)}
            min={2}
            max={maxMultiplier}
            step={1}
            showValue
            valueSuffix="×"
            onChange={(v) => change({ multiplier: v })}
          />
        </PanelSectionRow>
        <PanelSectionRow>
          <DropdownItem
            label={t("panel.frame_size")}
            description={t("panel.frame_size_note")}
            rgOptions={sizeOptions}
            selectedOption={s.resolution}
            onChange={(o) => change({ resolution: o.data })}
          />
        </PanelSectionRow>
        {s.resolution !== "native" && (
          <>
            <PanelSectionRow>
              <DropdownItem
                label={t("panel.upscaling")}
                rgOptions={[
                  { data: "bilinear_sharpen", label: t("upscaler.bilinear_sharpen") },
                  { data: "bilinear", label: t("upscaler.bilinear") },
                  { data: "fsr_edge", label: "FSR Edge" },
                  { data: "fsr", label: t("upscaler.fsr") },
                ]}
                selectedOption={s.upscaler}
                onChange={(o) => change({ upscaler: o.data })}
              />
            </PanelSectionRow>
            {(s.upscaler === "bilinear_sharpen" || s.upscaler === "fsr") && (
              <PanelSectionRow>
                <SliderField
                  label={t("panel.sharpness")}
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
          <ToggleField label={t("panel.show_stats")} checked={s.stats} onChange={(v) => change({ stats: v })} />
        </PanelSectionRow>
        <PanelSectionRow>
          <ToggleField
            label={t("panel.remember_for_game")}
            description={t("panel.remember_for_game_note")}
            checked={rememberForGame}
            onChange={async (v) => {
              setRemember(v);
              if (!v && state.per_game) setState(await forgetGame());
            }}
          />
        </PanelSectionRow>
        <PanelSectionRow>
          <ToggleField label={t("panel.advanced_settings")} checked={advanced} onChange={setAdvanced} />
        </PanelSectionRow>
      </PanelSection>
      {advanced && (
        <PanelSection title={t("panel.advanced")}>
          <PanelSectionRow>
            <DropdownItem
              label={t("panel.generation")}
              description={
                s.mode === "extrapolate"
                  ? t("panel.extrapolate_note")
                  : undefined
              }
              rgOptions={[
                { data: "interpolate", label: t("mode.interpolate") },
                { data: "extrapolate", label: t("mode.extrapolate") },
              ]}
              selectedOption={s.mode}
              onChange={(o) => change({ mode: o.data })}
            />
          </PanelSectionRow>
          <PanelSectionRow>
            <DropdownItem
              label={t("panel.backend")}
              description={
                s.backend === "auto" && status?.backend
                  ? t("panel.backend_chose", { backend: backendLabel(status.backend) })
                  : undefined
              }
              rgOptions={backendOptions}
              selectedOption={s.backend}
              onChange={(o) => change({ backend: o.data })}
            />
          </PanelSectionRow>
          {matrix && (
            <PanelSectionRow>
              <DropdownItem
                label={t("panel.matrix")}
                description={matrixNote || undefined}
                rgOptions={[
                  { data: "auto", label: t("matrix.automatic") },
                  ...(matrix !== "none" ? [{ data: "on", label: t("matrix.on") }] : []),
                  { data: "off", label: t("matrix.off") },
                ]}
                selectedOption={s.matrix}
                onChange={(o) => change({ matrix: o.data })}
              />
            </PanelSectionRow>
          )}
          <PanelSectionRow>
            <DropdownItem
              label={t("panel.capture_memory")}
              description={t("panel.capture_memory_note")}
              rgOptions={[
                { data: "fast", label: t("capture.fast") },
                { data: "compatible", label: t("capture.compatible") },
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
                t("panel.ms_per_frame", { ms: status.generation_ms_p50.toFixed(1) })}
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
