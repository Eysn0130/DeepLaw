import { TFile, type App, type EventRef } from "obsidian";

export type DeepLawBridge = {
  registerSource(relativePath: string): Promise<void>;
  refreshVisibleResult(): Promise<void>;
};

export function registerDeepLawBridge(app: App, bridge: DeepLawBridge): EventRef[] {
  const registrations: EventRef[] = [];
  app.workspace.onLayoutReady(() => {
    registrations.push(
      app.vault.on("create", async (file) => {
        if (file instanceof TFile && file.path.startsWith("sources/inbox/")) {
          await bridge.registerSource(file.path);
        }
      }),
    );
    registrations.push(
      app.vault.on("modify", async (file) => {
        if (file instanceof TFile && file.path.startsWith("drafts/")) {
          await bridge.refreshVisibleResult();
        }
      }),
    );
  });
  return registrations;
}
