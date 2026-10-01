/* A reader projection of recorded API messages. No fields are added to records. */
import { useEffect, useState } from "react";
import type { Ev, FullEvent } from "@/shared/types";
import { containsElision, splitContent } from "@/lib/content";
import { historyStart, messageEntries, type ChatMessage, type MessageEntry } from "@/lib/llm-history";
import { highlightJson } from "@/lib/markdown";
import { MdView } from "@/components/bits";

function Reasoning({ text, summarized }: { text: string; summarized: boolean }) {
  return <details data-reasoning="" className="rounded border bg-muted/20 px-2 py-1">
    <summary className="cursor-pointer text-xs text-muted-foreground">
      reasoning{summarized ? " (provider summary)" : ""} · {text.length} characters
    </summary>
    <MdView src={text} showSourceToggle={false} />
  </details>;
}

function MessageContent({ message }: { message: ChatMessage }) {
  const { text, reasoning, summarized, redacted } = splitContent(message);
  return <>
    {reasoning && <Reasoning text={reasoning} summarized={summarized} />}
    {redacted > 0 && <p className="text-xs text-muted-foreground">reasoning redacted by provider{redacted > 1 ? ` × ${redacted}` : ""}</p>}
    {text && <MdView src={text} showSourceToggle={false} />}
    {message.error && <p className="text-sm text-destructive">{message.error.type}: {message.error.message}</p>}
  </>;
}

function Message({ entry }: { entry: MessageEntry }) {
  const { message, contextOnly, results } = entry;
  const text = splitContent(message).text;
  if (message.role === "system") return <details data-message-role="system" className="rounded border bg-muted/20 px-2 py-1">
    <summary className="cursor-pointer text-xs text-muted-foreground"><b>system</b> · {text.split(/\r?\n/, 1)[0]}</summary>
    <div className="mt-2"><MessageContent message={message} /></div>
  </details>;
  return <section data-message-role={message.role} className="space-y-2 border-l-2 pl-3">
    <h5 className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
      {contextOnly ? "tool call from earlier history" : message.role}
      {message.role === "tool" && ` · ${message.function ?? message.tool_call_id ?? "unmatched result"}`}
    </h5>
    {!contextOnly && <MessageContent message={message} />}
    {(message.tool_calls ?? []).filter((call) => !contextOnly || results.has(call.id)).map((call) =>
      <section key={call.id} data-tool-call={call.id} className="space-y-2 rounded border p-2">
        <code className="text-xs font-semibold" title={call.id}>{call.function}</code>
        <pre data-tool-arguments="" className="overflow-auto whitespace-pre-wrap rounded bg-muted/30 p-2 font-mono text-xs"
          dangerouslySetInnerHTML={{ __html: highlightJson(typeof call.arguments === "string" ? call.arguments : JSON.stringify(call.arguments, null, 2)) }} />
        {call.parse_error && <p className="text-xs text-destructive">{call.parse_error}</p>}
        {results.get(call.id)?.map((result, index) => <div key={index} data-tool-result={call.id} className="space-y-1 border-t pt-2">
          <h6 className="text-[10px] uppercase text-muted-foreground">tool result</h6><MessageContent message={result} />
        </div>)}
      </section>)}
  </section>;
}

export function MessageList({ messages, start = 0 }: { messages: ChatMessage[]; start?: number }) {
  return <div className="space-y-3">{messageEntries(messages, start).map((entry) => <Message key={entry.index} entry={entry} />)}</div>;
}

export function LLMCallMessages({ record, previous, active, loadRecord }: {
  record: Ev; previous?: Ev; active: boolean; loadRecord?: (seq: number) => Promise<FullEvent>;
}) {
  const [showAll, setShowAll] = useState(false);
  const [previousFull, setPreviousFull] = useState<FullEvent | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const previousInput = previousFull?.record.run === previous?.run && previousFull?.record.seq === previous?.seq
    ? previousFull?.record.event.input : previous?.event.input;
  const needPrevious = !!previous && containsElision(previousInput);
  useEffect(() => {
    if (!active || !needPrevious || !previous || !loadRecord) return;
    let cancelled = false;
    setError(null);
    void loadRecord(previous.seq).then((full) => { if (!cancelled) setPreviousFull(full); })
      .catch((error) => { if (!cancelled) setError(String(error)); });
    return () => { cancelled = true; };
  }, [active, needPrevious, previous, loadRecord, attempt]);
  const event = record.event;
  const input: ChatMessage[] = Array.isArray(event.input) ? event.input : [];
  const delta = historyStart(input, Array.isArray(previousInput) ? previousInput : undefined);
  const waiting = containsElision(event.input) || (needPrevious && !!loadRecord && !error);
  const choices = event.output?.choices ?? [];
  const served = event.output?.model;
  const cacheRead = event.output?.usage?.input_tokens_cache_read;
  return <div data-llm-messages="" className="space-y-4">
    <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground" data-llm-meta="">
      {served && served !== event.model ? <><span>requested {event.model}</span><span>served {served}</span></>
        : event.model && <span>model {event.model}</span>}
      {event.via && <span>via {String(event.via)}</span>}
      {choices.map((choice: { stop_reason?: string }, index: number) => choice.stop_reason && choice.stop_reason !== "stop"
        ? <span key={index}>stop reason{choices.length > 1 ? ` (${index + 1})` : ""}: {choice.stop_reason}</span> : null)}
      {cacheRead != null && <span>cache read: {cacheRead} tokens</span>}
    </div>
    {event.error && <p className="text-sm text-destructive">error: {typeof event.error === "string" ? event.error : `${event.error.kind} — ${event.error.message}`}</p>}
    <section data-input-messages="" className="space-y-2">
      <h4 className="text-xs font-semibold">Input</h4>
      {waiting ? <p className="text-xs text-muted-foreground">Loading recorded history…</p> : <>
        {error && <p role="alert" className="text-xs text-destructive">Could not load previous call; showing all messages. <button onClick={() => setAttempt((n) => n + 1)}>retry</button></p>}
        {delta.start > 0 && <div className="flex items-center gap-3 text-xs text-muted-foreground">
          <span>{input.length - delta.start} new messages</span>
          <button className="underline" onClick={() => setShowAll((value) => !value)}>
            {showAll ? "show only new messages" : `show all ${input.length} messages`}
          </button>
        </div>}
        <MessageList messages={input} start={showAll ? 0 : delta.start} />
        {!input.length && <p className="text-xs text-muted-foreground">No input messages recorded.</p>}
      </>}
    </section>
    {choices.map((choice: { message: ChatMessage }, index: number) => <section key={index} data-output-messages="" className="space-y-2">
      <h4 className="text-xs font-semibold">Output{choices.length > 1 ? ` ${index + 1}` : ""}</h4>
      <MessageList messages={[choice.message]} />
    </section>)}
  </div>;
}
