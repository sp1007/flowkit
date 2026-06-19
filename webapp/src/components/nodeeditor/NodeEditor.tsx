import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import {
  ReactFlow,
  ReactFlowProvider,
  Background,
  Controls,
  Handle,
  Position,
  addEdge,
  useNodesState,
  useEdgesState,
  MarkerType,
  type Node,
  type Edge,
  type Connection,
  type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { api, graphApi, type Entity } from "../../api/client";
import Lightbox from "../common/Lightbox";

export interface EditorTarget {
  kind: "shot" | "entity";
  id: string;
  title: string;
  // What this edit produces: an image (storyboard frame / asset art) or a video (shot).
  goal?: "image" | "video";
  prompt?: string | null;
  refEntityIds?: string[];
  imageMediaId?: string | null;
  imageSrc?: string | null;
  videoSrc?: string | null;
}

// ─── Node type metadata (icon / label / accent color) ───────
const META: Record<string, { label: string; icon: string; color: string }> = {
  source: { label: "Nguồn ảnh", icon: "🖼", color: "#f59e0b" },
  prompt: { label: "Prompt đầu vào", icon: "≣", color: "#3b82f6" },
  refs: { label: "References", icon: "🔗", color: "#0ea5e9" },
  image: { label: "Tạo ảnh AI", icon: "🎨", color: "#a855f7" },
  editImage: { label: "Sửa ảnh AI", icon: "🖌", color: "#f59e0b" },
  video: { label: "Tạo video AI", icon: "🎬", color: "#a855f7" },
  output: { label: "Output", icon: "📤", color: "#64748b" },
};

// "refs" intentionally dropped — use one "Nguồn ảnh" (source) node per reference image.
const PALETTE = ["source", "prompt", "image", "video", "editImage", "output"];

const prettyModel = (m: string) =>
  m.replace(/_/g, " ").toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase());

// Shared state for custom nodes (update fn + lookups). Avoids prop drilling into
// React Flow's nodeTypes (which must be stable module-level components).
const NodeOps = createContext<{
  update: (id: string, patch: any) => void;
  remove: (id: string) => void;
  preview: (src: string, video: boolean) => void;
  results: Record<string, { web: string; ext: string }>;
  entities: Entity[];
  imageModels: string[];
}>({
  update: () => {},
  remove: () => {},
  preview: () => {},
  results: {},
  entities: [],
  imageModels: [],
});

const handleStyle = (color: string) => ({
  width: 18,
  height: 18,
  background: color,
  border: "3px solid #0e1411",
  boxShadow: "0 0 0 1px rgba(255,255,255,0.15)",
});

function Shell({
  type,
  id,
  children,
  inputs = true,
  outputs = true,
}: {
  type: string;
  id?: string;
  children: React.ReactNode;
  inputs?: boolean;
  outputs?: boolean;
}) {
  const { remove } = useContext(NodeOps);
  const m = META[type] || META.output;
  return (
    <div
      className="w-[228px] overflow-hidden rounded-xl border border-neutral-700/80 bg-[#0e1411] shadow-xl"
      style={{ borderTopColor: m.color, borderTopWidth: 3 }}
    >
      <div className="flex items-center gap-2 px-3 py-2 text-[11px] font-semibold uppercase tracking-wide text-neutral-300">
        <span style={{ color: m.color }}>{m.icon}</span>
        <span className="truncate">{m.label}</span>
        {id && (
          <button
            onClick={() => remove(id)}
            title="Xóa node"
            className="nodrag ml-auto grid h-5 w-5 place-items-center rounded text-neutral-500 hover:bg-rose-600/80 hover:text-white"
          >
            ✕
          </button>
        )}
      </div>
      <div className="space-y-2 px-3 pb-3">{children}</div>
      {inputs && <Handle type="target" position={Position.Left} style={handleStyle(m.color)} />}
      {outputs && <Handle type="source" position={Position.Right} style={handleStyle(m.color)} />}
    </div>
  );
}

const fieldCls =
  "nodrag w-full rounded-md border border-neutral-700 bg-neutral-900 px-2 py-1 text-[11px] text-neutral-200 outline-none focus:border-indigo-500";

