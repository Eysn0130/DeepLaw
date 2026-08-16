import {
  FileSystemAdapter,
  MarkdownView,
  Modal,
  Notice,
  Plugin,
  Setting,
  SuggestModal,
  TFile,
  type App,
  type Editor
} from "obsidian";

import {
  assertWritableRelativePath,
  DeepLawClient,
  canonicalVaultRelativePath,
  canonicalWikiPath,
  extractFragmentIds,
  parseAgentContextEnvelope,
  parseCompilationRunPickerItems,
  parseQueryV6Response,
  parseSourcePickerItems,
  sanitizeProviderValue,
  type AgentContextOptions,
  type CommandResult,
  type CompilationRunPickerItem,
  type ContextPurpose,
  type SourcePickerItem
} from "./deeplaw-client";
import { DEFAULT_SETTINGS, DeepLawSettingTab, type DeepLawSettings } from "./settings";

interface PickerOption<T> {
  readonly value: T;
  readonly label: string;
  readonly metadata: readonly string[];
}

interface WikiFileOption {
  readonly file: TFile;
  readonly label: string;
}

interface FragmentOption {
  readonly id: string;
  readonly label: string;
  readonly metadata: readonly string[];
}

interface LinkPage {
  readonly value: Record<string, unknown>;
  readonly links: readonly string[];
  readonly totalCount: number;
  readonly cursor: string | null;
}

function isCommandResult(value: unknown): value is CommandResult {
  return Boolean(
    value &&
    typeof value === "object" &&
    !Array.isArray(value) &&
    "value" in value &&
    typeof value.value === "object" &&
    value.value !== null &&
    !Array.isArray(value.value)
  );
}

function asRecord(value: unknown, label: string): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`${label} is not a bounded JSON object`);
  }
  return value as Record<string, unknown>;
}

function boundedString(value: unknown, label: string, maximum: number): string {
  if (typeof value !== "string" || value.length === 0 || value.length > maximum || /[\0\u0001-\u001f\u007f]/.test(value)) {
    throw new Error(`${label} is invalid or exceeds its bound`);
  }
  return value;
}

function boundedText(value: unknown, label: string, maximum: number): string {
  if (typeof value !== "string" || value.length === 0 || value.length > maximum || /[\0\u0001-\u0008\u000b\u000c\u000e-\u001f\u007f\r]/.test(value)) {
    throw new Error(`${label} is invalid or exceeds its bound`);
  }
  return value;
}

function wikiPathFromResponse(value: unknown): string {
  const root = asRecord(value, "Wiki page response");
  return canonicalWikiPath(boundedString(root.wiki_path, "Wiki page response wiki_path", 500));
}

function parseLinkPage(value: unknown): LinkPage {
  const root = asRecord(value, "Wiki links response");
  const rawLinks = root.links;
  if (!Array.isArray(rawLinks) || rawLinks.length > 20) throw new Error("Wiki links response is invalid");
  const links = rawLinks.map((item, index) => boundedString(item, `Wiki links response links[${index}]`, 500));
  const totalCount = root.total_count;
  if (typeof totalCount !== "number" || !Number.isSafeInteger(totalCount) || totalCount < 0 || totalCount > 1_000_000) {
    throw new Error("Wiki links response total_count is invalid");
  }
  const cursor = root.cursor === null || root.cursor === undefined
    ? null
    : boundedString(root.cursor, "Wiki links response cursor", 256);
  return { value: root, links, totalCount, cursor };
}

