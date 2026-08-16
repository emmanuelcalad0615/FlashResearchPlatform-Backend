#!/usr/bin/env node
'use strict';

const fs = require('fs');
const path = require('path');

function fail(msg) {
  console.error('ERROR: ' + msg);
  process.exit(1);
}

const inputPath = process.argv[2];
const outputPath = process.argv[3];

if (!inputPath || !outputPath) {
  fail('Usage: node ua-tour-analyze.js <input.json> <output.json>');
}

let raw;
try {
  raw = fs.readFileSync(inputPath, 'utf8');
} catch (e) {
  fail('Could not read input file: ' + e.message);
}

let data;
try {
  data = JSON.parse(raw);
} catch (e) {
  fail('Invalid JSON in input file: ' + e.message);
}

const nodes = Array.isArray(data.nodes) ? data.nodes : [];
const edges = Array.isArray(data.edges) ? data.edges : [];
const layers = Array.isArray(data.layers) ? data.layers : [];

if (nodes.length === 0) {
  fail('No nodes found in input.');
}

const nodeById = new Map();
for (const n of nodes) {
  nodeById.set(n.id, n);
}

// Only consider edges where both endpoints exist as real nodes for fan-in/out
// (some edges reference synthetic sub-nodes like steps/tables not in nodes list -
// handle gracefully by allowing target/source not present, but only count toward
// fan-in/out of nodes that DO exist in the nodes array)

const fanIn = new Map();
const fanOut = new Map();
for (const n of nodes) {
  fanIn.set(n.id, 0);
  fanOut.set(n.id, 0);
}

for (const e of edges) {
  if (fanOut.has(e.source)) {
    fanOut.set(e.source, fanOut.get(e.source) + 1);
  }
  if (fanIn.has(e.target)) {
    fanIn.set(e.target, fanIn.get(e.target) + 1);
  }
}

function topN(map, n, keyName) {
  const arr = Array.from(map.entries()).map(([id, val]) => {
    const node = nodeById.get(id);
    return { id, [keyName]: val, name: node ? node.name : id };
  });
  arr.sort((a, b) => b[keyName] - a[keyName]);
  return arr.slice(0, n);
}

const fanInRanking = topN(fanIn, 20, 'fanIn');
const fanOutRanking = topN(fanOut, 20, 'fanOut');

// Entry point candidates
const ENTRY_FILENAMES = new Set([
  'index.ts', 'index.js', 'main.ts', 'main.js', 'app.ts', 'app.js',
  'server.ts', 'server.js', 'mod.rs', 'main.go', 'main.py', 'main.rs',
  'manage.py', 'app.py', 'wsgi.py', 'asgi.py', 'run.py', '__main__.py',
  'Application.java', 'Main.java', 'Program.cs', 'config.ru', 'index.php',
  'App.swift', 'Application.kt', 'main.cpp', 'main.c'
]);

// compute percentile thresholds for fan-out (top 10%) and fan-in (bottom 25%)
const allFanOutVals = Array.from(fanOut.values()).sort((a, b) => a - b);
const allFanInVals = Array.from(fanIn.values()).sort((a, b) => a - b);

function percentileThreshold(sortedVals, percentileFromTop) {
  if (sortedVals.length === 0) return 0;
  const idx = Math.floor(sortedVals.length * (1 - percentileFromTop));
  return sortedVals[Math.min(idx, sortedVals.length - 1)];
}

const fanOutTop10Threshold = percentileThreshold(allFanOutVals, 0.10);
const fanInBottom25Threshold = allFanInVals[Math.floor(allFanInVals.length * 0.25)] ?? 0;

const entryPointCandidates = [];

for (const n of nodes) {
  let score = 0;
  const filePath = n.filePath || '';
  const baseName = n.name || path.basename(filePath);
  const depth = filePath.split('/').filter(Boolean).length; // number of path segments

  if (n.type === 'file' || n.type === undefined) {
    if (ENTRY_FILENAMES.has(baseName)) score += 3;
    if (depth <= 2) score += 1; // root or one level deep
    if (fanOut.get(n.id) >= fanOutTop10Threshold && fanOut.get(n.id) > 0) score += 1;
    if (fanIn.get(n.id) <= fanInBottom25Threshold) score += 1;
  } else if (n.type === 'document') {
    if (baseName === 'README.md' && depth <= 1) score += 5;
    else if (baseName.endsWith('.md') && depth <= 1) score += 2;
  }

  if (score > 0) {
    entryPointCandidates.push({
      id: n.id,
      score,
      name: n.name,
      summary: n.summary || ''
    });
  }
}

entryPointCandidates.sort((a, b) => b.score - a.score);
const topEntryPointCandidates = entryPointCandidates.slice(0, 5);

// Determine top CODE entry point (type === 'file') for BFS start
const topCodeEntry = entryPointCandidates.find(c => {
  const node = nodeById.get(c.id);
  return node && node.type === 'file';
});

