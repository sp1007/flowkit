import { useState } from "react";
import { type Project } from "./api/client";
import StatusPills from "./components/StatusPills";
import ProjectGrid from "./components/ProjectGrid";
import ProjectWorkspace from "./components/ProjectWorkspace";
import AllImages from "./components/AllImages";
import SettingsDrawer from "./components/settings/SettingsDrawer";

type Home = "projects" | "images";

export default function App() {
  const [open, setOpen] = useState<Project | null>(null);
  const [home, setHome] = useState<Home>("projects");
  const [settings, setSettings] = useState(false);

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center justify-between border-b border-neutral-800 px-6 py-3">
        <button
          onClick={() => setOpen(null)}
          className="flex items-center gap-2 text-lg font-semibold tracking-tight"
        >
          <span className="grid h-7 w-7 place-items-center rounded-lg bg-gradient-to-br from-indigo-500 to-fuchsia-500 text-sm text-white">
            ▶
          </span>
          Flow Studio
        </button>

        {!open && (
          <nav className="flex gap-1 rounded-xl bg-neutral-900 p-1">
            <button
              onClick={() => setHome("projects")}
              className={`rounded-lg px-3 py-1.5 text-sm transition ${
                home === "projects" ? "bg-neutral-700 text-white" : "text-neutral-400 hover:text-neutral-200"
              }`}
            >
              Dự án
            </button>
            <button
              onClick={() => setHome("images")}
              className={`rounded-lg px-3 py-1.5 text-sm transition ${
                home === "images" ? "bg-neutral-700 text-white" : "text-neutral-400 hover:text-neutral-200"
              }`}
            >
              🖼 Tất cả ảnh
            </button>
          </nav>
        )}

        <div className="flex items-center gap-3">
          <StatusPills />
          <button
            onClick={() => setSettings(true)}
            title="Settings"
            className="grid h-8 w-8 place-items-center rounded-lg text-neutral-400 hover:bg-neutral-800 hover:text-neutral-200"
          >
            ⚙
          </button>
        </div>
      </header>

      <main className={`flex-1 ${open ? "overflow-hidden" : "overflow-auto"}`}>
        {open ? (
          <ProjectWorkspace project={open} onBack={() => setOpen(null)} />
        ) : home === "images" ? (
          <AllImages />
        ) : (
          <ProjectGrid onOpen={setOpen} />
        )}
      </main>

      {settings && <SettingsDrawer onClose={() => setSettings(false)} />}
    </div>
  );
}
