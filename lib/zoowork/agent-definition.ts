import { readFileSync } from "node:fs";
import { join } from "node:path";
import type { AgentResource, ThinkingLevel } from "@zoowork-ai/sdk";
import { parse } from "yaml";
import { StarterError } from "./errors";

/**
 * The Agent is defined by one file, `agent/agent.md`: YAML frontmatter for the
 * configuration, and the Markdown body as the instructions (sent as the
 * Agent's `AGENTS.md` persona document). `npm run setup` sends it; editing the
 * file and running setup again updates the same Agent.
 */
export const AGENT_FILE = join("agent", "agent.md");

const THINKING: readonly ThinkingLevel[] = ["off", "low", "medium", "high", "max"];
const KEYS = new Set(["name", "model", "thinking", "max_tokens", "tools", "permissions", "global_skills", "skills", "timezone"]);
const TOOL_NAME = /^[A-Za-z0-9_.:-]+\*?$/;
/** Permission keys are exact tool names: the Platform does not expand wildcards in them. */
const EXACT_TOOL_NAME = /^[A-Za-z0-9_.:-]+$/;
/** The values `tool_policy.permissions` accepts (docs: Build → Permission policies). */
export const PERMISSIONS = ["always_ask", "always_allow"] as const;
export type Permission = (typeof PERMISSIONS)[number];

/** Is `tool` exposed by a `tools` entry? An entry is an exact name, `*`, or one trailing `prefix*`. */
function listed(tool: string, tools: readonly string[]): boolean {
  return tools.some((entry) => (entry.endsWith("*") ? tool.startsWith(entry.slice(0, -1)) : entry === tool));
}

export interface AgentDefinition {
  /** The exact resource sections setup sends, without the starter's ownership labels. */
  resource: AgentResource;
}

function fail(detail: string): never {
  throw new StarterError(`invalid_agent_definition: ${detail}`);
}

function splitFrontmatter(source: string): { frontmatter: string; body: string } {
  const text = source.replace(/^﻿/, "").replace(/\r\n/g, "\n");
  if (!text.startsWith("---\n")) fail("the file must start with a --- frontmatter block");
  const end = text.indexOf("\n---", 4);
  if (end === -1) fail("the frontmatter block is not closed with ---");
  const after = text.slice(end + 4);
  if (after && !after.startsWith("\n")) fail("the closing --- must be on its own line");
  return { frontmatter: text.slice(4, end), body: after.trim() };
}

