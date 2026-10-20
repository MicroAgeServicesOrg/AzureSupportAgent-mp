import { useMemo, useState, type ChangeEvent, type ReactNode } from "react";
import { api, type KnowMeResponse, type KnowMeRevisionContent, type SlaComponent, type SlaExpression, type SlaJourney, type SlaSnapshot } from "../../api";
import { formatError } from "../../utils/format";
import { Markdown } from "../LazyMarkdown";
import { parseFieldToken } from "./fields";

const BUTTON = "rounded-lg border px-2.5 py-1.5 text-xs font-medium hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50";
const INPUT = "w-full rounded-lg border border-gray-300 bg-white px-2.5 py-1.5 text-sm text-gray-800";
const MAX_RESOURCES = 16;
const MAX_NODES = 128;
const MAX_DEPTH = 8;
const SOURCE_HOSTS = new Set([
  "learn.microsoft.com", "docs.microsoft.com", "azure.microsoft.com",
  "microsoft.com", "www.microsoft.com", "download.microsoft.com",
]);

function sourceLink(value: string | null): string | null {
  if (!value) return null;
  try {
    const url = new URL(value);
    return url.protocol === "https:" && !url.username && !url.password &&
      (!url.port || url.port === "443") && SOURCE_HOSTS.has(url.hostname) ? url.href : null;
  } catch { return null; }
}

function dateLabel(value: string | null) {
  if (!value) return "Not recorded";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "Not recorded" : date.toLocaleString();
}

function expressionStats(expression: SlaExpression) {
  const ids = new Set<string>();
  let nodes = 0;
  let depth = 0;
  let error = "";
  function visit(node: SlaExpression, level: number) {
    nodes++;
    depth = Math.max(depth, level);
    if (level > MAX_DEPTH || nodes > MAX_NODES) {
      error = `Use at most ${MAX_NODES} expression nodes and ${MAX_DEPTH} levels.`;
      return;
    }
    if (node.operator === "component") {
      if (!node.component_id) error = "Choose a component for every resource dependency.";
      else ids.add(node.component_id);
    } else {
      if ((node.children?.length ?? 0) < 2) error = "Every ALL / ANY group needs at least two dependencies.";
      node.children?.forEach((child) => visit(child, level + 1));
    }
  }
  visit(expression, 1);
  return { ids, nodes, depth, error };
}

function copyExpression(node: SlaExpression): SlaExpression {
  return node.operator === "component"
    ? { operator: "component", component_id: node.component_id ?? "" }
    : { operator: node.operator, children: (node.children ?? []).map(copyExpression) };
}

function lines(value: string) {
  return value.split("\n").map((line) => line.trim()).filter(Boolean);
}

