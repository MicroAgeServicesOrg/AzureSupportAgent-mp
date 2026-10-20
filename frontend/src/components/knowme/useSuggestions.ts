import { useEffect, useMemo, useRef, useState } from "react";
import { api, type KnowMeTodo } from "../../api";
import { formatError } from "../../utils/format";

type Result = { status: "queued" | "loading" | "ready" | "error"; choices?: string[]; error?: string };
const LIMIT = 5;

function eligible(todo: KnowMeTodo) {
  return todo.choice_source !== "platform" && todo.choice_source !== "ai" &&
    !todo.choices?.length && !todo.suggestions?.length && !(todo.value || "").trim();
}

export function useSuggestions(kmId: string, raw: KnowMeTodo[], source: string, enabled: boolean) {
  const current = useRef(raw);
  current.current = raw;
  const [saved, setResults] = useState<{ signature: string; values: Record<string, Result> }>({ signature: "", values: {} });
  const enqueue = useRef<(id: string) => void>(() => {});
  const signature = JSON.stringify([kmId, source, raw.map((t) => [t.id, t.label, t.field_key, t.section_key, t.type])]);
  const results = saved.signature === signature ? saved.values : {};

  useEffect(() => {
    let alive = true;
    const controllers = new Set<AbortController>();
    const state: Record<string, Result> = {};
    const queue: { id: string; manual: boolean }[] = [];
    let active = 0;
    const publish = () => { if (alive) setResults({ signature, values: { ...state } }); };
    setResults({ signature, values: {} });
    if (!enabled) {
      enqueue.current = () => {};
      return () => { alive = false; };
    }
    function pump() {
      // A lazy route transition can retain this component after the browser URL has changed.
      if (window.location.pathname.replace(/\/$/, "") !== `/knowme/${kmId}`) {
        queue.length = 0;
        controllers.forEach((controller) => controller.abort());
        for (const [id, result] of Object.entries(state)) {
          if (result.status === "queued" || result.status === "loading") delete state[id];
        }
        publish();
        return;
      }
      while (alive && active < LIMIT && queue.length) {
        const item = queue.shift()!;
        const todo = current.current.find((t) => t.id === item.id);
        if (!todo || (!item.manual && !eligible(todo))) {
          delete state[item.id];
          publish();
          continue;
        }
        const ctrl = new AbortController();
        controllers.add(ctrl);
        active++;
        state[item.id] = { status: "loading" };
        publish();
        void api.suggestKnowMeField(kmId, item.id, ctrl.signal, true).then((response) => {
          if (alive) state[item.id] = { status: "ready", choices: response.choices };
        }).catch((error: unknown) => {
          if (alive && !ctrl.signal.aborted) state[item.id] = { status: "error", error: formatError(error) };
        }).finally(() => {
          controllers.delete(ctrl);
          active--;
          publish();
          pump();
        });
      }
    }
    enqueue.current = (id) => {
      if (state[id]?.status === "loading" || state[id]?.status === "queued") return;
      state[id] = { status: "queued" };
      queue.unshift({ id, manual: true });
      publish();
      pump();
    };
    for (const todo of current.current.filter(eligible)) {
      state[todo.id] = { status: "queued" };
      queue.push({ id: todo.id, manual: false });
    }
    publish();
    pump();
    return () => {
      alive = false;
      enqueue.current = () => {};
      controllers.forEach((controller) => controller.abort());
    };
  }, [signature, enabled, kmId]);

  const todos = useMemo(() => raw.map((todo) => {
    const result = results[todo.id];
    if (result?.status !== "ready" || todo.choices?.length || todo.choice_source === "platform") return todo;
    return { ...todo, choices: result.choices ?? [], choice_source: "ai" as const, allow_custom: true };
  }), [raw, results]);

  return {
    todos, results,
    retry: (id: string) => enqueue.current(id),
    pending: (id: string) => results[id]?.status === "queued" || results[id]?.status === "loading",
    failures: Object.values(results).filter((result) => result.status === "error").length,
  };
}