// BFS from top code entry point following imports/calls edges (forward only)
const adjacency = new Map();
for (const n of nodes) adjacency.set(n.id, []);
for (const e of edges) {
  if ((e.type === 'imports' || e.type === 'calls') && adjacency.has(e.source) && nodeById.has(e.target)) {
    adjacency.get(e.source).push(e.target);
  }
}

let bfsTraversal = { startNode: null, order: [], depthMap: {}, byDepth: {} };

if (topCodeEntry) {
  const startId = topCodeEntry.id;
  const visited = new Set([startId]);
  const order = [startId];
  const depthMap = { [startId]: 0 };
  const queue = [startId];

  while (queue.length > 0) {
    const cur = queue.shift();
    const curDepth = depthMap[cur];
    const neighbors = adjacency.get(cur) || [];
    for (const nb of neighbors) {
      if (!visited.has(nb)) {
        visited.add(nb);
        depthMap[nb] = curDepth + 1;
        order.push(nb);
        queue.push(nb);
      }
    }
  }

  const byDepth = {};
  for (const [id, d] of Object.entries(depthMap)) {
    if (!byDepth[d]) byDepth[d] = [];
    byDepth[d].push(id);
  }

  bfsTraversal = {
    startNode: startId,
    order,
    depthMap,
    byDepth
  };
}

// Non-code file inventory
const nonCodeFiles = {
  documentation: [],
  infrastructure: [],
  data: [],
  config: []
};

for (const n of nodes) {
  const entry = { id: n.id, name: n.name, type: n.type, summary: n.summary || '' };
  if (n.type === 'document') {
    nonCodeFiles.documentation.push(entry);
  } else if (n.type === 'service' || n.type === 'pipeline' || n.type === 'resource') {
    nonCodeFiles.infrastructure.push(entry);
  } else if (n.type === 'table' || n.type === 'schema' || n.type === 'endpoint') {
    nonCodeFiles.data.push(entry);
  } else if (n.type === 'config') {
    nonCodeFiles.config.push(entry);
  }
}

// Tightly coupled clusters
// Build bidirectional edge set for imports/calls
const edgeSet = new Set();
for (const e of edges) {
  if (e.type === 'imports' || e.type === 'calls') {
    if (nodeById.has(e.source) && nodeById.has(e.target)) {
      edgeSet.add(e.source + '->' + e.target);
    }
  }
}

const clusterPairs = [];
for (const e of edges) {
  if ((e.type === 'imports' || e.type === 'calls') && nodeById.has(e.source) && nodeById.has(e.target)) {
    const reverseKey = e.target + '->' + e.source;
    if (edgeSet.has(reverseKey) && e.source < e.target) {
      clusterPairs.push([e.source, e.target]);
    }
  }
}

// Union-find style expansion: start with bidirectional pairs as seed clusters,
// then expand by adding nodes connecting to 2+ existing cluster members.
const clusters = [];
const usedInCluster = new Set();

for (const [a, b] of clusterPairs) {
  if (usedInCluster.has(a) || usedInCluster.has(b)) continue;
  const clusterNodes = new Set([a, b]);

  // try expand: any node with edges (either direction) to 2+ members
  let expanded = true;
  while (expanded && clusterNodes.size < 5) {
    expanded = false;
    for (const n of nodes) {
      if (clusterNodes.has(n.id)) continue;
      let connections = 0;
      for (const e of edges) {
        if (!(e.type === 'imports' || e.type === 'calls')) continue;
        if (e.source === n.id && clusterNodes.has(e.target)) connections++;
        if (e.target === n.id && clusterNodes.has(e.source)) connections++;
      }
      if (connections >= 2) {
        clusterNodes.add(n.id);
        expanded = true;
        break;
      }
    }
  }

  const nodeArr = Array.from(clusterNodes);
  let edgeCount = 0;
  for (const e of edges) {
    if ((e.type === 'imports' || e.type === 'calls') && nodeArr.includes(e.source) && nodeArr.includes(e.target)) {
      edgeCount++;
    }
  }

  clusters.push({ nodes: nodeArr, edgeCount });
  for (const id of nodeArr) usedInCluster.add(id);
}

clusters.sort((a, b) => b.edgeCount - a.edgeCount);
const topClusters = clusters.slice(0, 10);

// Layers
const layersOut = {
  count: layers.length,
  list: layers.map(l => ({ id: l.id, name: l.name, description: l.description }))
};

// Node summary index
const nodeSummaryIndex = {};
for (const n of nodes) {
  nodeSummaryIndex[n.id] = { name: n.name, type: n.type, summary: n.summary || '' };
}

const result = {
  scriptCompleted: true,
  entryPointCandidates: topEntryPointCandidates,
  fanInRanking,
  fanOutRanking,
  bfsTraversal,
  nonCodeFiles,
  clusters: topClusters,
  layers: layersOut,
  nodeSummaryIndex,
  totalNodes: nodes.length,
  totalEdges: edges.length
};

try {
  fs.writeFileSync(outputPath, JSON.stringify(result, null, 2), 'utf8');
} catch (e) {
  fail('Could not write output file: ' + e.message);
}

console.log('Analysis complete. Wrote results to ' + outputPath);
process.exit(0);