function fragmentContext(value: unknown, fragmentId: string): { locator?: string; excerpt?: string } {
  if (Array.isArray(value)) {
    for (const item of value) {
      const match = fragmentContext(item, fragmentId);
      if (match.locator || match.excerpt) return match;
    }
    return {};
  }
  if (!value || typeof value !== "object") return {};
  const record = value as Record<string, unknown>;
  if (record.fragment_id === fragmentId || record.fragment_revision_id === fragmentId) {
    const result: { locator?: string; excerpt?: string } = {};
    if (typeof record.locator === "string") result.locator = record.locator;
    if (typeof record.excerpt === "string") result.excerpt = record.excerpt;
    else if (typeof record.text === "string") result.excerpt = record.text;
    return result;
  }
  for (const item of Object.values(record)) {
    const match = fragmentContext(item, fragmentId);
    if (match.locator || match.excerpt) return match;
  }
  return {};
}

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
    return adapter.getFullPath(assertWritableRelativePath(file.path));
  }

  private async execute(label: string, action: () => Promise<unknown>): Promise<void> {
    try {
      const result = await action();
      const value = isCommandResult(result) ? result.value : result;
      if (value !== undefined) new ResultModal(this.app, label, value).open();
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
      await this.execute("source inbox registration", async () => {
        const files = this.app.vault.getFiles().filter((file) => file.path.startsWith("sources/inbox/"));
        const receipts: Record<string, unknown>[] = [];
        for (const file of files) receipts.push((await this.client().ingestSource(this.sourceAbsolutePath(file))).value);
        return { sources_registered: receipts.length, receipts };
      });
    });
    command("list-uncompiled", "List Sources", async () =>
      this.execute("source status", () => this.client().sourceList()));
    command("begin-compilation", "Begin Living Wiki compilation", async () =>
      this.execute("compilation begin", async () => {
        const source = await this.pickSource("Select Source Revision to compile");
        return this.client().beginCompilation(source.sourceRevisionId, this.settings.compilerGrantId);
      }));
    command("compilation-status", "Display Compilation Run status", async () =>
      this.execute("compilation status", async () => {
        const run = await this.pickCompilationRun("Select Compilation Run");
        const status = await this.client().compilationStatus(run.compilationRunId);
        return this.compilationStatusView(status.value);
      }));
    command("resume-projection", "Resume projection", async () =>
      this.execute("projection resume", async () => {
        const run = await this.pickCompilationRun("Select Compilation Run to resume");
        return this.client().resumeProjection(run.compilationRunId, this.settings.compilerGrantId);
      }));
    command("list-stale", "List stale knowledge and Syntheses", async () =>
      this.execute("stale knowledge listing", () => this.client().gaps()));
    command("trigger-refresh", "Trigger explicit refresh", async () =>
      this.execute("refresh", async () => {
        const source = await this.pickSource("Select Source Revision to refresh");
        const replacement = await this.pickReplacementSource(source.sourceRevisionId);
        return this.client().refresh(
          source.sourceRevisionId,
          this.settings.compilerGrantId,
          replacement
        );
      }));
    command("context-active-note", "Preview bounded context for active note", async () =>
      this.execute("active note context", () => this.contextPreview(this.activeFile().basename)));
    this.addCommand({
      id: "context-selection",
      name: "Preview bounded context for selected text",
      editorCallback: (editor: Editor) => {
        const selection = editor.getSelection().trim();
        void this.execute("selection context", () => this.contextPreview(selection));
      }
    });
    command("display-duty-coverage", "Display Query v6 Duty Coverage", async () =>
      this.execute("duty coverage", async () => {
        const run = await this.pickCompilationRun("Select Compilation Run for Duty Coverage");
        const status = await this.client().compilationStatus(run.compilationRunId);
        return this.compilationDutyCoverageView(status.value);
      }));
    command("display-statement-evidence", "Display Statement Evidence", async () =>
      this.execute("statement evidence", async () => {
        const result = await this.client().query(this.activeFile().basename, "verify", "audit", this.queryOptions());
        return this.statementEvidenceView(result.value);
      }));
    command("drilldown-source-fragment", "Open exact Source Fragment from Query Evidence", async () =>
      this.execute("source fragment", async () => {
        const query = await this.client().query(this.activeFile().basename, "verify", "audit", this.queryOptions());
        const ids = extractFragmentIds(query.value);
        if (ids.length === 0) throw new Error("Query Evidence contains no admitted Source Fragment");
        const selected = await this.pickFragment(ids, query.value);
        return this.client().sourceFragment(selected.id);
      }));
    command("open-source-page", "Open exact Wiki page", async () => this.openWikiPage());
    command("open-knowledge-page", "Open exact Knowledge Wiki page", async () => this.openWikiPage());
    command("wiki-backlinks", "Show Wiki backlinks (paged)", async () => this.showWikiLinks("backlinks"));
    command("wiki-outlinks", "Show Wiki outlinks (paged)", async () => this.showWikiLinks("outlinks"));
    command("display-gaps", "Display gaps", async () => this.execute("gap report", () => this.client().gaps()));
    command("display-contradictions", "Display contradictions", async () =>
      this.execute("contradiction report", async () =>
        (await this.client().query(this.activeFile().basename, "verify", "audit", this.queryOptions())).value));
    command("display-freshness", "Display freshness", async () =>
      this.execute("freshness", async () =>
        (await this.client().query(this.activeFile().basename, "freshness_check", "audit", this.queryOptions())).value));
    command("verify-vault", "Verify DeepLaw Vault", async () => this.execute("verification", () => this.client().verify()));
    command("open-canvas", "Open generated Canvas", async () => this.openCanvas());
    command("show-evidence-locator", "Show read-only evidence locator", async () =>
      this.execute("evidence locator", async () =>
        (await this.client().query(this.activeFile().basename, "quote", "audit", this.queryOptions())).value));
  }

  private queryOptions(): { scope: DeepLawSettings["defaultScope"]; maxSensitivity: DeepLawSettings["maxSensitivity"] } {
    return { scope: this.settings.defaultScope, maxSensitivity: this.settings.maxSensitivity };
  }

  private workspaceIdentity(): string {
    const slug = this.app.vault.getName().trim().replace(/[^A-Za-z0-9._-]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 200);
    return `obsidian-workspace-${slug || "vault"}`;
  }

  private repositoryIdentity(): string {
    const slug = this.app.vault.getName().trim().replace(/[^A-Za-z0-9._-]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 200);
    return `obsidian-repository-${slug || "vault"}`;
  }

  private contextOptions(task: string, selectedText?: string): AgentContextOptions {
    const file = this.activeFile();
    const activeFiles = [canonicalVaultRelativePath(file.path)];
    const currentNote = activeFiles[0];
    if (!currentNote) throw new Error("Active note path is unavailable");
    const openTabs = this.app.workspace.getLeavesOfType("markdown")
      .map((leaf) => leaf.view instanceof MarkdownView && leaf.view.file ? canonicalVaultRelativePath(leaf.view.file.path) : null)
      .filter((path): path is string => path !== null);
    return {
      task,
      workspaceIdentity: this.workspaceIdentity(),
      repositoryIdentity: this.repositoryIdentity(),
      activeFiles,
      openTabs: [...new Set(openTabs)].slice(0, 32),
      currentNote,
      ...(selectedText ? { selectedText: selectedText.slice(0, 12_000) } : {}),
      purpose: "answer",
      scope: this.settings.defaultScope,
      maxSensitivity: this.settings.maxSensitivity,
      maxTokens: 4_000
    };
  }

  private async contextPreview(task: string, selectedText?: string): Promise<Record<string, unknown>> {
    const boundedTask = boundedText(task.trim(), "Context task", 5_000);
    const context = await this.client().agentContext(this.contextOptions(boundedTask, selectedText));
    const query = await this.client().query(boundedTask, "answer", "compact", this.queryOptions());
    const envelope = parseAgentContextEnvelope(context.value);
    const queryValue = parseQueryV6Response(query.value);
    return {
      agent_context: envelope,
      query_v6: queryValue,
      capsule: queryValue.capsule
    };
  }

  private compilationStatusView(value: unknown): Record<string, unknown> {
    const root = asRecord(value, "Compilation status response");
    const transaction = root.transaction;
    const semantic = root.semantic_status;
    const freshness = root.freshness ?? (transaction && typeof transaction === "object" && !Array.isArray(transaction)
      ? (transaction as Record<string, unknown>).freshness
      : null);
    const verification = root.verification ?? (transaction && typeof transaction === "object" && !Array.isArray(transaction)
      ? (transaction as Record<string, unknown>).verification
      : null);
    return {
      schema_version: root.schema_version,
      current_transaction: transaction,
      current_semantic: semantic,
      freshness,
      verification,
      duty_reports: root.duty_reports,
      gaps: root.gaps,
      raw_status: root
    };
  }

  private compilationDutyCoverageView(value: unknown): Record<string, unknown> {
    const root = asRecord(value, "Compilation status response");
    return {
      schema_version: root.schema_version,
      duty_coverage: {
        semantic_status: root.semantic_status,
        duty_reports: root.duty_reports,
        gaps: root.gaps
      },
      raw_status: root
    };
  }

  private statementEvidenceView(value: unknown): Record<string, unknown> {
    const root = asRecord(value, "Query v6 response");
    return {
      schema_version: root.schema_version,
      statement_evidence: {
        statements: root.statements,
        evidence: root.evidence,
        contradictions: root.contradictions
      },
      gaps: root.gaps,
      raw_query: root
    };
  }

  private async pickSource(title: string): Promise<SourcePickerItem> {
    const response = await this.client().sourceList();
    const sources = parseSourcePickerItems(response.value);
    return this.pick(title, sources.map((source) => ({
      value: source,
      label: source.title || source.logicalPath || "Untitled Source Revision",
      metadata: [
        source.logicalPath || "path unavailable",
        `status: ${source.status || "unknown"}`,
        `trust: ${source.trust || "unknown"}`,
        `sensitivity: ${source.sensitivity || "unknown"}`,
        ...(source.warnings.length ? [`warnings: ${source.warnings.join("; ")}`] : [])
      ]
    })));
  }

  private async pickReplacementSource(sourceRevisionId: string): Promise<string | undefined> {
    const response = await this.client().sourceList();
    const sources = parseSourcePickerItems(response.value).filter((source) => source.sourceRevisionId !== sourceRevisionId);
    const options: PickerOption<string | null>[] = [
      { value: null, label: "No replacement Source Revision", metadata: ["Keep the selected source revision"] },
      ...sources.map((source) => ({
        value: source.sourceRevisionId,
        label: source.title || source.logicalPath || "Untitled Source Revision",
        metadata: [source.logicalPath || "path unavailable", `status: ${source.status || "unknown"}`]
      }))
    ];
    const selected = await this.pick("Select replacement Source Revision", options);
    return selected ?? undefined;
  }

  private async pickCompilationRun(title: string): Promise<CompilationRunPickerItem> {
    const response = await this.client().compilationList();
    const runs = parseCompilationRunPickerItems(response.value);
    const sourceResponse = await this.client().sourceList();
    const sourceByRevision = new Map(
      parseSourcePickerItems(sourceResponse.value).map((source) => [source.sourceRevisionId, source])
    );
    return this.pick(title, runs.map((run) => ({
      value: run,
      label: `${sourceByRevision.get(run.sourceRevisionId)?.title || sourceByRevision.get(run.sourceRevisionId)?.logicalPath || "Source Revision"} · ${run.status || "unknown"}`,
      metadata: [
        `source: ${sourceByRevision.get(run.sourceRevisionId)?.logicalPath || "path unavailable"}`,
        `compiler: ${run.compilerProfile} v${run.compilerProfileVersion}`,
        `packets: ${run.packetCount}`,
        `created: ${run.createdAt}`,
        `updated: ${run.updatedAt}`
      ]
    })));
  }

  private async pickFragment(ids: readonly string[], queryValue: unknown): Promise<FragmentOption> {
    const options: PickerOption<FragmentOption>[] = ids.map((id, index) => {
      const context = fragmentContext(queryValue, id);
      const excerpt = context.excerpt?.replace(/\s+/g, " ").slice(0, 120);
      const option: FragmentOption = {
        id,
        label: `Evidence fragment ${index + 1}`,
        metadata: [context.locator || "locator unavailable", excerpt || "exact source text available after selection"]
      };
      return { value: option, label: option.label, metadata: option.metadata };
    });
    return this.pick("Select Source Fragment evidence", options);
  }

  private async pick<T>(title: string, options: readonly PickerOption<T>[]): Promise<T> {
    if (options.length === 0) throw new Error("DeepLaw picker has no admitted choices");
    return new Promise<T>((resolve, reject) => {
      new DeepLawPickerModal(this.app, title, options, resolve, reject).open();
    });
  }

  private async openWikiPage(): Promise<void> {
    try {
      const selected = await this.pickWikiFile("Select exact Wiki page");
      const response = await this.client().wikiPage(selected.file.path);
      const canonical = wikiPathFromResponse(response.value);
      const file = this.app.vault.getFileByPath(canonical);
      if (!(file instanceof TFile)) throw new Error("Canonical Wiki page is not present in this Vault");
      await this.app.workspace.getLeaf(false).openFile(file);
    } catch (error) {
      const message = error instanceof Error ? error.message : "Wiki page operation failed";
      new Notice(`DeepLaw: Wiki page failed closed (${message}).`);
    }
  }

  private async showWikiLinks(direction: "backlinks" | "outlinks"): Promise<void> {
    try {
      const selected = await this.pickWikiFile(`Select exact Wiki page for ${direction}`);
      const page = await this.client().wikiPage(selected.file.path);
      const canonical = wikiPathFromResponse(page.value);
      const response = await this.client().wikiLinks(direction, canonical);
      const parsed = parseLinkPage(response.value);
      new WikiLinksModal(this.app, direction, canonical, parsed, async (cursor) => {
        const next = await this.client().wikiLinks(direction, canonical, cursor);
        return parseLinkPage(next.value);
      }).open();
    } catch (error) {
      const message = error instanceof Error ? error.message : "Wiki links operation failed";
      new Notice(`DeepLaw: Wiki ${direction} failed closed (${message}).`);
    }
  }

  private async pickWikiFile(title: string): Promise<WikiFileOption> {
    const files = this.app.vault.getMarkdownFiles()
      .filter((file) => file.path.startsWith("wiki/"))
      .sort((left, right) => left.path.localeCompare(right.path));
    return this.pick(title, files.map((file) => ({
      value: { file, label: file.path },
      label: file.path,
      metadata: ["select to verify canonical Wiki identity before opening"]
    })));
  }

  private async openCanvas(): Promise<void> {
    try {
      const files = this.app.vault.getFiles()
        .filter((file) => file.path.startsWith("canvas/"))
        .sort((left, right) => left.path.localeCompare(right.path));
      const selected = await this.pick("Select generated Canvas", files.map((file) => ({
        value: file,
        label: file.path,
        metadata: ["read-only generated view"]
      })));
      await this.app.workspace.getLeaf(false).openFile(selected);
    } catch (error) {
      const message = error instanceof Error ? error.message : "Canvas operation failed";
      new Notice(`DeepLaw: Canvas failed closed (${message}).`);
    }
  }
}

