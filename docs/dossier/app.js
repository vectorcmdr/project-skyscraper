/* DOSSIER PAGE - TECH DEMO SHOWCASE */

import * as THREE from 'three';

/* HELPERS */
function resizeCanvas(canvas, container) {
  const rect = container.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  canvas.width = rect.width * dpr;
  canvas.height = rect.height * dpr;
  canvas.style.width = rect.width + 'px';
  canvas.style.height = rect.height + 'px';
  const ctx = canvas.getContext('2d');
  if (ctx) ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return { w: rect.width, h: rect.height, dpr };
}

function lerp(a, b, t) { return a + (b - a) * t; }

/* ===== 1. CYBERBRAIN SPHERE ===== */
function initCyberbrain() {
  const container = document.getElementById('cyberbrainContainer');
  if (!container) return;

  const rect = container.getBoundingClientRect();
  const w = rect.width, h = rect.height;

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x050505);

  const camera = new THREE.PerspectiveCamera(35, w / h, 0.1, 20);
  camera.position.set(0, 0.1, 3.2);

  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
  renderer.setSize(w, h);
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.setClearColor(0x050505);
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.0;
  container.appendChild(renderer.domElement);

  /* ---- Lights (Synchron-style: ambient + 3 directionals) ---- */
  const ambient = new THREE.AmbientLight(0x002211, 0.4);
  scene.add(ambient);
  const keyLight = new THREE.DirectionalLight(0x88ffbb, 1.5);
  keyLight.position.set(3, 3, 4);
  scene.add(keyLight);
  const fillLight = new THREE.DirectionalLight(0x004422, 0.4);
  fillLight.position.set(-3, 1, 2);
  scene.add(fillLight);
  const rimLight = new THREE.DirectionalLight(0x00aa55, 0.3);
  rimLight.position.set(-1, 2, -3);
  scene.add(rimLight);

  /* ---- Brain mesh generation from SDF ---- */
  const brainGroup = new THREE.Group();

  /* Volume test: is point inside the brain? */
  function isInsideBrain(x, y, z) {
    const ax = Math.abs(x);
    const sx = x < 0 ? -1 : 1;
    const fissureW = 0.04 + 0.04 * Math.max(0, 1 - Math.abs(z) / 1.0);
    if (ax < fissureW && z < 0.6 && y > -0.2) return false;
    if (ax < 0.02 && z < 0.4 && y > -0.3) return false;
    const zNorm = z / 0.85;
    const rx = 0.65 + 0.15 * Math.max(0, -zNorm);
    const ry = 0.50;
    const rz = 0.80;
    const dCerebrum = (x*x)/(rx*rx) + (y*y)/(ry*ry) + (z*z)/(rz*rz);
    let inside = dCerebrum <= 1.0;
    const tCx = sx * 0.60, tCy = -0.08, tCz = 0.20;
    const tRx = 0.12, tRy = 0.14, tRz = 0.22;
    const dTemporal = ((x-tCx)*(x-tCx))/(tRx*tRx) + ((y-tCy)*(y-tCy))/(tRy*tRy) + ((z-tCz)*(z-tCz))/(tRz*tRz);
    if (dTemporal <= 1.0) inside = true;
    const cbCx = 0, cbCy = -0.35, cbCz = -0.78;
    const cbRx = 0.32, cbRy = 0.20, cbRz = 0.22;
    const dCerebellum = ((x-cbCx)*(x-cbCx))/(cbRx*cbRx) + ((y-cbCy)*(y-cbCy))/(cbRy*cbRy) + ((z-cbCz)*(z-cbCz))/(cbRz*cbRz);
    if (dCerebellum <= 1.0) inside = true;
    const bsCx = 0, bsCy = -0.73, bsCz = -0.28;
    const bsRx = 0.10, bsRy = 0.28, bsRz = 0.10;
    const dBrainstem = ((x-bsCx)*(x-bsCx))/(bsRx*bsRx) + ((y-bsCy)*(y-bsCy))/(bsRy*bsRy) + ((z-bsCz)*(z-bsCz))/(bsRz*bsRz);
    if (dBrainstem <= 1.0) inside = true;
    return inside;
  }

  /* Find outer surface point of brain along a given direction */
  function findSurface(dx, dy, dz) {
    const maxR = 2.0;
    const step = 0.02;
    let lastInside = null;
    let lastT = 0;
    for (let t = step; t <= maxR; t += step) {
      if (isInsideBrain(dx * t, dy * t, dz * t)) {
        lastInside = { x: dx * t, y: dy * t, z: dz * t };
        lastT = t;
      } else if (lastInside) {
        let lo = lastT, hi = t;
        for (let i = 0; i < 20; i++) {
          const mid = (lo + hi) / 2;
          if (isInsideBrain(dx * mid, dy * mid, dz * mid)) lo = mid;
          else hi = mid;
        }
        return { x: dx * lo, y: dy * lo, z: dz * lo };
      }
    }
    if (lastInside) return lastInside;
    return null;
  }

  /* Generate solid mesh by projecting sphere vertices onto SDF surface */
  const sphereGeo = new THREE.SphereGeometry(1, 80, 60);
  const pos = sphereGeo.attributes.position;
  const idx = sphereGeo.index;
  const count = pos.count;
  const verts = new Float32Array(count * 3);
  for (let i = 0; i < count; i++) {
    const x = pos.getX(i), y = pos.getY(i), z = pos.getZ(i);
    const len = Math.sqrt(x*x + y*y + z*z) || 1;
    const surf = findSurface(x/len, y/len, z/len);
    if (surf) {
      verts[i*3] = surf.x;
      verts[i*3+1] = surf.y;
      verts[i*3+2] = surf.z;
    } else {
      verts[i*3] = x * 0.01;
      verts[i*3+1] = y * 0.01;
      verts[i*3+2] = z * 0.01;
    }
  }
  const brainGeo = new THREE.BufferGeometry();
  brainGeo.setAttribute('position', new THREE.BufferAttribute(verts, 3));
  brainGeo.setIndex(idx);
  brainGeo.computeVertexNormals();

  /* Bounding box for glow region placement */
  brainGeo.computeBoundingBox();
  const bb = brainGeo.boundingBox;
  const bmid = new THREE.Vector3(); bb.getCenter(bmid);
  const bsz = new THREE.Vector3(); bb.getSize(bsz);

  /* Synchron-style shader material — matte gray with animated green glows */
  const brainMat = new THREE.ShaderMaterial({
    vertexShader: `
      varying vec3 vNormal;
      varying vec3 vViewPos;
      varying vec3 vWorldPos;
      void main() {
        vNormal = normalize(normalMatrix * normal);
        vec4 mvPos = modelViewMatrix * vec4(position, 1.0);
        vViewPos = -mvPos.xyz;
        vWorldPos = (modelMatrix * vec4(position, 1.0)).xyz;
        gl_Position = projectionMatrix * mvPos;
      }
    `,
    fragmentShader: `
      varying vec3 vNormal;
      varying vec3 vViewPos;
      varying vec3 vWorldPos;
      uniform float uTime;
      #define NUM_GLOWS 8
      uniform vec3 uGlowCenters[NUM_GLOWS];
      uniform float uGlowRadii[NUM_GLOWS];
      uniform float uGlowPhases[NUM_GLOWS];
      void main() {
        vec3 n = normalize(vNormal);
        vec3 v = normalize(vViewPos);
        vec3 l1 = normalize(vec3(3.0, 3.0, 4.0));
        vec3 l2 = normalize(vec3(-3.0, 1.0, 2.0));
        vec3 l3 = normalize(vec3(-1.0, 2.0, -3.0));
        float diff1 = (dot(n, l1) * 0.5 + 0.5) * 0.45;
        float diff2 = (dot(n, l2) * 0.5 + 0.5) * 0.25;
        float diff3 = (dot(n, l3) * 0.5 + 0.5) * 0.15;
        float ambient = 0.4;
        float lighting = ambient + diff1 + diff2 + diff3;
        vec3 brainColor = vec3(0.25, 0.28, 0.27) * lighting;
        vec3 greenColor = vec3(0.0, 1.0, 0.4);
        vec3 whiteGlow = vec3(0.6, 1.0, 0.7);
        for (int i = 0; i < NUM_GLOWS; i++) {
          float dist = distance(vWorldPos, uGlowCenters[i]);
          float baseRadius = uGlowRadii[i];
          float phase = uGlowPhases[i];
          float cycle = mod(uTime + phase, 12.0);
          float radiusScale = smoothstep(0.0, 3.0, cycle) * (1.0 - smoothstep(5.0, 7.0, cycle));
          float currentRadius = baseRadius * radiusScale;
          float intensity = smoothstep(0.0, 2.0, cycle) * (1.0 - smoothstep(4.5, 7.0, cycle));
          float spread = smoothstep(currentRadius, currentRadius * 0.1, dist) * intensity;
          float core = smoothstep(currentRadius * 0.6, 0.0, dist) * intensity;
          brainColor = mix(brainColor, greenColor * 1.1, spread * 0.5);
          brainColor += whiteGlow * core * core * 0.35;
          brainColor += greenColor * spread * 0.2;
        }
        gl_FragColor = vec4(brainColor, 1.0);
      }
    `,
    uniforms: {
      uTime: { value: 0 },
      uGlowCenters: { value: [
        new THREE.Vector3(bmid.x, bmid.y + bsz.y * 0.4, bmid.z + bsz.z * 0.05),
        new THREE.Vector3(bmid.x - bsz.x * 0.35, bmid.y + bsz.y * 0.15, bmid.z + bsz.z * 0.25),
        new THREE.Vector3(bmid.x + bsz.x * 0.35, bmid.y + bsz.y * 0.15, bmid.z + bsz.z * 0.25),
        new THREE.Vector3(bmid.x - bsz.x * 0.35, bmid.y - bsz.y * 0.1, bmid.z - bsz.z * 0.15),
        new THREE.Vector3(bmid.x + bsz.x * 0.35, bmid.y - bsz.y * 0.1, bmid.z - bsz.z * 0.15),
        new THREE.Vector3(bmid.x, bmid.y + bsz.y * 0.15, bmid.z - bsz.z * 0.35),
        new THREE.Vector3(bmid.x - bsz.x * 0.2, bmid.y + bsz.y * 0.35, bmid.z - bsz.z * 0.1),
        new THREE.Vector3(bmid.x + bsz.x * 0.2, bmid.y + bsz.y * 0.35, bmid.z - bsz.z * 0.1),
      ]},
      uGlowRadii: { value: [bsz.y*0.4, bsz.y*0.35, bsz.y*0.35, bsz.y*0.3, bsz.y*0.3, bsz.y*0.3, bsz.y*0.28, bsz.y*0.28] },
      uGlowPhases: { value: [0.0, 4.0, 8.0, 2.0, 6.0, 3.5, 7.0, 1.0] },
    },
    side: THREE.DoubleSide,
  });

  const brainMesh = new THREE.Mesh(brainGeo, brainMat);
  brainGroup.add(brainMesh);

  brainGroup.scale.set(1.7, 1.7, 1.7);
  brainGroup.rotation.y = Math.PI / 2;
  brainGroup.position.y = -0.05;
  scene.add(brainGroup);

  /* ---- Mouse drag rotation (Synchron-style) ---- */
  let isDragging = false;
  let prevMouseX = 0, prevMouseY = 0;
  let rotY = Math.PI / 2, rotX = 0;

  renderer.domElement.addEventListener('pointerdown', (e) => {
    isDragging = true;
    prevMouseX = e.clientX;
    prevMouseY = e.clientY;
  });
  window.addEventListener('pointermove', (e) => {
    if (!isDragging) return;
    const dx = e.clientX - prevMouseX;
    const dy = e.clientY - prevMouseY;
    rotY += dx * 0.005;
    rotX += dy * 0.003;
    rotX = Math.max(-0.8, Math.min(0.8, rotX));
    prevMouseX = e.clientX;
    prevMouseY = e.clientY;
  });
  window.addEventListener('pointerup', () => { isDragging = false; });
  window.addEventListener('pointerleave', () => { isDragging = false; });

  /* ---- Stars background ---- */
  const starCount = 800;
  const starGeo = new THREE.BufferGeometry();
  const starPos = new Float32Array(starCount * 3);
  for (let i = 0; i < starCount * 3; i++) starPos[i] = (Math.random() - 0.5) * 40;
  starGeo.setAttribute('position', new THREE.BufferAttribute(starPos, 3));
  const starMat = new THREE.PointsMaterial({
    color: 0x226644, size: 0.02, transparent: true, opacity: 0.3,
    blending: THREE.AdditiveBlending,
  });
  const stars = new THREE.Points(starGeo, starMat);
  scene.add(stars);

  /* ---- Resize ---- */
  function resize() {
    const r = container.getBoundingClientRect();
    camera.aspect = r.width / r.height;
    camera.updateProjectionMatrix();
    renderer.setSize(r.width, r.height);
  }
  window.addEventListener('resize', resize);

  /* ---- Animate ---- */
  function animate(time) {
    requestAnimationFrame(animate);
    const t = time * 0.001;

    brainMat.uniforms.uTime.value = t;

    if (!isDragging) rotY += 0.005;
    brainGroup.rotation.y = rotY;
    brainGroup.rotation.x = rotX;
    brainGroup.position.y = -0.05 + Math.sin(t * 0.5) * 0.015;

    renderer.render(scene, camera);
  }

  animate(0);
}

