#!/usr/bin/env bun
/** Optional bounded static doctor. The Python validator is authoritative. */

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import yaml from "yaml";

const SCRIPT_DIR = path.dirname(fileURLToPath(import.meta.url));
const POLICY_PATH = path.resolve(SCRIPT_DIR, "../assets/local-path-policy.json");
const RUNTIME_POLICY_PATH = path.resolve(SCRIPT_DIR, "../references/supported-runtimes.yml");
const DEFAULT_RUNTIME_PREFIXES = ["17.3.x-scala2.12", "17.3.x-gpu-ml-scala2.12", "17.3.x-cpu-ml-scala2.12"];
const REQUIRED_TAGS = ["Team", "Project", "Owner", "DataClassification", "Environment", "ApplicationName", "ResourceOwner", "CiscoMailAlias", "DataTaxonomy", "IntendedPublic"];
const TASK_TYPES = new Set(["notebook_task", "python_wheel_task", "spark_python_task", "spark_jar_task", "sql_task", "dbt_task", "run_job_task", "pipeline_task", "for_each_task"]);
const PERMISSION_PRINCIPAL_KEYS = ["user_name", "group_name", "service_principal_name"];
const BUNDLE_PERMISSION_LEVELS = new Set(["CAN_VIEW", "CAN_MANAGE", "CAN_RUN"]);
const RESOURCE_PERMISSION_LEVELS = new Map([
  ["alerts", new Set(["CAN_EDIT", "CAN_MANAGE", "CAN_READ", "CAN_RUN"])],
  ["apps", new Set(["CAN_MANAGE", "CAN_USE"])],
  ["cluster_policies", new Set(["CAN_USE"])],
  ["clusters", new Set(["CAN_ATTACH_TO", "CAN_MANAGE", "CAN_RESTART"])],
  ["dashboards", new Set(["CAN_EDIT", "CAN_MANAGE", "CAN_READ", "CAN_RUN"])],
  ["database_instances", new Set(["CAN_CREATE", "CAN_MANAGE", "CAN_USE"])],
  ["genie_spaces", new Set(["CAN_EDIT", "CAN_MANAGE", "CAN_RUN", "CAN_VIEW"])],
  ["experiments", new Set(["CAN_EDIT", "CAN_MANAGE", "CAN_READ", "CAN_RUN"])],
  ["jobs", new Set(["CAN_MANAGE", "CAN_MANAGE_RUN", "CAN_VIEW", "IS_OWNER"])],
  ["instance_pools", new Set(["CAN_ATTACH_TO", "CAN_MANAGE"])],
  ["model_serving_endpoints", new Set(["CAN_MANAGE", "CAN_QUERY", "CAN_VIEW"])],
  ["models", new Set(["CAN_EDIT", "CAN_MANAGE", "CAN_MANAGE_PRODUCTION_VERSIONS", "CAN_MANAGE_STAGING_VERSIONS", "CAN_READ"])],
  ["pipelines", new Set(["CAN_MANAGE", "CAN_RUN", "CAN_VIEW", "IS_OWNER"])],
  ["secret_scopes", new Set(["MANAGE", "READ", "WRITE"])],
  ["sql_warehouses", new Set(["CAN_MANAGE", "CAN_MONITOR", "CAN_USE", "CAN_VIEW", "IS_OWNER"])],
  ["vector_search_endpoints", new Set(["CAN_CREATE", "CAN_MANAGE", "CAN_USE"])],
]);
const GENERIC_RESOURCE_PERMISSION_LEVELS = new Set([
  "CAN_ATTACH_TO", "CAN_BIND", "CAN_CREATE", "CAN_EDIT", "CAN_EDIT_METADATA", "CAN_MANAGE",
  "CAN_MANAGE_PRODUCTION_VERSIONS", "CAN_MANAGE_RUN", "CAN_MANAGE_STAGING_VERSIONS", "CAN_MONITOR",
  "CAN_QUERY", "CAN_READ", "CAN_RESTART", "CAN_RUN", "CAN_USE", "CAN_VIEW",
  "CAN_VIEW_METADATA", "IS_OWNER",
]);
const SAFE_BASENAME = /^[A-Za-z0-9._-]{1,96}$/;
const SENSITIVE_NAME = /secret|token|password|credential|bearer|api[_-]?key/i;
const FORBIDDEN_OBJECT_KEYS = new Set(["__proto__", "constructor", "prototype"]);

const mkErr = (message, p = "", source = "") => ({ level: "error", message, path: p, source });
const mkWarn = (message, p = "", source = "") => ({ level: "warning", message, path: p, source });
const asArray = (value) => Array.isArray(value) ? value : [];
const asObject = (value) => value && typeof value === "object" && !Array.isArray(value) ? value : {};

function loadPolicy() {
  try {
    const policy = JSON.parse(fs.readFileSync(POLICY_PATH, "utf8"));
    if (!policy?.limits || !Array.isArray(policy.dynamic_markers) || !Array.isArray(policy.remote_prefixes) || !Array.isArray(policy.windows_reserved_device_components)
      || !Object.values(policy.limits).every((value) => Number.isInteger(value) && value > 0)) throw new Error();
    return policy;
  } catch { throw new Error("The local path policy fixture is unavailable or invalid"); }
}

function safeBasename(reference) {
  const basename = reference.replaceAll("\\", "/").split("/").at(-1) || "";
  return SAFE_BASENAME.test(basename) && !SENSITIVE_NAME.test(basename) ? basename : "local-file";
}

function contained(candidate, roots) {
  return roots.some((root) => {
    const relative = path.relative(root, candidate);
    return relative === "" || (!relative.startsWith(`..${path.sep}`) && relative !== ".." && !path.isAbsolute(relative));
  });
}

function safeRelative(filePath, bundleRoot) {
  const relative = path.relative(bundleRoot, filePath).split(path.sep).join("/");
  if (!relative || relative.startsWith("../") || path.isAbsolute(relative) || relative.length > 192) return "bundle-config";
  return relative.split("/").every((part) => SAFE_BASENAME.test(part)) ? relative : "bundle-config";
}

