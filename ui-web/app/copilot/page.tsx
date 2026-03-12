"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import { Bar, BarChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { sendCopilotMessage, ChatTurn, CopilotResponse } from "@/lib/copilotApi";

interface UiMessage {
  role: "user" | "assistant";
  content: string;
  response?: CopilotResponse;
}

function getPlottableSeries(chart: CopilotResponse["chart"] | undefined | null): Array<{ label: string; value: number }> {
  if (!chart || !chart.datasets?.length) return [];
  const source = chart.datasets[0]?.data ?? [];
  return chart.labels
    .map((label, index) => ({ label: String(label), value: Number(source[index]) }))
    .filter((point) => Number.isFinite(point.value));
}

const QUICK_QUESTIONS = [
  "Summarize today's factory performance",
  "Which machine consumed the most power today?",
  "Show recent alerts today",
  "What is today's idle running cost?",
  "Which machine has the lowest efficiency today?",
];

const STORAGE_KEY = "factoryops_copilot_messages";

export default function CopilotPage() {
  const [messages, setMessages] = useState<UiMessage[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const endRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    if (!raw) return;
    try {
      const parsed = JSON.parse(raw) as UiMessage[];
      setMessages(parsed);
    } catch {
      sessionStorage.removeItem(STORAGE_KEY);
    }
  }, []);

  useEffect(() => {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(messages));
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const history: ChatTurn[] = useMemo(
    () =>
      messages.map((m) => ({
        role: m.role,
        content: m.content,
      })),
    [messages]
  );

  async function ask(question: string) {
    const trimmed = question.trim();
    if (!trimmed || loading) return;

    setError(null);
    setLoading(true);
    setMessages((prev) => [...prev, { role: "user", content: trimmed }]);

    try {
      const response = await sendCopilotMessage(trimmed, history);
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: response.answer,
          response,
        },
      ]);
    } catch {
      setError("Could not get answer. Please try again.");
    } finally {
      setLoading(false);
      setInput("");
    }
  }

  function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    void ask(input);
  }

  function newChat() {
    setMessages([]);
    setError(null);
    sessionStorage.removeItem(STORAGE_KEY);
  }

  return (
    <div className="min-h-screen bg-slate-50 p-6">
      <div className="mx-auto max-w-6xl">
        <div className="mb-4 flex items-center justify-between">
          <h1 className="text-2xl font-semibold text-slate-900">Factory Copilot</h1>
          <button
            onClick={newChat}
            className="rounded-md border border-slate-300 bg-white px-4 py-2 text-sm text-slate-700 hover:bg-slate-100"
          >
            New Chat
          </button>
        </div>

        <div className="rounded-xl border border-slate-200 bg-white p-4">
          <div className="mb-4 h-[62vh] overflow-y-auto rounded-lg border border-slate-100 bg-slate-50 p-4">
            {messages.length === 0 && (
              <div className="rounded-lg border border-blue-100 bg-blue-50 p-4 text-sm text-slate-700">
                Hello! Ask me anything about machines, energy, alerts, waste, and trends.
              </div>
            )}

            <div className="space-y-4">
              {messages.map((msg, idx) => (
                <div key={idx} className={msg.role === "user" ? "text-right" : "text-left"}>
                  <div
                    className={
                      msg.role === "user"
                        ? "ml-auto inline-block max-w-[80%] rounded-lg bg-blue-600 px-4 py-2 text-white"
                        : "inline-block max-w-[90%] rounded-lg border border-slate-200 bg-white px-4 py-3 text-slate-800"
                    }
                  >
                    <p className="text-sm">{msg.content}</p>
                  </div>

                  {msg.role === "assistant" && msg.response && (
                    <div className="mt-3 space-y-3 rounded-lg border border-slate-200 bg-white p-4">
                      <div className="rounded-md bg-slate-50 p-3 text-sm text-slate-700">
                        <div className="mb-1 font-medium text-slate-900">Reasoning</div>
                        {msg.response.reasoning_sections ? (
                          <div className="space-y-2">
                            <div>
                              <div className="font-medium text-slate-900">What happened</div>
                              <div>{msg.response.reasoning_sections.what_happened}</div>
                            </div>
                            <div>
                              <div className="font-medium text-slate-900">Why it matters</div>
                              <div>{msg.response.reasoning_sections.why_it_matters}</div>
                            </div>
                            <div>
                              <div className="font-medium text-slate-900">How calculated</div>
                              <div>{msg.response.reasoning_sections.how_calculated}</div>
                            </div>
                          </div>
                        ) : (
                          <div className="whitespace-pre-line">{msg.response.reasoning}</div>
                        )}
                      </div>

                      {msg.response.data_table && (
                        <div className="overflow-x-auto">
                          <table className="w-full border-collapse text-left text-sm">
                            <thead>
                              <tr className="bg-slate-100">
                                {msg.response.data_table.headers.map((h) => (
                                  <th key={h} className="border border-slate-200 px-3 py-2 font-medium text-slate-700">
                                    {h}
                                  </th>
                                ))}
                              </tr>
                            </thead>
                            <tbody>
                              {msg.response.data_table.rows.map((row, i) => (
                                <tr key={i}>
                                  {row.map((cell, j) => (
                                    <td key={`${i}-${j}`} className="border border-slate-200 px-3 py-2 text-slate-700">
                                      {cell as string | number | null}
                                    </td>
                                  ))}
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      )}

                      {msg.response.chart && (
                        <div className="rounded-md border border-slate-200 p-3">
                          <div className="mb-2 text-sm font-medium text-slate-800">{msg.response.chart.title}</div>
                          {(() => {
                            const series = getPlottableSeries(msg.response.chart);
                            if (series.length === 0) {
                              return <div className="text-sm text-slate-600">No plottable numeric data for chart.</div>;
                            }
                            return (
                              <div className="h-[280px] w-full">
                                <ResponsiveContainer width="100%" height="100%">
                                  {msg.response.chart.type === "bar" ? (
                                    <BarChart data={series}>
                                      <CartesianGrid strokeDasharray="3 3" />
                                      <XAxis dataKey="label" />
                                      <YAxis />
                                      <Tooltip />
                                      <Bar dataKey="value" fill="#2563eb" />
                                    </BarChart>
                                  ) : (
                                    <LineChart data={series}>
                                      <CartesianGrid strokeDasharray="3 3" />
                                      <XAxis dataKey="label" />
                                      <YAxis />
                                      <Tooltip />
                                      <Line type="monotone" dataKey="value" stroke="#2563eb" dot={false} />
                                    </LineChart>
                                  )}
                                </ResponsiveContainer>
                              </div>
                            );
                          })()}
                        </div>
                      )}

                      {msg.response.page_links && msg.response.page_links.length > 0 && (
                        <div className="flex flex-wrap gap-2">
                          {msg.response.page_links.map((pl) => (
                            <Link key={pl.route + pl.label} href={pl.route} className="rounded-md border border-blue-200 bg-blue-50 px-3 py-1.5 text-sm text-blue-700">
                              {pl.label}
                            </Link>
                          ))}
                        </div>
                      )}

                      {msg.response.follow_up_suggestions.length > 0 && (
                        <div className="flex flex-wrap gap-2">
                          {msg.response.follow_up_suggestions.map((q) => (
                            <button
                              key={q}
                              onClick={() => void ask(q)}
                              className="rounded-full border border-slate-300 bg-white px-3 py-1 text-xs text-slate-700 hover:bg-slate-100"
                            >
                              {q}
                            </button>
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              ))}
            </div>

            {loading && (
              <div className="mt-4 inline-block rounded-md border border-slate-200 bg-white px-3 py-2 text-sm text-slate-600">
                Thinking...
              </div>
            )}
            <div ref={endRef} />
          </div>

          {error && <div className="mb-3 rounded-md bg-amber-50 px-3 py-2 text-sm text-amber-700">{error}</div>}

          <form onSubmit={onSubmit} className="mb-3 flex gap-2">
            <input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="Ask about your factory..."
              className="flex-1 rounded-md border border-slate-300 px-3 py-2 text-sm"
            />
            <button
              type="submit"
              disabled={loading || !input.trim()}
              className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
            >
              Send
            </button>
          </form>

          <div className="flex flex-wrap gap-2">
            {QUICK_QUESTIONS.map((q) => (
              <button
                key={q}
                onClick={() => void ask(q)}
                className="rounded-full border border-slate-300 bg-white px-3 py-1 text-xs text-slate-700 hover:bg-slate-100"
              >
                {q}
              </button>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
