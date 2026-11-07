// The shape of app.config.ts, with a description of each field, and the
// values the page derives from it (brand colours, the page icon).

export type Suggestion = {
  /** The chip's main text. */
  title: string;
  /** Optional second line on the chip. */
  label?: string;
  /** The message sent when the chip is clicked. */
  prompt: string;
};

export type AppConfig = {
  /** Product name: page title, sidebar and welcome heading. */
  name: string;
  /** One sentence under the name. */
  description: string;
  /** Heading on an empty conversation. */
  greeting: string;
  /** Composer placeholder. */
  placeholder: string;
  /** Starter prompts on an empty conversation. Three or four work best. */
  suggestions: Suggestion[];
  /**
   * Brand color, any CSS color: buttons, highlights and links. In dark mode
   * links use a lighter mix of it, so a dark brand color stays readable.
   */
  accentColor: string;
  /** Text color on top of accentColor. */
  accentForeground: string;
  /**
   * Optional logo: an image in `public/`, referenced from the site root, such
   * as "/logo.svg" for `public/logo.svg`. Used as the sidebar mark and the
   * browser tab icon. Without it, both show the first letter of `name` on
   * accentColor.
   */
  icon?: string;
  /** Small print under the conversation list. */
  footnote: string;
};

/** The CSS variables app.config.ts sets on the page (see app/globals.css). */
export function brandVariables(config: Pick<AppConfig, "accentColor" | "accentForeground">): Record<string, string> {
  return {
    "--primary": config.accentColor,
    "--primary-foreground": config.accentForeground,
    // Links and other brand highlights follow the accent too.
    "--zw-brand": config.accentColor,
  };
}

const xml = (text: string) =>
  text.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&apos;" })[c]!);

/** The first letter of the product name, for the default mark. */
export const initialOf = (name: string) => [...name.trim()][0]?.toUpperCase() ?? "?";

/** A square letter mark in the brand colors, as SVG. */
export function letterMarkSvg(config: Pick<AppConfig, "name" | "accentColor" | "accentForeground">): string {
  return [
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">',
    `<rect width="64" height="64" rx="14" fill="${xml(config.accentColor)}"/>`,
    `<text x="32" y="44" text-anchor="middle" font-family="system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif" font-size="34" font-weight="600" fill="${xml(config.accentForeground)}">${xml(initialOf(config.name))}</text>`,
    "</svg>",
  ].join("");
}

/** The browser tab icon: the configured image, or the letter mark as a data URL. */
export function pageIconUrl(config: Pick<AppConfig, "name" | "accentColor" | "accentForeground" | "icon">): string {
  if (config.icon) return config.icon;
  return `data:image/svg+xml,${encodeURIComponent(letterMarkSvg(config))}`;
}