/** Return dynamic, remote, local-relative, or unsupported-host without probing. */
export function classifyPathReference(reference, policy = loadPolicy()) {
  const value = String(reference || "").trim();
  const lowered = value.toLowerCase();
  if (policy.dynamic_markers.some((marker) => value.includes(marker))) return "dynamic";
  if (policy.remote_prefixes.some((prefix) => lowered.startsWith(prefix.toLowerCase()))) return "remote";
  if (!value || value.startsWith("/") || value.startsWith("\\") || value.startsWith("~") || /^[A-Za-z]:/.test(value)
    || lowered.startsWith("file:") || value.startsWith("//") || value.startsWith("\\\\") || value.startsWith("\\?\\") || hasUnsafeWindowsComponent(value, policy)) return "unsupported-host";
  return "local-relative";
}

function hasUnsafeWindowsComponent(value, policy) {
  return value.split(/[\\/]/).some((component) => {
    if (!component || component === "." || component === "..") return false;
    if (component.includes(":")) return true;
    const deviceBase = component.replace(/[ .]+$/, "").split(".", 1)[0].toUpperCase();
    return policy.windows_reserved_device_components.includes(deviceBase);
  });
}

class SourceContext {
  constructor() { this.entries = new Map(); this.members = new Map(); }
  markContainer(value, directory, source) { this.entries.set(value, { directory, source }); }
  markMember(container, key, directory, source) {
    if (!this.members.has(container)) this.members.set(container, new Map());
    this.members.get(container).set(key, { directory, source });
  }
  mark(value, directory, source) {
    if (!value || typeof value !== "object") return;
    this.markContainer(value, directory, source);
    if (Array.isArray(value)) value.forEach((item, index) => { this.markMember(value, index, directory, source); this.mark(item, directory, source); });
    else Object.entries(value).forEach(([key, item]) => { this.markMember(value, key, directory, source); this.mark(item, directory, source); });
  }
  directoryFor(value, fallback) { return this.entries.get(value)?.directory || fallback; }
  sourceFor(value) { return this.entries.get(value)?.source || "databricks.yml"; }
  memberOrigin(container, key, fallbackDirectory, fallbackSource) { return this.members.get(container)?.get(key) || { directory: fallbackDirectory, source: fallbackSource }; }
  originFor(value, fallbackDirectory, fallbackSource) {
    const origin = this.entries.get(value);
    return origin || { directory: fallbackDirectory, source: fallbackSource };
  }
}

function guardYaml(node, policy, source, depth = 0, counts = { nodes: 0, scalars: 0 }) {
  if (node == null) return;
  counts.nodes += 1;
  if (counts.nodes > policy.limits.max_yaml_nodes) throw new Error(`YAML node count exceeds the configured limit in ${source}`);
  if (depth > policy.limits.max_yaml_depth) throw new Error(`YAML nesting exceeds the configured limit in ${source}`);
  if (yaml.isAlias(node)) throw new Error(`YAML aliases are not allowed in ${source}`);
  if (yaml.isScalar(node)) {
    counts.scalars += 1;
    if (counts.scalars > policy.limits.max_yaml_scalars) throw new Error(`YAML node or scalar count exceeds the configured limit in ${source}`);
    return;
  }
  if (yaml.isMap(node)) {
    const keys = new Set();
    for (const pair of node.items) {
      if (!yaml.isScalar(pair.key)) throw new Error(`YAML mapping keys must be scalars in ${source}`);
      const key = String(pair.key.value);
      if (key === "<<") throw new Error(`YAML merge keys are not allowed in ${source}`);
      if (FORBIDDEN_OBJECT_KEYS.has(key)) throw new Error(`YAML contains a reserved mapping key in ${source}`);
      if (keys.has(key)) throw new Error(`YAML duplicate keys are not allowed in ${source}`);
      keys.add(key);
      guardYaml(pair.key, policy, source, depth + 1, counts);
      guardYaml(pair.value, policy, source, depth + 1, counts);
    }
    return;
  }
  if (yaml.isSeq(node)) { node.items.forEach((item) => guardYaml(item, policy, source, depth + 1, counts)); return; }
  throw new Error(`YAML contains an unsupported node in ${source}`);
}

function readYaml(filePath, policy, source) {
  try {
    const stat = fs.statSync(filePath);
    if (!stat.isFile()) throw new Error("not-file");
    if (stat.size > policy.limits.max_yaml_file_bytes) throw new Error("size");
    const text = fs.readFileSync(filePath, "utf8");
    const doc = yaml.parseDocument(text, { uniqueKeys: true, maxAliasCount: -1, merge: false, prettyErrors: false });
    if (doc.errors.length) throw new Error(`Invalid YAML in ${source}`);
    guardYaml(doc.contents, policy, source);
    const data = doc.toJS({ mapAsMap: false, maxAliasCount: 0 });
    if (data != null && (!data || typeof data !== "object" || Array.isArray(data))) throw new Error(`Top-level YAML in ${source} must be a mapping`);
    return { data: data || {}, text };
  } catch (error) {
    if (error?.message === "size") throw new Error(`Bundle configuration source '${source}' exceeds the per-file size limit`);
    if (String(error?.message || "").includes("YAML")) throw error;
    throw new Error(`Bundle configuration source '${source}' could not be read`);
  }
}

function bundleFileFor(input) {
  const requested = path.resolve(input || ".");
  return fs.existsSync(requested) && fs.statSync(requested).isDirectory() ? path.join(requested, "databricks.yml") : requested;
}

function canonicalPotential(candidate) {
  const suffix = [path.basename(candidate)];
  let parent = path.dirname(candidate);
  while (true) {
    try {
      const canonicalParent = fs.realpathSync.native(parent);
      try { return fs.realpathSync.native(candidate); }
      catch (error) {
        if (error?.code === "ENOENT" || error?.code === "ENOTDIR") return path.resolve(canonicalParent, ...suffix);
        throw error;
      }
    } catch (error) {
      if (error?.code !== "ENOENT" && error?.code !== "ENOTDIR") throw error;
      const next = path.dirname(parent);
      if (next === parent) throw error;
      suffix.unshift(path.basename(parent));
      parent = next;
    }
  }
}

