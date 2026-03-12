import { COPILOT_SERVICE_BASE } from "./api";

export interface ChatTurn {
  role: "user" | "assistant";
  content: string;
}

export interface CopilotResponse {
  answer: string;
  reasoning: string;
  data_table?: {
    headers: string[];
    rows: Array<Array<string | number | null>>;
  } | null;
  chart?: {
    type: "bar" | "line" | "pie";
    title: string;
    labels: string[];
    datasets: Array<{ label: string; data: number[] }>;
  } | null;
  page_links?: Array<{ label: string; route: string }> | null;
  follow_up_suggestions: string[];
  error_code?: string | null;
}

export async function sendCopilotMessage(
  message: string,
  history: ChatTurn[]
): Promise<CopilotResponse> {
  const res = await fetch(`${COPILOT_SERVICE_BASE}/api/v1/copilot/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      message,
      conversation_history: history.slice(-5).map((h) => ({
        role: h.role,
        content: h.content,
      })),
    }),
  });

  if (!res.ok) {
    throw new Error(`HTTP ${res.status}`);
  }

  return res.json();
}