/* ===== 2. WAVEFORM GRAPH ===== */
function initWaveform() {
  const container = document.getElementById('waveformContainer');
  const canvas = document.getElementById('waveformCanvas');
  if (!container || !canvas) return;

  let dims = resizeCanvas(canvas, container);
  let scroll = 0;
  let phase = 0;

  function drawFrame() {
    dims = resizeCanvas(canvas, container);
    const ctx = canvas.getContext('2d');
    const { w, h } = dims;
    const dpr = window.devicePixelRatio || 1;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    ctx.fillStyle = '#0a0a0a';
    ctx.fillRect(0, 0, w, h);

    const pad = 12;
    const gw = w - pad * 2;
    const gh = h - pad * 2;
    const cx = pad;
    const cy = pad;
    const midY = cy + gh / 2;

    /* Grid notches */
    ctx.strokeStyle = 'rgba(255,255,255,0.05)';
    ctx.lineWidth = 0.5;
    const notchSpacing = 30;
    for (let x = 0; x <= gw; x += notchSpacing) {
      ctx.beginPath();
      ctx.moveTo(cx + x, cy);
      ctx.lineTo(cx + x, cy + gh);
      ctx.stroke();
    }
    for (let y = 0; y <= gh; y += notchSpacing) {
      ctx.beginPath();
      ctx.moveTo(cx, cy + y);
      ctx.lineTo(cx + gw, cy + y);
      ctx.stroke();
    }

    /* XY notch ticks */
    ctx.strokeStyle = 'rgba(255,255,255,0.15)';
    ctx.lineWidth = 1;
    for (let x = 0; x <= gw; x += notchSpacing * 2) {
      ctx.beginPath();
      ctx.moveTo(cx + x, cy + gh);
      ctx.lineTo(cx + x, cy + gh + 5);
      ctx.stroke();
    }
    for (let y = 0; y <= gh; y += notchSpacing * 2) {
      ctx.beginPath();
      ctx.moveTo(cx, cy + y);
      ctx.lineTo(cx - 5, cy + y);
      ctx.stroke();
    }

    /* Center (zero) line */
    ctx.strokeStyle = 'rgba(255,255,255,0.7)';
    ctx.lineWidth = 1;
    ctx.setLineDash([3, 5]);
    ctx.beginPath();
    ctx.moveTo(cx, midY);
    ctx.lineTo(cx + gw, midY);
    ctx.stroke();
    ctx.setLineDash([]);

    /* Waveforms - clean sine, more vertical space */
    const amplitude = gh * 0.35;

    function drawWave(offset, phaseOff, color, ampScale) {
      ctx.strokeStyle = color;
      ctx.lineWidth = 1.5;
      ctx.shadowColor = color;
      ctx.shadowBlur = 6;
      ctx.beginPath();
      const cycles = 3;
      for (let px = 0; px <= gw; px++) {
        const t = (px / gw) * Math.PI * 2 * cycles + scroll;
        const yVal = Math.sin(t + phaseOff) * amplitude * ampScale;
        const py = midY + offset + yVal;
        px === 0 ? ctx.moveTo(cx + px, py) : ctx.lineTo(cx + px, py);
      }
      ctx.stroke();
      ctx.shadowBlur = 0;
    }

    drawWave(0, phase, 'rgba(0,170,255,0.85)', 0.95);
    drawWave(0, phase + 0.6, 'rgba(255,0,170,0.75)', 0.95);
  }

  function animate() {
    scroll += 0.035;
    phase += 0.012;
    drawFrame();
    requestAnimationFrame(animate);
  }

  window.addEventListener('resize', drawFrame);
  animate();
}