function validateLocal(sourceDirectory, roots, reference, field, label, policy, source) {
  if (typeof reference !== "string" || !reference.trim()) return { findings: [mkErr(`${label} must be a non-empty string`, field, source)], resolved: null };
  const kind = classifyPathReference(reference, policy);
  if (kind === "dynamic" || kind === "remote") return { findings: [], resolved: null };
  if (kind === "unsupported-host") return { findings: [mkErr(`${label} must be a relative local path, supported remote/workspace path, or dynamic substitution; host-specific paths are not probed`, field, source)], resolved: null };
  const lexical = path.resolve(sourceDirectory, reference);
  const basename = safeBasename(reference);
  if (!contained(lexical, roots.lexical)) return { findings: [mkErr(`${label} local file '${basename}' is outside the bundle root and declared sync.paths; declare its containing source in sync.paths or use a supported remote path`, field, source)], resolved: null };
  let resolved;
  try { resolved = canonicalPotential(lexical); }
  catch { return { findings: [mkErr(`${label} local file '${basename}' could not be resolved safely`, field, source)], resolved: null }; }
  if (!contained(resolved, roots.canonical)) return { findings: [mkErr(`${label} local file '${basename}' escapes its declared local source through a reparse point; declare its containing source in sync.paths or use a supported remote path`, field, source)], resolved: null };
  try {
    if (!fs.existsSync(resolved) || !fs.statSync(resolved).isFile()) return { findings: [mkErr(`${label} local file '${basename}' is missing or not a regular file; restore it, declare its containing source in sync.paths, or use a supported remote path`, field, source)], resolved: null };
    const handle = fs.openSync(resolved, "r");
    try { fs.readSync(handle, Buffer.alloc(1), 0, 1, 0); } finally { fs.closeSync(handle); }
  } catch { return { findings: [mkErr(`${label} local file '${basename}' is not readable; restore it, declare its containing source in sync.paths, or use a supported remote path`, field, source)], resolved: null }; }
  return { findings: [], resolved };
}

