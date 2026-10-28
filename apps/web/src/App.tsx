import * as Tabs from "@radix-ui/react-tabs";
import { useQuery } from "@tanstack/react-query";
import { Panel as RPanel, PanelGroup, PanelResizeHandle } from "react-resizable-panels";
import { api, ApiFail } from "./api";
import Assembly from "./components/Assembly";
import Copilot from "./components/Copilot";
import Hardware from "./components/Hardware";
import Header from "./components/Header";
import Inspector from "./components/Inspector";
import Inventory from "./components/Inventory";
import Timeline from "./components/Timeline";
import Twin from "./components/Twin";
import { Toast } from "./components/ui";
import Workspace from "./components/Workspace";
import { useUi } from "./store";

const TABS = [
  ["copilot", "Copilot"],
  ["design", "Design"],
  ["assembly", "Assembly"],
  ["hardware", "Hardware"],
] as const;

export default function App() {
  const pid = useUi((s) => s.projectId);
  const tab = useUi((s) => s.tab);
  const setTab = useUi((s) => s.setTab);
  const q = useQuery({ queryKey: ["view", pid], queryFn: () => api.view(pid!), enabled: !!pid, refetchInterval: 2500 });
  const view = q.data;
  const offline = q.error instanceof ApiFail && q.error.code === "backend_unreachable";

  return (
    <div className="flex h-full flex-col">
      <Header project={view?.project} />
      {offline && (
        <div role="alert" className="border-b border-bad/50 bg-bad/10 px-3 py-1.5 text-xs text-bad">
          Backend unreachable. Start it with <code className="font-mono">scripts\dev.ps1</code>.
        </div>
      )}
      {!pid ? (
        <main className="flex flex-1 flex-col items-center justify-center gap-6 p-6">
          <div className="text-center">
            <h1 className="text-lg font-semibold tracking-tight">Create a project to begin</h1>
            <p className="mt-1 max-w-xl text-sm text-dim">BuildLoop keeps a digital twin of your bench, designs with Meta Muse Spark, validates with deterministic rules and verifies on real hardware.</p>
          </div>
          <div className="grid w-full max-w-3xl gap-3 md:grid-cols-2">
            <div className="rounded-lg border border-line bg-panel p-4">
              <p className="text-sm font-semibold">Demo project</p>
              <p className="mt-1 text-xs text-dim">Synthetic frame, deterministic planner and a simulated board. Everything is labelled. No credentials needed.</p>
            </div>
            <div className="rounded-lg border border-line bg-panel p-4">
              <p className="text-sm font-semibold">Real project</p>
              <p className="mt-1 text-xs text-dim">Live Meta SAM 3.1 and Muse Spark, your camera and your ESP32. Needs <code className="font-mono">MODEL_API_KEY</code> in the backend.</p>
            </div>
          </div>
          <p className="text-xs text-faint">Use the buttons in the header to create one.</p>
        </main>
      ) : (
        <PanelGroup direction="horizontal" className="min-h-0 flex-1" autoSaveId="buildloop-main">
          <RPanel defaultSize={34} minSize={22}>
            <PanelGroup direction="vertical" autoSaveId="buildloop-left">
              <RPanel defaultSize={62} minSize={30}>
                <Workspace view={view} />
              </RPanel>
              <PanelResizeHandle className="resize-handle h-px" />
              <RPanel defaultSize={38} minSize={15}>
                <Inventory view={view} />
              </RPanel>
            </PanelGroup>
          </RPanel>
          <PanelResizeHandle className="resize-handle w-px" />
          <RPanel defaultSize={32} minSize={20}>
            <PanelGroup direction="vertical" autoSaveId="buildloop-mid">
              <RPanel defaultSize={64} minSize={25}>
                <Twin view={view} />
              </RPanel>
              <PanelResizeHandle className="resize-handle h-px" />
              <RPanel defaultSize={36} minSize={15}>
                <Timeline view={view} />
              </RPanel>
            </PanelGroup>
          </RPanel>
          <PanelResizeHandle className="resize-handle w-px" />
          <RPanel defaultSize={34} minSize={24}>
            <Tabs.Root value={tab} onValueChange={(v) => setTab(v as typeof tab)} className="flex h-full flex-col bg-panel">
              <Tabs.List className="flex h-9 shrink-0 border-b border-line" aria-label="Engineering panels">
                {TABS.map(([v, label]) => (
                  <Tabs.Trigger key={v} value={v} className="flex-1 border-b-2 border-transparent text-[11px] font-semibold uppercase tracking-wider text-dim data-[state=active]:border-accent data-[state=active]:text-accent">
                    {label}
                  </Tabs.Trigger>
                ))}
              </Tabs.List>
              <div className="min-h-0 flex-1 overflow-auto">
                <Tabs.Content value="copilot"><Copilot view={view} /></Tabs.Content>
                <Tabs.Content value="design"><Inspector view={view} /></Tabs.Content>
                <Tabs.Content value="assembly"><Assembly view={view} /></Tabs.Content>
                <Tabs.Content value="hardware"><Hardware view={view} /></Tabs.Content>
              </div>
            </Tabs.Root>
          </RPanel>
        </PanelGroup>
      )}
      <Toast />
    </div>
  );
}
