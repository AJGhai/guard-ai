// User-facing text for the error codes the server returns. Codes are stable
// and safe to show; see the README troubleshooting table.

export const NOTICES: Record<string, string> = {
  agent_changed_restart_dev: "The recorded Agent changed since this server started (cleanup or a new setup). Restart npm run dev.",
  run_setup_first: "The Agent is not set up yet. Run npm run setup, then reload.",
  platform_project_key_required: "ZOOWORK_API_KEY is missing or is not a Platform Project key (zwp_live_). Check .env and restart.",
  session_archived: "This conversation is archived. Start a new one to continue.",
  session_not_found: "This conversation no longer exists.",
  message_not_accepted: "The Agent did not accept the message. Wait for the current reply to finish, then try again.",
  invalid_message: "The message is empty or too long. Edit it and send again.",
  invalid_idempotency_key: "The request could not be retried safely. Send the message again.",
  approval_not_pending: "This approval was already decided or has expired.",
  decision_not_allowed: "That decision is not allowed for this tool call.",
  invalid_decision: "That decision is not recognized. Choose Allow or Deny.",
  invalid_thread_id: "This conversation link is not valid. Start a new conversation.",
  local_only_add_authentication_first: "This starter only answers on localhost. Add authentication before exposing it.",
  http_401: "The Project key was rejected. Check ZOOWORK_API_KEY and restart.",
  http_402: "The Organization has no available credits. Check Billing in Platform Console.",
  http_403: "The Project key cannot access this Agent. Check the key and its Project.",
  http_409: "The Agent is busy or the conversation changed. Try again in a moment.",
  http_429: "Rate limited. Wait a moment, then try again.",
};

/**
 * The app server could not get an answer from ZooWork Platform. Safe calls
 * were already retried once, so this is usually a slow or broken connection
 * between this computer and the Platform, not a problem in the app.
 */
const UNREACHABLE: Record<string, string> = {
  request_failed: "the app could not reach ZooWork Platform. Check your internet connection and try again.",
  timeout_or_cancelled: "ZooWork Platform did not answer in time. Check your internet connection and try again.",
  http_502: "ZooWork Platform is temporarily unavailable. Try again in a moment.",
  http_503: "ZooWork Platform is temporarily unavailable. Try again in a moment.",
  http_504: "ZooWork Platform did not answer in time. Try again in a moment.",
};

/** Connection-class codes: worth a second look before reporting them, and worth retrying. */
export const isTransientCode = (code: string | undefined) => !!code && (code in UNREACHABLE || code === "network_error");

export function describe(code: string | undefined, fallback: string): string {
  if (code && NOTICES[code]) return NOTICES[code];
  if (code && UNREACHABLE[code]) {
    const reason = UNREACHABLE[code];
    return `${fallback}: ${reason} (${code})`;
  }
  return code ? `${fallback} (${code})` : fallback;
}