/* ===== 3. NEURAL GRID ===== */
function initNeuralGrid() {
  const grid = document.getElementById('grid');
  const densityEl = document.getElementById('synapticDensity');
  if (!grid) return;

  const totalBlocks = 16 * 16;
  let blocks = [];

  for (let i = 0; i < totalBlocks; i++) {
    const block = document.createElement('div');
    block.className = 'data-block';
    block.style.background = '#000000';
    grid.appendChild(block);
    blocks.push(block);
  }

  function updateDensity() {
    if (!densityEl) return;
    const active = blocks.filter(b => b.classList.contains('active')).length;
    densityEl.textContent = ((active / totalBlocks) * 100).toFixed(1);
  }

  function activateRandom() {
    const inactive = blocks.filter(b => !b.classList.contains('active'));
    if (inactive.length === 0) return;
    const block = inactive[Math.floor(Math.random() * inactive.length)];
    block.style.background = '#ddd';
    block.classList.add('active');
    updateDensity();
  }

  function deactivateRandom() {
    const active = blocks.filter(b => b.classList.contains('active'));
    if (active.length === 0) return;
    const block = active[Math.floor(Math.random() * active.length)];
    block.style.background = '#000000';
    block.classList.remove('active');
    updateDensity();
  }

  setInterval(() => {
    const actions = Math.floor(Math.random() * 3) + 2;
    for (let i = 0; i < actions; i++) {
      if (Math.random() < 0.5) {
        activateRandom();
      } else {
        deactivateRandom();
      }
    }
  }, 320);

  for (let i = 0; i < 45; i++) {
    setTimeout(() => activateRandom(), i * 35);
  }
}

