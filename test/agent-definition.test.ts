import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { parseAgentDefinition } from "../lib/zoowork/agent-definition";

const def = (frontmatter: string, body = "Be helpful.") => `---\n${frontmatter}\n---\n\n${body}\n`;

// agent/agent.md is meant to be edited, so this checks its structure only,
// never the example's name, tools or wording.
test("agent/agent.md parses into a complete Agent definition", () => {
  const { resource } = parseAgentDefinition(readFileSync("agent/agent.md", "utf8"));
  assert.ok(resource.name.trim(), "name is set");
  assert.equal(resource.persona?.docs.length, 1);
  assert.equal(resource.persona?.docs[0]?.name, "AGENTS.md");
  assert.ok(resource.persona?.docs[0]?.content.trim(), "the instructions are not empty");
  assert.equal(typeof resource.include_global_skills, "boolean");
  const policy = resource.tool_policy as { allow?: unknown; deny?: unknown };
  assert.ok(Array.isArray(policy.allow) || Array.isArray(policy.deny), "tools become an allow or deny list");
});

test("permissions become tool_policy.permissions next to the allow list", () => {
  const { resource } = parseAgentDefinition(
    def(["name: A", "tools: [web_search, web_fetch, mcp__crm__*]", "permissions:", "  web_fetch: always_ask", "  mcp__crm__delete: always_ask", "  web_search: always_allow"].join("\n")),
  );
  assert.deepEqual(resource.tool_policy, {
    allow: ["web_search", "web_fetch", "mcp__crm__*"],
    permissions: { web_fetch: "always_ask", mcp__crm__delete: "always_ask", web_search: "always_allow" },
  });
});

test("without permissions, or with an empty block, the policy has none", () => {
  for (const extra of ["", "\npermissions:", "\npermissions: {}"]) {
    const { resource } = parseAgentDefinition(def(`name: A\ntools: [web_fetch]${extra}`));
    assert.deepEqual(resource.tool_policy, { allow: ["web_fetch"] });
  }
});

test("maps model, thinking, skills and timezone", () => {
  const { resource } = parseAgentDefinition(
    def(
      [
        "name: Helper",
        "model: provider/model-x",
        "thinking: low",
        "max_tokens: 2048",
        "tools: [web_search, web_search]",
        "global_skills: true",
        "skills:",
        "  - pdf-reader",
        "  - { skill_id: skl_1, version: 3 }",
        "timezone: Asia/Shanghai",
      ].join("\n"),
    ),
  );
  assert.deepEqual(resource.model, { primary: "provider/model-x", thinking: "low", max_tokens: 2048 });
  assert.deepEqual(resource.tool_policy, { allow: ["web_search"] });
  assert.equal(resource.include_global_skills, true);
  assert.deepEqual(resource.skills, [{ name: "pdf-reader" }, { skill_id: "skl_1", version: 3 }]);
  assert.equal(resource.userTimezone, "Asia/Shanghai");
});

test("an empty tool list denies every tool", () => {
  const { resource } = parseAgentDefinition(def("name: Text only\ntools: []"));
  assert.deepEqual(resource.tool_policy, { deny: ["*"] });
});

test("rejects mistakes instead of ignoring them", () => {
  const bad: [string, RegExp][] = [
    [def("name: A\ntools: []\ntool: [x]"), /unknown key "tool"/],
    [def("tools: []"), /name/],
    [def("name: A"), /tools must be a list/],
    [def("name: A\ntools: []", ""), /instructions .* empty/],
    [def("name: A\ntools: []\nthinking: low"), /model is required/],
    [def("name: A\ntools: []\nmodel: m\nthinking: extreme"), /thinking must be one of/],
    [def("name: A\ntools: [\"rm -rf\"]"), /exact tool name/],
    ["name: A\n", /must start with a --- frontmatter block/],
    ["---\nname: A\n", /not closed/],
    [def("name: [unclosed"), /not valid YAML/],
    [def("name: A\ntools: [web_fetch]\npermissions: [web_fetch]"), /permissions must be a mapping/],
    [def("name: A\ntools: [web_fetch]\npermissions:\n  web_fetch: ask"), /permissions.web_fetch must be one of always_ask, always_allow/],
    [def("name: A\ntools: [web_fetch]\npermissions:\n  web_fetch: auto"), /must be one of/],
    [def("name: A\ntools: [web_fetch]\npermissions:\n  web_search: always_ask"), /permissions.web_search names a tool that is not in tools/],
    [def("name: A\ntools: []\npermissions:\n  web_fetch: always_ask"), /not in tools/],
    [def("name: A\ntools: [\"mcp__crm__*\"]\npermissions:\n  \"mcp__crm__*\": always_ask"), /exact tool name/],
    [def("name: A\ntools: []\ntool_policy: {}"), /unknown key "tool_policy"/],
  ];
  for (const [source, message] of bad) assert.throws(() => parseAgentDefinition(source), message);
});
