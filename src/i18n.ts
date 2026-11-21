// The panel's text in Steam's own language.
//
// The language is chosen once, when the plugin loads: Steam's client language
// first, then the browser's, then English. A text a translation lacks shows in
// English.

import de from "./locales/de.json";
import en from "./locales/en.json";
import es from "./locales/es.json";
import ja from "./locales/ja.json";
import ko from "./locales/ko.json";
import ptBR from "./locales/pt-BR.json";
import ru from "./locales/ru.json";
import zhCN from "./locales/zh-CN.json";

export type Key = keyof typeof en;
type Table = Partial<Record<Key, string>>;

const TABLES: Record<string, Table> = {
  en,
  "zh-CN": zhCN,
  ja,
  ko,
  de,
  es,
  "pt-BR": ptBR,
  ru,
};

// Steam's language names (its API language codes) for the translations here.
const STEAM_LANGUAGES: Record<string, string> = {
  english: "en",
  schinese: "zh-CN",
  japanese: "ja",
  koreana: "ko",
  korean: "ko",
  german: "de",
  spanish: "es",
  latam: "es",
  brazilian: "pt-BR",
  // Portuguese has one translation: the Brazilian one, as in the desktop app.
  portuguese: "pt-BR",
  russian: "ru",
};

type SteamGlobals = {
  LocalizationManager?: { m_rgLocalesToUse?: string[] };
  SteamClient?: { Settings?: { GetCurrentLanguage?: () => Promise<string> } };
};

const globals = window as unknown as SteamGlobals;

/** A translation for one of Steam's language names; English for a language
 *  with none. */
function fromSteam(name: string): string {
  return STEAM_LANGUAGES[name.trim().toLowerCase()] ?? "en";
}

/** A translation for a browser language tag such as "pt-BR" or "zh-Hans-CN". */
function fromTag(tag: string): string {
  const parts = tag.toLowerCase().replace(/_/g, "-").split("-");
  switch (parts[0]) {
    case "zh":
      // Traditional Chinese has no translation here.
      return parts.some((p) => p === "hant" || p === "tw" || p === "hk" || p === "mo") ? "en" : "zh-CN";
    case "pt":
      return "pt-BR";
    case "ja":
    case "ko":
    case "de":
    case "es":
    case "ru":
      return parts[0];
    default:
      return "en";
  }
}

/** Steam's language, when the client says which it is right away. */
function steamLanguage(): string | null {
  try {
    const name = globals.LocalizationManager?.m_rgLocalesToUse?.[0];
    return typeof name === "string" && name ? fromSteam(name) : null;
  } catch {
    return null;
  }
}

function browserLanguage(): string {
  try {
    return typeof navigator !== "undefined" && navigator.language ? fromTag(navigator.language) : "en";
  } catch {
    return "en";
  }
}

const steam = steamLanguage();
let language = steam ?? browserLanguage();

// Without the localization manager, ask the Steam client; until it answers,
// the browser's language stands in.
if (steam == null) {
  try {
    globals.SteamClient?.Settings?.GetCurrentLanguage?.()
      .then((name) => {
        if (typeof name === "string" && name) language = fromSteam(name);
      })
      .catch(() => undefined);
  } catch {
    // No Steam client to ask: keep the browser's language.
  }
}

/** The text for `key` in the panel's language, each `{name}` replaced by
 *  `params[name]`. */
export function t(key: Key, params?: Record<string, string | number>): string {
  const text = TABLES[language]?.[key] ?? en[key] ?? key;
  if (!params) return text;
  return text.replace(/\{([A-Za-z0-9_]+)\}/g, (whole, name: string) =>
    name in params ? String(params[name]) : whole,
  );
}