class DeepLawPickerModal<T> extends SuggestModal<PickerOption<T>> {
  constructor(
    app: App,
    private readonly pickerTitle: string,
    private readonly options: readonly PickerOption<T>[],
    private readonly resolveValue: (value: T) => void,
    private readonly rejectValue: (error: Error) => void
  ) {
    super(app);
    this.setPlaceholder("Search human-readable DeepLaw choices");
    this.emptyStateText = "No admitted DeepLaw choice matches this search.";
  }

  private chosen = false;

  onOpen(): void {
    this.titleEl.setText(this.pickerTitle);
  }

  getSuggestions(query: string): PickerOption<T>[] {
    const normalized = query.trim().toLocaleLowerCase();
    return this.options
      .filter((option) => !normalized || `${option.label} ${option.metadata.join(" ")}`.toLocaleLowerCase().includes(normalized))
      .slice(0, 200);
  }

  renderSuggestion(option: PickerOption<T>, element: HTMLElement): void {
    const label = element.createDiv({ cls: "deeplaw-picker-label" });
    label.setText(option.label);
    for (const metadata of option.metadata) {
      const line = element.createDiv({ cls: "deeplaw-picker-meta" });
      line.setText(metadata);
    }
  }

  onChooseSuggestion(option: PickerOption<T>): void {
    this.chosen = true;
    this.resolveValue(option.value);
    this.close();
  }

