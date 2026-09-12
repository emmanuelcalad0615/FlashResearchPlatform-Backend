#!/usr/bin/env node
const fs = require('fs');
const path = require('path');

function main() {
  const [, , inputPath, outputPath] = process.argv;
  if (!inputPath || !outputPath) {
    console.error('Usage: node ua-arch-analyze.js <input.json> <output.json>');
    process.exit(1);
  }
  let data;
  try {
    data = JSON.parse(fs.readFileSync(inputPath, 'utf8'));
  } catch (e) {
    console.error('Failed to read/parse input: ' + e.message);
    process.exit(1);
  }

  const fileNodes = data.fileNodes || [];
  const importEdges = data.importEdges || [];
  const allEdges = data.allEdges || [];

  const nodeById = new Map();
  for (const n of fileNodes) nodeById.set(n.id, n);

  // --- A. Directory Grouping ---
  const filePaths = fileNodes.map(n => n.filePath || '');
  function commonPrefix(paths) {
    if (paths.length === 0) return '';
    const split = paths.map(p => p.split('/'));
    const minLen = Math.min(...split.map(s => s.length));
    const prefixParts = [];
    for (let i = 0; i < minLen - 1; i++) { // exclude filename itself
      const seg = split[0][i];
      if (split.every(s => s[i] === seg)) prefixParts.push(seg);
      else break;
    }
    return prefixParts.length ? prefixParts.join('/') + '/' : '';
  }
  const prefix = commonPrefix(filePaths);

  function firstSegmentAfterPrefix(fp) {
    let rest = fp.startsWith(prefix) ? fp.slice(prefix.length) : fp;
    const parts = rest.split('/');
    if (parts.length > 1) return parts[0];
    return null; // flat, no subdirectory
  }

  const directoryGroups = {};
  const flatFiles = [];
  for (const n of fileNodes) {
    const seg = firstSegmentAfterPrefix(n.filePath || '');
    if (seg) {
      directoryGroups[seg] = directoryGroups[seg] || [];
      directoryGroups[seg].push(n.id);
    } else {
      flatFiles.push(n);
    }
  }

  // handle flat files by extension/pattern grouping if any exist and no dirs found
  if (flatFiles.length > 0) {
    for (const n of flatFiles) {
      const fp = n.filePath || '';
      const base = path.basename(fp);
      let group;
      if (/\.(test|spec)\./.test(base) || /^test_/.test(base) || /_test\./.test(base)) group = 'test';
      else if (/\.config\./.test(base) || /config/i.test(base)) group = 'config';
      else {
        // fallback: root
        group = 'root';
      }
      directoryGroups[group] = directoryGroups[group] || [];
      directoryGroups[group].push(n.id);
    }
  }

  // --- B. Node Type Grouping ---
  const nodeTypeGroups = {};
  for (const n of fileNodes) {
    nodeTypeGroups[n.type] = nodeTypeGroups[n.type] || [];
    nodeTypeGroups[n.type].push(n.id);
  }

  // --- C. Import Adjacency Matrix ---
  const fileFanOut = {};
  const fileFanIn = {};
  const adjacency = {}; // id -> Set of imported ids
  for (const n of fileNodes) {
    fileFanOut[n.id] = 0;
    fileFanIn[n.id] = 0;
    adjacency[n.id] = new Set();
  }
  for (const e of importEdges) {
    if (!nodeById.has(e.source) || !nodeById.has(e.target)) continue;
    adjacency[e.source].add(e.target);
    fileFanOut[e.source] = (fileFanOut[e.source] || 0) + 1;
    fileFanIn[e.target] = (fileFanIn[e.target] || 0) + 1;
  }

  // id -> group lookup
  const idToGroup = {};
  for (const [group, ids] of Object.entries(directoryGroups)) {
    for (const id of ids) idToGroup[id] = group;
  }

  const groupImportsFrom = {}; // group -> Set(group)
  const groupImportedBy = {}; // group -> Set(group)
  for (const g of Object.keys(directoryGroups)) {
    groupImportsFrom[g] = new Set();
    groupImportedBy[g] = new Set();
  }
  for (const e of importEdges) {
    const sg = idToGroup[e.source];
    const tg = idToGroup[e.target];
    if (!sg || !tg || sg === tg) continue;
    groupImportsFrom[sg].add(tg);
    groupImportedBy[tg].add(sg);
  }

  // --- D. Cross-Category Dependency Analysis ---
  const crossCategoryCounts = {}; // key: fromType|toType|edgeType -> count
  for (const e of allEdges) {
    const s = nodeById.get(e.source);
    const t = nodeById.get(e.target);
    if (!s || !t) continue; // only count edges between known file-level nodes
    if (s.type === t.type) continue; // cross-category only
    const key = `${s.type}|${t.type}|${e.type}`;
    crossCategoryCounts[key] = (crossCategoryCounts[key] || 0) + 1;
  }
  const crossCategoryEdges = Object.entries(crossCategoryCounts).map(([key, count]) => {
    const [fromType, toType, edgeType] = key.split('|');
    return { fromType, toType, edgeType, count };
  });

  // --- E. Inter-Group Import Frequency ---
  const interGroupCounts = {};
  for (const e of importEdges) {
    const sg = idToGroup[e.source];
    const tg = idToGroup[e.target];
    if (!sg || !tg || sg === tg) continue;
    const key = `${sg}=>${tg}`;
    interGroupCounts[key] = (interGroupCounts[key] || 0) + 1;
  }
  const interGroupImports = Object.entries(interGroupCounts).map(([key, count]) => {
    const [from, to] = key.split('=>');
    return { from, to, count };
  });

  // --- F. Intra-Group Import Density ---
  const intraGroupDensity = {};
  for (const g of Object.keys(directoryGroups)) {
    let internalEdges = 0;
    let totalEdges = 0;
    for (const e of importEdges) {
      const sg = idToGroup[e.source];
      const tg = idToGroup[e.target];
      if (sg === g || tg === g) {
        totalEdges++;
        if (sg === g && tg === g) internalEdges++;
      }
    }
    intraGroupDensity[g] = {
      internalEdges,
      totalEdges,
      density: totalEdges > 0 ? +(internalEdges / totalEdges).toFixed(3) : 0
    };
  }

  // --- G. Directory Pattern Matching ---
  const dirPatternMap = {
    routes: 'api', api: 'api', controllers: 'api', endpoints: 'api', handlers: 'api',
    services: 'service', core: 'service', lib: 'service', domain: 'service', logic: 'service',
    models: 'data', db: 'data', data: 'data', persistence: 'data', repository: 'data', entities: 'data',
    components: 'ui', views: 'ui', pages: 'ui', ui: 'ui', layouts: 'ui', screens: 'ui',
    middleware: 'middleware', plugins: 'middleware', interceptors: 'middleware', guards: 'middleware',
    utils: 'utility', helpers: 'utility', common: 'utility', shared: 'utility', tools: 'utility',
    config: 'config', constants: 'config', env: 'config', settings: 'config',
    __tests__: 'test', test: 'test', tests: 'test', spec: 'test', specs: 'test',
    types: 'types', interfaces: 'types', schemas: 'types', contracts: 'types', dtos: 'types',
    hooks: 'hooks',
    store: 'state', state: 'state', reducers: 'state', actions: 'state', slices: 'state',
    assets: 'assets', static: 'assets', public: 'assets',
    migrations: 'data',
    management: 'config', commands: 'config',
    templatetags: 'utility',
    signals: 'service',
    serializers: 'api',
    cmd: 'entry',
    internal: 'service',
    pkg: 'utility',
    dto: 'types', request: 'types', response: 'types',
    entity: 'data',
    controller: 'api',
    routers: 'api',
    composables: 'service',
    blueprints: 'api',
    mailers: 'service', jobs: 'service', channels: 'service',
    bin: 'entry',
    docs: 'documentation', documentation: 'documentation', wiki: 'documentation',
    deploy: 'infrastructure', deployment: 'infrastructure', infra: 'infrastructure', infrastructure: 'infrastructure',
    k8s: 'infrastructure', kubernetes: 'infrastructure', helm: 'infrastructure', charts: 'infrastructure',
    terraform: 'infrastructure', tf: 'infrastructure',
    docker: 'infrastructure',
    sql: 'data', database: 'data', schema: 'data',
    apps: null, packages: null // ambiguous top-level containers, no direct pattern
  };
  const patternMatches = {};
  for (const g of Object.keys(directoryGroups)) {
    const lower = g.toLowerCase();
    if (dirPatternMap[lower]) patternMatches[g] = dirPatternMap[lower];
  }

  // --- H. Deployment Topology Detection ---
  const infraFiles = [];
  let hasDockerfile = false, hasCompose = false, hasK8s = false, hasTerraform = false, hasCI = false;
  for (const n of fileNodes) {
    const fp = n.filePath || '';
    const base = path.basename(fp);
    if (/^Dockerfile/.test(base)) { hasDockerfile = true; infraFiles.push(fp); }
    if (/docker-compose/.test(base)) { hasCompose = true; infraFiles.push(fp); }
    if (/\.tf$|\.tfvars$/.test(base)) { hasTerraform = true; infraFiles.push(fp); }
    if (/k8s|kubernetes/i.test(fp)) { hasK8s = true; infraFiles.push(fp); }
    if (fp.startsWith('.github/workflows/') || /\.gitlab-ci\.yml$/.test(base) || base === 'Jenkinsfile') {
      hasCI = true; infraFiles.push(fp);
    }
    if (base === 'Makefile') infraFiles.push(fp);
  }
  const deploymentTopology = {
    hasDockerfile, hasCompose, hasK8s, hasTerraform, hasCI,
    infraFiles: Array.from(new Set(infraFiles))
  };

  // --- I. Data Pipeline Detection ---
  const schemaFiles = [];
  const migrationFiles = [];
  const dataModelFiles = [];
  const apiHandlerFiles = [];
  for (const n of fileNodes) {
    const fp = n.filePath || '';
    const tags = n.tags || [];
    if (/\.sql$/.test(fp) || /\.graphql$/.test(fp) || /\.proto$/.test(fp) || tags.includes('schema')) schemaFiles.push(fp);
    if (fp.includes('migrations/') && /\.py$/.test(fp) && !/env\.py|script\.py\.mako/.test(fp)) migrationFiles.push(fp);
    if (tags.includes('data-model') || tags.includes('orm') || fp.includes('/models/')) dataModelFiles.push(fp);
    if (tags.includes('api-handler') || fp.includes('/routers/') || fp.includes('/routes/')) apiHandlerFiles.push(fp);
  }
  const dataPipeline = {
    schemaFiles: Array.from(new Set(schemaFiles)),
    migrationFiles: Array.from(new Set(migrationFiles)),
    dataModelFiles: Array.from(new Set(dataModelFiles)),
    apiHandlerFiles: Array.from(new Set(apiHandlerFiles))
  };

  // --- J. Documentation Coverage ---
  const docFiles = fileNodes.filter(n => n.type === 'document').map(n => n.filePath);
  const groupsWithDocsSet = new Set();
  for (const g of Object.keys(directoryGroups)) {
    // crude: check if any doc file path includes group name, or README at root covers all
    if (docFiles.some(d => d.toLowerCase().includes(g.toLowerCase()))) groupsWithDocsSet.add(g);
  }
  // README.md at root documents multiple things per allEdges 'documents' edges; count those too
  for (const e of allEdges) {
    if (e.type === 'documents') {
      const t = nodeById.get(e.target);
      if (t) {
        const seg = firstSegmentAfterPrefix(t.filePath || '');
        if (seg && directoryGroups[seg]) groupsWithDocsSet.add(seg);
      }
    }
  }
  const totalGroups = Object.keys(directoryGroups).length;
  const groupsWithDocs = groupsWithDocsSet.size;
  const undocumentedGroups = Object.keys(directoryGroups).filter(g => !groupsWithDocsSet.has(g));
  const docCoverage = {
    groupsWithDocs,
    totalGroups,
    coverageRatio: totalGroups > 0 ? +(groupsWithDocs / totalGroups).toFixed(3) : 0,
    undocumentedGroups
  };

  // --- K. Dependency Direction ---
  const dependencyDirection = [];
  const seenPairs = new Set();
  for (const { from, to, count } of interGroupImports) {
    const pairKey = [from, to].sort().join('|');
    if (seenPairs.has(pairKey)) continue;
    const reverseCount = interGroupCounts[`${to}=>${from}`] || 0;
    if (count > reverseCount) {
      dependencyDirection.push({ dependent: from, dependsOn: to });
    } else if (reverseCount > count) {
      dependencyDirection.push({ dependent: to, dependsOn: from });
    }
    seenPairs.add(pairKey);
  }

  // --- File Stats ---
  const filesPerGroup = {};
  for (const [g, ids] of Object.entries(directoryGroups)) filesPerGroup[g] = ids.length;
  const nodeTypeCounts = {};
  for (const [t, ids] of Object.entries(nodeTypeGroups)) nodeTypeCounts[t] = ids.length;

  const result = {
    scriptCompleted: true,
    commonPrefix: prefix,
    directoryGroups,
    nodeTypeGroups,
    crossCategoryEdges,
    interGroupImports,
    intraGroupDensity,
    patternMatches,
    deploymentTopology,
    dataPipeline,
    docCoverage,
    dependencyDirection,
    fileStats: {
      totalFileNodes: fileNodes.length,
      filesPerGroup,
      nodeTypeCounts
    },
    fileFanIn,
    fileFanOut,
    groupImportsFromMap: Object.fromEntries(Object.entries(groupImportsFrom).map(([k, v]) => [k, Array.from(v)])),
    groupImportedByMap: Object.fromEntries(Object.entries(groupImportedBy).map(([k, v]) => [k, Array.from(v)]))
  };

  try {
    fs.writeFileSync(outputPath, JSON.stringify(result, null, 2));
  } catch (e) {
    console.error('Failed to write output: ' + e.message);
    process.exit(1);
  }
  console.log('Analysis complete. Output written to ' + outputPath);
  process.exit(0);
}

main();
