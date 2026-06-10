(function () {
  const DATA_ROOT = '../data';

  const COLORS = {
    sitemap:  '#ff2d2d',
    page:     '#88aaff',
    post:     '#4488ff',
    media:    '#aa88aa',
    external: '#8844ff',
  };

  const RADII = {
    sitemap:  14,
    page:     7,
    post:     7,
    media:    5,
    external: 5,
  };

  let svg, g, simulation, nodes, links, zoom;
  let isolateNode = null;

  const container = document.getElementById('graph-container');
  const loadingEl = document.getElementById('loading');
  const infoPanel = document.getElementById('infoPanel');
  const infoLabel = document.getElementById('infoLabel');
  const infoType = document.getElementById('infoType');
  const infoUrl = document.getElementById('infoUrl');
  const infoAuthor = document.getElementById('infoAuthor');
  const infoDate = document.getElementById('infoDate');
  const infoConns = document.getElementById('infoConns');
  const viewAllBtn = document.getElementById('viewAllBtn');

  const nodeCountEl = document.getElementById('nodeCount');
  const linkCountEl = document.getElementById('linkCount');

  var maxDate = null;

  function getRadius(d) {
    var base = RADII[d.type] || 5;
    if (d.connectionCount) {
      base += Math.min(d.connectionCount * 0.3, 8);
    }
    if (maxDate && (d.type === 'page' || d.type === 'post')) {
      var ageDays = (maxDate - new Date(d.date)) / 86400000;
      if (ageDays > 3) {
        base *= 0.6;
      }
    }
    return base;
  }

  function initGraph(dataNodes, dataLinks) {
    loadingEl.classList.add('hidden');

    nodeCountEl.textContent = 'nodes: ' + dataNodes.length;
    linkCountEl.textContent = 'links: ' + dataLinks.length;

    var width = container.clientWidth;
    var height = container.clientHeight;

    svg = d3.select('#graph-container')
      .append('svg')
      .attr('width', width)
      .attr('height', height)
      .style('display', 'block');

    svg.append('defs').append('marker')
      .attr('id', 'arrow')
      .attr('viewBox', '0 -5 10 10')
      .attr('refX', 20)
      .attr('refY', 0)
      .attr('markerWidth', 6)
      .attr('markerHeight', 6)
      .attr('orient', 'auto')
      .append('path')
      .attr('d', 'M0,-5L10,0L0,5')
      .attr('fill', '#444');

    g = svg.append('g');

    zoom = d3.zoom()
      .scaleExtent([0.1, 4])
      .on('zoom', function (event) {
        g.attr('transform', event.transform);
      });
    svg.call(zoom);

    var connCount = {};
    dataLinks.forEach(function (l) {
      connCount[l.source] = (connCount[l.source] || 0) + 1;
      connCount[l.target] = (connCount[l.target] || 0) + 1;
    });
    dataNodes.forEach(function (n) {
      n.connectionCount = connCount[n.id] || 0;
    });

    nodes = dataNodes.map(function (n) { return Object.assign({}, n); });
    links = dataLinks.map(function (l) { return Object.assign({}, l); });

    maxDate = null;
    nodes.forEach(function (n) {
      if (n.date) {
        var t = new Date(n.date);
        if (!isNaN(t) && (!maxDate || t > maxDate)) maxDate = t;
      }
    });

    simulation = d3.forceSimulation(nodes)
      .force('link', d3.forceLink(links).id(function (d) { return d.id; }).distance(130))
      .force('charge', d3.forceManyBody().strength(-400).theta(0.6))
      .force('center', d3.forceCenter(width / 2, height / 2))
      .force('collision', d3.forceCollide().radius(function (d) { return getRadius(d) + 6; }))
      .alphaDecay(0.03)
      .velocityDecay(0.4);

    var link = g.append('g')
      .selectAll('line')
      .data(links)
      .join('line')
      .attr('class', 'link')
      .attr('stroke', '#333')
      .attr('stroke-width', 0.8)
      .attr('stroke-opacity', 0.4);

    var node = g.append('g')
      .selectAll('circle')
      .data(nodes)
      .join('circle')
      .attr('class', 'node-circle')
      .attr('r', getRadius)
      .attr('fill', function (d) { return COLORS[d.type] || '#666'; })
      .on('click', function (event, d) {
        if (d.type === 'sitemap') return;
        event.stopPropagation();
        clickNode(d);
      })
      .on('mouseenter', function (event, d) {
        if (isolateNode || _filterActive) return;
        highlightConnections(d, true);
      })
      .on('mouseleave', function () {
        if (isolateNode || _filterActive) return;
        resetHighlights();
      });

    var label = g.append('g')
      .selectAll('text')
      .data(nodes)
      .join('text')
      .attr('class', 'node-label')
      .attr('dx', function (d) { return getRadius(d) + 3; })
      .attr('dy', 3)
      .attr('fill', '#eee')
      .text(function (d) {
        var label = d.label || d.id;
        if (label.length > 22) label = label.slice(0, 20) + '..';
        return label;
      });

    svg.on('click', function () {
      if (isolateNode) resetGraph();
    });

    simulation.on('tick', function () {
      link
        .attr('x1', function (d) { return d.source.x; })
        .attr('y1', function (d) { return d.source.y; })
        .attr('x2', function (d) { return d.target.x; })
        .attr('y2', function (d) { return d.target.y; });

      node
        .attr('cx', function (d) { return d.x; })
        .attr('cy', function (d) { return d.y; });

      label
        .attr('x', function (d) { return d.x; })
        .attr('y', function (d) { return d.y; });
    });

    simulation.alpha(0.3).restart();
  }

  function getNeighborIds(d) {
    var ids = new Set();
    ids.add(d.id);
    links.forEach(function (l) {
      var sid = typeof l.source === 'object' ? l.source.id : l.source;
      var tid = typeof l.target === 'object' ? l.target.id : l.target;
      if (sid === d.id) ids.add(tid);
      if (tid === d.id) ids.add(sid);
    });
    return ids;
  }

  var savedTransform = null;
  var savedPositions = null;

  function clickNode(d) {
    isolateNode = d;

    savedTransform = d3.zoomTransform(svg.node());
    savedPositions = {};
    nodes.forEach(function (n) { savedPositions[n.id] = { x: n.x, y: n.y }; });

    var neighborIds = getNeighborIds(d);

    g.selectAll('circle')
      .transition().duration(300)
      .attr('opacity', function (n) { return neighborIds.has(n.id) ? 1 : 0; })
      .attr('stroke', function (n) { return n.id === d.id ? '#d00' : '#0a0a0a'; })
      .attr('stroke-width', function (n) { return n.id === d.id ? 3 : 1.5; });

    g.selectAll('line')
      .transition().duration(300)
      .attr('opacity', function (l) {
        var sid = typeof l.source === 'object' ? l.source.id : l.source;
        var tid = typeof l.target === 'object' ? l.target.id : l.target;
        return neighborIds.has(sid) && neighborIds.has(tid) ? 1 : 0;
      })
      .attr('stroke', function (l) {
        var sid = typeof l.source === 'object' ? l.source.id : l.source;
        var tid = typeof l.target === 'object' ? l.target.id : l.target;
        return (sid === d.id || tid === d.id) ? '#d00' : '#333';
      })
      .attr('stroke-width', function (l) {
        var sid = typeof l.source === 'object' ? l.source.id : l.source;
        var tid = typeof l.target === 'object' ? l.target.id : l.target;
        return (sid === d.id || tid === d.id) ? 2 : 0.8;
      });

    g.selectAll('text')
      .transition().duration(300)
      .attr('opacity', function (n) { return neighborIds.has(n.id) ? 1 : 0; });

    if (simulation) {
      simulation.stop();

      var dim = container.getBoundingClientRect();
      var cx = dim.width / 2;
      var cy = dim.height / 2;
      d.x = cx;
      d.y = cy;
      d.fx = cx;
      d.fy = cy;

      var nbors = [];
      neighborIds.forEach(function (id) {
        if (id !== d.id) nbors.push(id);
      });
      var radius = Math.min(dim.width, dim.height) * 0.25;
      nbors.forEach(function (id, i) {
        var angle = (2 * Math.PI * i) / nbors.length;
        var nnode = nodes.filter(function (n) { return n.id === id; })[0];
        if (nnode) {
          nnode.x = cx + radius * Math.cos(angle);
          nnode.y = cy + radius * Math.sin(angle);
        }
      });

      simulation.alpha(0).tick();

      svg.transition().duration(450).call(
        zoom.transform,
        d3.zoomIdentity.translate(0, 0).scale(1)
      );

      setTimeout(function () {
        if (!isolateNode) return;
        d.fx = null;
        d.fy = null;
        simulation.alpha(0.4).restart();
      }, 500);
    }

    showInfo(d);
    viewAllBtn.classList.remove('hidden');
  }

  function resetGraph() {
    isolateNode = null;

    if (savedPositions) {
      nodes.forEach(function (n) {
        var p = savedPositions[n.id];
        if (p) { n.x = p.x; n.y = p.y; }
        n.fx = null;
        n.fy = null;
      });
      savedPositions = null;
    }

    g.selectAll('circle')
      .transition().duration(400)
      .attr('opacity', 1)
      .attr('stroke', '#0a0a0a')
      .attr('stroke-width', 1.5);

    g.selectAll('line')
      .transition().duration(400)
      .attr('opacity', 1)
      .attr('stroke-opacity', 0.4)
      .attr('stroke', '#333')
      .attr('stroke-width', 0.8);

    g.selectAll('text')
      .transition().duration(400)
      .attr('opacity', 1);

    if (savedTransform) {
      svg.transition().duration(450).call(zoom.transform, savedTransform);
      savedTransform = null;
    }

    if (simulation) simulation.alpha(0.3).restart();

    infoPanel.classList.add('hidden');
    viewAllBtn.classList.add('hidden');

    if (filterInput && filterInput.value.trim()) {
      setTimeout(function () { applyFilter(filterInput.value.trim().toLowerCase()); }, 450);
    }
  }

  function highlightConnections(d, show) {
    var neighborIds = new Set();
    neighborIds.add(d.id);
    links.forEach(function (l) {
      if (l.source.id === d.id) neighborIds.add(l.target.id);
      if (l.target.id === d.id) neighborIds.add(l.source.id);
    });

    g.selectAll('circle')
      .attr('opacity', function (n) { return neighborIds.has(n.id) ? 1 : 0.15; });
    g.selectAll('line')
      .attr('stroke-opacity', function (l) {
        return (l.source.id === d.id || l.target.id === d.id) ? 0.8 : 0.05;
      });
    g.selectAll('text')
      .attr('opacity', function (n) { return neighborIds.has(n.id) ? 1 : 0.1; });
  }

  function resetHighlights() {
    if (isolateNode) return;
    g.selectAll('circle').attr('opacity', 1);
    g.selectAll('line').attr('stroke-opacity', 0.4);
    g.selectAll('text').attr('opacity', 1);
  }

  function showInfo(d) {
    infoLabel.textContent = d.label || d.id;
    infoType.textContent = d.type.toUpperCase();

    if (d.url) {
      infoUrl.innerHTML = '<a href="' + esc(d.url) + '" target="_blank" rel="noopener">' + esc(d.url) + '</a>';
    } else {
      infoUrl.textContent = '-';
    }

    infoAuthor.textContent = d.author || '-';
    infoDate.textContent = d.date || '-';
    infoConns.textContent = d.connectionCount || 0;

    infoPanel.classList.remove('hidden');
  }

  viewAllBtn.addEventListener('click', function () {
    resetGraph();
  });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && isolateNode) resetGraph();
  });

  function esc(s) {
    var e = document.createElement('div');
    e.textContent = s;
    return e.innerHTML;
  }

  /* -- OPERATOR ----------------------------------------- */
  function setOperator() {
    var el = document.getElementById('operatorDisplay');
    if (!el) return;
    var name = localStorage.getItem('operator') || '';
    el.textContent = name ? 'Operator: ' + name : 'Operator: <anon>';
  }

  /* -- TRACE (Discourse online status) ------------------- */
  var traceTick = null;

  function fmtElapsed(seconds) {
    var h = Math.floor(seconds / 3600);
    var m = Math.floor((seconds % 3600) / 60);
    var s = Math.floor(seconds % 60);
    if (h > 0) return h + 'h' + String(m).padStart(2,'0') + 'm' + String(s).padStart(2,'0') + 's';
    if (m > 0) return m + 'm' + String(s).padStart(2,'0') + 's';
    return s + 's';
  }

  function renderTrace(data) {
    var el = document.getElementById('traceStatus');
    if (!el) return;

    if (data.state === 'ACTIVE') {
      el.innerHTML = '<span class="trace-dot trace-dot--active"></span><span class="trace-label">TRACE: ACTIVE</span>';
    } else if (data.state === 'LOST' && data.lastSeenAt) {
      var then = new Date(data.lastSeenAt);
      var elapsed = (Date.now() - then.getTime()) / 1000;
      el.innerHTML = '<span class="trace-dot trace-dot--lost"></span><span class="trace-label">TRACE: LOST</span> <span class="trace-time">-' + fmtElapsed(elapsed) + '</span>';
    } else {
      el.innerHTML = '';
    }
  }

  function updateTrace() {
    fetch('../status/trace.json')
      .then(function (r) { return r.ok ? r.json() : Promise.reject(r.status); })
      .then(function (data) {
        renderTrace(data);
        if (data.state === 'LOST') {
          if (traceTick) clearInterval(traceTick);
          traceTick = setInterval(function () { renderTrace(data); }, 1000);
        } else {
          if (traceTick) { clearInterval(traceTick); traceTick = null; }
        }
      })
      .catch(function () {
        var el = document.getElementById('traceStatus');
        if (el) el.innerHTML = '';
      });
  }

  /* -- STATUS BADGE ---------------------------------------- */
  function setStatusOnline(on) {
    var el = document.getElementById('statusBadge');
    if (!el) return;
    if (on) {
      el.textContent = '\u25CF ONLINE';
      el.className = 'topbar-status status-online';
    } else {
      el.textContent = '\u25CF OFFLINE';
      el.className = 'topbar-status';
    }
  }

  /* -- LOAD DATA ----------------------------------------- */
  fetch(DATA_ROOT + '/graph.json')
    .then(function (r) {
      if (!r.ok) throw new Error('Failed to load graph.json');
      return r.json();
    })
    .then(function (data) {
      if (data.nodes && data.links) {
        initGraph(data.nodes, data.links);
      }
      setStatusOnline(true);
    })
    .catch(function (err) {
      loadingEl.textContent = 'ERROR LOADING GRAPH DATA';
      console.error(err);
      setStatusOnline(false);
    });

  setOperator();
  updateTrace();
  setInterval(updateTrace, 30000);

  /* -- FILTER ------------------------------------------- */
  var filterInput = document.getElementById('filterInput');
  var filterClearBtn = document.getElementById('filterClearBtn');
  var filterCount = document.getElementById('filterCount');
  var _filterActive = false;

  function applyFilter(q) {
    if (!q || !g) {
      _filterActive = false;
      if (filterClearBtn) filterClearBtn.classList.add('hidden');
      g.selectAll('circle').attr('opacity', 1).attr('stroke', '#0a0a0a').attr('stroke-width', 1.5);
      g.selectAll('line').attr('stroke-opacity', 0.4).attr('stroke', '#333').attr('stroke-width', 0.8);
      g.selectAll('text').attr('opacity', 1);
      filterCount.textContent = '';
      return;
    }
    _filterActive = true;
    if (filterClearBtn) filterClearBtn.classList.remove('hidden');
    var matchIds = {};
    nodes.forEach(function (n) {
      if (n.label.toLowerCase().indexOf(q) !== -1 || n.type.toLowerCase().indexOf(q) !== -1 || (n.id && n.id.toLowerCase().indexOf(q) !== -1) || (n.url && n.url.toLowerCase().indexOf(q) !== -1)) {
        matchIds[n.id] = true;
      }
    });
    var neighborIds = {};
    links.forEach(function (l) {
      var sid = typeof l.source === 'object' ? l.source.id : l.source;
      var tid = typeof l.target === 'object' ? l.target.id : l.target;
      if (matchIds[sid] || matchIds[tid]) {
        neighborIds[sid] = true;
        neighborIds[tid] = true;
      }
    });
    Object.keys(neighborIds).forEach(function (id) { matchIds[id] = true; });
    var count = Object.keys(matchIds).length;
    filterCount.textContent = count + '/' + nodes.length;
    g.selectAll('circle').attr('opacity', function (n) { return matchIds[n.id] ? 1 : 0.06; });
    g.selectAll('line').attr('stroke-opacity', function (l) {
      var sid = typeof l.source === 'object' ? l.source.id : l.source;
      var tid = typeof l.target === 'object' ? l.target.id : l.target;
      return (matchIds[sid] && matchIds[tid]) ? 0.5 : 0.02;
    }).attr('stroke', '#333').attr('stroke-width', 0.8);
    g.selectAll('text').attr('opacity', function (n) { return matchIds[n.id] ? 1 : 0.04; });
  }

  if (filterInput) {
    filterInput.addEventListener('input', function () {
      applyFilter(this.value.trim().toLowerCase());
    });
  }
  if (filterClearBtn) {
    filterClearBtn.addEventListener('click', function () {
      filterInput.value = '';
      applyFilter('');
      filterInput.focus();
    });
  }

  /* -- RESIZE ------------------------------------------- */
  window.addEventListener('resize', function () {
    if (!svg) return;
    var w = container.clientWidth;
    var h = container.clientHeight;
    svg.attr('width', w).attr('height', h);
    if (simulation) {
      simulation.force('center', d3.forceCenter(w / 2, h / 2));
      simulation.alpha(0.1).restart();
    }
  });
})();