  onClose(): void {
    if (!this.chosen) this.rejectValue(new Error("Owner cancelled the explicit operation"));
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
    pre.setText(JSON.stringify(sanitizeProviderValue(this.value), null, 2) ?? "null");
  }

  onClose(): void {
    this.contentEl.empty();
  }
}

class WikiLinksModal extends Modal {
  private readonly pages: LinkPage[];
  private loading = false;

  constructor(
    app: App,
    private readonly direction: "backlinks" | "outlinks",
    private readonly wikiPath: string,
    initial: LinkPage,
    private readonly loadPage: (cursor: string) => Promise<LinkPage>
  ) {
    super(app);
    this.pages = [initial];
  }

  onOpen(): void {
    this.render();
  }

  private render(): void {
    this.contentEl.empty();
    this.titleEl.setText(`DeepLaw: ${this.direction}`);
    const heading = this.contentEl.createEl("div", { cls: "deeplaw-links-heading" });
    heading.setText(`${this.wikiPath} · ${this.direction}`);
    const latest = this.pages[this.pages.length - 1];
    if (!latest) return;
    const total = this.contentEl.createEl("div", { cls: "deeplaw-pagination" });
    total.setText(`total_count: ${latest.totalCount} · cursor: ${latest.cursor ?? "none"}`);
    const pre = this.contentEl.createEl("pre", { cls: "deeplaw-result deeplaw-readonly" });
    const links = this.pages.flatMap((page) => page.links);
    pre.setText(JSON.stringify(sanitizeProviderValue({
      schema_version: latest.value.schema_version,
      wiki_path: this.wikiPath,
      direction: this.direction,
      links,
      total_count: latest.totalCount,
      cursor: latest.cursor,
      truncated: latest.value.truncated
    }), null, 2) ?? "null");
    if (latest.cursor) {
      new Setting(this.contentEl).addButton((button) => button
        .setButtonText(this.loading ? "Loading…" : "Load next page")
        .setDisabled(this.loading)
        .onClick(() => { void this.loadNext(button); }));
    }
  }

  private async loadNext(button: { setDisabled(disabled: boolean): unknown }): Promise<void> {
    const latest = this.pages[this.pages.length - 1];
    if (!latest?.cursor || this.loading) return;
    this.loading = true;
    button.setDisabled(true);
    try {
      this.pages.push(await this.loadPage(latest.cursor));
      this.loading = false;
      this.render();
    } catch (error) {
      const message = error instanceof Error ? error.message : "Wiki pagination failed";
      new Notice(`DeepLaw: Wiki pagination failed closed (${message}).`);
      this.loading = false;
      this.render();
    }
  }

  onClose(): void {
    this.contentEl.empty();
  }
}
