import { useEffect, useRef, useState, type CSSProperties } from "react";
import type { KnowMeTodo } from "../../api";
import { FieldInput } from "./FieldInput";
import { FIELD_META, validateField } from "./fields";

export function FieldPopover({
  todo, anchor, saving, onSave, onClose, onSuggest, suggesting, suggestionError, onStep,
}: {
  todo: KnowMeTodo;
  anchor: DOMRect;
  saving: boolean;
  onSave: (value: string) => void;
  onClose: () => void;
  onSuggest: () => void;
  suggesting: boolean;
  suggestionError?: string;
  onStep: (dir: 1 | -1, draft: string) => void;
}) {
  const [draft, setDraft] = useState(todo.value || "");
  const ref = useRef<HTMLDivElement>(null);
  const width = 340;
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const left = Math.max(8, Math.min(anchor.left, vw - width - 8));
  const below = anchor.bottom + 8;
  const above = below + 220 > vh && anchor.top > vh - anchor.bottom;
  const style: CSSProperties = above
    ? { position: "fixed", left, bottom: vh - anchor.top + 8, width, zIndex: 50 }
    : { position: "fixed", left, top: below, width, zIndex: 50 };

  useEffect(() => {
    // Capture before a selected combobox option unmounts; bubbling would look like an outside click.
    const onDown = (event: MouseEvent) => {
      if (ref.current?.contains(event.target as Node)) return;
      onClose();
    };
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
    window.addEventListener("mousedown", onDown, true);
    window.addEventListener("keydown", onKey);
    window.addEventListener("scroll", onClose, true);
    return () => {
      window.removeEventListener("mousedown", onDown, true);
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("scroll", onClose, true);
    };
  }, [onClose]);
  const error = validateField(todo, draft);
  const meta = FIELD_META[todo.type];

  return (
    <div ref={ref} style={style} data-testid="knowme-field-popover" className="rounded-xl border border-gray-200 bg-white p-3 shadow-2xl">
      <div className="mb-1.5 flex items-start gap-2">
        <span className="min-w-0 flex-1 text-[13px] font-semibold text-gray-800">{todo.label}</span>
        {todo.required && <span className="shrink-0 rounded-full bg-amber-50 px-1.5 py-0.5 text-[10px] font-medium text-amber-700">required</span>}
        <div className="shrink-0 flex items-center gap-0.5">
          {([-1, 1] as const).map((direction) => (
            <button key={direction} onClick={() => onStep(direction, draft)} disabled={saving || !!error}
              className="rounded-sm p-0.5 text-gray-400 hover:bg-gray-100 hover:text-gray-700 disabled:opacity-40"
              title={direction === -1 ? "Previous field" : "Next field"} aria-label={direction === -1 ? "Previous field" : "Next field"}>
              {direction === -1 ? "↤" : "↦"}
            </button>
          ))}
        </div>
        <button onClick={onClose} aria-label="Close" className="shrink-0 rounded-sm p-0.5 text-gray-400 hover:bg-gray-100">✕</button>
      </div>
      {meta?.label && <div className="mb-1.5 text-[11px] text-gray-400">{meta.label}{meta.placeholder ? ` · e.g. ${meta.placeholder}` : ""}</div>}
      <FieldInput todo={todo} value={draft} onChange={setDraft} onEnter={() => { if (!error) onSave(draft); }}
        autoFocus onSuggest={onSuggest} suggesting={suggesting} suggestionError={suggestionError} />
      <div className="mt-2.5 flex items-center gap-2">
        <button onClick={() => onSave(draft)} disabled={saving || !!error}
          className="rounded-lg bg-brand px-3 py-1.5 text-xs font-semibold text-white hover:bg-brand-dark disabled:opacity-50">
          {saving ? "Saving…" : "Save"}
        </button>
        {todo.value && <button onClick={() => onSave("")} disabled={saving} title="Clear this field"
          className="rounded-lg border px-2.5 py-1.5 text-xs text-gray-500 hover:bg-gray-50 disabled:opacity-50">Clear</button>}
        <button onClick={onClose} className="ml-auto rounded-lg border px-2.5 py-1.5 text-xs text-gray-600 hover:bg-gray-50">Cancel</button>
      </div>
    </div>
  );
}