/** Parse and validate an agent definition. Unknown keys are rejected so a typo is not silently ignored. */
export function parseAgentDefinition(source: string): AgentDefinition {
  const { frontmatter, body } = splitFrontmatter(source);
  let data: unknown;
  try {
    data = parse(frontmatter);
  } catch {
    fail("the frontmatter is not valid YAML");
  }
  if (!data || typeof data !== "object" || Array.isArray(data)) fail("the frontmatter must be a YAML mapping");
  const fm = data as Record<string, unknown>;
  for (const key of Object.keys(fm)) if (!KEYS.has(key)) fail(`unknown key "${key}"`);

  const name = fm.name;
  if (typeof name !== "string" || !name.trim() || name.length > 100) fail("name must be a non-empty string of at most 100 characters");
  if (!body) fail("the instructions below the frontmatter are empty");

  const resource: AgentResource = {
    name: name.trim(),
    persona: { docs: [{ name: "AGENTS.md", content: body }] },
    include_global_skills: false,
  };

  if (fm.model !== undefined || fm.thinking !== undefined || fm.max_tokens !== undefined) {
    if (typeof fm.model !== "string" || !fm.model.trim()) fail("model is required when thinking or max_tokens is set; run npm run models");
    const model: NonNullable<AgentResource["model"]> = { primary: fm.model.trim() };
    if (fm.thinking !== undefined) {
      if (!THINKING.includes(fm.thinking as ThinkingLevel)) fail(`thinking must be one of ${THINKING.join(", ")}`);
      model.thinking = fm.thinking as ThinkingLevel;
    }
    if (fm.max_tokens !== undefined) {
      if (!Number.isInteger(fm.max_tokens) || (fm.max_tokens as number) <= 0) fail("max_tokens must be a positive integer");
      model.max_tokens = fm.max_tokens as number;
    }
    resource.model = model;
  }

  if (!Array.isArray(fm.tools)) fail("tools must be a list of tool names; use [] for no tools");
  const tools = fm.tools as unknown[];
  if (!tools.every((t): t is string => typeof t === "string" && TOOL_NAME.test(t))) fail("each tool must be an exact tool name");
  // An allow list restricts the tool surface to these names. An empty list
  // denies every tool, so the Agent can only reply in text.
  const policy: Record<string, unknown> = tools.length ? { allow: [...new Set(tools)] } : { deny: ["*"] };

  // Which calls wait for Allow / Deny in the chat. Tools without an entry run
  // without asking. The whole policy is written on every setup, so this file
  // is the only place approvals are kept.
  if (fm.permissions !== undefined && fm.permissions !== null) {
    if (typeof fm.permissions !== "object" || Array.isArray(fm.permissions)) {
      fail("permissions must be a mapping of tool name to always_ask or always_allow");
    }
    const permissions: Record<string, Permission> = {};
    for (const [tool, value] of Object.entries(fm.permissions as Record<string, unknown>)) {
      if (!EXACT_TOOL_NAME.test(tool)) fail(`permissions: "${tool}" must be an exact tool name (no wildcards)`);
      if (!PERMISSIONS.includes(value as Permission)) fail(`permissions.${tool} must be one of ${PERMISSIONS.join(", ")}`);
      if (!listed(tool, tools)) fail(`permissions.${tool} names a tool that is not in tools`);
      permissions[tool] = value as Permission;
    }
    if (Object.keys(permissions).length) policy.permissions = permissions;
  }
  resource.tool_policy = policy;

  if (fm.global_skills !== undefined) {
    if (typeof fm.global_skills !== "boolean") fail("global_skills must be true or false");
    resource.include_global_skills = fm.global_skills;
  }

  if (fm.skills !== undefined) {
    if (!Array.isArray(fm.skills)) fail("skills must be a list");
    resource.skills = fm.skills.map((entry, i): NonNullable<AgentResource["skills"]>[number] => {
      const s = typeof entry === "string" ? { name: entry } : entry;
      if (!s || typeof s !== "object" || Array.isArray(s)) fail(`skills[${i}] must be a name or a mapping`);
      const { name: skillName, skill_id, version, ...rest } = s as Record<string, unknown>;
      if (Object.keys(rest).length) fail(`skills[${i}] has unknown keys`);
      if (version !== undefined && version !== "latest" && !(Number.isInteger(version) && (version as number) > 0)) {
        fail(`skills[${i}].version must be a positive integer or "latest"`);
      }
      const v = version === undefined ? {} : { version: version as number | "latest" };
      if (typeof skill_id === "string" && skill_id) return { skill_id, ...(typeof skillName === "string" ? { name: skillName } : {}), ...v };
      if (typeof skillName === "string" && skillName) return { name: skillName, ...v };
      fail(`skills[${i}] needs a name or skill_id`);
    });
  }

  if (fm.timezone !== undefined) {
    if (typeof fm.timezone !== "string" || !fm.timezone.includes("/")) fail("timezone must be a named IANA timezone such as Asia/Shanghai");
    resource.userTimezone = fm.timezone;
  }

  return { resource };
}

export function loadAgentDefinition(cwd = process.cwd()): AgentDefinition {
  let source: string;
  try {
    source = readFileSync(join(cwd, AGENT_FILE), "utf8");
  } catch {
    throw new StarterError("agent_definition_missing");
  }
  return parseAgentDefinition(source);
}
