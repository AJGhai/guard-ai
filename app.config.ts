// What the chat page says and looks like. Change these values to rebrand the
// app; no component code needs to change. This file is sent to the browser,
// so never put keys or private data here. The Agent itself is defined in
// agent/agent.md.

import type { AppConfig } from "@/lib/app-config";

export const appConfig: AppConfig = {
  name: "ZooWork Agent Starter",
  description: "A minimal chat app for an Agent you define in agent/agent.md.",
  greeting: "How can I help you today?",
  placeholder: "Send a message…",
  suggestions: [
    { title: "What can you do?", label: "a quick overview", prompt: "What can you help me with? Give me a short overview." },
    { title: "Search the web", label: "with sources", prompt: "What are the main differences between HTTP/2 and HTTP/3? Cite your sources." },
    { title: "Explain a concept", label: "in plain words", prompt: "Explain what an idempotency key is, with a short example." },
  ],
  accentColor: "#5755c6",
  accentForeground: "#ffffff",
  // Optional logo from public/, e.g. "/logo.svg". Without it: the first letter of name.
  // icon: "/logo.svg",
  footnote: "Conversations are stored as ZooWork Sessions. Replies can be wrong; check important facts.",
};
