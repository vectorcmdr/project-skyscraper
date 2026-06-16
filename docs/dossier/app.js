/* DOSSIER PAGE - TECH DEMO SHOWCASE */

import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

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
  scene.fog = new THREE.Fog(0x050505, 5, 12);

  const camera = new THREE.PerspectiveCamera(45, w / h, 0.1, 20);
  camera.position.set(0, 0.5, 5.5);
  camera.lookAt(0, 0, 0);

  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
  renderer.setSize(w, h);
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.setClearColor(0x050505);
  container.appendChild(renderer.domElement);

  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.dampingFactor = 0.05;
  controls.autoRotate = false;
  controls.minDistance = 2.5;
  controls.maxDistance = 10;

  /* ---- Lights (green-tinted) ---- */
  const ambient = new THREE.AmbientLight(0x002211, 0.6);
  scene.add(ambient);
  const light1 = new THREE.DirectionalLight(0x44ff88, 1.5);
  light1.position.set(3, 4, 2);
  scene.add(light1);
  const light2 = new THREE.DirectionalLight(0x004422, 0.5);
  light2.position.set(-3, -2, 1);
  scene.add(light2);

  /* ---- Sphere with worldspace scanline ---- */
  const sphereGeo = new THREE.SphereGeometry(2.0, 48, 32);
  const sphereMat = new THREE.ShaderMaterial({
    uniforms: {
      uTime: { value: 0 },
      uColor1: { value: new THREE.Color(0x00ff66) },
      uColor2: { value: new THREE.Color(0x003318) },
      uGridColor: { value: new THREE.Color(0x00cc66) },
      uScanColor: { value: new THREE.Color(0x88ffbb) },
    },
    transparent: true,
    opacity: 1.0,
    side: THREE.DoubleSide,
    depthWrite: false,
    wireframe: false,
    vertexShader: `
      varying vec2 vUv;
      varying vec3 vNormal;
      varying vec3 vWorldPos;
      void main() {
        vUv = uv;
        vNormal = normalize(normalMatrix * normal);
        vec4 wp = modelMatrix * vec4(position, 1.0);
        vWorldPos = wp.xyz;
        gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
      }
    `,
    fragmentShader: `
      uniform float uTime;
      uniform vec3 uColor1;
      uniform vec3 uColor2;
      uniform vec3 uGridColor;
      uniform vec3 uScanColor;
      varying vec2 vUv;
      varying vec3 vNormal;
      varying vec3 vWorldPos;

      void main() {
        vec3 base = mix(uColor1, uColor2, vUv.y);
        base *= 0.5;

        float gridX = step(0.94, abs(fract(vUv.x * 6.0 + 0.5) - 0.5) * 2.0);
        float gridY = step(0.94, abs(fract(vUv.y * 4.0 + 0.5) - 0.5) * 2.0);
        float grid = max(gridX, gridY) * 0.4;

        float sweepDir = uTime * 0.04;
        float scan = 0.0;
        for (int i = 0; i < 3; i++) {
          float fi = float(i);
          float sweepX = fract(sweepDir + fi * 0.333) * 6.0 - 3.0;
          float dist = vWorldPos.x * 1.2 - sweepX;
          scan = max(scan, exp(-dist * dist * 250.0));
        }

        float rim = 1.0 - abs(dot(vNormal, vec3(0.0, 0.0, 1.0)));
        rim = pow(rim, 2.0) * 0.3;

        vec3 color = base + grid * uGridColor + scan * uScanColor + rim * uGridColor * 0.5;
        float alpha = 0.08 + grid * 0.20 + rim * 0.15 + scan * 0.9;

        gl_FragColor = vec4(color, alpha);
      }
    `,
  });
  const sphere = new THREE.Mesh(sphereGeo, sphereMat);
  sphere.renderOrder = 0;
  scene.add(sphere);

  /* ---- Faint outline ring ---- */
  const ringGeo = new THREE.TorusGeometry(2.0, 0.008, 16, 64);
  const ringMat = new THREE.MeshBasicMaterial({
    color: 0x00ff66,
    transparent: true,
    opacity: 0.12,
  });
  const ring = new THREE.Mesh(ringGeo, ringMat);
  ring.rotation.x = Math.PI / 3;
  ring.renderOrder = 0;
  scene.add(ring);

  /* ---- Brain particle system (SDF-based brain shape) ---- */
  const brainGroup = new THREE.Group();
  brainGroup.renderOrder = 1;

  /* Volume test: is point inside the brain? */
  function isInsideBrain(x, y, z) {
    const ax = Math.abs(x);
    const sx = x < 0 ? -1 : 1;

    /* Longitudinal fissure — gap between hemispheres */
    const fissureW = 0.04 + 0.04 * Math.max(0, 1 - Math.abs(z) / 1.0);
    if (ax < fissureW && z < 0.6 && y > -0.2) return false;
    if (ax < 0.02 && z < 0.4 && y > -0.3) return false;

    /* Main cerebral hemispheres — wider at back (occipital), tapered at front (frontal) */
    const zNorm = z / 0.85;
    const rx = 0.62 + 0.20 * Math.max(0, -zNorm);
    const ry = 0.52;
    const rz = 0.82;

    const dCerebrum = (x*x)/(rx*rx) + (y*y)/(ry*ry) + (z*z)/(rz*rz);
    let inside = dCerebrum <= 1.0;

    /* Temporal lobes — slightly larger, shifted slightly forward */
    const tCx = sx * 0.59, tCy = -0.07, tCz = 0.16;
    const tRx = 0.14, tRy = 0.16, tRz = 0.24;
    const dTemporal = ((x-tCx)*(x-tCx))/(tRx*tRx) + ((y-tCy)*(y-tCy))/(tRy*tRy) + ((z-tCz)*(z-tCz))/(tRz*tRz);
    if (dTemporal <= 1.0) inside = true;

    /* Cerebellum — slightly larger, more distinct */
    const cbCx = 0, cbCy = -0.36, cbCz = -0.80;
    const cbRx = 0.34, cbRy = 0.22, cbRz = 0.23;
    const dCerebellum = ((x-cbCx)*(x-cbCx))/(cbRx*cbRx) + ((y-cbCy)*(y-cbCy))/(cbRy*cbRy) + ((z-cbCz)*(z-cbCz))/(cbRz*cbRz);
    if (dCerebellum <= 1.0) inside = true;

    /* Brainstem */
    const bsCx = 0, bsCy = -0.74, bsCz = -0.29;
    const bsRx = 0.10, bsRy = 0.29, bsRz = 0.10;
    const dBrainstem = ((x-bsCx)*(x-bsCx))/(bsRx*bsRx) + ((y-bsCy)*(y-bsCy))/(bsRy*bsRy) + ((z-bsCz)*(z-bsCz))/(bsRz*bsRz);
    if (dBrainstem <= 1.0) inside = true;

    return inside;
  }

  /* Rejection sampling within bounding box */
  const BOUNDS = { x: [-1.2, 1.2], y: [-1.0, 0.8], z: [-1.0, 1.0] };
  const maxPoints = 4000;
  let brainPoints = [];
  let attempts = 0;
  while (brainPoints.length < maxPoints && attempts < maxPoints * 30) {
    const px = BOUNDS.x[0] + Math.random() * (BOUNDS.x[1] - BOUNDS.x[0]);
    const py = BOUNDS.y[0] + Math.random() * (BOUNDS.y[1] - BOUNDS.y[0]);
    const pz = BOUNDS.z[0] + Math.random() * (BOUNDS.z[1] - BOUNDS.z[0]);
    attempts++;
    if (isInsideBrain(px, py, pz)) {
      brainPoints.push(new THREE.Vector3(px, py, pz));
    }
  }

  const particlePos = new Float32Array(brainPoints.length * 3);
  const particleCol = new Float32Array(brainPoints.length * 3);
  for (let i = 0; i < brainPoints.length; i++) {
    particlePos[i*3] = brainPoints[i].x;
    particlePos[i*3+1] = brainPoints[i].y;
    particlePos[i*3+2] = brainPoints[i].z;
    const b = 0.3 + Math.random() * 0.7;
    particleCol[i*3] = 0.05 + Math.random() * 0.15;
    particleCol[i*3+1] = 0.3 + b * 0.7;
    particleCol[i*3+2] = 0.05 + Math.random() * 0.15;
  }

  const pGeo = new THREE.BufferGeometry();
  pGeo.setAttribute('position', new THREE.BufferAttribute(particlePos, 3));
  pGeo.setAttribute('color', new THREE.BufferAttribute(particleCol, 3));

  const pMat = new THREE.PointsMaterial({
    size: 0.08,
    vertexColors: true,
    transparent: true,
    opacity: 0.95,
    blending: THREE.AdditiveBlending,
    depthWrite: false,
  });
  const particles = new THREE.Points(pGeo, pMat);
  brainGroup.add(particles);

  /* Connections */
  const connPos = [];
  for (let i = 0; i < brainPoints.length; i++) {
    for (let j = i + 1; j < brainPoints.length; j++) {
      const d = brainPoints[i].distanceTo(brainPoints[j]);
      if (d < 0.35 && Math.random() < 0.04) {
        connPos.push(brainPoints[i].x, brainPoints[i].y, brainPoints[i].z);
        connPos.push(brainPoints[j].x, brainPoints[j].y, brainPoints[j].z);
      }
    }
  }
  const cGeo = new THREE.BufferGeometry();
  cGeo.setAttribute('position', new THREE.Float32BufferAttribute(connPos, 3));
  const cMat = new THREE.LineBasicMaterial({
    color: 0x00ff66,
    transparent: true,
    opacity: 0.10,
  });
  const connections = new THREE.LineSegments(cGeo, cMat);
  brainGroup.add(connections);

  /* Center glow */
  const glowGeo2 = new THREE.SphereGeometry(0.08, 8, 8);
  const glowMat2 = new THREE.MeshBasicMaterial({
    color: 0x00ff88,
    transparent: true,
    opacity: 0.6,
    depthWrite: false,
  });
  const centerGlow = new THREE.Mesh(glowGeo2, glowMat2);
  brainGroup.add(centerGlow);

  brainGroup.scale.set(1.75, 1.75, 1.75);
  brainGroup.position.y = -0.05;
  brainGroup.rotation.y = Math.PI / 2;
  scene.add(brainGroup);

  /* ---- Stars background ---- */
  const starCount = 800;
  const starGeo3 = new THREE.BufferGeometry();
  const starPos2 = new Float32Array(starCount * 3);
  for (let i = 0; i < starCount * 3; i++) {
    starPos2[i] = (Math.random() - 0.5) * 40;
  }
  starGeo3.setAttribute('position', new THREE.BufferAttribute(starPos2, 3));
  const starMat3 = new THREE.PointsMaterial({
    color: 0x226644,
    size: 0.02,
    transparent: true,
    opacity: 0.3,
    blending: THREE.AdditiveBlending,
  });
  const stars = new THREE.Points(starGeo3, starMat3);
  scene.add(stars);

  /* ---- Resize ---- */
  function resize() {
    const r = container.getBoundingClientRect();
    const nw = r.width, nh = r.height;
    camera.aspect = nw / nh;
    camera.updateProjectionMatrix();
    renderer.setSize(nw, nh);
  }
  window.addEventListener('resize', resize);

  /* ---- Animate ---- */
  function animate(time) {
    requestAnimationFrame(animate);
    const t = time * 0.001;

    sphereMat.uniforms.uTime.value = t;
    sphere.rotation.y = t * 0.025;
    ring.rotation.z = t * 0.02;

    controls.update();
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
