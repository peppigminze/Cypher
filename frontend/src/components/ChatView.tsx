import { useCallback, useEffect, useReducer, useRef, useState, type FormEvent } from "react";
import { ApprovalCard } from "./ApprovalCard";
import { chatReducer, initialChatState } from "../hooks/chatState";
import { ChatHttpError, fetchChatHistory, streamApproval, streamChat, type ChatEvent } from "../services/chatStream";

const CONVERSATION_KEY = "cypher.chat.conversation.v1";

function storedConversation(): string | null {
  try {
    return localStorage.getItem(CONVERSATION_KEY);
  } catch {
    return null;
  }
}

function storeConversation(id: string | null): void {
  try {
    if (id) localStorage.setItem(CONVERSATION_KEY, id);
    else localStorage.removeItem(CONVERSATION_KEY);
  } catch {
    /* private mode */
  }
}

/**
 * Text chat with Cypher - the same agent as the voice view (tools,
 * approvals, memory), answers stream in word by word (/api/chat, SSE).
 * The conversation continues across reloads (id in localStorage, history
 * from the backend) until "Neues Gespräch".
 */
export function ChatView() {
  const [state, dispatch] = useReducer(chatReducer, initialChatState);
  const [draft, setDraft] = useState("");
  const abortRef = useRef<AbortController | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const id = storedConversation();
    if (!id) return;
    void fetchChatHistory(id).then((history) => dispatch({ type: "load", conversationId: id, history }));
    return () => abortRef.current?.abort();
  }, []);

  useEffect(() => {
    storeConversation(state.conversationId);
  }, [state.conversationId]);

  useEffect(() => {
    scrollRef.current?.scrollTo?.({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [state.turns]);

  const consume = useCallback(async (open: (signal: AbortSignal) => Promise<AsyncGenerator<ChatEvent>>) => {
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      for await (const event of await open(controller.signal)) {
        dispatch({ type: "event", event, now: Date.now() });
      }
      dispatch({ type: "finished" });
    } catch (err) {
      if (controller.signal.aborted) {
        dispatch({ type: "finished" });
      } else {
        const message = err instanceof ChatHttpError ? err.message : "Cypher ist gerade nicht erreichbar. Läuft das Backend?";
        dispatch({ type: "failed", message });
      }
    }
  }, []);

  const send = (event: FormEvent) => {
    event.preventDefault();
    const text = draft.trim();
    if (!text || state.busy) return;
    setDraft("");
    dispatch({ type: "send", text });
    void consume((signal) => streamChat(text, state.conversationId, signal));
  };

  const decide = (approvalId: string, approved: boolean) => {
    dispatch({ type: "resume" });
    void consume((signal) => streamApproval(approvalId, approved, signal));
  };

  const stop = () => abortRef.current?.abort();

  const newConversation = () => {
    stop();
    dispatch({ type: "reset" });
  };

  return (
    <div className="chat">
      <div className="chat__toolbar">
        <button type="button" className="icon-btn" onClick={newConversation} disabled={state.turns.length === 0}>
          Neues Gespräch
        </button>
      </div>

      <div className="chat__messages" ref={scrollRef} aria-live="polite">
        {state.turns.length === 0 && <div className="empty-state">Frag Cypher etwas - oder gib einen Befehl.</div>}
        {state.turns.map((t) => (
          <div key={t.id} className={`chat-msg chat-msg--${t.role === "user" ? "user" : "cypher"}`}>
            {t.tools.length > 0 && (
              <ul className="chat-msg__tools" aria-label="Werkzeuge">
                {t.tools.map((tool, i) => (
                  <li key={i} className={tool.running ? "is-running" : tool.success === false ? "is-error" : "is-done"}>
                    {tool.summary || tool.name}
                    {tool.running ? " …" : tool.success === false ? " - hat nicht geklappt" : " ✓"}
                  </li>
                ))}
              </ul>
            )}
            {(t.text || t.streaming) && (
              <div className="chat-msg__bubble">
                {t.text}
                {t.streaming && <span className="chat-msg__cursor" aria-hidden="true">▍</span>}
              </div>
            )}
            {t.approval && <ApprovalCard card={t.approval} onDecide={(approved) => decide(t.approval!.approvalId, approved)} />}
            {t.error && (
              <div className="chat-msg__error" role="alert">
                {t.error.message}
                {t.error.hint && <span className="chat-msg__hint">{t.error.hint}</span>}
              </div>
            )}
          </div>
        ))}
      </div>

      <form className="chat__composer" onSubmit={send}>
        <input
          className="text-input"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="Nachricht an Cypher …"
          aria-label="Nachricht an Cypher"
          maxLength={4000}
        />
        {state.busy ? (
          <button type="button" className="btn-secondary" onClick={stop}>
            Stopp
          </button>
        ) : (
          <button type="submit" className="btn-primary" disabled={!draft.trim()}>
            Senden
          </button>
        )}
      </form>
    </div>
  );
}