function Preview({
  nodeId,
  src,
  video,
  label,
}: {
  nodeId?: string;
  src?: string;
  video?: boolean;
  label: string;
}) {
  const { results, preview } = useContext(NodeOps);
  // A run result (kept in a separate map keyed by node id) survives graph reloads and
  // wins over the seeded/transient src.
  const run = nodeId ? results[nodeId] : undefined;
  const effSrc = run?.web || src;
  const isVideo = run ? run.ext === "mp4" : !!video;
  if (!effSrc)
    return (
      <div className="grid aspect-video w-full place-items-center rounded-md bg-neutral-800/70 text-[11px] text-neutral-500">
        {label}
      </div>
    );
  const open = () => preview(effSrc, isVideo);
  return isVideo ? (
    <div className="relative">
      <video key={effSrc} src={effSrc} controls className="aspect-video w-full rounded-md bg-black object-cover" />
      <button
        onClick={open}
        title="Phóng to"
        className="nodrag absolute right-1 top-1 grid h-6 w-6 place-items-center rounded bg-black/60 text-xs text-white hover:bg-black/80"
      >
        ⛶
      </button>
    </div>
  ) : (
    <img
      key={effSrc}
      src={effSrc}
      onClick={open}
      title="Phóng to"
      className="nodrag aspect-video w-full cursor-zoom-in rounded-md object-cover"
    />
  );
}

function Slider({
  label,
  value,
  min,
  max,
  step,
  suffix,
  onChange,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  suffix?: string;
  onChange: (v: number) => void;
}) {
  return (
    <div>
      <div className="mb-0.5 flex justify-between text-[10px] uppercase tracking-wide text-neutral-500">
        <span>{label}</span>
        <span className="text-neutral-300">{value}{suffix}</span>
      </div>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="nodrag h-1 w-full cursor-pointer accent-indigo-500"
      />
    </div>
  );
}

// ─── Node components ────────────────────────────────────────
function SourceNode({ id, data }: NodeProps) {
  const { update, entities } = useContext(NodeOps);
  const d = data as any;
  const withImg = entities.filter((e) => e.media_id && e.image_path);
  const pick = (eid: string) => {
    const e = withImg.find((x) => x.id === eid);
    if (e) update(id, { entity_id: e.id, media_id: e.media_id, web: e.image_path, label: e.name });
  };
  return (
    <Shell type="source" id={id} inputs={false}>
      <Preview nodeId={id} src={d.web} label="Chọn ảnh bên dưới" />
      <select className={fieldCls} value={d.entity_id || ""} onChange={(e) => pick(e.target.value)}>
        <option value="">{d.web ? "(ảnh hiện tại)" : "— chọn ảnh asset —"}</option>
        {withImg.map((e) => (
          <option key={e.id} value={e.id}>{e.name}</option>
        ))}
      </select>
      {d.label && <div className="truncate text-[10px] text-neutral-500">↳ {d.label}</div>}
    </Shell>
  );
}

function PromptNode({ id, data }: NodeProps) {
  const { update } = useContext(NodeOps);
  const d = data as any;
  return (
    <Shell type="prompt" id={id} inputs={false}>
      <textarea
        className={`${fieldCls} nowheel h-24 resize-none leading-snug`}
        value={d.text || ""}
        placeholder="Nhập prompt…"
        onChange={(e) => update(id, { text: e.target.value })}
      />
      <div className="text-[10px] text-neutral-500">ⓘ viết prompt chi tiết để AI hiểu rõ hơn</div>
    </Shell>
  );
}

function RefsNode({ id, data }: NodeProps) {
  const { update, entities } = useContext(NodeOps);
  const ids: string[] = (data as any).entity_ids || [];
  const toggle = (eid: string) =>
    update(id, {
      entity_ids: ids.includes(eid) ? ids.filter((x) => x !== eid) : [...ids, eid],
    });
  return (
    <Shell type="refs" id={id} inputs={false}>
      <div className="nodrag nowheel max-h-36 space-y-0.5 overflow-auto">
        {entities.length === 0 && <p className="text-[11px] text-neutral-600">Chưa có asset.</p>}
        {entities.map((e) => (
          <label key={e.id} className="flex items-center gap-1.5 rounded px-1 py-0.5 text-[11px] hover:bg-neutral-800">
            <input type="checkbox" checked={ids.includes(e.id)} onChange={() => toggle(e.id)} className="h-3 w-3 accent-indigo-500" />
            <span className={`h-1.5 w-1.5 rounded-full ${e.media_id ? "bg-emerald-400" : "bg-neutral-600"}`} />
            <span className="truncate text-neutral-300">{e.name}</span>
          </label>
        ))}
      </div>
    </Shell>
  );
}