function ReviewInput({ label, value, onChange, rows, required = true }: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  rows?: number;
  required?: boolean;
}) {
  const props = {
    "aria-label": label, className: `${INPUT} mt-1`, value, required,
    onChange: (event: ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => onChange(event.target.value),
  };
  return <label className="block text-xs">{label}{rows ? <textarea {...props} rows={rows} /> : <input {...props} />}</label>;
}

function ExpressionEditor({
  node, components, path = "1", depth = 1, selected, nodeCount, onChange, onRemove,
}: {
  node: SlaExpression;
  components: SlaComponent[];
  path?: string;
  depth?: number;
  selected: Set<string>;
  nodeCount: number;
  onChange: (node: SlaExpression) => void;
  onRemove?: () => void;
}) {
  const children = node.children ?? [];
  const canAdd = depth < MAX_DEPTH && nodeCount < MAX_NODES;
  function append(child: SlaExpression) {
    onChange({ operator: node.operator, children: [...children, child] });
  }
  return (
    <fieldset className="min-w-0 space-y-2 rounded-lg border border-gray-200 bg-gray-50/50 p-3">
      <legend className="px-1 text-xs font-semibold text-gray-700">
        {node.operator === "all" ? "ALL required" : node.operator === "any" ? "ANY alternatives" : "Resource dependency"} · {path}
      </legend>
      <div className="flex items-center gap-2">
        <label className="flex-1 text-xs">
          Dependency {path}
          <select
            aria-label={`Dependency type ${path}`}
            className={`${INPUT} mt-1`}
            value={node.operator}
            onChange={(e) => {
              const operator = e.target.value as SlaExpression["operator"];
              onChange(operator === "component"
                ? { operator, component_id: "" }
                : { operator, children: node.operator === "component" ? [] : children });
            }}
          >
            <option value="all">ALL required — every dependency must be available</option>
            <option value="any">ANY alternatives — at least one must be available</option>
            <option value="component">Single resource</option>
          </select>
        </label>
        {onRemove && <button type="button" className={BUTTON} aria-label={`Remove dependency ${path}`} onClick={onRemove}>Remove</button>}
      </div>
      {node.operator === "component" ? (
        <label className="block text-xs">
          Inventoried resource
          <select
            aria-label={`Component ${path}`}
            className={`${INPUT} mt-1`}
            value={node.component_id ?? ""}
            onChange={(e) => onChange({ operator: "component", component_id: e.target.value })}
          >
            <option value="">Choose a component…</option>
            {node.component_id && !components.some((c) => c.component_id === node.component_id) &&
              <option value={node.component_id}>Missing from current inventory — choose another resource</option>}
            {components.map((c) => (
              <option key={c.component_id} value={c.component_id} disabled={selected.size >= MAX_RESOURCES && !selected.has(c.component_id)}>
                {c.name || c.resource_id} · {c.resource_type} · {c.location || "unknown region"} · {c.sku || "unknown SKU"}
              </option>
            ))}
          </select>
        </label>
      ) : (
        <>
          <p className="text-xs text-gray-600">{node.operator === "all" ? "Every child is required for this journey." : "These children are alternatives; one available child is sufficient."}</p>
          {children.map((child, index) => (
            <ExpressionEditor
              key={index} node={child} components={components} path={`${path}.${index + 1}`}
              depth={depth + 1} selected={selected} nodeCount={nodeCount}
              onChange={(next) => onChange({ operator: node.operator, children: children.map((old, i) => i === index ? next : old) })}
              onRemove={() => onChange({ operator: node.operator, children: children.filter((_, i) => i !== index) })}
            />
          ))}
          <div className="flex flex-wrap gap-2">
            <button type="button" className={BUTTON} disabled={!canAdd || !components.length} onClick={() => append({ operator: "component", component_id: "" })} aria-label={`Add resource to group ${path}`}>+ Resource</button>
            <button type="button" className={BUTTON} disabled={!canAdd} onClick={() => append({ operator: "all", children: [] })} aria-label={`Add ALL group to group ${path}`}>+ ALL group</button>
            <button type="button" className={BUTTON} disabled={!canAdd} onClick={() => append({ operator: "any", children: [] })} aria-label={`Add ANY group to group ${path}`}>+ ANY group</button>
          </div>
        </>
      )}
    </fieldset>
  );
}

function ExpressionRead({ node, components }: { node: SlaExpression; components: SlaComponent[] }) {
  if (node.operator === "component") {
    const component = components.find((c) => c.component_id === node.component_id);
    return <li className="break-all">{component?.name || node.component_id || "Unselected component"}</li>;
  }
  return (
    <li>
      <b>{node.operator === "all" ? "ALL required" : "ANY alternatives"}</b>
      <ul className="ml-4 list-disc space-y-1">{node.children?.map((child, i) => <ExpressionRead key={i} node={child} components={components} />)}</ul>
    </li>
  );
}

export function CompositeSla({
  kmId, snapshot, stale = false, canReview, readOnlyReason, busy = false, onUpdated,
}: {
  kmId: string;
  snapshot?: SlaSnapshot | null;
  stale?: boolean;
  canReview: boolean;
  readOnlyReason?: string;
  busy?: boolean;
  onUpdated: (response: KnowMeResponse) => void;
}) {
  const [pending, setPending] = useState<"refresh" | "review" | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState("");
  const [boundary, setBoundary] = useState("");
  const [expression, setExpression] = useState<SlaExpression>({ operator: "all", children: [] });
  const [exclusions, setExclusions] = useState<Record<string, string>>({});
  const [assumptions, setAssumptions] = useState("");
  const [externalDependencies, setExternalDependencies] = useState("");
  const [independent, setIndependent] = useState(false);
  const components = snapshot?.components ?? [];
  const locked = !canReview || !!readOnlyReason;
  const disabled = locked || busy || !!pending;
  const stats = expressionStats(expression);
  const excluded = components.filter((c) => !stats.ids.has(c.component_id));
  const missingComponent = [...stats.ids].some((id) => !components.some((c) => c.component_id === id));
  const validation = !name.trim() ? "Name the user journey." :
    !boundary.trim() ? "Describe the journey boundary and success condition." :
    stats.error || (stats.ids.size > MAX_RESOURCES ? `Select at most ${MAX_RESOURCES} distinct resources.` :
      missingComponent ? "Select dependencies only from the current inventory." :
      excluded.some((c) => !exclusions[c.component_id]?.trim()) ? "Give a nonblank reason for every excluded resource." :
      !lines(assumptions).length ? "Record at least one nonblank review assumption." :
      !independent ? "Explicitly acknowledge the independent-failures assumption." : "");

  const referenced = snapshot?.model ? expressionStats(snapshot.model.expression).ids : new Set<string>();
  const showPercentage = snapshot?.status === "calculated" && !!snapshot.percentage &&
    !!snapshot.availability && snapshot.inventory_complete && !snapshot.unresolved.length &&
    !!snapshot.model && !snapshot.model.external_dependencies.length &&
    !components.some((c) => referenced.has(c.component_id) && (c.status !== "resolved" || c.availability === null));

  async function calculate(model?: SlaJourney) {
    if (disabled) return null;
    setPending(model ? "review" : "refresh");
    setError("");
    setNotice("");
    try {
      const response = await api.calculateKnowMeCompositeSla(kmId, model);
      onUpdated(response);
      setEditing(false);
      setNotice(model ? "Journey review saved; calculation updated." : "Snapshot refreshed from available inventory; no AI generation was run.");
      return response;
    } catch (e) {
      setError(`Composite SLA ${model ? "review" : "refresh"} failed: ${formatError(e)}`);
      return null;
    } finally { setPending(null); }
  }

  async function beginReview() {
    if (disabled) return;
    let current = snapshot;
    if (!current) {
      const response = await calculate();
      if (!response?.know_me?.composite_sla) return;
      current = response.know_me.composite_sla;
    }
    const model = current.model;
    setName(model?.name ?? "");
    setBoundary(model?.boundary ?? "");
    setExpression(model ? copyExpression(model.expression) : { operator: "all", children: [] });
    setExclusions(Object.fromEntries((model?.exclusions ?? []).map((item) => [item.component_id, item.reason])));
    setAssumptions((model?.assumptions ?? []).join("\n"));
    setExternalDependencies((model?.external_dependencies ?? []).join("\n"));
    setIndependent(false);
    setError("");
    setNotice("");
    setEditing(true);
  }

  return (
    <div data-testid="composite-sla" aria-label="Calculated Composite SLA" className="not-prose my-3 space-y-3 rounded-xl border border-brand/20 bg-white p-4 text-sm text-gray-700">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="font-semibold text-gray-900">Calculated Composite SLA</h3>
        <span className="rounded-full bg-gray-100 px-2 py-0.5 text-xs">{snapshot ? snapshot.status.replace("_", " ") : "Not calculated"}</span>
        {showPercentage && <strong data-testid="composite-sla-percentage" className="text-lg text-brand">{snapshot!.percentage!.replace(/%$/, "")}%</strong>}
      </div>
      <p className="text-xs">This is a configuration-based planning estimate, not a workload contract, SLO, or observed uptime. Human SLA / SLO commitments and TODO fields remain separate.</p>
      {snapshot?.disclaimer && <p className="text-xs text-gray-600">{snapshot.disclaimer}</p>}
      {!snapshot ? (
        <p>No calculated snapshot yet. Refresh to load inventoried resources, then explicitly review a user journey and its dependencies.</p>
      ) : (
        <>
          {!showPercentage && <p data-testid="composite-sla-withheld" className="font-medium text-amber-800">Whole-journey percentage withheld until the model is reviewed and all required inputs and dependencies are resolved.</p>}
          {stale && <p role="status" className="rounded-lg border border-orange-200 bg-orange-50 p-2 text-orange-800">Snapshot is stale: architecture inputs, workload membership, the SLA catalog, or the calculator version changed. Refresh and review dependencies before relying on this estimate.</p>}
          <div className="space-y-1 text-xs text-gray-600">
            <p>Calculated: {dateLabel(snapshot.calculated_at)} · Configuration observed: {dateLabel(snapshot.configuration_observed_at)}</p>
            <p>Reviewed: {dateLabel(snapshot.reviewed_at)}{snapshot.reviewed_by ? ` by ${snapshot.reviewed_by}` : ""}</p>
            {!snapshot.inventory_complete && <p className="text-amber-800">Inventory is incomplete; a whole-journey percentage cannot be provided.</p>}
          </div>
          {!!snapshot.unresolved.length && (
            <div>
              <h4 className="text-xs font-semibold">Missing inputs / unresolved dependencies</h4>
              <ul className="ml-4 list-disc text-xs">{snapshot.unresolved.map((reason, i) => <li key={i}>{reason}</li>)}</ul>
            </div>
          )}
          <details>
            <summary className="cursor-pointer text-xs font-semibold">Component evidence and sources ({components.length})</summary>
            <div className="mt-2 overflow-x-auto">
              <table className="w-full text-left text-xs">
                <caption className="sr-only">Composite SLA component evidence</caption>
                <thead className="border-b bg-gray-50"><tr>
                  <th scope="col" className="p-2">Resource / observed configuration</th>
                  <th scope="col" className="p-2">Resolution / assumptions</th>
                  <th scope="col" className="p-2">Availability (fraction)</th>
                  <th scope="col" className="p-2">Rule / operation</th>
                  <th scope="col" className="p-2">Microsoft source / measurement</th>
                </tr></thead>
                <tbody>{components.map((c) => {
                  const source = sourceLink(c.source_url);
                  return (
                    <tr key={c.component_id} className="border-b align-top">
                      <td className="max-w-xs space-y-1 p-2">
                        <b>{c.name || c.resource_id}</b>
                        <div className="break-all text-gray-500">{c.resource_id}</div>
                        <div>{c.resource_type} · {c.location || "Region unknown"} · {c.sku || "SKU unknown"}</div>
                        <details><summary className="cursor-pointer">Observed configuration for {c.name || c.resource_id}</summary><pre className="max-h-48 overflow-auto whitespace-pre-wrap break-all">{JSON.stringify(c.configuration, null, 2)}</pre><div className="break-all">Component ID: {c.component_id}</div></details>
                      </td>
                      <td className="max-w-xs space-y-1 p-2">
                        <b>{c.status}</b><div>{c.reason}</div>
                        {!!c.assumptions.length && <ul className="ml-4 list-disc">{c.assumptions.map((item, i) => <li key={i}>{item}</li>)}</ul>}
                      </td>
                      <td className="p-2">{c.availability ?? "Withheld"}</td>
                      <td className="p-2">{c.rule_id || "No rule"}<div>{c.operation || "Operation unknown"}</div></td>
                      <td className="max-w-xs space-y-1 p-2">
                        {source ? <a href={source} target="_blank" rel="noopener noreferrer" className="text-brand underline">Microsoft primary source</a> : <span>No verified Microsoft HTTPS citation</span>}
                        <div>Source version: {c.source_version || "Not recorded"}</div>
                        <div>{c.measurement_basis || "Measurement basis unknown"}</div>
                      </td>
                    </tr>
                  );
                })}</tbody>
              </table>
            </div>
            <div className="mt-2 space-y-1 break-all text-xs text-gray-500">
              <p>Catalog: {snapshot.catalog_version} · Calculator: {snapshot.calculator_version} · Schema: {snapshot.schema_version}</p>
              <p>Source fingerprint: {snapshot.source_fingerprint}</p>
              <p>Input fingerprint: {snapshot.input_fingerprint}</p>
            </div>
          </details>
          <details>
            <summary className="cursor-pointer text-xs font-semibold">Journey assumptions, exclusions and external dependencies</summary>
            {snapshot.model ? (
              <div className="mt-2 space-y-2 text-xs">
                <p><b>{snapshot.model.name}</b> — {snapshot.model.boundary}</p>
                <ul className="ml-4 list-disc"><ExpressionRead node={snapshot.model.expression} components={components} /></ul>
                <p>Independent failures assumed. Shared component IDs are counted once by the server evaluator.</p>
                <h4 className="font-semibold">Review assumptions</h4>
                <ul className="ml-4 list-disc">{snapshot.model.assumptions.map((item, i) => <li key={i}>{item}</li>)}</ul>
                <h4 className="font-semibold">Explicit exclusions</h4>
                {snapshot.model.exclusions.length ? <ul className="ml-4 list-disc">{snapshot.model.exclusions.map((item) => <li key={item.component_id} className="break-all">{components.find((c) => c.component_id === item.component_id)?.name || item.component_id}: {item.reason}</li>)}</ul> : <p>None.</p>}
                <h4 className="font-semibold">External dependencies</h4>
                {snapshot.model.external_dependencies.length ? <ul className="ml-4 list-disc text-amber-800">{snapshot.model.external_dependencies.map((item, i) => <li key={i}>{item}</li>)}</ul> : <p>None recorded.</p>}
              </div>
            ) : <p className="mt-2 text-xs text-amber-800">No reviewed journey model. Resource inventory alone does not define dependencies or redundancy.</p>}
          </details>
        </>
      )}
      <p className="text-xs text-gray-500">Snapshot freshness reflects the available inventory only. Azure configuration is not continuously monitored; a refresh does not perform an Azure scan or AI generation.</p>
      {locked ? <p className="text-xs text-gray-500">{readOnlyReason || "Read-only: architects.write permission is required to refresh or review."}</p> : (
        <div className="flex flex-wrap gap-2">
          <button type="button" className={BUTTON} disabled={disabled} onClick={() => void calculate()}>{pending === "refresh" ? "Refreshing snapshot…" : "Refresh SLA snapshot"}</button>
          <button type="button" className={`${BUTTON} border-brand/30 text-brand`} disabled={disabled} onClick={() => void beginReview()} aria-expanded={editing}>Review journey and dependencies</button>
        </div>
      )}
      {error && <p role="alert" className="rounded-lg border border-red-200 bg-red-50 p-2 text-red-700">{error}</p>}
      {notice && <p role="status" className="text-xs text-green-700">{notice}</p>}
      {editing && !locked && (
        <form
          aria-label="Composite SLA journey review"
          className="space-y-3 border-t pt-3"
          onSubmit={(e) => {
            e.preventDefault();
            if (validation || disabled) return;
            void calculate({
              name: name.trim(), boundary: boundary.trim(), expression: copyExpression(expression),
              exclusions: excluded.map((c) => ({ component_id: c.component_id, reason: exclusions[c.component_id].trim() })),
              independent_failures: true, assumptions: lines(assumptions), external_dependencies: lines(externalDependencies),
            });
          }}
        >
          <fieldset disabled={disabled} className="min-w-0 space-y-3">
            <legend className="font-semibold text-gray-900">Explicit journey / dependency review</legend>
            <p className="text-xs">Review one success path; inventory order is not a dependency model. Use ALL for required dependencies and ANY for genuine alternatives. At most 16 distinct selected resources, 128 nodes and 8 levels; repeated IDs share the same failure event.</p>
            {!components.length && <p className="text-amber-800">No inventoried components are available. Refresh after updating the architecture inventory.</p>}
            <ReviewInput label="Journey name" value={name} onChange={setName} />
            <ReviewInput label="Journey boundary / success condition" rows={2} value={boundary} onChange={setBoundary} />
            <ExpressionEditor node={expression} components={components} selected={stats.ids} nodeCount={stats.nodes} onChange={setExpression} />
            <p className="text-xs text-gray-500">{stats.ids.size}/{MAX_RESOURCES} distinct resources · {stats.nodes}/{MAX_NODES} nodes · {stats.depth}/{MAX_DEPTH} levels</p>
            <div className="space-y-2">
              <h4 className="text-xs font-semibold">Explicitly excluded inventory ({excluded.length})</h4>
              <p className="text-xs">Every inventoried resource must be selected above or excluded below with a reason.</p>
              {excluded.map((c) => (
                <label key={c.component_id} className="block text-xs">
                  Exclusion reason for {c.name || c.resource_id}
                  <span className="block break-all text-gray-500">{c.component_id}</span>
                  <input className={`${INPUT} mt-1`} aria-label={`Exclusion reason for ${c.name || c.resource_id}`} value={exclusions[c.component_id] ?? ""} onChange={(e) => setExclusions((old) => ({ ...old, [c.component_id]: e.target.value }))} required />
                </label>
              ))}
            </div>
            <ReviewInput label="Review assumptions (one per line)" rows={3} value={assumptions} onChange={setAssumptions} />
            <ReviewInput label="External dependencies (one per line)" rows={2} value={externalDependencies} onChange={setExternalDependencies} required={false} />
            {!!lines(externalDependencies).length && <p className="text-xs text-amber-800">External dependencies are unresolved inputs; the whole-journey percentage will be withheld.</p>}
            <label className="flex items-start gap-2 text-xs">
              <input type="checkbox" checked={independent} onChange={(e) => setIndependent(e.target.checked)} className="mt-0.5" required />
              <span>I explicitly acknowledge the independent-failures assumption for this model. Shared infrastructure, common-region failures and correlated dependencies can invalidate it.</span>
            </label>
            {validation && <p role="status" className="text-xs text-amber-800">{validation}</p>}
            <div className="flex gap-2">
              <button type="submit" className={`${BUTTON} bg-brand text-white hover:bg-brand-dark`} disabled={disabled || !!validation}>{pending === "review" ? "Saving review…" : "Save review and calculate"}</button>
              <button type="button" className={BUTTON} disabled={!!pending} onClick={() => setEditing(false)}>Cancel review</button>
            </div>
          </fieldset>
        </form>
      )}
    </div>
  );
}

export function HistoricalSlaDocument({
  kmId, revision, sections, components, proseClassName,
}: {
  kmId: string;
  revision: KnowMeRevisionContent;
  sections: { key: string; label: string; body: string; isResiliency: boolean }[];
  components: Record<string, unknown>;
  proseClassName: string;
}) {
  const readOnlyComponents = useMemo(() => ({
    ...components,
    code(props: { className?: string; children?: ReactNode }) {
      const text = String(Array.isArray(props.children) ? props.children.join("") : (props.children ?? ""));
      const field = parseFieldToken(text);
      if (field) return <span className="rounded-sm bg-amber-50 px-1 text-amber-800">{field.value || `TODO: ${field.label}`}</span>;
      return (components.code as (props: { className?: string; children?: ReactNode }) => ReactNode)(props);
    },
  }), [components]);

  return (
    <div className="space-y-3 rounded-xl border border-amber-200 bg-amber-50/20 p-5">
      <h1 className="text-2xl font-bold text-gray-900">{revision.title || "Know-Me revision"}</h1>
      <p className="text-xs text-gray-500">Copy and Markdown download use this revision and its saved SLA snapshot. Current-document PDF export is unavailable while viewing history.</p>
      {sections.map((section) => (
        <section key={section.key} data-kmsec={section.key}>
          <h2 className="text-lg font-bold text-gray-900">{section.label}</h2>
          <div className={proseClassName}><Markdown components={readOnlyComponents}>{section.body}</Markdown></div>
          {section.isResiliency && (
            <CompositeSla
              kmId={kmId} snapshot={revision.composite_sla} canReview={false}
              readOnlyReason="Historical revision: its saved snapshot is read-only and is not compared with current inventory."
              onUpdated={() => {}}
            />
          )}
        </section>
      ))}
    </div>
  );
}
