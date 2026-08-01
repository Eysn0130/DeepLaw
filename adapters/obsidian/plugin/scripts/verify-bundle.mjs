import { access, readFile } from "node:fs/promises";

for (const file of ["main.js", "manifest.json", "styles.css"]) await access(file);
const bundle = await readFile("main.js", "utf8");
if (!bundle.includes("onLayoutReady")) throw new Error("bundle has no layout-ready boundary");
if (/shell:\s*true/.test(bundle)) throw new Error("bundle enables shell execution");
if (/sk-[A-Za-z0-9_-]{20,}/.test(bundle)) throw new Error("bundle contains credential-like material");
console.log("DeepLaw Obsidian bundle verified");