function AspectModelRow({
  id,
  data,
  models,
  videoModels,
}: {
  id: string;
  data: any;
  models?: string[];
  videoModels?: boolean;
}) {
  const { update } = useContext(NodeOps);
  const aspects = videoModels ? ["16:9", "9:16"] : ["16:9", "9:16", "1:1"];
  return (
    <div className="flex gap-2">
      <label className="flex-1">
        <div className="mb-0.5 text-[10px] uppercase tracking-wide text-neutral-500">Tỷ lệ</div>
        <select className={fieldCls} value={data.aspect || "16:9"} onChange={(e) => update(id, { aspect: e.target.value })}>
          {aspects.map((a) => <option key={a} value={a}>{a}</option>)}
        </select>
      </label>
      <label className="flex-1">
        <div className="mb-0.5 text-[10px] uppercase tracking-wide text-neutral-500">Model</div>
        {videoModels ? (
          <select className={fieldCls} value={data.model || "omni"} onChange={(e) => update(id, { model: e.target.value })}>
            <option value="omni">Omni Flash</option>
            <option value="veo">Veo i2v</option>
          </select>
        ) : (
          <select className={fieldCls} value={data.model || ""} onChange={(e) => update(id, { model: e.target.value })}>
            <option value="">Mặc định</option>
            {(models || []).map((m) => <option key={m} value={m}>{prettyModel(m)}</option>)}
          </select>
        )}
      </label>
    </div>
  );
}

function ImageNode({ id, data, type }: NodeProps) {
  const { update, imageModels } = useContext(NodeOps);
  const d = data as any;
  return (
    <Shell type={type || "image"} id={id}>
      <Preview nodeId={id} src={d._result || d.preview} label="Kết quả ảnh" />
      <AspectModelRow id={id} data={d} models={imageModels} />
      <Slider label="Số lượng tạo" value={d.count || 1} min={1} max={4} step={1} onChange={(v) => update(id, { count: v })} />
    </Shell>
  );
}

function VideoNode({ id, data }: NodeProps) {
  const { update } = useContext(NodeOps);
  const d = data as any;
  const isOmni = (d.model || "omni") === "omni";
  return (
    <Shell type="video" id={id}>
      <Preview nodeId={id} src={d._result} video label="Kết quả video" />
      <AspectModelRow id={id} data={d} videoModels />
      <Slider label="Số lượng tạo" value={d.count || 1} min={1} max={4} step={1} onChange={(v) => update(id, { count: v })} />
      {isOmni && (
        <Slider label="Thời lượng" value={d.duration || 8} min={4} max={10} step={2} suffix="s" onChange={(v) => update(id, { duration: v })} />
      )}
    </Shell>
  );
}

function OutputNode({ id, data }: NodeProps) {
  const d = data as any;
  return (
    <Shell type="output" id={id} outputs={false}>
      <Preview nodeId={id} src={d._result} video={d._ext === "mp4"} label="Output cuối" />
    </Shell>
  );
}

const NODE_TYPES = {
  source: SourceNode,
  prompt: PromptNode,
  refs: RefsNode,
  image: ImageNode,
  editImage: ImageNode,
  video: VideoNode,
  output: OutputNode,
};

// ─── Default graph ──────────────────────────────────────────
// Built from the target's goal (image vs video) + its seeded sources, so storyboard
// edits open on an image graph and shot edits on a video graph. Each reference entity
// becomes its own "Nguồn ảnh" (source) node, pre-filled with that entity's image.
function defaultGraph(seed: EditorTarget, entities: Entity[]): { nodes: Node[]; edges: Edge[] } {
  const mk = (id: string, type: string, x: number, y: number, data: any = {}): Node => ({
    id,
    type,
    position: { x, y },
    data: { ...data, _type: type },
  });
  const prompt = seed.prompt ?? "";
  const goal = seed.goal || (seed.kind === "shot" ? "video" : "image");
  const byId = new Map(entities.map((e) => [e.id, e]));

  const nodes: Node[] = [mk("p", "prompt", 0, 20, { text: prompt })];
  const edges: Edge[] = [];

  if (goal === "video") {
    // the shot's own frame is the start/reference image
    nodes.push(
      mk("src", "source", 0, 250, {
        media_id: seed.imageMediaId || "",
        web: seed.imageSrc || "",
        label: seed.title,
      })
    );
    nodes.push(
      mk("v", "video", 340, 80, {
        model: "omni", aspect: "16:9", duration: 8, count: 1, _result: seed.videoSrc || "",
      })
    );
    nodes.push(mk("o", "output", 660, 110, { _result: seed.videoSrc || "", _ext: "mp4" }));
    edges.push(
      { id: "ep", source: "p", target: "v" },
      { id: "es", source: "src", target: "v" },
      { id: "eo", source: "v", target: "o" }
    );
    return { nodes, edges };
  }

  // image goal: one source node per referenced entity (pre-filled)
  const refIds = (seed.refEntityIds ?? []).filter((i) => byId.get(i)?.media_id);
  refIds.forEach((eid, k) => {
    const e = byId.get(eid)!;
    nodes.push(
      mk(`src${k}`, "source", 0, 200 + k * 150, {
        entity_id: e.id, media_id: e.media_id, web: e.image_path, label: e.name,
      })
    );
    edges.push({ id: `es${k}`, source: `src${k}`, target: "i" });
  });
  nodes.push(
    mk("i", "image", 340, 80, { aspect: "16:9", model: "", count: 1, _result: seed.imageSrc || "" })
  );
  nodes.push(mk("o", "output", 660, 110, { _result: seed.imageSrc || "" }));
  edges.push({ id: "ep", source: "p", target: "i" }, { id: "eo", source: "i", target: "o" });
  return { nodes, edges };
}