/* ===== 4. FOLDING PANEL ===== */
function initFoldingPanel() {
  const bar = document.getElementById('foldBar');
  const box = document.getElementById('foldBox');
  if (!bar || !box) return;

  let folded = false;

  bar.addEventListener('click', function() {
    folded = !folded;
    box.classList.toggle('folded', folded);
    bar.innerHTML = folded ? '&#9660; EXPAND' : '&#9650; COLLAPSE';
  });
}

/* ===== 5. SPINNING GLOBE (inside warning blocks) ===== */
function initGlobe() {
  const canvas = document.getElementById('globeCanvas');
  if (!canvas) return;

  const size = 50;
  const dpr = window.devicePixelRatio || 1;
  canvas.width = size * dpr;
  canvas.height = size * dpr;
  canvas.style.width = size + 'px';
  canvas.style.height = size + 'px';

  const ctx = canvas.getContext('2d');
  let angle = 0;

  function draw() {
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, size, size);

    const cx = size / 2, cy = size / 2, r = 20;

    /* Globe circle */
    ctx.strokeStyle = '#0088ff';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.arc(cx, cy, r, 0, Math.PI * 2);
    ctx.stroke();

    /* Latitude lines */
    for (let lat = -60; lat <= 60; lat += 30) {
      const rad = r * Math.cos(lat * Math.PI / 180);
      const yOff = r * Math.sin(lat * Math.PI / 180);
      ctx.beginPath();
      ctx.ellipse(cx, cy + yOff, rad, rad * 0.3, 0, 0, Math.PI * 2);
      ctx.stroke();
    }

    /* Longitude lines */
    for (let lon = 0; lon < 360; lon += 30) {
      const a = (lon + angle) * Math.PI / 180;
      ctx.beginPath();
      ctx.ellipse(cx, cy, r * Math.abs(Math.cos(a)), r, 0, 0, Math.PI * 2);
      ctx.stroke();
    }

    /* Highlight band */
    ctx.strokeStyle = 'rgba(0,200,255,0.3)';
    ctx.lineWidth = 2;
    const hA = angle * Math.PI / 180;
    ctx.beginPath();
    ctx.ellipse(cx, cy, r * Math.abs(Math.cos(hA)), r, 0, 0, Math.PI * 2);
    ctx.stroke();

    /* Center dot */
    ctx.fillStyle = '#00ccff';
    ctx.beginPath();
    ctx.arc(cx, cy, 2, 0, Math.PI * 2);
    ctx.fill();
  }

  function animate() {
    angle += 0.5;
    if (angle >= 360) angle -= 360;
    draw();
    requestAnimationFrame(animate);
  }

  animate();
}

