"use client";

import { useEffect, useRef, useState } from "react";
import { ApiError, askAssistant, sendAssistantFeedback } from "../lib/api";

const MAX_MESSAGE_LENGTH = 500;
const HISTORY_TURNS = 10;
const HISTORY_CONTENT_LIMIT = 2000;

const CUSTOMER_EXAMPLES = [
  "כמה הוצאתי החודש?",
  "מי הספק הכי זול לעגבניות?",
  "מה ההזמנה האחרונה שלי?",
];
const ADMIN_EXAMPLES = [
  "איזה לקוח הוציא הכי הרבה החודש?",
  "אילו הזמנות בוטלו השבוע?",
  "כמה הוצאו כל הלקוחות על עגבניות החודש?",
];

type Rating = "up" | "down";

interface ChatMessage {
  id: number;
  role: "user" | "assistant";
  content: string;
  isError?: boolean;
  callId?: number;
  rating?: Rating;
}

function errorText(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 429) return "הגעת למגבלת השאלות לשעה הקרובה. נסה שוב מאוחר יותר.";
    if (err.status >= 400 && err.status < 500) return err.message;
  }
  return "לא הצלחתי לקבל תשובה כרגע. נסה שוב.";
}

export function AssistantChat({ isAdmin }: { isAdmin: boolean }) {
  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const nextId = useRef(1);
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "end" });
  }, [messages, loading, open]);

  useEffect(() => {
    if (open) inputRef.current?.focus();
  }, [open]);

  async function send(text: string) {
    const question = text.trim();
    if (!question || loading) return;

    const history = messages
      .filter((m) => !m.isError)
      .slice(-HISTORY_TURNS)
      .map((m) => ({ role: m.role, content: m.content.slice(0, HISTORY_CONTENT_LIMIT) }));

    setMessages((prev) => [...prev, { id: nextId.current++, role: "user", content: question }]);
    setInput("");
    setLoading(true);
    try {
      const res = await askAssistant(question, history);
      setMessages((prev) => [
        ...prev,
        { id: nextId.current++, role: "assistant", content: res.answer, callId: res.call_id },
      ]);
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        { id: nextId.current++, role: "assistant", content: errorText(err), isError: true },
      ]);
    } finally {
      setLoading(false);
    }
  }

  function rate(message: ChatMessage, rating: Rating) {
    if (message.callId === undefined || message.rating) return;
    setMessages((prev) => prev.map((m) => (m.id === message.id ? { ...m, rating } : m)));
    sendAssistantFeedback(message.callId, rating).catch(() => {});
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      send(input);
    }
  }

  const examples = isAdmin ? ADMIN_EXAMPLES : CUSTOMER_EXAMPLES;

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-label={open ? "סגירת העוזר" : "פתיחת העוזר"}
        className="fixed bottom-16 left-4 z-30 h-14 w-14 rounded-full bg-green-700 text-white shadow-lg hover:bg-green-800 flex items-center justify-center text-2xl"
      >
        {open ? "×" : "💬"}
      </button>

      {open && (
        <div
          role="dialog"
          aria-label="עוזר נתונים"
          dir="rtl"
          className="fixed bottom-36 left-4 z-30 w-[calc(100vw-2rem)] max-w-sm h-[70vh] max-h-[560px] bg-white rounded-2xl shadow-xl flex flex-col overflow-hidden"
        >
          <div className="bg-green-900 text-white px-4 py-3 flex items-center justify-between">
            <span className="font-semibold">עוזר נתונים</span>
            <div className="flex items-center gap-3 text-sm">
              {messages.length > 0 && (
                <button type="button" onClick={() => setMessages([])} className="opacity-80 hover:opacity-100">
                  שיחה חדשה
                </button>
              )}
              <button
                type="button"
                onClick={() => setOpen(false)}
                aria-label="סגירה"
                className="text-xl leading-none opacity-80 hover:opacity-100"
              >
                &times;
              </button>
            </div>
          </div>

          <div className="flex-1 overflow-y-auto p-3 flex flex-col gap-2">
            {messages.length === 0 && (
              <div className="text-sm text-gray-600 space-y-3">
                <p>שאל אותי על ההזמנות, ההוצאות, המוצרים והספקים שלך. אני עונה מתוך הנתונים האמיתיים במערכת.</p>
                <div className="flex flex-col gap-2">
                  {examples.map((example) => (
                    <button
                      key={example}
                      type="button"
                      onClick={() => send(example)}
                      className="text-right border border-green-700 text-green-800 rounded-full px-3 py-1.5 hover:bg-green-50"
                    >
                      {example}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {messages.map((m) => (
              <div key={m.id} className={`flex flex-col ${m.role === "user" ? "items-start" : "items-end"}`}>
                <div
                  className={`max-w-[85%] rounded-2xl px-3 py-2 text-sm whitespace-pre-wrap break-words ${
                    m.role === "user"
                      ? "bg-green-700 text-white"
                      : m.isError
                        ? "bg-red-50 text-red-600"
                        : "bg-gray-100 text-gray-900"
                  }`}
                >
                  {m.content}
                </div>
                {m.role === "assistant" && m.callId !== undefined && (
                  <div className="mt-1 flex items-center gap-1 text-xs text-gray-400">
                    {m.rating ? (
                      <span>תודה</span>
                    ) : (
                      <>
                        <button type="button" onClick={() => rate(m, "up")} aria-label="תשובה טובה" className="hover:text-green-700">
                          👍
                        </button>
                        <button type="button" onClick={() => rate(m, "down")} aria-label="תשובה לא טובה" className="hover:text-red-600">
                          👎
                        </button>
                      </>
                    )}
                  </div>
                )}
              </div>
            ))}

            {loading && (
              <div className="flex flex-col items-end">
                <div className="rounded-2xl px-3 py-2 text-sm bg-gray-100 text-gray-500">חושב...</div>
              </div>
            )}
            <div ref={bottomRef} />
          </div>

          <form
            onSubmit={(e) => {
              e.preventDefault();
              send(input);
            }}
            className="border-t border-gray-200 p-2 flex items-end gap-2"
          >
            <textarea
              ref={inputRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={onKeyDown}
              maxLength={MAX_MESSAGE_LENGTH}
              rows={1}
              placeholder="כתוב שאלה..."
              className="flex-1 resize-none rounded-xl border border-gray-300 px-3 py-2 text-sm max-h-28 focus:outline-none focus:border-green-700"
            />
            <button
              type="submit"
              disabled={loading || !input.trim()}
              className="rounded-full bg-green-700 text-white text-sm px-4 py-2 hover:bg-green-800 disabled:opacity-50"
            >
              שלח
            </button>
          </form>
        </div>
      )}
    </>
  );
}
