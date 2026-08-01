import { FileSystemAdapter, Modal, Notice, Plugin, Setting, TFile, type App } from "obsidian";

import { DeepLawClient, canonicalVaultRelativePath, sanitizeProviderValue, type CommandResult } from "./deeplaw-client";
import { DEFAULT_SETTINGS, DeepLawSettingTab, type DeepLawSettings } from "./settings";

export default class DeepLawPlugin extends Plugin {
  settings: DeepLawSettings = DEFAULT_SETTINGS;
  private layoutReady = false;

  async onload(): Promise<void> {
    this.settings = Object.assign({}, DEFAULT_SETTINGS, await this.loadData());
    this.addSettingTab(new DeepLawSettingTab(this.app, this));
    this.registerCommands();
    this.app.workspace.onLayoutReady(() => {
      this.layoutReady = true;
      this.registerEvent(this.app.vault.on("create", (file) => {
        if (file instanceof TFile && file.path.startsWith("sources/inbox/")) {
          new Notice("DeepLaw: source inbox item is ready for explicit registration.");
        }
      }));
    });
  }

  async saveSettings(): Promise<void> {
    const serialized = { ...this.settings };
    if (/token|secret|private.?key/i.test(JSON.stringify(serialized))) {
      throw new Error("DeepLaw settings may not contain capability tokens or signing keys");
    }
    await this.saveData(serialized);
  }

  private client(): DeepLawClient {
    if (!this.layoutReady) throw new Error("Obsidian workspace layout is not ready");
    return new DeepLawClient(this.settings.executable, this.settings.vaultPath);
  }

  private activeFile(): TFile {
    const file = this.app.workspace.getActiveFile();
    if (!file) throw new Error("No active Obsidian file is available");
    return file;
  }

  private sourceAbsolutePath(file: TFile): string {
    const adapter = this.app.vault.adapter;
    if (!(adapter instanceof FileSystemAdapter)) throw new Error("Desktop filesystem Vault is required");
    return adapter.getFullPath(canonicalVaultRelativePath(file.path));
  }

  private async execute(label: string, action: () => Promise<unknown>): Promise<void> {
    try {
      const result = await action();
      if (result && typeof result === "object" && "value" in result) {
        new ResultModal(this.app, label, (result as CommandResult).value).open();
      }
      new Notice(`DeepLaw: ${label} completed.`);
    } catch (error) {
      const message = error instanceof Error ? error.message : "operation failed";
      new Notice(`DeepLaw: ${label} failed closed (${message}).`);
    }
  }

  private registerCommands(): void {
    const command = (id: string, name: string, callback: () => Promise<void>) =>
      this.addCommand({ id, name, callback });
    command("register-current-source", "Register current file as Source", async () =>
      this.execute("source registration", () => this.client().ingestSource(this.sourceAbsolutePath(this.activeFile()))));
    command("register-source-inbox", "Register files from sources/inbox", async () => {
      const files = this.app.vault.getFiles().filter((file) => file.path.startsWith("sources/inbox/"));
      for (const file of files) await this.client().ingestSource(this.sourceAbsolutePath(file));
    });
    command("list-uncompiled", "List uncompiled Sources", async () =>
      this.execute("source status", () => this.client().compilationStatus()));
    command("begin-compilation", "Begin Living Wiki compilation", async () =>
      this.execute("compilation begin", async () => {
        const sourceRevisionId = await this.prompt("Exact Source Revision ID");
        return this.client().beginCompilation(sourceRevisionId, this.settings.compilerGrantId);
      }));
    command("compilation-status", "Display Compilation Run status", async () =>
      this.execute("compilation status", async () =>
        this.client().compilationStatus(await this.prompt("Exact Compilation Run ID"))));
    command("resume-projection", "Resume projection", async () =>
      this.execute("projection resume", async () =>
        this.client().resumeProjection(await this.prompt("Exact Compilation Run ID"), this.settings.compilerGrantId)));
    command("list-stale", "List stale knowledge and Syntheses", async () =>
      this.execute("stale knowledge listing", () => this.client().gaps()));
    command("trigger-refresh", "Trigger explicit refresh", async () =>
      this.execute("refresh", async () => {
        const sourceRevisionId = await this.prompt("Stale Source Revision ID");
        const replacement = await this.prompt("Replacement Source Revision ID (or '-' for none)");
        return this.client().refresh(
          sourceRevisionId,
          this.settings.compilerGrantId,
          replacement === "-" ? undefined : replacement
        );
      }));
    command("context-active-note", "Compile context for active note", async () =>
      this.execute("active note context", () => this.client().context(`Active note: ${this.activeFile().basename}`)));
    this.addCommand({
      id: "context-selection",
      name: "Compile context for selected text",
      editorCallback: (editor) => {
        const selection = editor.getSelection().trim();
        void this.execute("selection context", () => this.client().context(selection));
      }
    });
    command("open-source-page", "Open Source page", async () => this.openGenerated("wiki/sources"));
    command("open-knowledge-page", "Open Entity, Concept, or Synthesis page", async () => this.openGenerated("wiki"));
    command("display-gaps", "Display gaps", async () => this.execute("gap report", () => this.client().gaps()));
    command("display-contradictions", "Display contradictions", async () =>
      this.execute("contradiction report", () => this.client().query("contradictions", "verify")));
    command("display-freshness", "Display freshness", async () => this.execute("freshness", () => this.client().gaps()));
    command("verify-vault", "Verify DeepLaw Vault", async () => this.execute("verification", () => this.client().verify()));
    command("open-canvas", "Open generated Canvas", async () => this.openGenerated("canvas"));
    command("show-evidence-locator", "Show read-only evidence locator", async () =>
      this.execute("evidence locator", () => this.client().query(this.activeFile().basename, "quote")));
  }

  private async openGenerated(prefix: string): Promise<void> {
    const file = this.app.vault.getFiles().find((candidate) => candidate.path.startsWith(`${prefix}/`));
    if (!file) throw new Error("No generated DeepLaw view is available");
    await this.app.workspace.getLeaf(false).openFile(file);
  }

  private prompt(label: string): Promise<string> {
    return new Promise((resolve, reject) => new PromptModal(this.app, label, resolve, reject).open());
  }
}

class PromptModal extends Modal {
  private value = "";

  constructor(
    app: App,
    private readonly label: string,
    private readonly resolveValue: (value: string) => void,
    private readonly rejectValue: (error: Error) => void
  ) {
    super(app);
  }

  onOpen(): void {
    this.titleEl.setText(this.label);
    new Setting(this.contentEl).addText((text) => text.onChange((value) => { this.value = value.trim(); }));
    new Setting(this.contentEl).addButton((button) => button.setButtonText("Continue").setCta().onClick(() => {
      if (!this.value) return;
      this.resolveValue(this.value);
      this.close();
    }));
  }

  onClose(): void {
    if (!this.value) this.rejectValue(new Error("Owner cancelled the explicit operation"));
    this.contentEl.empty();
  }
}

class ResultModal extends Modal {
  constructor(app: App, private readonly label: string, private readonly value: unknown) {
    super(app);
  }

  onOpen(): void {
    this.titleEl.setText(`DeepLaw: ${this.label}`);
    const pre = this.contentEl.createEl("pre", { cls: "deeplaw-result deeplaw-readonly" });
    pre.setText(JSON.stringify(sanitizeProviderValue(this.value), null, 2));
  }

  onClose(): void {
    this.contentEl.empty();
  }
}
