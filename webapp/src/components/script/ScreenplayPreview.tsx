import { useMemo, type ReactNode } from "react";

// Render a Fountain-ish screenplay as a classic printed page (cream paper, Courier,
// industry layout): centered title block, bold scene headings, right-aligned section
// labels / transitions, and indented character / parenthetical / dialogue blocks.
// The generator pads text with literal spaces to fake centering — we strip per-line
// whitespace and re-impose layout via CSS so it looks like a real script, not a dump.

type Block =
  | { t: "title"; text: string }
  | { t: "subtitle"; text: string }
  | { t: "section"; text: string }
  | { t: "cast"; lead: string; rest: string }
  | { t: "divider" }
  | { t: "scene"; text: string }
  | { t: "character"; text: string }
  | { t: "paren"; text: string }
  | { t: "dialogue"; text: string }
  | { t: "action"; text: string };

// Scene heading: INT./EXT./I-E. or Vietnamese NỘI./NGOẠI./CẢNH.
const SCENE_RE = /^(C[ẢA]NH\b|N[ỘO]I\.|NGO[ẠA]I\.|INT\.?\/?EXT\.?|INT\.|EXT\.|I\/E\.)/i;
const SUBTITLE_RE = /^(PHẦN|CHƯƠNG|HỒI|MÀN|PART|ACT|EPISODE|TẬP)\b/i;

function isUpper(s: string): boolean {
  const letters = s.replace(/[^\p{L}]/gu, "");
  return letters.length > 0 && s === s.toLocaleUpperCase("vi");
}

function parse(src: string): Block[] {
  const lines = (src || "").replace(/\r\n?/g, "\n").split("\n");
  const out: Block[] = [];
  let inDial = false;

  for (const raw of lines) {
    const line = raw.trim();
    if (!line) {
      inDial = false; // a blank line closes any open dialogue block
      continue;
    }
    if (/^#{1,3}\s+/.test(line)) {
      out.push({ t: "title", text: line.replace(/^#{1,3}\s+/, "") });
      inDial = false;
      continue;
    }
    if (/^(-{3,}|={3,}|_{3,}|\*{3,})$/.test(line)) {
      out.push({ t: "divider" });
      inDial = false;
      continue;
    }
    if (SUBTITLE_RE.test(line)) {
      out.push({ t: "subtitle", text: line });
      inDial = false;
      continue;
    }
    if (SCENE_RE.test(line)) {
      out.push({ t: "scene", text: line });
      inDial = false;
      continue;
    }
    if (/^\*\s+/.test(line)) {
      // Cast / list entry: bold the name up to the first ":" or "(".
      const body = line.replace(/^\*\s+/, "");
      const m = body.match(/^(.*?)(:|\s*\()/);
      const lead = (m ? body.slice(0, m[1].length) : body).replace(/\*/g, "").trim();
      const rest = m ? body.slice(m[1].length) : "";
      out.push({ t: "cast", lead, rest });
      inDial = false;
      continue;
    }
    // UPPERCASE line ending with ":" → section label (NHÂN VẬT:) or transition (CUT TO:).
    if (isUpper(line) && line.endsWith(":")) {
      out.push({ t: "section", text: line });
      inDial = false;
      continue;
    }
    if (/^\(.*\)$/.test(line)) {
      out.push({ t: "paren", text: line });
      continue; // stays within the dialogue block
    }
    // Character cue: a short all-caps line introducing dialogue.
    if (!inDial && isUpper(line) && line.length <= 40 && /\p{L}/u.test(line)) {
      out.push({ t: "character", text: line });
      inDial = true;
      continue;
    }
    if (inDial) {
      out.push({ t: "dialogue", text: line });
      continue;
    }
    out.push({ t: "action", text: line });
  }
  return out;
}

// Minimal inline emphasis: **bold**, *italic*, _underline_.
function inline(text: string): ReactNode {
  const nodes: ReactNode[] = [];
  const re = /\*\*(.+?)\*\*|\*(.+?)\*|_(.+?)_/g;
  let last = 0;
  let k = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text))) {
    if (m.index > last) nodes.push(text.slice(last, m.index));
    if (m[1] != null) nodes.push(<strong key={k++}>{m[1]}</strong>);
    else if (m[2] != null) nodes.push(<em key={k++}>{m[2]}</em>);
    else if (m[3] != null) nodes.push(<u key={k++}>{m[3]}</u>);
    last = re.lastIndex;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes;
}

function BlockView({ b }: { b: Block }) {
  switch (b.t) {
    case "title":
      return <h1 className="mb-1 mt-1 text-center text-lg font-bold uppercase tracking-wide">{b.text}</h1>;
    case "subtitle":
      return <p className="mb-7 text-center text-xs uppercase tracking-[0.35em] text-neutral-600">{b.text}</p>;
    case "section":
      return <p className="mb-2 mt-7 text-right text-sm font-bold uppercase tracking-wider">{b.text}</p>;
    case "cast":
      return (
        <p className="mb-1 pl-4 -indent-4">
          <span className="text-neutral-400">▸ </span>
          <strong>{b.lead}</strong>
          {b.rest ? inline(b.rest) : null}
        </p>
      );
    case "divider":
      return <div className="my-7 text-center tracking-[0.5em] text-neutral-400">• • •</div>;
    case "scene":
      return <p className="mb-3 mt-7 font-bold uppercase">{b.text}</p>;
    case "character":
      return <p className="mt-4 pl-[38%] font-bold uppercase">{b.text}</p>;
    case "paren":
      return <p className="pl-[30%] italic text-neutral-700">{b.text}</p>;
    case "dialogue":
      return <p className="pl-[24%] pr-[14%]">{inline(b.text)}</p>;
    default:
      return <p className="my-2">{inline(b.text)}</p>;
  }
}

export default function ScreenplayPreview({ script }: { script: string }) {
  const blocks = useMemo(() => parse(script), [script]);
  const empty = !script.trim();

  return (
    <div className="absolute inset-0 overflow-auto rounded-xl bg-neutral-800/40 px-4 py-6">
      <div
        className="mx-auto max-w-3xl rounded-md bg-[#f5f2e9] px-14 pt-12 text-[13px] leading-6 text-neutral-900 shadow-2xl"
        style={{ fontFamily: '"Courier Prime", "Courier New", ui-monospace, monospace', paddingBottom: 200 }}
      >
        {empty ? (
          <p className="py-16 text-center text-neutral-400">Chưa có kịch bản để hiển thị.</p>
        ) : (
          blocks.map((b, i) => <BlockView key={i} b={b} />)
        )}
      </div>
    </div>
  );
}
