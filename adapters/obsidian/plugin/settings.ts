import { App, PluginSettingTab, Setting } from "obsidian";

import type DeepLawPlugin from "./main";

export interface DeepLawSettings {
  executable: string;
  vaultPath: string;
  compilerGrantId: string;
  defaultScope: "personal" | "project" | "domain";
  maxSensitivity: "public" | "internal" | "private";
  readOnlyMcpCommand: string;
}

export const DEFAULT_SETTINGS: DeepLawSettings = {
  executable: "deeplaw",
  vaultPath: "",
  compilerGrantId: "",
  defaultScope: "project",
  maxSensitivity: "private",
  readOnlyMcpCommand: "deeplaw knowledge mcp --autonomous"
};

export class DeepLawSettingTab extends PluginSettingTab {
  constructor(app: App, private readonly plugin: DeepLawPlugin) {
    super(app, plugin);
  }

  display(): void {
    this.containerEl.empty();
    new Setting(this.containerEl)
      .setName("DeepLaw executable")
      .setDesc("Command name or absolute executable path; arguments are never accepted here.")
      .addText((text) => text.setValue(this.plugin.settings.executable).onChange(async (value) => {
        this.plugin.settings.executable = value.trim();
        await this.plugin.saveSettings();
      }));
    new Setting(this.containerEl)
      .setName("DeepLaw Vault")
      .setDesc("Absolute local path to the owner-controlled DeepLaw Vault.")
      .addText((text) => text.setValue(this.plugin.settings.vaultPath).onChange(async (value) => {
        this.plugin.settings.vaultPath = value.trim();
        await this.plugin.saveSettings();
      }));
    new Setting(this.containerEl)
      .setName("Compiler grant ID")
      .setDesc("Owner-created grant identity. Capability tokens and signing keys are never stored.")
      .addText((text) => text.setValue(this.plugin.settings.compilerGrantId).onChange(async (value) => {
        this.plugin.settings.compilerGrantId = value.trim();
        await this.plugin.saveSettings();
      }));
    new Setting(this.containerEl)
      .setName("Read-only MCP command")
      .setDesc("Display-only configuration for knowledge_support; no shell execution.")
      .addText((text) => text.setValue(this.plugin.settings.readOnlyMcpCommand).onChange(async (value) => {
        this.plugin.settings.readOnlyMcpCommand = value.trim();
        await this.plugin.saveSettings();
      }));
    new Setting(this.containerEl)
      .setName("Default scope")
      .addDropdown((dropdown) => dropdown
        .addOptions({ personal: "Personal", project: "Project", domain: "Domain" })
        .setValue(this.plugin.settings.defaultScope)
        .onChange(async (value) => {
          this.plugin.settings.defaultScope = value as DeepLawSettings["defaultScope"];
          await this.plugin.saveSettings();
        }));
    new Setting(this.containerEl)
      .setName("Maximum sensitivity")
      .addDropdown((dropdown) => dropdown
        .addOptions({ public: "Public", internal: "Internal", private: "Private" })
        .setValue(this.plugin.settings.maxSensitivity)
        .onChange(async (value) => {
          this.plugin.settings.maxSensitivity = value as DeepLawSettings["maxSensitivity"];
          await this.plugin.saveSettings();
        }));
  }
}