/* ===== 6. GAUGE GRID ===== */
function initGaugeGrid() {
  const canvas = document.getElementById('gaugeGridCanvas');
  const container = document.getElementById('gaugeGridContainer');
  if (!canvas || !container) return;

  let dims;
  let needlePos = 0.5;
  let needleTarget = 0.5;
  const gridSpacing = 30;

  function resize() {
    dims = resizeCanvas(canvas, container);
  }
  resize();
  window.addEventListener('resize', resize);

  function draw() {
    const ctx = canvas.getContext('2d');
    const dpr = window.devicePixelRatio || 1;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    const { w, h } = dims;
    ctx.fillStyle = '#0a0a0a';
    ctx.fillRect(0, 0, w, h);

    const gaugeW = 20;
    const gridLeft = gaugeW + 15;
    const gridRight = w - 10;

    /* Gauge track on left */
    const gaugeTop = 15;
    const gaugeBot = h - 15;
    const gaugeH = gaugeBot - gaugeTop;

    ctx.strokeStyle = '#333';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(3, gaugeTop);
    ctx.lineTo(3, gaugeBot);
    ctx.moveTo(3, gaugeTop);
    ctx.lineTo(gaugeW + 2, gaugeTop);
    ctx.moveTo(3, gaugeBot);
    ctx.lineTo(gaugeW + 2, gaugeBot);
    ctx.stroke();

    /* Gauge tick marks (from left edge inward) */
    for (let i = 0; i <= 10; i++) {
      const y = gaugeTop + (gaugeH * i) / 10;
      const tickLen = i % 5 === 0 ? 10 : 5;
      ctx.strokeStyle = i % 5 === 0 ? 'rgba(255,255,255,0.3)' : 'rgba(255,255,255,0.1)';
      ctx.beginPath();
      ctx.moveTo(3, y);
      ctx.lineTo(3 + tickLen, y);
      ctx.stroke();
    }

    /* Gauge needle */
    const needleY = gaugeTop + gaugeH * (1 - needlePos);
    ctx.strokeStyle = '#d00';
    ctx.lineWidth = 2;
    ctx.shadowColor = '#d00';
    ctx.shadowBlur = 8;
    ctx.beginPath();
    ctx.moveTo(3, needleY);
    ctx.lineTo(gaugeW + 5, needleY);
    ctx.stroke();
    ctx.shadowBlur = 0;

    /* Needle dot */
    ctx.fillStyle = '#ff2222';
    ctx.beginPath();
    ctx.arc(gaugeW, needleY, 3, 0, Math.PI * 2);
    ctx.fill();

    /* Grid with + intersections */
    const gridW = gridRight - gridLeft;
    const cols = Math.floor(gridW / gridSpacing);
    const rows = Math.floor(gaugeH / gridSpacing);
    const actualSpacingX = gridW / cols;
    const actualSpacingY = gaugeH / rows;

    ctx.strokeStyle = 'rgba(255,255,255,0.04)';
    ctx.lineWidth = 0.5;

    for (let r = 0; r <= rows; r++) {
      ctx.beginPath();
      ctx.moveTo(gridLeft, gaugeTop + r * actualSpacingY);
      ctx.lineTo(gridRight, gaugeTop + r * actualSpacingY);
      ctx.stroke();
    }
    for (let c = 0; c <= cols; c++) {
      ctx.beginPath();
      ctx.moveTo(gridLeft + c * actualSpacingX, gaugeTop);
      ctx.lineTo(gridLeft + c * actualSpacingX, gaugeBot);
      ctx.stroke();
    }

    /* + markers at intersections */
    const crossSize = 3;
    for (let r = 0; r <= rows; r++) {
      for (let c = 0; c <= cols; c++) {
        const px = gridLeft + c * actualSpacingX;
        const py = gaugeTop + r * actualSpacingY;
        const glow = 0.3 + Math.sin(r * 2.3 + c * 1.7 + Date.now() * 0.001) * 0.15;
        ctx.strokeStyle = `rgba(0, ${Math.floor(150 + glow * 100)}, 255, ${0.1 + glow * 0.2})`;
        ctx.lineWidth = 0.8;
        ctx.beginPath();
        ctx.moveTo(px - crossSize, py);
        ctx.lineTo(px + crossSize, py);
        ctx.moveTo(px, py - crossSize);
        ctx.lineTo(px, py + crossSize);
        ctx.stroke();
      }
    }
  }

  function animate() {
    if (Math.random() < 0.02) {
      needleTarget = Math.random();
    }
    needlePos += (needleTarget - needlePos) * 0.01;
    draw();
    requestAnimationFrame(animate);
  }

  animate();
}

