import { create } from "zustand";

type RightTab = "copilot" | "design" | "assembly" | "hardware";

interface UiState {
  projectId: string | null;
  selectedId: string | null;
  tab: RightTab;
  designId: string | null;
  toast: { kind: "error" | "info" | "ok"; text: string; hint?: string } | null;
  setProject: (id: string | null) => void;
  select: (id: string | null) => void;
  setTab: (t: RightTab) => void;
  setDesign: (id: string | null) => void;
  notify: (t: UiState["toast"]) => void;
}

function stored(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

export const useUi = create<UiState>((set) => ({
  projectId: stored("buildloop.project"),
  selectedId: null,
  tab: "copilot",
  designId: null,
  toast: null,
  setProject: (id) => {
    try {
      if (id) localStorage.setItem("buildloop.project", id);
      else localStorage.removeItem("buildloop.project");
    } catch {
      /* storage unavailable */
    }
    set({ projectId: id, selectedId: null, designId: null });
  },
  select: (id) => set({ selectedId: id }),
  setTab: (tab) => set({ tab }),
  setDesign: (designId) => set({ designId }),
  notify: (toast) => set({ toast }),
}));