// ─── Editor ─────────────────────────────────────────────────
function Editor({
  target,
  entities,
  onClose,
  onApplied,
}: {
  target: EditorTarget;
  entities: Entity[];
  onClose: () => void;
  onApplied: (r: any) => void;
}) {
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [imageModels, setImageModels] = useState<string[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  // Run results keyed by node id — kept apart from node.data so a graph reload
  // (e.g. after onApplied refreshes the parent) doesn't wipe the previews.
  const [results, setResults] = useState<Record<string, { web: string; ext: string }>>({});
  const [lightbox, setLightbox] = useState<{ src: string; video: boolean } | null>(null);

  // Thick edges + arrow markers; edges touching the active node animate (marching arrows)
  // so connections are easy to follow on a touch screen.
  const displayEdges = useMemo(
    () =>
      edges.map((e) => {
        const active = !!activeId && (e.source === activeId || e.target === activeId);
        return {
          ...e,
          animated: active,
          markerEnd: { type: MarkerType.ArrowClosed, width: 12, height: 12,
                       color: active ? "#818cf8" : "#6b7280" },
          style: { strokeWidth: active ? 5 : 3, stroke: active ? "#818cf8" : "#6b7280" },
        };
      }),
    [edges, activeId]
  );

  const update = useCallback(
    (id: string, patch: any) =>
      setNodes((ns) => ns.map((n) => (n.id === id ? { ...n, data: { ...n.data, ...patch } } : n))),
    [setNodes]
  );

  const remove = useCallback(
    (id: string) => {
      setNodes((ns) => ns.filter((n) => n.id !== id));
      setEdges((es) => es.filter((e) => e.source !== id && e.target !== id));
      setActiveId((a) => (a === id ? null : a));
    },
    [setNodes, setEdges]
  );

  useEffect(() => {
    api.options().then((o) => setImageModels(o.image_models || [])).catch(() => {});
  }, []);

  useEffect(() => {
    graphApi
      .get(target.kind, target.id)
      .then((r) => {
        const g = r.graph && r.graph.nodes?.length ? r.graph : defaultGraph(target, entities);
        setNodes(
          g.nodes.map((n: any) => ({
            id: n.id,
            type: n.type || n.data?._type || "prompt",
            position: n.position || { x: 0, y: 0 },
            data: { ...n.data, _type: n.type || n.data?._type },
          }))
        );
        setEdges((g.edges || []).map((e: any, i: number) => ({ id: e.id || `e${i}`, source: e.source, target: e.target })));
      })
      .catch(() => {
        const g = defaultGraph(target, entities);
        setNodes(g.nodes);
        setEdges(g.edges);
      });
  }, [target.id, entities]);

  const onConnect = useCallback(
    (c: Connection) => setEdges((es) => addEdge({ ...c, id: `e${Date.now()}` }, es)),
    [setEdges]
  );

  // Touch-friendly: click a connection to remove it (no keyboard needed).
  const onEdgeClick = useCallback(
    (_: React.MouseEvent, edge: Edge) => setEdges((es) => es.filter((e) => e.id !== edge.id)),
    [setEdges]
  );

  const addNode = (type: string) => {
    const id = `${type}-${Date.now()}`;
    const base: any = { _type: type };
    if (type === "prompt") base.text = "";
    if (type === "refs") base.entity_ids = [];
    if (type === "image" || type === "editImage") Object.assign(base, { aspect: "16:9", model: "", count: 1 });
    if (type === "video") Object.assign(base, { aspect: "16:9", model: "omni", duration: 8, count: 1 });
    setNodes((ns) => [
      ...ns,
      { id, type, position: { x: 80 + Math.random() * 160, y: 80 + Math.random() * 200 }, data: base },
    ]);
  };

  const serialize = () => ({
    nodes: nodes.map((n) => {
      const { _result, ...rest } = n.data as any; // drop transient preview
      return { id: n.id, type: n.type, data: rest, position: n.position };
    }),
    edges: edges.map((e) => ({ source: e.source, target: e.target })),
  });

  const save = () => graphApi.save(target.kind, target.id, serialize());

  const run = async () => {
    setBusy(true);
    setErr(null);
    setDone(false);
    try {
      const r = await graphApi.run(target.kind, target.id, serialize());
      const outs = (r.node_outputs || {}) as Record<string, string>;
      const mapped: Record<string, { web: string; ext: string }> = {};
      for (const [k, web] of Object.entries(outs)) {
        if (web) mapped[k] = { web, ext: web.toLowerCase().endsWith(".mp4") ? "mp4" : "png" };
      }
      setResults((prev) => ({ ...prev, ...mapped }));
      setNodes((ns) =>
        ns.map((n) => (outs[n.id] ? { ...n, data: { ...n.data, _result: outs[n.id] } } : n))
      );
      setDone(true);
      onApplied(r);
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  };

  const preview = useCallback((src: string, video: boolean) => setLightbox({ src, video }), []);

  const ops = useMemo(
    () => ({ update, remove, preview, results, entities, imageModels }),
    [update, remove, preview, results, entities, imageModels]
  );

  return (
    <div className="fixed inset-0 z-[70] flex flex-col bg-neutral-950">
      <div className="flex items-center gap-3 border-b border-neutral-800 px-4 py-2.5">
        <span className="font-medium">Node Editor — {target.title}</span>
        <div className="ml-2 flex flex-wrap gap-1">
          {PALETTE.map((p) => (
            <button
              key={p}
              onClick={() => addNode(p)}
              className="rounded-md border border-neutral-700 px-2 py-1 text-xs hover:bg-neutral-800"
              style={{ borderLeftColor: META[p].color, borderLeftWidth: 3 }}
            >
              + {META[p].label}
            </button>
          ))}
        </div>
        <div className="ml-auto flex items-center gap-2">
          {done && <span className="text-xs text-emerald-400">✓ Đã tạo & áp dụng</span>}
          <button onClick={save} className="rounded-lg border border-neutral-700 px-3 py-1.5 text-sm hover:bg-neutral-800">
            Lưu
          </button>
          <button
            onClick={run}
            disabled={busy}
            className="rounded-lg bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-500 disabled:opacity-40"
          >
            {busy ? "Đang chạy…" : "▶ Run"}
          </button>
          <button onClick={onClose} className="rounded-lg px-3 py-1.5 text-sm text-neutral-400 hover:bg-neutral-800">
            Đóng
          </button>
        </div>
      </div>
      {err && <div className="bg-rose-950/50 px-4 py-1.5 text-sm text-rose-300">{err}</div>}
      <div className="flex-1">
        <NodeOps.Provider value={ops}>
          <ReactFlow
            nodes={nodes}
            edges={displayEdges}
            nodeTypes={NODE_TYPES}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={onConnect}
            onEdgeClick={onEdgeClick}
            onNodeClick={(_, n) => setActiveId(n.id)}
            onPaneClick={() => setActiveId(null)}
            edgesFocusable
            deleteKeyCode={["Backspace", "Delete"]}
            connectionLineStyle={{ strokeWidth: 4, stroke: "#818cf8" }}
            defaultEdgeOptions={{ markerEnd: { type: MarkerType.ArrowClosed } }}
            fitView
            minZoom={0.3}
            colorMode="dark"
            proOptions={{ hideAttribution: true }}
          >
            <Background gap={20} color="#1f2937" />
            <Controls />
          </ReactFlow>
        </NodeOps.Provider>
      </div>
      <div className="border-t border-neutral-800 px-4 py-1 text-[11px] text-neutral-500">
        ⓘ Nhấn vào ảnh/video để phóng to · Nhấn vào đường nối để xóa kết nối · Nút ✕ trên node để xóa node
      </div>
      {lightbox && (
        <Lightbox
          imageSrc={lightbox.video ? undefined : lightbox.src}
          videoSrc={lightbox.video ? lightbox.src : undefined}
          onClose={() => setLightbox(null)}
        />
      )}
    </div>
  );
}

export default function NodeEditor(props: {
  target: EditorTarget;
  entities: Entity[];
  onClose: () => void;
  onApplied: (r: any) => void;
}) {
  return (
    <ReactFlowProvider>
      <Editor {...props} />
    </ReactFlowProvider>
  );
}