/* ===== 7. SHUFFLE DECK ===== */
function initShuffleDeck() {
  const cards = [
    document.getElementById('shuffle0'),
    document.getElementById('shuffle1'),
    document.getElementById('shuffle2'),
  ].filter(Boolean);

  if (cards.length < 3) return;

  const positions = [
    { x: 0, y: 0 },
    { x: -4, y: -3 },
    { x: -8, y: -6 },
  ];

  const cardPos = [0, 1, 2];

  function applyPositions() {
    cards.forEach((card, i) => {
      const p = positions[cardPos[i]];
      card.style.transform = `translate(${p.x}px, ${p.y}px)`;
      card.style.zIndex = 3 - cardPos[i];
    });
  }

  function cycle() {
    const first = cardPos.shift();
    cardPos.push(first);
    applyPositions();
  }

  applyPositions();
  setInterval(cycle, 3000);
}

/* ===== 8. DNA BELT TAPE ===== */
function initDnaScanner() {
  const canvas = document.getElementById('dnaCanvas');
  const container = document.getElementById('dnaContainer');
  if (!canvas || !container) return;

  const chars = 'CTAG';
  let dims;
  let scrollY = 0;
  const charH = 14;
  const tapeWidthRatio = 0.85;
  let charCache = {};

  function resize() {
    dims = resizeCanvas(canvas, container);
    charCache = {};
  }
  resize();
  window.addEventListener('resize', resize);

  function getChar(r, c) {
    const key = `${r},${c}`;
    if (!charCache[key]) {
      charCache[key] = chars[Math.floor(Math.random() * chars.length)];
    }
    return charCache[key];
  }

  function draw() {
    const ctx = canvas.getContext('2d');
    const dpr = window.devicePixelRatio || 1;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    const { w, h } = dims;

    ctx.fillStyle = '#080808';
    ctx.fillRect(0, 0, w, h);

    /* Tape strip — full height, centered width */
    const tw = Math.round(w * tapeWidthRatio);
    const tx = Math.round((w - tw) / 2);
    const tapeTop = 2;
    const tapeBot = h - 2;
    const tapeH = tapeBot - tapeTop;

    scrollY -= 0.4;

    /* Tape background */
    ctx.fillStyle = '#0a0a0a';
    ctx.fillRect(tx, tapeTop, tw, tapeH);

    /* DNA characters scrolling up the strip */
    const cols = Math.max(1, Math.floor((tw - 8) / 11));
    const numRows = Math.ceil(tapeH / charH) + 2;
    ctx.font = '10px monospace';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';

    const rowOffset = Math.floor(scrollY / charH);
    const pixelOffset = ((scrollY % charH) + charH) % charH;

    for (let r = -1; r < numRows; r++) {
      const y = tapeTop + r * charH - pixelOffset;
      if (y < tapeTop - charH || y > tapeBot + charH) continue;

      const fadeMargin = 16;
      let rowAlpha = 1;
      const distTop = y - tapeTop;
      const distBot = tapeBot - y;
      if (distTop < fadeMargin) rowAlpha = Math.max(0, distTop / fadeMargin);
      if (distBot < fadeMargin) rowAlpha = Math.min(rowAlpha, Math.max(0, distBot / fadeMargin));

      const hue = 180 + Math.sin(r * 1.3 + scrollY * 0.02) * 20;

      for (let c = 0; c < cols; c++) {
        const x = tx + 6 + c * 11 + Math.round(11 / 2);
        const ch = getChar(r + rowOffset, c);
        const flicker = 0.7 + Math.random() * 0.3;
        ctx.fillStyle = `hsla(${hue}, 80%, ${50 + flicker * 20}%, ${rowAlpha * flicker * 0.85})`;
        ctx.fillText(ch, x, y + charH / 2);
      }
    }

    /* Half-hexagon bulges at chunk 2 and chunk 7 — mask text then draw rail lines */
    const chunkH = tapeH / 8;
    const hexR = tw * 0.18;
    const hw = hexR * Math.sqrt(3) / 2;
    const hh = hexR / 2;

    function drawRailWithBulge(x, dir) {
      ctx.beginPath();
      ctx.moveTo(x, tapeTop);
      [1.5, 6.5].forEach(chunkPos => {
        const cy = tapeTop + chunkPos * chunkH;
        ctx.lineTo(x, cy - 1.5 * hh);
        ctx.lineTo(x + dir * hw / 2, cy - hh);
        ctx.lineTo(x + dir * hw / 2, cy + hh);
        ctx.lineTo(x, cy + 1.5 * hh);
      });
      ctx.lineTo(x, tapeBot);
      ctx.stroke();
    }

    /* Fill hexagon areas with tape background to mask letters underneath */
    ctx.fillStyle = '#0a0a0a';
    [1.5, 6.5].forEach(chunkPos => {
      const cy = tapeTop + chunkPos * chunkH;
      [-1, 1].forEach(side => {
        const rx = side === -1 ? tx + tw : tx;
        const dir = side === -1 ? -1 : 1;
        ctx.beginPath();
        ctx.moveTo(rx, cy - 1.5 * hh);
        ctx.lineTo(rx + dir * hw / 2, cy - hh);
        ctx.lineTo(rx + dir * hw / 2, cy + hh);
        ctx.lineTo(rx, cy + 1.5 * hh);
        ctx.closePath();
        ctx.fill();
      });
    });

    /* Tape edge rails with half-hexagon bulges */
    ctx.strokeStyle = 'rgba(0,200,150,0.35)';
    ctx.lineWidth = 1.5;
    drawRailWithBulge(tx, 1);
    drawRailWithBulge(tx + tw, -1);

    /* Inner rail lines (straight, no bulge) */
    ctx.strokeStyle = 'rgba(0,200,150,0.08)';
    ctx.lineWidth = 0.5;
    ctx.beginPath();
    ctx.moveTo(tx + 2, tapeTop); ctx.lineTo(tx + 2, tapeBot);
    ctx.moveTo(tx + tw - 2, tapeTop); ctx.lineTo(tx + tw - 2, tapeBot);
    ctx.stroke();

    /* Fade at top/bottom edges */
    const gradTop = ctx.createLinearGradient(0, tapeTop, 0, tapeTop + 16);
    gradTop.addColorStop(0, '#080808');
    gradTop.addColorStop(1, 'transparent');
    ctx.fillStyle = gradTop;
    ctx.fillRect(tx, tapeTop, tw, 16);

    const gradBot = ctx.createLinearGradient(0, tapeBot, 0, tapeBot - 16);
    gradBot.addColorStop(0, '#080808');
    gradBot.addColorStop(1, 'transparent');
    ctx.fillStyle = gradBot;
    ctx.fillRect(tx, tapeBot - 16, tw, 16);

    /* Side gradient overlays */
    const gradLeft = ctx.createLinearGradient(0, 0, 20, 0);
    gradLeft.addColorStop(0, '#080808');
    gradLeft.addColorStop(1, 'transparent');
    ctx.fillStyle = gradLeft;
    ctx.fillRect(0, 0, 20, h);

    const gradRight = ctx.createLinearGradient(w, 0, w - 20, 0);
    gradRight.addColorStop(0, '#080808');
    gradRight.addColorStop(1, 'transparent');
    ctx.fillStyle = gradRight;
    ctx.fillRect(w - 20, 0, 20, h);
  }

  function animate() {
    draw();
    requestAnimationFrame(animate);
  }
  animate();
}