function validateGlob(sourceDirectory, roots, pattern, field, policy, source) {
  if (typeof pattern !== "string" || !pattern.trim()) return [mkErr("Pipeline glob include must be a non-empty string", field, source)];
  const kind = classifyPathReference(pattern, policy);
  if (kind === "dynamic" || kind === "remote") return [];
  if (kind === "unsupported-host") return [mkErr("Pipeline glob include must be a relative local pattern, supported remote/workspace path, or dynamic substitution; host-specific paths are not probed", field, source)];
  const components = pattern.replaceAll("\\", "/").split("/");
  if (components.includes("..")) return [mkErr("Pipeline glob include cannot use parent-directory traversal; declare a contained sync.paths source and use a pattern rooted there", field, source)];
  const staticComponents = [];
  for (const component of components) {
    if (/[?*\[]/.test(component)) break;
    staticComponents.push(component);
  }
  const lexical = path.resolve(sourceDirectory, staticComponents.join("/") || ".");
  if (!contained(lexical, roots.lexical)) return [mkErr("Pipeline glob include is outside the bundle root and declared sync.paths; declare its containing source in sync.paths or use a supported remote path", field, source)];
  let resolved;
  try { resolved = canonicalPotential(lexical); }
  catch { return [mkErr("Pipeline glob include could not be resolved safely", field, source)]; }
  return contained(resolved, roots.canonical) ? [] : [mkErr("Pipeline glob include escapes its declared local source through a reparse point; declare its containing source in sync.paths or use a supported remote path", field, source)];
}

function pipCheck(resolved, field, policy, source) {
  if (!resolved) return [];
  try {
    if (fs.statSync(resolved).size > policy.limits.max_task_scan_bytes) return [mkWarn("Local notebook was not scanned for %pip install because it exceeds the bounded scan limit", field, source)];
    return /^\s*%pip\s+install\b/m.test(fs.readFileSync(resolved, "utf8")) ? [mkWarn("Notebook uses %pip install; declare dependencies in the bundle config instead", field, source)] : [];
  } catch { return [mkWarn("Could not scan local notebook for %pip install", field, source)]; }
}

function validateInclude(pattern, field, source, policy) {
  if (typeof pattern !== "string" || !pattern.trim()) return [mkErr("Include entries must be non-empty strings", field, source)];
  if (classifyPathReference(pattern, policy) !== "local-relative") return [mkErr("Include entries must be contained relative local patterns; remote, dynamic, and host-specific paths are not loaded", field, source)];
  const components = pattern.replaceAll("\\", "/").split("/").filter((part) => part && part !== ".");
  if (components.includes("..")) return [mkErr("Include entries cannot escape the bundle root; keep shared configuration inside the bundle", field, source)];
  if (components.slice(0, -1).some((part) => /[*?]/.test(part)) || pattern.includes("**")) return [mkErr("Include entries support bounded flat file globs only", field, source)];
  return [];
}

function includeMatches(callerDirectory, bundleRoot, pattern, field, source, policy) {
  const normalized = pattern.replaceAll("\\", "/");
  const split = normalized.lastIndexOf("/");
  const directory = path.resolve(callerDirectory, split < 0 ? "." : normalized.slice(0, split));
  const filename = split < 0 ? normalized : normalized.slice(split + 1);
  if (!contained(directory, [bundleRoot])) return { matches: [], findings: [mkErr("Include entries cannot escape the bundle root; keep shared configuration inside the bundle", field, source)] };
  let names;
  try { names = fs.readdirSync(directory).sort(); } catch { return { matches: [], findings: [mkWarn("Include pattern matched no files", field, source)] }; }
  const expression = new RegExp(`^${filename.replace(/[.+^${}()|[\]\\]/g, "\\$&").replaceAll("*", ".*").replaceAll("?", ".")}$`);
  const findings = [], matches = [];
  for (const name of names) {
    if (!expression.test(name)) continue;
    let candidate;
    try { candidate = canonicalPotential(path.join(directory, name)); } catch { continue; }
    if (!contained(candidate, [bundleRoot])) { findings.push(mkErr("An include match escapes the bundle root through a reparse point and was rejected", field, source)); continue; }
    try { if (!fs.statSync(candidate).isFile()) continue; } catch { continue; }
    matches.push(candidate);
    if (matches.length > policy.limits.max_include_matches_per_pattern) return { matches: [], findings: [mkErr("Include pattern exceeds the configured match limit; split the include into narrower patterns", field, source)] };
  }
  if (!matches.length) findings.push(mkWarn("Include pattern matched no files", field, source));
  return { matches: [...new Set(matches)].sort((a, b) => safeRelative(a, bundleRoot).localeCompare(safeRelative(b, bundleRoot))), findings };
}

function merge(base, incoming, findings, source, context, sourceDirectory, prefix = "") {
  for (const [key, value] of Object.entries(incoming)) {
    const field = prefix ? `${prefix}.${key}` : key;
    const incomingOrigin = context.memberOrigin(incoming, key, sourceDirectory, source);
    if (!(key in base)) { base[key] = value; context.markMember(base, key, incomingOrigin.directory, incomingOrigin.source); continue; }
    if (base[key] && value && typeof base[key] === "object" && typeof value === "object" && !Array.isArray(base[key]) && !Array.isArray(value)) merge(base[key], value, findings, source, context, sourceDirectory, field);
    else if (Array.isArray(base[key]) && Array.isArray(value)) {
      const existing = base[key], combined = existing.concat(value);
      const existingOrigin = context.memberOrigin(base, key, context.directoryFor(base, sourceDirectory), context.sourceFor(base));
      context.markContainer(combined, existingOrigin.directory, existingOrigin.source);
      existing.forEach((_, index) => { const origin = context.memberOrigin(existing, index, existingOrigin.directory, existingOrigin.source); context.markMember(combined, index, origin.directory, origin.source); });
      value.forEach((_, index) => { const origin = context.memberOrigin(value, index, incomingOrigin.directory, incomingOrigin.source); context.markMember(combined, existing.length + index, origin.directory, origin.source); });
      base[key] = combined;
    } else { if (JSON.stringify(base[key]) !== JSON.stringify(value)) findings.push(mkWarn(`Include file overrides '${field}'`, field, source)); base[key] = value; context.markMember(base, key, incomingOrigin.directory, incomingOrigin.source); }
  }
}

function loadBundle(input, policy) {
  const requestedRootFile = bundleFileFor(input);
  if (!fs.existsSync(requestedRootFile)) throw new Error("No bundle file found at the requested bundle path");
  const rootFile = fs.realpathSync.native(requestedRootFile);
  const bundleRoot = path.dirname(rootFile);
  const rootSource = safeRelative(rootFile, bundleRoot);
  const root = readYaml(rootFile, policy, rootSource);
  const context = new SourceContext();
  context.mark(root.data, path.dirname(rootFile), rootSource);
  const files = [{ path: rootFile, relativePath: rootSource, text: root.text }], findings = [];
  let bytes = Buffer.byteLength(root.text, "utf8");
  const seen = new Set([fs.realpathSync.native(rootFile)]);
  function loadRootIncludes(config, callerFile, callerSource) {
    if (config.include == null) return;
    if (!Array.isArray(config.include)) { findings.push(mkErr("'include' must be a list", "include", callerSource)); return; }
    if (config.include.length > policy.limits.max_include_patterns) { findings.push(mkErr("Include list exceeds the configured pattern limit", "include", callerSource)); return; }
    config.include.forEach((pattern, index) => {
      const field = `include[${index}]`, preflight = validateInclude(pattern, field, callerSource, policy);
      findings.push(...preflight); if (preflight.length) return;
      const expansion = includeMatches(path.dirname(callerFile), bundleRoot, pattern, field, callerSource, policy);
      findings.push(...expansion.findings);
      for (const includeFile of expansion.matches) {
        const canonical = fs.realpathSync.native(includeFile), includeSource = safeRelative(canonical, bundleRoot);
        if (seen.has(canonical)) { findings.push(mkWarn("Duplicate include was loaded once and ignored thereafter", field, callerSource)); continue; }
        if (files.length >= policy.limits.max_include_files) { findings.push(mkErr("Include graph exceeds the configured file limit", field, callerSource)); return; }
        let included;
        try { included = readYaml(canonical, policy, includeSource); } catch (error) { findings.push(mkErr(error.message, field, callerSource)); continue; }
        const size = Buffer.byteLength(included.text, "utf8");
        if (bytes + size > policy.limits.max_yaml_aggregate_bytes) { findings.push(mkErr("Included YAML exceeds the aggregate size limit", field, callerSource)); return; }
        bytes += size; seen.add(canonical);
        context.mark(included.data, path.dirname(canonical), includeSource); files.push({ path: canonical, relativePath: includeSource, text: included.text });
        const fragment = { ...included.data };
        if ("include" in fragment) {
          findings.push(mkWarn("Include directives in included fragments are ignored; only root databricks.yml include is applied", "include", includeSource));
          delete fragment.include;
        }
        context.mark(fragment, path.dirname(canonical), includeSource);
        merge(config, fragment, findings, includeSource, context, path.dirname(canonical));
      }
    });
  }
  loadRootIncludes(root.data, rootFile, rootSource);
  return { config: root.data, findings, files, context, bundleRoot, bundleFile: rootSource };
}

function cloneWithOrigin(value, context, fallbackDirectory) {
  if (Array.isArray(value)) {
    const clone = [];
    const directory = context.directoryFor(value, fallbackDirectory), source = context.sourceFor(value);
    context.markContainer(clone, directory, source);
    value.forEach((item, index) => { const origin = context.memberOrigin(value, index, directory, source); context.markMember(clone, index, origin.directory, origin.source); clone.push(cloneWithOrigin(item, context, fallbackDirectory)); });
    return clone;
  }
  if (value && typeof value === "object") {
    const clone = {};
    const directory = context.directoryFor(value, fallbackDirectory), source = context.sourceFor(value);
    context.markContainer(clone, directory, source);
    Object.entries(value).forEach(([key, item]) => { const origin = context.memberOrigin(value, key, directory, source); context.markMember(clone, key, origin.directory, origin.source); clone[key] = cloneWithOrigin(item, context, fallbackDirectory); });
    return clone;
  }
  return value;
}

function mergeEffective(base, overlay, context, fallbackDirectory) {
  Object.entries(overlay).forEach(([key, incoming]) => {
    if (!(key in base)) { const origin = context.memberOrigin(overlay, key, context.directoryFor(overlay, fallbackDirectory), context.sourceFor(overlay)); base[key] = cloneWithOrigin(incoming, context, fallbackDirectory); context.markMember(base, key, origin.directory, origin.source); return; }
    if (key === "permissions" && Array.isArray(base[key]) && Array.isArray(incoming)) {
      const start = base[key].length, directory = context.directoryFor(incoming, fallbackDirectory), source = context.sourceFor(incoming);
      incoming.forEach((item, index) => { const origin = context.memberOrigin(incoming, index, directory, source); context.markMember(base[key], start + index, origin.directory, origin.source); base[key].push(cloneWithOrigin(item, context, fallbackDirectory)); });
      return;
    }
    if (base[key] && incoming && typeof base[key] === "object" && typeof incoming === "object" && !Array.isArray(base[key]) && !Array.isArray(incoming)) mergeEffective(base[key], incoming, context, fallbackDirectory);
    else { const origin = context.memberOrigin(overlay, key, context.directoryFor(overlay, fallbackDirectory), context.sourceFor(overlay)); base[key] = cloneWithOrigin(incoming, context, fallbackDirectory); context.markMember(base, key, origin.directory, origin.source); }
  });
}

function effectiveResources(rootResources, targetResources, bundleRoot, context) {
  if (targetResources == null) return cloneWithOrigin(rootResources, context, bundleRoot);
  if (!targetResources || typeof targetResources !== "object" || Array.isArray(targetResources)) return targetResources;
  if (!rootResources || typeof rootResources !== "object" || Array.isArray(rootResources)) return cloneWithOrigin(targetResources, context, bundleRoot);
  const result = cloneWithOrigin(rootResources, context, bundleRoot);
  mergeEffective(result, targetResources, context, bundleRoot);
  return result;
}

function sourceRoots(bundleRoot, context, policy, syncMappings) {
  const lexical = [bundleRoot], canonical = [bundleRoot], findings = [];
  for (const [syncField, sync] of syncMappings) {
    if (sync == null) continue;
    const source = context.sourceFor(sync), directory = context.directoryFor(sync, bundleRoot);
    if (!sync || typeof sync !== "object" || Array.isArray(sync)) { findings.push(mkErr("sync must be a mapping", syncField, source)); continue; }
    if (sync.paths == null) continue;
    if (!Array.isArray(sync.paths)) { findings.push(mkErr("sync.paths must be a list of local directories", `${syncField}.paths`, source)); continue; }
    sync.paths.forEach((entry, index) => {
      const field = `${syncField}.paths[${index}]`;
      if (typeof entry !== "string" || !entry.trim()) { findings.push(mkErr("sync.paths entries must be non-empty relative local directories", field, source)); return; }
      if (classifyPathReference(entry, policy) !== "local-relative") { findings.push(mkErr("sync.paths entries must be relative local directories; dynamic, remote, and host-specific values cannot authorize local file checks", field, source)); return; }
      const lexicalRoot = path.resolve(directory, entry);
      try {
        const canonicalRoot = fs.realpathSync.native(lexicalRoot);
        if (!fs.statSync(canonicalRoot).isDirectory()) throw new Error();
        lexical.push(lexicalRoot); canonical.push(canonicalRoot);
      } catch { findings.push(mkErr("Declared sync.paths directory is missing or not a directory", field, source)); }
    });
  }
  return { roots: { lexical: [...new Set(lexical)], canonical: [...new Set(canonical)] }, findings };
}

function tagFindings(tags, field, source) {
  if (!tags || typeof tags !== "object" || Array.isArray(tags)) return [mkWarn("Tags should be a mapping so required tag keys can be validated", field, source)];
  const missing = REQUIRED_TAGS.filter((tag) => !(tag in tags));
  return missing.length ? [mkWarn(`Tags missing required keys: ${missing.join(", ")}`, field, source)] : [];
}

function dependencyFindings(deps, field, source) {
  const findings = [];
  asArray(deps).forEach((entry, index) => {
    const packageName = typeof entry === "string" ? entry.trim() : typeof entry?.pypi === "string" ? entry.pypi.trim() : typeof entry?.pypi?.package === "string" ? entry.pypi.package.trim() : null;
    if (!packageName || /[\\/]|\$\{|:\/\//.test(packageName) || packageName.toLowerCase().endsWith(".whl")) return;
    if (!packageName.split(";", 1)[0].includes("==")) findings.push(mkWarn(`Dependency '${packageName}' is not pinned with ==`, `${field}[${index}]`, source));
  });
  return findings;
}

function permissionFindings(permissions, field, allowedLevels, source, context = null) {
  if (!Array.isArray(permissions)) return [mkErr("'permissions' must be a list", field, source)];
  const findings = [];
  permissions.forEach((entry, index) => {
    const entryField = `${field}[${index}]`;
    const entrySource = context ? context.memberOrigin(permissions, index, "", source).source : source;
    if (!entry || typeof entry !== "object" || Array.isArray(entry)) {
      findings.push(mkErr("Permission entry must be a mapping", entryField, entrySource));
      return;
    }
    const presentPrincipals = PERMISSION_PRINCIPAL_KEYS.filter((key) => Object.hasOwn(entry, key));
    if (presentPrincipals.length !== 1) {
      const detail = presentPrincipals.length ? `; found ${presentPrincipals.join(", ")}` : "";
      findings.push(mkErr(`Permission entry must define exactly one principal key: user_name, group_name, or service_principal_name${detail}`, entryField, entrySource));
    }
    presentPrincipals.forEach((key) => {
      if (typeof entry[key] !== "string" || !entry[key].trim()) findings.push(mkErr(`Permission principal '${key}' must be a non-empty string`, `${entryField}.${key}`, entrySource));
    });
    if (typeof entry.level !== "string" || !entry.level.trim()) findings.push(mkErr("Permission level must be a non-empty string", `${entryField}.level`, entrySource));
    else if (!allowedLevels.has(entry.level)) findings.push(mkErr(`Permission level '${entry.level}' is not allowed here; expected one of: ${[...allowedLevels].sort().join(", ")}`, `${entryField}.level`, entrySource));
  });
  return findings;
}

function runtimePrefixes(extra, configuredPath = null) {
  try {
    const policy = yaml.parse(fs.readFileSync(configuredPath || RUNTIME_POLICY_PATH, "utf8"));
    if (Array.isArray(policy?.classic_runtime_prefixes) && policy.classic_runtime_prefixes.length) return [...new Set([...policy.classic_runtime_prefixes, ...extra])];
  } catch { /* The conservative defaults remain available. */ }
  return [...new Set([...DEFAULT_RUNTIME_PREFIXES, ...extra])];
}

function taskFindings(task, field, roots, bundleRoot, context, policy, prefixes, jobClusters, requiredKey, keys) {
  if (!task || typeof task !== "object" || Array.isArray(task)) return [mkErr("Task entry must be a mapping", field)];
  const findings = [], source = context.sourceFor(task), directory = context.directoryFor(task, bundleRoot);
  if (requiredKey) {
    if (typeof task.task_key !== "string" || !task.task_key.trim()) findings.push(mkErr("Task missing 'task_key'", field, source));
    else if (keys.has(task.task_key)) findings.push(mkErr("Duplicate task_key", field, source));
    else keys.add(task.task_key);
  }
  const types = [...TASK_TYPES].filter((type) => type in task);
  if (!types.length) findings.push(mkErr("Task has no task type defined", field, source));
  else if (types.length > 1) findings.push(mkErr(`Task has multiple task types: ${types.join(", ")}`, field, source));
  if (task.notebook_task && typeof task.notebook_task === "object") {
    const location = `${field}.notebook_task.notebook_path`, check = validateLocal(directory, roots, task.notebook_task.notebook_path, location, "Notebook", policy, source);
    findings.push(...check.findings, ...pipCheck(check.resolved, location, policy, source));
  }
  if (task.spark_python_task && typeof task.spark_python_task === "object") findings.push(...validateLocal(directory, roots, task.spark_python_task.python_file, `${field}.spark_python_task.python_file`, "Python file", policy, source).findings);
  if (task.new_cluster?.spark_version && !prefixes.some((prefix) => task.new_cluster.spark_version.startsWith(prefix))) findings.push(mkWarn("Cluster spark_version should match a verified runtime prefix from supported-runtimes.yml", `${field}.new_cluster.spark_version`, source));
  if (typeof task.job_cluster_key === "string" && jobClusters[task.job_cluster_key]?.spark_version && !prefixes.some((prefix) => jobClusters[task.job_cluster_key].spark_version.startsWith(prefix))) findings.push(mkWarn("Cluster spark_version should match a verified runtime prefix from supported-runtimes.yml", `${field}.job_cluster_key`, source));
  if (task.for_each_task != null) {
    if (!task.for_each_task || typeof task.for_each_task !== "object" || Array.isArray(task.for_each_task)) findings.push(mkErr("for_each_task must be a mapping", `${field}.for_each_task`, source));
    else if (!("task" in task.for_each_task)) findings.push(mkErr("for_each_task must define a nested task", `${field}.for_each_task.task`, source));
    else findings.push(...taskFindings(task.for_each_task.task, `${field}.for_each_task.task`, roots, bundleRoot, context, policy, prefixes, jobClusters, false, new Set()));
  }
  if (task.libraries != null) findings.push(...dependencyFindings(task.libraries, `${field}.libraries`, source));
  return findings;
}

function jobFindings(job, field, roots, bundleRoot, context, policy, prefixes) {
  if (!job || typeof job !== "object" || Array.isArray(job)) return [mkErr("Job definition must be a mapping", field)];
  const findings = [], source = context.sourceFor(job);
  if (typeof job.name !== "string" || !job.name.trim()) findings.push(mkErr("Job missing 'name' field", field, source));
  if (job.tags && typeof job.tags === "object" && !Array.isArray(job.tags)) findings.push(...tagFindings(job.tags, `${field}.tags`, source));
  if (job.libraries != null) findings.push(...dependencyFindings(job.libraries, `${field}.libraries`, source));
  asArray(job.environments).forEach((environment, index) => {
    const spec = asObject(environment?.spec);
    if (!Object.keys(spec).length) return;
    const version = String(spec.environment_version || "");
    if (!version) findings.push(mkErr("Serverless environment missing 'environment_version' - pin the project/workspace-supported environment version", `${field}.environments[${index}].spec.environment_version`, source));
    else if (version.toLowerCase() === "latest") findings.push(mkWarn("Serverless environment_version should be an explicit project/workspace-supported value, not 'latest'", `${field}.environments[${index}].spec.environment_version`, source));
    if (spec.dependencies != null) findings.push(...dependencyFindings(spec.dependencies, `${field}.environments[${index}].spec.dependencies`, source));
  });
  const clusters = {};
  asArray(job.job_clusters).forEach((cluster, index) => {
    if (cluster?.job_cluster_key && cluster?.new_cluster && typeof cluster.new_cluster === "object") clusters[cluster.job_cluster_key] = cluster.new_cluster;
    if (cluster?.new_cluster?.spark_version && !prefixes.some((prefix) => cluster.new_cluster.spark_version.startsWith(prefix))) findings.push(mkWarn("Cluster spark_version should match a verified runtime prefix from supported-runtimes.yml", `${field}.job_clusters[${index}].new_cluster.spark_version`, source));
  });
  if (!Array.isArray(job.tasks)) findings.push(mkErr("Job 'tasks' must be a list", `${field}.tasks`, source));
  else if (!job.tasks.length) findings.push(mkErr("Job has no tasks defined", `${field}.tasks`, source));
  else { const keys = new Set(); job.tasks.forEach((task, index) => findings.push(...taskFindings(task, `${field}.tasks[${index}]`, roots, bundleRoot, context, policy, prefixes, clusters, true, keys))); }
  return findings;
}

function pipelineFindings(pipeline, field, roots, bundleRoot, context, policy) {
  if (!pipeline || typeof pipeline !== "object" || Array.isArray(pipeline)) return [mkErr("Pipeline definition must be a mapping", field)];
  const findings = [], source = context.sourceFor(pipeline), directory = context.directoryFor(pipeline, bundleRoot);
  if (typeof pipeline.name !== "string" || !pipeline.name.trim()) findings.push(mkErr("Pipeline missing 'name' field", field, source));
  if (!Array.isArray(pipeline.libraries)) findings.push(mkWarn("Pipeline missing 'libraries' - define the notebook or file that runs the pipeline", `${field}.libraries`, source));
  else pipeline.libraries.forEach((library, index) => {
    const libraryOrigin = context.originFor(library, directory, source);
    if (library?.notebook && typeof library.notebook === "object" && "path" in library.notebook) {
      const location = `${field}.libraries[${index}].notebook.path`, check = validateLocal(libraryOrigin.directory, roots, library.notebook.path, location, "Pipeline notebook", policy, libraryOrigin.source);
      findings.push(...check.findings, ...pipCheck(check.resolved, location, policy, libraryOrigin.source));
    }
    if (library?.file && typeof library.file === "object" && "path" in library.file) findings.push(...validateLocal(libraryOrigin.directory, roots, library.file.path, `${field}.libraries[${index}].file.path`, "Pipeline file", policy, libraryOrigin.source).findings);
    if (library?.glob && typeof library.glob === "object" && "include" in library.glob) {
      const includes = Array.isArray(library.glob.include) ? library.glob.include : [library.glob.include];
      includes.forEach((include, globIndex) => findings.push(...validateGlob(libraryOrigin.directory, roots, include, `${field}.libraries[${index}].glob.include${Array.isArray(library.glob.include) ? `[${globIndex}]` : ""}`, policy, libraryOrigin.source)));
    }
  });
  if (typeof pipeline.target === "string" && !pipeline.target.includes("${var.catalog}.${var.schema}")) findings.push(mkWarn("Pipeline target should use ${var.catalog}.${var.schema} so UC output is explicit", `${field}.target`, source));
  return findings;
}

function resourceFindings(resources, field, roots, bundleRoot, context, policy, prefixes) {
  if (!resources || typeof resources !== "object" || Array.isArray(resources)) return [mkErr("resources must be a mapping", field, context.sourceFor(resources))];
  const findings = [], source = context.sourceFor(resources);
  Object.entries(resources).forEach(([resourceType, definitions]) => {
    if (!definitions || typeof definitions !== "object" || Array.isArray(definitions)) return;
    const allowedLevels = RESOURCE_PERMISSION_LEVELS.get(resourceType) || GENERIC_RESOURCE_PERMISSION_LEVELS;
    Object.entries(definitions).forEach(([resourceName, definition]) => {
      if (!definition || typeof definition !== "object" || Array.isArray(definition) || !("permissions" in definition)) return;
      const origin = context.memberOrigin(definition, "permissions", context.directoryFor(definition, bundleRoot), context.sourceFor(definition));
      const permissionField = `${field}.${resourceType}.${resourceName}.permissions`;
      findings.push(...permissionFindings(definition.permissions, permissionField, allowedLevels, origin.source, context));
    });
  });
  if (resources.jobs != null && (!resources.jobs || typeof resources.jobs !== "object" || Array.isArray(resources.jobs))) findings.push(mkErr("resources.jobs must be a mapping", `${field}.jobs`, source));
  else Object.entries(asObject(resources.jobs)).forEach(([name, job]) => findings.push(...jobFindings(job, `${field}.jobs.${name}`, roots, bundleRoot, context, policy, prefixes)));
  if (resources.pipelines != null && (!resources.pipelines || typeof resources.pipelines !== "object" || Array.isArray(resources.pipelines))) findings.push(mkErr("resources.pipelines must be a mapping", `${field}.pipelines`, source));
  else Object.entries(asObject(resources.pipelines)).forEach(([name, pipeline]) => findings.push(...pipelineFindings(pipeline, `${field}.pipelines.${name}`, roots, bundleRoot, context, policy)));
  return findings;
}

function checkBundle(loaded, policy, prefixes) {
  const { config, files, context, bundleRoot } = loaded, findings = [];
  if (!config.bundle || typeof config.bundle !== "object" || Array.isArray(config.bundle)) findings.push(mkErr("Missing required 'bundle' section", "bundle"));
  else if (typeof config.bundle.name !== "string" || !config.bundle.name.trim()) findings.push(mkErr("Missing or empty 'bundle.name'", "bundle.name"));
  if (!config.targets || typeof config.targets !== "object" || Array.isArray(config.targets)) findings.push(mkWarn("Missing 'targets' section - prefer dev/prod targets", "targets"));
  const targets = asObject(config.targets), targetNames = Object.keys(targets);
  if (targetNames.length) {
    const defaultCount = targetNames.filter((name) => targets[name]?.default).length;
    if (defaultCount === 0) findings.push(mkWarn("No default target specified - one target should have 'default: true'", "targets"));
    if (defaultCount > 1) findings.push(mkErr("Multiple targets marked as default", "targets"));
    const missingStarterTargets = ["dev", "prod"].filter((name) => !(name in targets));
    if (missingStarterTargets.length) findings.push(mkWarn(`No ${missingStarterTargets.join("/")} target found; acceptable when repo docs define a different target topology`, "targets"));
    if (targets.prod && !config.run_as?.service_principal_name && !targets.prod.run_as?.service_principal_name) findings.push(mkWarn("Production target should run as a service principal via targets.prod.run_as.service_principal_name or root run_as.service_principal_name", "targets.prod.run_as.service_principal_name"));
  }
  if (!("permissions" in config)) findings.push(mkWarn("Missing 'permissions' section - resources will have limited access", "permissions"));
  else {
    const permissionSource = context.memberOrigin(config, "permissions", context.directoryFor(config, bundleRoot), context.sourceFor(config)).source;
    findings.push(...permissionFindings(config.permissions, "permissions", BUNDLE_PERMISSION_LEVELS, permissionSource, context));
    if (Array.isArray(config.permissions) && !config.permissions.some((entry) => entry && typeof entry === "object" && entry.level === "CAN_MANAGE")) findings.push(mkWarn("No manager permission found - consider adding CAN_MANAGE", "permissions", permissionSource));
  }
  Object.entries(targets).forEach(([target, value]) => {
    if (value && typeof value === "object" && !Array.isArray(value) && "permissions" in value) {
      const origin = context.memberOrigin(value, "permissions", context.directoryFor(value, bundleRoot), context.sourceFor(value));
      findings.push(...permissionFindings(value.permissions, `targets.${target}.permissions`, BUNDLE_PERMISSION_LEVELS, origin.source, context));
    }
  });
  const variables = asObject(config.variables);
  ["user_name", "catalog", "schema"].forEach((name) => { if (!(name in variables)) findings.push(mkWarn(`Define variables.${name} so workspace owner and Unity Catalog targets are explicit`, `variables.${name}`)); });
  if (variables.tags) findings.push(...tagFindings(variables.tags.default, "variables.tags.default"));
  files.forEach((file) => file.text.split(/\r?\n/).forEach((line, index) => { if (line.includes("${workspace.current_user.userName}")) findings.push(mkWarn("Avoid ${workspace.current_user.userName}; use variables.user_name and ${var.user_name} for headless validation and service-principal deploys", `${file.relativePath}:${index + 1}`)); }));
  const rootResources = config.resources;
  let hasResources = false;
  if (rootResources != null) {
    hasResources = true;
    const rootRoots = sourceRoots(bundleRoot, context, policy, [["sync", config.sync]]);
    findings.push(...rootRoots.findings, ...resourceFindings(rootResources, "resources", rootRoots.roots, bundleRoot, context, policy, prefixes));
  }
  Object.entries(targets).forEach(([target, value]) => {
    if (!value || typeof value !== "object" || Array.isArray(value) || (rootResources == null && value.resources == null)) return;
    hasResources = true;
    const targetRoots = sourceRoots(bundleRoot, context, policy, [["sync", config.sync], [`targets.${target}.sync`, value.sync]]);
    findings.push(...targetRoots.findings, ...resourceFindings(effectiveResources(rootResources, value.resources, bundleRoot, context), `targets.${target}.resources`, targetRoots.roots, bundleRoot, context, policy, prefixes));
  });
  if (!hasResources) findings.push(mkWarn("Missing 'resources' section - bundle has nothing to deploy", "resources"));
  return findings;
}

function output(result, asJson) {
  if (asJson) console.log(JSON.stringify(result, null, 2));
  else {
    if (result.bundleFile) console.log(`Bundle: ${result.bundleFile}`);
    for (const finding of result.findings || []) console.log(`${finding.level === "warning" ? "  WARN" : "  ERR "}${finding.path ? ` at ${finding.path}` : ""}${finding.source ? ` [${finding.source}]` : ""}: ${finding.message}`);
    console.log(`Doctor: ${result.ok ? "PASS" : "FAIL"}`);
  }
  process.exitCode = result.ok ? 0 : 2;
}

function safeLoadFailure(error) {
  const message = String(error?.message || "");
  return /^(No bundle file|Bundle configuration source|Invalid YAML|Top-level YAML|YAML )/.test(message)
    ? message
    : "Bundle could not be loaded safely";
}

function main() {
  const args = process.argv.slice(2), asJson = args.includes("--json"), classifyIndex = args.indexOf("--classify-path");
  let policy;
  try { policy = loadPolicy(); } catch (error) { return output({ ok: false, findings: [mkErr(error.message)] }, asJson); }
  if (classifyIndex >= 0) {
    const value = args[classifyIndex + 1];
    return output(typeof value === "string" ? { ok: true, classification: classifyPathReference(value, policy), findings: [] } : { ok: false, findings: [mkErr("--classify-path requires a value")] }, asJson);
  }
  const target = args.find((arg) => !arg.startsWith("-")) || ".";
  const extra = args.filter((arg) => arg.startsWith("--allow-runtime-prefix=")).map((arg) => arg.split("=", 2)[1]).filter(Boolean);
  const configuredRuntime = args.find((arg) => arg.startsWith("--runtime-config="));
  try {
    const loaded = loadBundle(target, policy), findings = loaded.findings.concat(checkBundle(loaded, policy, runtimePrefixes(extra, configuredRuntime ? path.resolve(configuredRuntime.split("=", 2)[1]) : null)));
    const errorCount = findings.filter((finding) => finding.level === "error").length;
    return output({ ok: errorCount === 0, bundleFile: loaded.bundleFile, errorCount, warnCount: findings.length - errorCount, findings }, asJson);
  } catch (error) { return output({ ok: false, findings: [mkErr(safeLoadFailure(error))] }, asJson); }
}

main();
