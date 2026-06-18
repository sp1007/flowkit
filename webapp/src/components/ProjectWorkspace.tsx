import { useEffect, useState } from "react";
import { api, type Project } from "../api/client";
import ScriptTab from "./script/ScriptTab";
import AssetsTab from "./assets/AssetsTab";
import StoryboardTab from "./storyboard/StoryboardTab";
import ShotsTab from "./shots/ShotsTab";
import AssembleTab from "./assemble/AssembleTab";

const TABS = ["Script", "Assets", "Storyboard", "Shots", "Assemble"] as const;
type Tab = (typeof TABS)[number];

export default function ProjectWorkspace({
  project: initial,
  onBack,
}: {
  project: Project;
  onBack: () => void;
}) {
  const [tab, setTab] = useState<Tab>("Script");
  const [project, setProject] = useState(initial);
  const [style, setStyle] = useState(initial.style);

  // Fetch the full project (with script_raw) on open.
  useEffect(() => {
    api.getProject(initial.id).then(setProject).catch(() => {});
  }, [initial.id]);

  const saveStyle = async () => {
    if (style !== project.style) {
      try {
        await api.updateProject(project.id, { style });
      } catch {
        /* ignore */
      }
    }
  };

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-4 border-b border-neutral-800 px-6 py-3">
        <button
          onClick={onBack}
          className="rounded-lg px-2 py-1 text-sm text-neutral-400 hover:bg-neutral-800 hover:text-neutral-200"
        >
          ← Dự án
        </button>
        <div className="min-w-0">
          <div className="truncate font-medium">{project.title}</div>
        </div>
        <nav className="mx-auto flex gap-1 rounded-xl bg-neutral-900 p-1">
          {TABS.map((t, i) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`rounded-lg px-3 py-1.5 text-sm transition ${
                tab === t
                  ? "bg-neutral-700 text-white"
                  : "text-neutral-400 hover:text-neutral-200"
              }`}
            >
              <span className="mr-1 text-neutral-500">{i + 1}.</span>
              {t}
            </button>
          ))}
        </nav>
        <div className="flex items-center gap-2">
          <span className="text-xs text-neutral-500">Style</span>
          <input
            value={style}
            onChange={(e) => setStyle(e.target.value)}
            onBlur={saveStyle}
            className="w-44 rounded-lg border border-neutral-700 bg-neutral-950 px-2.5 py-1.5 text-sm outline-none focus:border-indigo-500"
          />
        </div>
      </div>

      <div className="flex-1 overflow-hidden">
        {tab === "Script" ? (
          <ScriptTab key={project.id} project={project} />
        ) : tab === "Assets" ? (
          <AssetsTab key={project.id} project={project} />
        ) : tab === "Storyboard" ? (
          <StoryboardTab key={project.id} project={project} />
        ) : tab === "Shots" ? (
          <ShotsTab key={project.id} project={project} />
        ) : (
          <AssembleTab key={project.id} project={project} />
        )}
      </div>
    </div>
  );
}