/* ===== 9. DATA STREAM ===== */
function initDataStream() {
  const canvas = document.getElementById('dataStreamCanvas');
  const container = document.getElementById('dataStreamContainer');
  if (!canvas || !container) return;

  const chars = '0123456789ABCDEF';
  const words = ['CONNECTION', 'DETECTED', 'ACCESS', 'DENIED', 'BREACH',
    'GHOST', 'SYSTEM', 'ALERT', 'TRACE', 'CIPHER', 'WARNING', 'TARGET'];
  let columns = [];
  let dims;
  let fontSize = 12;
  let colW;
  let frozen = false;
  let freezeTimer = 0;
  let freezeWord = '';
  let freezeLetters = []; /* which column indices get each word letter */

  function resize() {
    dims = resizeCanvas(canvas, container);
    fontSize = Math.max(10, Math.min(16, dims.w / 30));
    colW = fontSize * 1.2;
    const numCols = Math.floor(dims.w / colW);
    while (columns.length < numCols) {
      columns.push({
        y: Math.random() * -dims.h,
        speed: 0.5 + Math.random() * 2,
        delay: Math.random() * 100,
        trail: 10 + Math.floor(Math.random() * 20),
      });
    }
    columns.length = numCols;
  }
  resize();
  window.addEventListener('resize', resize);

  function draw() {
    const ctx = canvas.getContext('2d');
    const dpr = window.devicePixelRatio || 1;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    const { w, h } = dims;

    ctx.fillStyle = 'rgba(8,0,0,0.08)';
    ctx.fillRect(0, 0, w, h);

    ctx.font = `${fontSize}px monospace`;
    ctx.textAlign = 'center';

    columns.forEach((col, ci) => {
      if (!frozen) {
        col.delay -= 1;
        if (col.delay > 0) return;
        col.y += col.speed;
        if (col.y > h + 20) {
          col.y = -20;
          col.speed = 0.5 + Math.random() * 2;
          col.delay = Math.random() * 30;
        }
      }

      const x = ci * colW + colW / 2;
      const isLetterCol = frozen && freezeLetters[ci] !== undefined;

      for (let i = 0; i < col.trail; i++) {
        const ty = col.y - i * fontSize;
        if (ty < -fontSize || ty > h + fontSize) continue;
        const alpha = 1 - (i / col.trail);
        const fade = alpha * alpha;

        let ch;
        if (frozen) {
          /* During freeze: all chars stay frozen. Leading char gets word letter if selected. */
          if (i === 0 && isLetterCol) {
            ch = freezeLetters[ci];
          } else {
            ch = chars[Math.floor(Math.random() * chars.length)];
          }
        } else {
          ch = chars[Math.floor(Math.random() * chars.length)];
        }

        if (i === 0) {
          ctx.fillStyle = `rgba(255,200,200,${fade})`;
          ctx.shadowColor = isLetterCol ? 'rgba(255,255,255,0.8)' : 'rgba(255,0,0,0.5)';
          ctx.shadowBlur = isLetterCol ? 12 : 6;
        } else {
          ctx.fillStyle = `rgba(255,${Math.floor(100 * fade)},${Math.floor(50 * fade)},${fade * 0.6})`;
          ctx.shadowBlur = 0;
        }
        ctx.fillText(ch, x, ty);
      }
      ctx.shadowBlur = 0;
    });
  }

  function animate() {
    /* Check if container dimensions changed (handles delayed layout) */
    const r = container.getBoundingClientRect();
    if (r.width !== dims.w || r.height !== dims.h) {
      resize();
    }

    if (!frozen) {
      if (Math.random() < 0.005) {
        const word = words[Math.floor(Math.random() * words.length)];
        const wordLen = word.length;
        const marginCols = 5;
        const minStart = marginCols;
        const maxStart = columns.length - marginCols - wordLen;
        const safeTop = dims.h * 0.05;
        const safeBot = dims.h * 0.95;

        /* Try up to 20 random start positions to find one where all word columns are in the safe vertical zone */
        let startCol = -1;
        for (let attempt = 0; attempt < 20; attempt++) {
          let testCol;
          if (minStart <= maxStart) {
            testCol = minStart + Math.floor(Math.random() * (maxStart - minStart + 1));
          } else {
            testCol = Math.max(0, Math.floor((columns.length - wordLen) / 2));
          }

          let inZone = true;
          for (let li = 0; li < wordLen; li++) {
            const col = columns[testCol + li];
            if (!col || col.y < safeTop || col.y > safeBot) {
              inZone = false;
              break;
            }
          }

          if (inZone) {
            startCol = testCol;
            break;
          }
        }

        if (startCol >= 0) {
          frozen = true;
          freezeWord = word;
          freezeTimer = 80 + Math.floor(Math.random() * 60);
          freezeLetters = {};
          for (let li = 0; li < word.length; li++) {
            freezeLetters[startCol + li] = word[li];
          }
        }
      }
    } else {
      freezeTimer--;
      if (freezeTimer <= 0) {
        frozen = false;
        freezeWord = '';
        freezeLetters = {};
      }
    }
    draw();
    requestAnimationFrame(animate);
  }

  animate();
}

/* ===== TAB SWITCHING ===== */
function initTabs() {
  document.querySelectorAll('.dossier-tab').forEach(tab => {
    tab.addEventListener('click', function() {
      const tabId = this.dataset.tab;
      document.querySelectorAll('.dossier-tab').forEach(t => t.classList.remove('active'));
      document.querySelectorAll('.dossier-pane').forEach(p => p.classList.remove('active'));
      this.classList.add('active');
      const pane = document.getElementById('pane-' + tabId);
      if (pane) pane.classList.add('active');
    });
  });
}

/* ===== INIT ===== */
document.addEventListener('DOMContentLoaded', function() {
  initTabs();
  initCyberbrain();
  initWaveform();
  initNeuralGrid();
  initFoldingPanel();
  initGlobe();
  initGaugeGrid();
  initShuffleDeck();
  initDnaScanner();
  initDataStream();
});
